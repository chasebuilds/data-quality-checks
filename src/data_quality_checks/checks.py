"""Compile each check to one DuckDB query that returns the rows breaking the rule.

A check passes when its query returns zero rows. Row-level checks return the offending
rows themselves; table-level checks (row_count, freshness) return one summary row.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

from .config import CheckSpec
from .sql import literal, quote

Compiler = Callable[[CheckSpec, date], str]


def _not_null(c: CheckSpec, today: date) -> str:
    col = quote(c.params["column"])
    cond = f"{col} IS NULL"
    if c.params["blank_is_null"]:
        cond += f" OR trim(CAST({col} AS VARCHAR)) = ''"
    return f"SELECT * FROM {quote(c.table or '')} WHERE {cond}"


def _unique(c: CheckSpec, today: date) -> str:
    cols = ", ".join(quote(x) for x in c.params["columns"])
    present = " AND ".join(f"{quote(x)} IS NOT NULL" for x in c.params["columns"])
    return (
        f"SELECT * EXCLUDE (_dqc_copies), _dqc_copies AS copies FROM ("
        f"SELECT *, count(*) OVER (PARTITION BY {cols}) AS _dqc_copies "
        f"FROM {quote(c.table or '')} WHERE {present}) "
        f"WHERE _dqc_copies > 1 ORDER BY {cols}"
    )


def _accepted_values(c: CheckSpec, today: date) -> str:
    col = quote(c.params["column"])
    allowed = ", ".join(literal(str(v)) for v in c.params["values"])
    return (f"SELECT * FROM {quote(c.table or '')} "
            f"WHERE {col} IS NOT NULL AND CAST({col} AS VARCHAR) NOT IN ({allowed})")


def _range(c: CheckSpec, today: date) -> str:
    col = quote(c.params["column"])
    num = f"TRY_CAST({col} AS DOUBLE)"
    bad = [f"{num} IS NULL"]
    if "min" in c.params:
        bad.append(f"{num} < {literal(c.params['min'])}")
    if "max" in c.params:
        bad.append(f"{num} > {literal(c.params['max'])}")
    return (f"SELECT * FROM {quote(c.table or '')} "
            f"WHERE {col} IS NOT NULL AND ({' OR '.join(bad)})")


def _regex(c: CheckSpec, today: date) -> str:
    col = quote(c.params["column"])
    return (f"SELECT * FROM {quote(c.table or '')} WHERE {col} IS NOT NULL "
            f"AND NOT regexp_full_match(CAST({col} AS VARCHAR), {literal(c.params['pattern'])})")


def _row_count(c: CheckSpec, today: date) -> str:
    bad = []
    if "min" in c.params:
        bad.append(f"row_count < {literal(c.params['min'])}")
    if "max" in c.params:
        bad.append(f"row_count > {literal(c.params['max'])}")
    return (f"SELECT row_count FROM (SELECT count(*) AS row_count FROM {quote(c.table or '')}) "
            f"WHERE {' OR '.join(bad)}")


def _foreign_key(c: CheckSpec, today: date) -> str:
    col, ref_col = quote(c.params["column"]), quote(c.params["ref_column"])
    return (
        f"SELECT child.* FROM {quote(c.table or '')} AS child "
        f"WHERE child.{col} IS NOT NULL AND NOT EXISTS ("
        f"SELECT 1 FROM {quote(c.params['ref_table'])} AS parent "
        f"WHERE CAST(parent.{ref_col} AS VARCHAR) = CAST(child.{col} AS VARCHAR))"
    )


def _freshness(c: CheckSpec, today: date) -> str:
    col = quote(c.params["column"])
    as_of = c.params.get("as_of") or today
    as_date = f"coalesce(TRY_CAST({col} AS DATE), CAST(TRY_CAST({col} AS TIMESTAMP) AS DATE))"
    return (
        f"SELECT latest, {literal(as_of)} - latest AS age_days, "
        f"{literal(c.params['max_age_days'])} AS max_age_days "
        f"FROM (SELECT max({as_date}) AS latest FROM {quote(c.table or '')}) "
        f"WHERE latest IS NULL OR {literal(as_of)} - latest > {literal(c.params['max_age_days'])}"
    )


def _sql(c: CheckSpec, today: date) -> str:
    query = c.params["query"].strip().rstrip(";").strip()
    return f"SELECT * FROM (\n{query}\n) AS custom_check"


COMPILERS: dict[str, Compiler] = {
    "not_null": _not_null,
    "unique": _unique,
    "accepted_values": _accepted_values,
    "range": _range,
    "regex": _regex,
    "row_count": _row_count,
    "foreign_key": _foreign_key,
    "freshness": _freshness,
    "sql": _sql,
}


def compile_check(check: CheckSpec, today: date | None = None) -> str:
    return COMPILERS[check.type](check, today or date.today())
