"""Quoting helpers. Every name and value that reaches SQL passes through here."""

from __future__ import annotations

from datetime import date


def quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def literal(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int | float):
        return repr(value)
    if isinstance(value, date):
        return f"DATE '{value.isoformat()}'"
    return "'" + str(value).replace("'", "''") + "'"
