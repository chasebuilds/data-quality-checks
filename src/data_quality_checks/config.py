"""Read a checks file (YAML or TOML) into a validated Suite."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

SEVERITIES = ("error", "warn")
FILE_SUFFIXES = (".csv", ".tsv", ".txt", ".parquet", ".xlsx", ".xlsm")
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# For each check type: required keys and optional keys (besides type, name, table, severity).
CHECK_KEYS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "not_null": (frozenset({"column"}), frozenset({"blank_is_null"})),
    "unique": (frozenset(), frozenset({"column", "columns"})),
    "accepted_values": (frozenset({"column", "values"}), frozenset()),
    "range": (frozenset({"column"}), frozenset({"min", "max"})),
    "regex": (frozenset({"column", "pattern"}), frozenset()),
    "row_count": (frozenset(), frozenset({"min", "max"})),
    "foreign_key": (frozenset({"column", "references"}), frozenset()),
    "freshness": (frozenset({"column", "max_age_days"}), frozenset({"as_of"})),
    "sql": (frozenset({"query"}), frozenset()),
}
CHECK_TYPES = tuple(CHECK_KEYS)


class ConfigError(ValueError):
    """The checks file is missing, unreadable or describes an invalid check."""


@dataclass(frozen=True)
class TableSource:
    name: str
    path: Path
    sheet: str | None = None


@dataclass(frozen=True)
class CheckSpec:
    type: str
    name: str
    table: str | None
    severity: str = "error"
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Suite:
    tables: dict[str, TableSource]
    checks: list[CheckSpec]
    source: Path | None = None


def identifier(value: object, what: str) -> str:
    if not isinstance(value, str) or not _IDENT.match(value):
        raise ConfigError(f"{what} must be a plain name (letters, digits, _): {value!r}")
    return value


def load_suite(path: str | Path) -> Suite:
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"checks file not found: {path}")
    text = path.read_text(encoding="utf-8")
    try:
        raw = tomllib.loads(text) if path.suffix.lower() == ".toml" else yaml.safe_load(text)
    except (tomllib.TOMLDecodeError, yaml.YAMLError) as exc:
        raise ConfigError(f"could not parse {path.name}: {exc}") from exc
    return parse_suite(raw, base_dir=path.parent, source=path)


def parse_suite(raw: object, base_dir: Path, source: Path | None = None) -> Suite:
    if not isinstance(raw, dict):
        raise ConfigError("checks file must be a mapping with 'tables' and 'checks'")
    unknown = set(raw) - {"tables", "checks"}
    if unknown:
        raise ConfigError(f"unknown top-level keys: {', '.join(sorted(unknown))}")
    tables = _parse_tables(raw.get("tables"), base_dir)
    raw_checks = raw.get("checks")
    if not isinstance(raw_checks, list) or not raw_checks:
        raise ConfigError("'checks' must be a non-empty list")
    checks = [_parse_check(item, i, tables) for i, item in enumerate(raw_checks, start=1)]
    names = [c.name for c in checks]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        raise ConfigError(f"check names must be unique; repeated: {', '.join(dupes)}")
    return Suite(tables=tables, checks=checks, source=source)


def _parse_tables(raw: object, base_dir: Path) -> dict[str, TableSource]:
    if not isinstance(raw, dict) or not raw:
        raise ConfigError("'tables' must map table names to file paths")
    tables: dict[str, TableSource] = {}
    for name, spec in raw.items():
        identifier(name, "table name")
        sheet = None
        if isinstance(spec, dict):
            extra = set(spec) - {"path", "sheet"}
            if extra or "path" not in spec:
                raise ConfigError(f"table {name}: use 'path' and optional 'sheet'")
            sheet, spec = spec.get("sheet"), spec["path"]
        if not isinstance(spec, str):
            raise ConfigError(f"table {name}: path must be text")
        path = Path(spec)
        if not path.is_absolute():
            path = base_dir / path
        if path.suffix.lower() not in FILE_SUFFIXES:
            raise ConfigError(f"table {name}: unsupported file type {path.suffix or '(none)'}")
        tables[name] = TableSource(name=name, path=path, sheet=sheet)
    return tables


def _parse_check(item: object, index: int, tables: dict[str, TableSource]) -> CheckSpec:
    where = f"check {index}"
    if not isinstance(item, dict):
        raise ConfigError(f"{where}: must be a mapping")
    kind = item.get("type")
    if kind not in CHECK_KEYS:
        raise ConfigError(f"{where}: type must be one of {', '.join(CHECK_TYPES)}, got {kind!r}")
    required, optional = CHECK_KEYS[kind]
    params = {k: v for k, v in item.items() if k not in {"type", "name", "table", "severity"}}
    missing = required - set(params)
    if missing:
        raise ConfigError(f"{where} ({kind}): missing {', '.join(sorted(missing))}")
    unknown = set(params) - required - optional
    if unknown:
        raise ConfigError(f"{where} ({kind}): unknown keys {', '.join(sorted(unknown))}")

    table = item.get("table")
    if kind != "sql" or table is not None:
        identifier(table, f"{where}: table")
        if table not in tables:
            raise ConfigError(f"{where}: table {table!r} is not listed under 'tables'")
    severity = item.get("severity", "error")
    if severity not in SEVERITIES:
        raise ConfigError(f"{where}: severity must be error or warn")

    params = _check_params(kind, params, where, tables)
    name = item.get("name") or _default_name(kind, table, params)
    if not isinstance(name, str):
        raise ConfigError(f"{where}: name must be text")
    return CheckSpec(type=kind, name=name, table=table, severity=severity, params=params)


def _check_params(
    kind: str, params: dict[str, Any], where: str, tables: dict[str, TableSource]
) -> dict[str, Any]:
    out = dict(params)
    if "column" in out:
        identifier(out["column"], f"{where}: column")
    if kind == "unique":
        cols = out.pop("columns", None) or ([out.pop("column")] if "column" in out else None)
        out.pop("column", None)
        if not isinstance(cols, list) or not cols:
            raise ConfigError(f"{where} (unique): give 'column' or a list of 'columns'")
        out["columns"] = [identifier(c, f"{where}: column") for c in cols]
    elif kind == "not_null":
        out["blank_is_null"] = bool(out.get("blank_is_null", True))
    elif kind == "accepted_values":
        if not isinstance(out["values"], list) or not out["values"]:
            raise ConfigError(f"{where} (accepted_values): 'values' must be a non-empty list")
    elif kind in ("range", "row_count"):
        if "min" not in out and "max" not in out:
            raise ConfigError(f"{where} ({kind}): give 'min', 'max' or both")
        for key in ("min", "max"):
            if key in out and (isinstance(out[key], bool) or
                               not isinstance(out[key], int | float)):
                raise ConfigError(f"{where} ({kind}): {key} must be a number")
        if "min" in out and "max" in out and out["min"] > out["max"]:
            raise ConfigError(f"{where} ({kind}): min is greater than max")
    elif kind == "regex":
        try:
            re.compile(out["pattern"])
        except (re.error, TypeError) as exc:
            raise ConfigError(f"{where} (regex): bad pattern: {exc}") from exc
    elif kind == "foreign_key":
        ref = out["references"]
        if not isinstance(ref, str) or ref.count(".") != 1:
            raise ConfigError(f"{where} (foreign_key): references must be 'table.column'")
        ref_table, ref_col = ref.split(".")
        identifier(ref_table, f"{where}: referenced table")
        identifier(ref_col, f"{where}: referenced column")
        if ref_table not in tables:
            raise ConfigError(f"{where}: referenced table {ref_table!r} is not listed")
        out["ref_table"], out["ref_column"] = ref_table, ref_col
    elif kind == "freshness":
        days = out["max_age_days"]
        if isinstance(days, bool) or not isinstance(days, int) or days < 0:
            raise ConfigError(f"{where} (freshness): max_age_days must be a whole number >= 0")
        as_of = out.get("as_of")
        if as_of is not None:
            try:
                out["as_of"] = as_of if isinstance(as_of, date) else date.fromisoformat(as_of)
            except (TypeError, ValueError) as exc:
                raise ConfigError(f"{where} (freshness): as_of must be YYYY-MM-DD") from exc
    elif kind == "sql" and (not isinstance(out["query"], str) or not out["query"].strip()):
        raise ConfigError(f"{where} (sql): query must be non-empty text")
    return out


def _default_name(kind: str, table: str | None, params: dict[str, Any]) -> str:
    if "columns" in params:
        target = f"{table}.{'+'.join(params['columns'])}"
    elif "column" in params:
        target = f"{table}.{params['column']}"
    else:
        target = table or "query"
    return f"{kind}:{target}"
