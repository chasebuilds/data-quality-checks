from datetime import date

import pytest

from data_quality_checks.checks import compile_check
from data_quality_checks.config import parse_suite
from data_quality_checks.loader import connect
from data_quality_checks.runner import run_check, run_suite

TODAY = date(2026, 11, 21)


def _run(tmp_path, shop, check, sample_rows=5):
    suite = parse_suite({"tables": shop, "checks": [check]}, base_dir=tmp_path)
    con = connect(suite.tables)
    try:
        return run_check(con, suite.checks[0], sample_rows, today=TODAY)
    finally:
        con.close()


def _ids(result, column):
    index = result.columns.index(column)
    return sorted(row[index] for row in result.sample)


def test_not_null_counts_blanks_by_default(tmp_path, shop):
    result = _run(tmp_path, shop, {"table": "customers", "type": "not_null", "column": "email"})
    assert result.status == "fail"
    assert result.failing_rows == 1
    assert _ids(result, "customer_id") == [2]


def test_not_null_can_allow_blanks(tmp_path, write_csv):
    write_csv("t.csv", ["a", "b"], [[1, " "], [2, "x"]])
    result = _run(tmp_path, {"t": "t.csv"},
                  {"table": "t", "type": "not_null", "column": "b", "blank_is_null": False})
    assert result.status == "pass"


def test_unique_returns_every_copy(tmp_path, shop):
    result = _run(tmp_path, shop,
                  {"table": "customers", "type": "unique", "column": "customer_id"})
    assert result.failing_rows == 2
    assert _ids(result, "customer_id") == [3, 3]
    assert set(r[result.columns.index("copies")] for r in result.sample) == {2}


def test_unique_on_several_columns_ignores_nulls(tmp_path, write_csv):
    write_csv("t.csv", ["a", "b"], [[1, "x"], [1, "y"], [None, "z"], [None, "z"]])
    result = _run(tmp_path, {"t": "t.csv"}, {"table": "t", "type": "unique", "columns": ["a", "b"]})
    assert result.status == "pass"


def test_accepted_values(tmp_path, shop):
    result = _run(tmp_path, shop, {"table": "customers", "type": "accepted_values",
                                   "column": "tier", "values": ["gold", "silver", "bronze"]})
    assert result.failing_rows == 1
    assert result.sample[0][result.columns.index("tier")] == "platinum"


def test_range_flags_out_of_bounds_and_non_numbers(tmp_path, write_csv):
    write_csv("t.csv", ["v"], [["5"], ["-1"], ["200"], ["abc"], [""]])
    result = _run(tmp_path, {"t": "t.csv"},
                  {"table": "t", "type": "range", "column": "v", "min": 0, "max": 100})
    assert sorted(r[0] for r in result.sample) == ["-1", "200", "abc"]


def test_range_with_only_a_minimum(tmp_path, shop):
    result = _run(tmp_path, shop, {"table": "orders", "type": "range", "column": "amount",
                                   "min": 0})
    assert _ids(result, "order_id") == [11]


def test_regex_must_match_the_whole_value(tmp_path, shop):
    result = _run(tmp_path, shop, {"table": "customers", "type": "regex", "column": "email",
                                   "pattern": r"[^@\s]+@[^@\s]+\.[a-z]{2,}"})
    assert _ids(result, "customer_id") == [3]


def test_row_count(tmp_path, shop):
    low = _run(tmp_path, shop, {"table": "orders", "type": "row_count", "min": 5})
    assert low.status == "fail"
    assert low.columns == ["row_count"]
    assert low.sample == [[3]]
    high = _run(tmp_path, shop, {"table": "orders", "type": "row_count", "max": 2})
    assert high.status == "fail"
    ok = _run(tmp_path, shop, {"table": "orders", "type": "row_count", "min": 1, "max": 3})
    assert ok.status == "pass"


def test_foreign_key(tmp_path, shop):
    result = _run(tmp_path, shop, {"table": "orders", "type": "foreign_key",
                                   "column": "customer_id", "references": "customers.customer_id"})
    assert _ids(result, "order_id") == [12]


def test_foreign_key_compares_as_text(tmp_path, write_csv):
    write_csv("p.csv", ["id"], [["A1"], ["B2"]])
    write_csv("c.csv", ["pid"], [["A1"], [None], ["C3"]])
    result = _run(tmp_path, {"p": "p.csv", "c": "c.csv"},
                  {"table": "c", "type": "foreign_key", "column": "pid", "references": "p.id"})
    assert result.sample == [["C3"]]


def test_freshness(tmp_path, shop):
    fresh = _run(tmp_path, shop, {"table": "orders", "type": "freshness",
                                  "column": "order_date", "max_age_days": 30})
    assert fresh.status == "pass"
    stale = _run(tmp_path, shop, {"table": "orders", "type": "freshness",
                                  "column": "order_date", "max_age_days": 7})
    assert stale.status == "fail"
    assert stale.sample == [["2026-11-03", 18, 7]]
    pinned = _run(tmp_path, shop, {"table": "orders", "type": "freshness",
                                   "column": "order_date", "max_age_days": 0,
                                   "as_of": "2026-11-03"})
    assert pinned.status == "pass"


def test_freshness_reads_timestamps_and_fails_on_no_dates(tmp_path, write_csv):
    write_csv("t.csv", ["ts", "junk"], [["2026-11-20 08:30:00", "soon"]])
    ok = _run(tmp_path, {"t": "t.csv"},
              {"table": "t", "type": "freshness", "column": "ts", "max_age_days": 1})
    assert ok.status == "pass"
    none = _run(tmp_path, {"t": "t.csv"},
                {"table": "t", "type": "freshness", "column": "junk", "max_age_days": 1})
    assert none.status == "fail"
    assert none.sample[0][0] is None


def test_custom_sql(tmp_path, shop):
    check = {"type": "sql", "name": "big orders from silver",
             "query": "SELECT o.* FROM orders o JOIN customers c USING (customer_id) "
                      "WHERE c.tier = 'gold' AND o.amount > 10;"}
    result = _run(tmp_path, shop, check)
    assert result.failing_rows == 1
    passing = _run(tmp_path, shop, {"type": "sql", "query": "SELECT * FROM orders WHERE false"})
    assert passing.status == "pass"


def test_broken_check_is_an_error_not_a_crash(tmp_path, shop):
    result = _run(tmp_path, shop, {"table": "orders", "type": "not_null", "column": "nope"})
    assert result.status == "error"
    assert "nope" in (result.error or "")
    assert result.blocking


def test_sample_size_is_capped(tmp_path, write_csv):
    write_csv("t.csv", ["a"], [[None]] * 12)
    result = _run(tmp_path, {"t": "t.csv"}, {"table": "t", "type": "not_null", "column": "a"},
                  sample_rows=3)
    assert result.failing_rows == 12
    assert len(result.sample) == 3
    none = _run(tmp_path, {"t": "t.csv"}, {"table": "t", "type": "not_null", "column": "a"},
                sample_rows=0)
    assert none.failing_rows == 12
    assert none.sample == []


def test_names_and_values_are_quoted(tmp_path, write_csv):
    write_csv("t.csv", ["kind"], [["it's"], ["plain"]])
    suite = parse_suite({"tables": {"t": "t.csv"}, "checks": [
        {"table": "t", "type": "accepted_values", "column": "kind", "values": ["it's"]},
    ]}, base_dir=tmp_path)
    sql = compile_check(suite.checks[0])
    assert "'it''s'" in sql
    assert '"kind"' in sql


@pytest.mark.parametrize("kind", ["warn", "error"])
def test_suite_result_and_severity(tmp_path, shop, kind):
    suite = parse_suite({"tables": shop, "checks": [
        {"table": "orders", "type": "row_count", "min": 1},
        {"table": "orders", "type": "range", "column": "amount", "min": 0, "severity": kind},
    ]}, base_dir=tmp_path)
    result = run_suite(suite, today=TODAY)
    assert [r.status for r in result.results] == ["pass", "fail"]
    assert result.passed is (kind == "warn")
    expected = {"pass": 1, "fail": 0, "warn": 0, "error": 0}
    expected["warn" if kind == "warn" else "fail"] = 1
    assert result.counts() == expected
