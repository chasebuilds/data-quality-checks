"""Expose each listed file to DuckDB as a view or registered table."""

from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
from openpyxl import load_workbook

from .config import ConfigError, TableSource
from .sql import literal, quote


def connect(tables: dict[str, TableSource]) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    for source in tables.values():
        register(con, source)
    return con


def register(con: duckdb.DuckDBPyConnection, source: TableSource) -> None:
    path = source.path
    if not path.is_file():
        raise ConfigError(f"table {source.name}: file not found: {path}")
    suffix = path.suffix.lower()
    name = quote(source.name)
    try:
        if suffix in (".csv", ".tsv", ".txt"):
            con.execute(f"CREATE VIEW {name} AS SELECT * FROM read_csv_auto({literal(str(path))})")
        elif suffix == ".parquet":
            con.execute(f"CREATE VIEW {name} AS SELECT * FROM read_parquet({literal(str(path))})")
        else:
            con.register(source.name, read_excel(path, source.sheet))
        con.execute(f"SELECT * FROM {name} LIMIT 0")
    except duckdb.Error as exc:
        raise ConfigError(f"table {source.name}: could not read {path.name}: {exc}") from exc


def read_excel(path: Path, sheet: str | None = None) -> pa.Table:
    """First row is the header. Columns with mixed value types are read as text."""
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        if sheet is not None and sheet not in workbook.sheetnames:
            raise ConfigError(f"{path.name}: no sheet named {sheet!r}")
        rows = workbook[sheet] if sheet else workbook.worksheets[0]
        values = [list(r) for r in rows.iter_rows(values_only=True)]
    finally:
        workbook.close()
    if not values:
        raise ConfigError(f"{path.name}: sheet is empty")
    header = [str(h).strip() if h is not None else f"column_{i}"
              for i, h in enumerate(values[0], start=1)]
    body = [r for r in values[1:] if any(v is not None and v != "" for v in r)]
    columns: dict[str, list[Any]] = {}
    for i, name in enumerate(header):
        cells = [(r[i] if i < len(r) else None) for r in body]
        columns[name] = _column(cells)
    return pa.table(columns)


def _column(cells: list[Any]) -> list[Any]:
    cells = [None if isinstance(c, str) and not c.strip() else c for c in cells]
    kinds = {_kind(c) for c in cells if c is not None}
    if len(kinds) <= 1 or kinds == {"int", "float"}:
        if kinds == {"int", "float"}:
            return [None if c is None else float(c) for c in cells]
        return cells
    return [None if c is None else _text(c) for c in cells]


def _kind(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, datetime):
        return "datetime"
    if isinstance(value, date):
        return "date"
    if isinstance(value, time):
        return "time"
    return "str"


def _text(value: Any) -> str:
    if isinstance(value, datetime) and value.time() == time(0):
        return value.date().isoformat()
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    return str(value)
