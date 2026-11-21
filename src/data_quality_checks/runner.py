"""Run a suite against its files and collect one result per check."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb

from .checks import compile_check
from .config import CheckSpec, Suite
from .loader import connect


@dataclass
class CheckResult:
    check: CheckSpec
    sql: str
    status: str  # "pass", "fail" or "error"
    failing_rows: int = 0
    columns: list[str] = field(default_factory=list)
    sample: list[list[Any]] = field(default_factory=list)
    error: str | None = None
    seconds: float = 0.0

    @property
    def blocking(self) -> bool:
        """True when this result should make the run fail."""
        return self.status != "pass" and self.check.severity == "error"


@dataclass
class SuiteResult:
    results: list[CheckResult]
    files: dict[str, str]
    started: datetime
    seconds: float

    @property
    def passed(self) -> bool:
        return not any(r.blocking for r in self.results)

    def counts(self) -> dict[str, int]:
        out = {"pass": 0, "fail": 0, "warn": 0, "error": 0}
        for r in self.results:
            if r.status == "pass":
                out["pass"] += 1
            elif r.status == "error":
                out["error"] += 1
            elif r.check.severity == "warn":
                out["warn"] += 1
            else:
                out["fail"] += 1
        return out


def run_check(
    con: duckdb.DuckDBPyConnection, check: CheckSpec, sample_rows: int = 5,
    today: date | None = None,
) -> CheckResult:
    sql = compile_check(check, today)
    started = time.perf_counter()
    try:
        count_row = con.execute(f"SELECT count(*) FROM ({sql}) AS q").fetchone()
        failing = int(count_row[0]) if count_row else 0
        columns: list[str] = []
        sample: list[list[Any]] = []
        if failing and sample_rows > 0:
            cursor = con.execute(f"SELECT * FROM ({sql}) AS q LIMIT {int(sample_rows)}")
            columns = [d[0] for d in cursor.description]
            sample = [[_plain(v) for v in row] for row in cursor.fetchall()]
    except duckdb.Error as exc:
        return CheckResult(check, sql, "error", error=str(exc).splitlines()[0],
                           seconds=time.perf_counter() - started)
    return CheckResult(check, sql, "fail" if failing else "pass", failing, columns, sample,
                       seconds=time.perf_counter() - started)


def run_suite(suite: Suite, sample_rows: int = 5, today: date | None = None) -> SuiteResult:
    started_at = datetime.now().replace(microsecond=0)
    started = time.perf_counter()
    con = connect(suite.tables)
    try:
        results = [run_check(con, c, sample_rows, today) for c in suite.checks]
    finally:
        con.close()
    files = {name: _display(src.path, suite.source) for name, src in suite.tables.items()}
    return SuiteResult(results, files, started_at, time.perf_counter() - started)


def _display(path: Path, checks_file: Path | None) -> str:
    if checks_file is not None:
        try:
            return path.relative_to(checks_file.parent).as_posix()
        except ValueError:
            pass
    return path.as_posix()


def _plain(value: Any) -> Any:
    """Make a DuckDB value safe for JSON and HTML."""
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    return str(value)
