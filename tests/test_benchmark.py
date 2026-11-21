import csv
import random
from pathlib import Path

import pytest

from benchmark.generate import FAULTS, FAULTS_BY_NAME, make_dataset, make_samples, plant
from benchmark.run_benchmark import TABLE_LEVEL, flagged, result_table, score_clean, score_fault

ROOT = Path(__file__).resolve().parent.parent
SMALL = {"customers": 60, "orders": 300, "products": 20}


def test_generator_is_seeded(tmp_path):
    make_samples(tmp_path / "a", seed=3)
    make_samples(tmp_path / "b", seed=3)
    make_samples(tmp_path / "c", seed=4)
    for name in ("customers.csv", "checks.yml", "planted.csv"):
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes()
    assert (tmp_path / "a" / "customers.csv").read_bytes() != (
        tmp_path / "c" / "customers.csv").read_bytes()


def test_clean_data_raises_no_alarms(tmp_path):
    clean = score_clean(2, SMALL, seed=11, work=tmp_path)
    assert clean == {"trials": 2, "checks_run": 22, "failing": 0, "rows": 0}


def test_one_row_never_carries_two_faults():
    ds = make_dataset(**SMALL, seed=5)
    rng = random.Random(5)
    for fault in FAULTS:
        if fault.key:
            plant(ds, fault, 20, rng)
    keys = [(table, key) for name, table, key in ds.planted
            if name != "duplicate_customer"]
    assert len(keys) == len(set(keys))


@pytest.mark.parametrize("fault", [f for f in FAULTS if f.target and f.name != "bad_email"],
                         ids=lambda f: f.name)
def test_each_intended_check_catches_its_fault(tmp_path, fault):
    ds = make_dataset(**SMALL, seed=9)
    keys = set(plant(ds, fault, 4, random.Random(9)))
    hits = flagged(ds, tmp_path)
    expected = {TABLE_LEVEL} if not fault.key else keys
    assert expected <= hits[fault.target]


def test_email_format_misses_a_domain_cut_short(tmp_path):
    """mail.example.net cut to mail.example still looks like a valid address."""
    ds = make_dataset(**SMALL, seed=9)
    keys = set(plant(ds, FAULTS_BY_NAME["bad_email"], 4, random.Random(9)))
    emails = {c["customer_id"]: c["email"] for c in ds.customers}
    caught = flagged(ds, tmp_path)["customer email format"]
    for key in keys:
        assert (key in caught) != emails[key].endswith("@mail.example"), emails[key]


def test_plausible_faults_are_not_caught(tmp_path):
    score = score_fault(FAULTS_BY_NAME["plausible_quantity"], 1, 5, SMALL, 3, tmp_path)
    assert (score.planted, score.caught_by_target, score.caught_by_any) == (5, 0, 0)
    assert "| plausible_quantity | (none) | 5 | 0 | **0.0%** |" in result_table([score])


def test_committed_samples_report_every_planted_row():
    """The sample report must flag every planted row that a check is meant to catch."""
    import json

    report = json.loads((ROOT / "samples" / "report" / "report.json").read_text("utf-8"))
    by_name = {c["name"]: c for c in report["checks"]}
    with open(ROOT / "samples" / "planted.csv", encoding="utf-8") as handle:
        planted = list(csv.DictReader(handle))
    for row in planted:
        check = by_name.get(row["meant_to_be_caught_by"])
        if check is None:
            continue
        assert check["status"] == "fail"
        keys = {str(r[0]) for r in check["sample_rows"]}
        assert row["key"] in keys, row


def test_docs_have_no_em_dash():
    for path in [*ROOT.glob("*.md"), *ROOT.glob("src/**/*.py"), *ROOT.glob("benchmark/*.py")]:
        assert chr(0x2014) not in path.read_text(encoding="utf-8"), path
