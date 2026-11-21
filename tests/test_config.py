from datetime import date

import pytest

from data_quality_checks import ConfigError, load_suite
from data_quality_checks.config import parse_suite
from data_quality_checks.sql import literal, quote


def _suite(tmp_path, checks, tables=None):
    tables = tables or {"t": "t.csv", "u": "u.csv"}
    return parse_suite({"tables": tables, "checks": checks}, base_dir=tmp_path)


def test_loads_yaml_and_resolves_paths(tmp_path, write_checks, shop):
    path = write_checks({"tables": shop, "checks": [
        {"table": "customers", "type": "unique", "column": "customer_id"},
    ]})
    suite = load_suite(path)
    assert suite.tables["customers"].path == tmp_path / "customers.csv"
    check = suite.checks[0]
    assert check.name == "unique:customers.customer_id"
    assert check.params == {"columns": ["customer_id"]}
    assert check.severity == "error"


def test_loads_toml(tmp_path):
    path = tmp_path / "checks.toml"
    path.write_text(
        '[tables]\nt = "t.csv"\n\n[[checks]]\ntable = "t"\ntype = "row_count"\nmin = 1\n',
        encoding="utf-8",
    )
    suite = load_suite(path)
    assert suite.checks[0].params == {"min": 1}


def test_table_can_name_a_sheet(tmp_path):
    suite = _suite(tmp_path, [{"table": "x", "type": "row_count", "min": 1}],
                   tables={"x": {"path": "book.xlsx", "sheet": "Data"}})
    assert suite.tables["x"].sheet == "Data"


@pytest.mark.parametrize("check, message", [
    ({"table": "t", "type": "nope"}, "type must be one of"),
    ({"table": "t", "type": "not_null"}, "missing column"),
    ({"table": "t", "type": "not_null", "column": "a", "colour": 1}, "unknown keys colour"),
    ({"table": "zz", "type": "row_count", "min": 1}, "not listed"),
    ({"table": "t", "type": "row_count"}, "give 'min', 'max' or both"),
    ({"table": "t", "type": "range", "column": "a", "min": 5, "max": 1}, "min is greater"),
    ({"table": "t", "type": "range", "column": "a", "min": "low"}, "must be a number"),
    ({"table": "t", "type": "regex", "column": "a", "pattern": "("}, "bad pattern"),
    ({"table": "t", "type": "foreign_key", "column": "a", "references": "u"}, "table.column"),
    ({"table": "t", "type": "foreign_key", "column": "a", "references": "q.id"}, "not listed"),
    ({"table": "t", "type": "freshness", "column": "d", "max_age_days": -1}, "whole number"),
    ({"table": "t", "type": "freshness", "column": "d", "max_age_days": 3,
      "as_of": "yesterday"}, "YYYY-MM-DD"),
    ({"table": "t", "type": "accepted_values", "column": "a", "values": []}, "non-empty"),
    ({"table": "t", "type": "unique"}, "give 'column'"),
    ({"type": "sql", "query": "  "}, "non-empty"),
    ({"table": "t", "type": "not_null", "column": "a; DROP"}, "plain name"),
    ({"table": "t", "type": "row_count", "min": 1, "severity": "fatal"}, "error or warn"),
])
def test_rejects_bad_checks(tmp_path, check, message):
    with pytest.raises(ConfigError, match=message):
        _suite(tmp_path, [check])


def test_rejects_bad_files(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_suite(tmp_path / "missing.yml")
    bad = tmp_path / "bad.yml"
    bad.write_text("tables: [", encoding="utf-8")
    with pytest.raises(ConfigError, match="could not parse"):
        load_suite(bad)
    with pytest.raises(ConfigError, match="mapping"):
        parse_suite([1], tmp_path)
    with pytest.raises(ConfigError, match="unknown top-level"):
        parse_suite({"tables": {"t": "t.csv"}, "checks": [], "extra": 1}, tmp_path)
    with pytest.raises(ConfigError, match="non-empty list"):
        parse_suite({"tables": {"t": "t.csv"}, "checks": []}, tmp_path)
    with pytest.raises(ConfigError, match="unsupported file type"):
        parse_suite({"tables": {"t": "t.json"}, "checks": [{}]}, tmp_path)
    with pytest.raises(ConfigError, match="use 'path'"):
        parse_suite({"tables": {"t": {"file": "t.csv"}}, "checks": [{}]}, tmp_path)


def test_rejects_repeated_names(tmp_path):
    check = {"table": "t", "type": "row_count", "min": 1, "name": "same"}
    with pytest.raises(ConfigError, match="repeated: same"):
        _suite(tmp_path, [check, check])


def test_parses_freshness_date_and_foreign_key(tmp_path):
    suite = _suite(tmp_path, [
        {"table": "t", "type": "freshness", "column": "d", "max_age_days": 2,
         "as_of": "2026-11-21"},
        {"table": "t", "type": "foreign_key", "column": "a", "references": "u.id"},
        {"type": "sql", "query": "select 1", "severity": "warn"},
    ])
    assert suite.checks[0].params["as_of"] == date(2026, 11, 21)
    assert suite.checks[1].params["ref_table"] == "u"
    assert suite.checks[2].name == "sql:query"
    assert suite.checks[2].severity == "warn"


def test_quoting():
    assert quote('a"b') == '"a""b"'
    assert literal("it's") == "'it''s'"
    assert literal(None) == "NULL"
    assert literal(True) == "TRUE"
    assert literal(2.5) == "2.5"
    assert literal(date(2026, 1, 2)) == "DATE '2026-01-02'"
