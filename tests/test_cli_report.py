import json
import subprocess
import sys

import pytest

from data_quality_checks.cli import main
from data_quality_checks.config import parse_suite
from data_quality_checks.report import render_html, to_dict
from data_quality_checks.runner import run_suite

PASSING = [
    {"table": "orders", "type": "row_count", "min": 1},
    {"table": "orders", "type": "unique", "column": "order_id"},
]
FAILING = PASSING + [
    {"table": "customers", "type": "not_null", "column": "email"},
    {"table": "orders", "type": "range", "column": "amount", "min": 0, "severity": "warn"},
]


def test_exit_zero_when_everything_passes(tmp_path, write_checks, shop, capsys):
    path = write_checks({"tables": shop, "checks": PASSING})
    assert main(["run", str(path), "-o", str(tmp_path / "out")]) == 0
    out = capsys.readouterr().out
    assert "PASS   row_count:orders" in out
    assert "2 passed, 0 failed" in out
    assert (tmp_path / "out" / "report.html").is_file()
    assert json.loads((tmp_path / "out" / "report.json").read_text())["passed"] is True


def test_exit_one_on_failure(tmp_path, write_checks, shop, capsys):
    path = write_checks({"tables": shop, "checks": FAILING})
    assert main(["run", str(path), "-o", str(tmp_path / "out")]) == 1
    out = capsys.readouterr().out
    assert "FAIL   not_null:customers.email" in out
    assert "WARN   range:orders.amount" in out
    assert "1 failed, 1 warned" in out


def test_warnings_alone_pass_unless_strict(tmp_path, write_checks, shop):
    path = write_checks({"tables": shop, "checks": PASSING + [FAILING[-1]]})
    assert main(["run", str(path), "--no-report"]) == 0
    assert main(["run", str(path), "--no-report", "--strict"]) == 1
    assert not (tmp_path / "dqc-report").exists()


def test_exit_two_on_config_errors(tmp_path, write_checks, shop, capsys):
    assert main(["run", str(tmp_path / "missing.yml")]) == 2
    assert "checks file not found" in capsys.readouterr().err
    path = write_checks({"tables": {"x": "gone.csv"},
                         "checks": [{"table": "x", "type": "row_count", "min": 1}]})
    assert main(["run", str(path)]) == 2
    assert "file not found" in capsys.readouterr().err
    good = write_checks({"tables": shop, "checks": PASSING}, name="good.yml")
    assert main(["run", str(good), "--sample", "-1"]) == 2
    with pytest.raises(SystemExit) as exc:
        main(["run", str(good), "--as-of", "today"])
    assert exc.value.code == 2


def test_show_sql_prints_each_query(tmp_path, write_checks, shop, capsys):
    path = write_checks({"tables": shop, "checks": PASSING})
    main(["run", str(path), "--no-report", "--show-sql", "--as-of", "2026-11-21"])
    out = capsys.readouterr().out
    assert "-- row_count:orders\nSELECT row_count FROM" in out
    assert 'PARTITION BY "order_id"' in out


def test_json_report_lists_samples_and_sql(tmp_path, write_checks, shop):
    path = write_checks({"tables": shop, "checks": FAILING})
    main(["run", str(path), "-o", str(tmp_path / "out"), "--sample", "1"])
    report = json.loads((tmp_path / "out" / "report.json").read_text())
    assert report["passed"] is False
    assert report["counts"] == {"pass": 2, "fail": 1, "warn": 1, "error": 0}
    assert report["files"] == {"customers": "customers.csv", "orders": "orders.csv"}
    email = next(c for c in report["checks"] if c["type"] == "not_null")
    assert email["failing_rows"] == 1
    assert email["sample_columns"][:2] == ["customer_id", "email"]
    assert email["sample_rows"][0][0] == 2
    assert email["sql"].startswith("SELECT * FROM")


def test_html_report_escapes_and_links_failures(tmp_path, write_csv):
    write_csv("t.csv", ["note"], [["<script>x</script>"]])
    suite = parse_suite({"tables": {"t": "t.csv"}, "checks": [
        {"table": "t", "type": "accepted_values", "column": "note", "values": ["ok"],
         "name": "note <ok>"},
        {"table": "t", "type": "not_null", "column": "missing"},
    ]}, base_dir=tmp_path)
    page = render_html(run_suite(suite))
    assert "<script>x" not in page
    assert "&lt;script&gt;x&lt;/script&gt;" in page
    assert 'href="#c-note--ok-"' in page
    assert "could not run" in page
    assert "FAILED" in page
    assert to_dict(run_suite(suite))["counts"]["error"] == 1


def test_html_report_when_all_pass(tmp_path, write_csv):
    write_csv("t.csv", ["a"], [[1]])
    suite = parse_suite({"tables": {"t": "t.csv"},
                         "checks": [{"table": "t", "type": "row_count", "min": 1}]},
                        base_dir=tmp_path)
    page = render_html(run_suite(suite))
    assert "PASSED" in page
    assert "No failures." in page


def test_module_entry_point(tmp_path, write_checks, shop):
    path = write_checks({"tables": shop, "checks": FAILING})
    done = subprocess.run([sys.executable, "-m", "data_quality_checks", "run", str(path),
                           "--no-report"], capture_output=True, text=True)
    assert done.returncode == 1
    assert "FAIL" in done.stdout
