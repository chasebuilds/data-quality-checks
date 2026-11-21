from __future__ import annotations

import csv
from pathlib import Path

import pytest
import yaml


@pytest.fixture
def write_csv(tmp_path: Path):
    def _write(name: str, headers: list[str], rows: list[list[object]]) -> Path:
        path = tmp_path / name
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(headers)
            writer.writerows(rows)
        return path

    return _write


@pytest.fixture
def write_checks(tmp_path: Path):
    def _write(config: dict, name: str = "checks.yml") -> Path:
        path = tmp_path / name
        path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
        return path

    return _write


@pytest.fixture
def shop(write_csv):
    """Two small tables with one planted problem of each kind."""
    write_csv("customers.csv", ["customer_id", "email", "tier", "signup_date"], [
        [1, "ann@example.com", "gold", "2026-01-05"],
        [2, "", "silver", "2026-02-11"],
        [3, "bob@example", "bronze", "2026-03-02"],
        [3, "cy@example.com", "platinum", "2026-04-20"],
    ])
    write_csv("orders.csv", ["order_id", "customer_id", "amount", "order_date"], [
        [10, 1, 25.0, "2026-11-01"],
        [11, 2, -4.0, "2026-11-02"],
        [12, 9, 80.0, "2026-11-03"],
    ])
    return {"customers": "customers.csv", "orders": "orders.csv"}
