"""Seeded synthetic shop data (customers CSV, orders Parquet, products Excel) with planted faults.

    python -m benchmark.generate --out samples --seed 7

Every planted fault is recorded with the table and key it touched, so a run of the checks
can be scored against what was really planted.
"""

from __future__ import annotations

import argparse
import copy
import csv
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import yaml
from openpyxl import Workbook

AS_OF = date(2026, 11, 21)
TIERS = ["bronze", "silver", "gold"]
STATUSES = ["placed", "shipped", "delivered", "returned"]
CATEGORIES = ["tools", "garden", "kitchen", "office"]
FIRST = ["ann", "bo", "cara", "dev", "eli", "fay", "gus", "hana", "ivan", "jo", "kai", "lena"]
DOMAINS = ["example.com", "example.org", "mail.example.net"]

Row = dict[str, Any]


@dataclass
class Dataset:
    customers: list[Row]
    orders: list[Row]
    products: list[Row]
    as_of: date = AS_OF
    expected_orders: int = 0  # row count of the clean export, before any fault
    planted: list[tuple[str, str, Any]] = field(default_factory=list)  # (fault, table, key)

    def copy(self) -> Dataset:
        return copy.deepcopy(self)


def make_dataset(customers: int = 200, orders: int = 1000, products: int = 50, seed: int = 7,
                 as_of: date = AS_OF) -> Dataset:
    rng = random.Random(seed)
    custs = []
    for i in range(1, customers + 1):
        user = f"{rng.choice(FIRST)}.{i}"
        custs.append({
            "customer_id": i,
            "email": f"{user}@{rng.choice(DOMAINS)}",
            "tier": rng.choice(TIERS),
            "signup_date": as_of - timedelta(days=rng.randint(30, 900)),
        })
    prods = [{
        "sku": f"P-{i:04d}",
        "name": f"{rng.choice(CATEGORIES)} item {i}",
        "category": rng.choice(CATEGORIES),
        "price": round(rng.uniform(2, 400), 2),
    } for i in range(1, products + 1)]
    ords = []
    for i in range(1, orders + 1):
        ordered = as_of - timedelta(days=rng.randint(1, 90))
        ords.append({
            "order_id": 100000 + i,
            "customer_id": rng.randint(1, customers),
            "sku": f"P-{rng.randint(1, products):04d}",
            "quantity": rng.randint(1, 20),
            "order_date": ordered,
            "ship_date": ordered + timedelta(days=rng.randint(0, 6)),
            "status": rng.choice(STATUSES),
        })
    ords[-1]["order_date"] = as_of - timedelta(days=1)
    ords[-1]["ship_date"] = ords[-1]["order_date"]
    return Dataset(custs, ords, prods, as_of, expected_orders=len(ords))


def checks_config(ds: Dataset, files: dict[str, str]) -> dict[str, Any]:
    """The checks file the samples and the benchmark run. One check per planted fault type."""
    return {
        "tables": files,
        "checks": [
            {"name": "customer email present", "table": "customers", "type": "not_null",
             "column": "email"},
            {"name": "customer id unique", "table": "customers", "type": "unique",
             "column": "customer_id"},
            {"name": "customer email format", "table": "customers", "type": "regex",
             "column": "email", "pattern": r"[a-z0-9._%+-]+@[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}"},
            {"name": "order status known", "table": "orders", "type": "accepted_values",
             "column": "status", "values": STATUSES},
            {"name": "order quantity 1 to 50", "table": "orders", "type": "range",
             "column": "quantity", "min": 1, "max": 50},
            {"name": "order customer exists", "table": "orders", "type": "foreign_key",
             "column": "customer_id", "references": "customers.customer_id"},
            {"name": "order product exists", "table": "orders", "type": "foreign_key",
             "column": "sku", "references": "products.sku"},
            {"name": "orders not truncated", "table": "orders", "type": "row_count",
             "min": max(1, ds.expected_orders // 2)},
            {"name": "orders updated in last 3 days", "table": "orders", "type": "freshness",
             "column": "order_date", "max_age_days": 3, "as_of": ds.as_of.isoformat()},
            {"name": "product price 0.01 to 10000", "table": "products", "type": "range",
             "column": "price", "min": 0.01, "max": 10000},
            {"name": "ship date not before order date", "type": "sql",
             "query": "SELECT * FROM orders WHERE ship_date < order_date"},
        ],
    }


# ---- planted faults -------------------------------------------------------------------
# Each planter changes n rows (or the whole table) and returns the keys it touched.

Planter = Callable[[Dataset, int, random.Random], list[Any]]


KEYS = {"customers": "customer_id", "orders": "order_id", "products": "sku"}


def _pick(ds: Dataset, table: str, n: int, rng: random.Random) -> list[Row]:
    """n rows no earlier fault touched, so one row never carries two planted faults."""
    used = {k for _, t, k in ds.planted if t == table}
    rows: list[Row] = getattr(ds, table)
    pool = [r for r in rows if r[KEYS[table]] not in used]
    return rng.sample(pool, min(n, len(pool)))


def _blank_email(ds: Dataset, n: int, rng: random.Random) -> list[Any]:
    rows = _pick(ds, "customers", n, rng)
    for row in rows:
        row["email"] = rng.choice([None, "", "   "])
    return [r["customer_id"] for r in rows]


def _duplicate_customer(ds: Dataset, n: int, rng: random.Random) -> list[Any]:
    rows = _pick(ds, "customers", n, rng)
    for row in rows:
        dupe = dict(row, tier=rng.choice(TIERS))
        ds.customers.insert(rng.randint(0, len(ds.customers)), dupe)
    return [r["customer_id"] for r in rows]


def _bad_email(ds: Dataset, n: int, rng: random.Random) -> list[Any]:
    rows = _pick(ds, "customers", n, rng)
    for row in rows:
        user, domain = row["email"].split("@")
        row["email"] = rng.choice([
            f"{user}{domain}", f"{user}@{domain.rsplit('.', 1)[0]}", f"{user} @{domain}",
            f"{user}@@{domain}", f"{user}@{domain}.",
        ])
    return [r["customer_id"] for r in rows]


def _bad_status(ds: Dataset, n: int, rng: random.Random) -> list[Any]:
    rows = _pick(ds, "orders", n, rng)
    for row in rows:
        row["status"] = rng.choice(["SHIPPED", "shiped", "delivered ", "cancelled", "?"])
    return [r["order_id"] for r in rows]


def _bad_quantity(ds: Dataset, n: int, rng: random.Random) -> list[Any]:
    rows = _pick(ds, "orders", n, rng)
    for row in rows:
        row["quantity"] = rng.choice([0, -1, -12, 51, 500, 10000])
    return [r["order_id"] for r in rows]


def _orphan_customer(ds: Dataset, n: int, rng: random.Random) -> list[Any]:
    rows = _pick(ds, "orders", n, rng)
    top = max(c["customer_id"] for c in ds.customers)
    for row in rows:
        row["customer_id"] = top + rng.randint(1, 5000)
    return [r["order_id"] for r in rows]


def _orphan_sku(ds: Dataset, n: int, rng: random.Random) -> list[Any]:
    rows = _pick(ds, "orders", n, rng)
    for row in rows:
        row["sku"] = rng.choice(["P-9999", "p-0001", "P-01", "P-0001 ", "SKU-7"])
    return [r["order_id"] for r in rows]


def _ship_before_order(ds: Dataset, n: int, rng: random.Random) -> list[Any]:
    rows = _pick(ds, "orders", n, rng)
    for row in rows:
        row["ship_date"] = row["order_date"] - timedelta(days=rng.randint(1, 30))
    return [r["order_id"] for r in rows]


def _bad_price(ds: Dataset, n: int, rng: random.Random) -> list[Any]:
    rows = _pick(ds, "products", n, rng)
    for row in rows:
        row["price"] = rng.choice([0.0, -5.0, -0.01, 25000.0, 99999.99])
    return [r["sku"] for r in rows]


def _truncated(ds: Dataset, n: int, rng: random.Random) -> list[Any]:
    keep = max(1, int(len(ds.orders) * 0.3))
    last = ds.orders[-1]
    ds.orders = ds.orders[: keep - 1] + [last]
    return ["orders"]


def _stale(ds: Dataset, n: int, rng: random.Random) -> list[Any]:
    shift = timedelta(days=rng.randint(4, 60))
    for row in ds.orders:
        row["order_date"] -= shift
        row["ship_date"] -= shift
    return ["orders"]


def _plausible_quantity(ds: Dataset, n: int, rng: random.Random) -> list[Any]:
    rows = _pick(ds, "orders", n, rng)
    for row in rows:
        row["quantity"] = rng.choice([q for q in range(1, 21) if q != row["quantity"]])
    return [r["order_id"] for r in rows]


def _plausible_email(ds: Dataset, n: int, rng: random.Random) -> list[Any]:
    rows = _pick(ds, "customers", n, rng)
    for row in rows:
        user, domain = row["email"].split("@")
        row["email"] = f"{user}x@{domain}"
    return [r["customer_id"] for r in rows]


@dataclass(frozen=True)
class Fault:
    name: str
    table: str
    key: str  # column that identifies a flagged row; "" for table-level faults
    target: str | None  # check meant to catch it; None when no check covers it
    plant: Planter
    description: str


FAULTS = [
    Fault("blank_email", "customers", "customer_id", "customer email present", _blank_email,
          "email set to null, empty or spaces"),
    Fault("duplicate_customer", "customers", "customer_id", "customer id unique",
          _duplicate_customer, "customer row exported twice"),
    Fault("bad_email", "customers", "customer_id", "customer email format", _bad_email,
          "email missing @, has a space, @@ or a trailing dot, or domain cut short"),
    Fault("bad_status", "orders", "order_id", "order status known", _bad_status,
          "status miscased, misspelled, trailing space or unknown"),
    Fault("bad_quantity", "orders", "order_id", "order quantity 1 to 50", _bad_quantity,
          "quantity 0, negative or far too large"),
    Fault("orphan_customer", "orders", "order_id", "order customer exists", _orphan_customer,
          "order points at a customer id that does not exist"),
    Fault("orphan_sku", "orders", "order_id", "order product exists", _orphan_sku,
          "order SKU missing, miscased, short or padded"),
    Fault("ship_before_order", "orders", "order_id", "ship date not before order date",
          _ship_before_order, "ship date 1 to 30 days before order date"),
    Fault("bad_price", "products", "sku", "product price 0.01 to 10000", _bad_price,
          "price zero, negative or too large"),
    Fault("truncated_orders", "orders", "", "orders not truncated", _truncated,
          "orders file cut to 30% of its rows"),
    Fault("stale_orders", "orders", "", "orders updated in last 3 days", _stale,
          "every order date shifted 4 to 60 days back"),
    Fault("plausible_quantity", "orders", "order_id", None, _plausible_quantity,
          "quantity changed to a different value from 1 to 20"),
    Fault("plausible_email", "customers", "customer_id", None, _plausible_email,
          "email changed to another valid-looking address"),
]
FAULTS_BY_NAME = {f.name: f for f in FAULTS}


def plant(ds: Dataset, fault: Fault, n: int, rng: random.Random) -> list[Any]:
    keys = fault.plant(ds, n, rng)
    ds.planted.extend((fault.name, fault.table, k) for k in keys)
    return keys


# ---- writing ---------------------------------------------------------------------------

def write_dataset(ds: Dataset, out: Path) -> Path:
    """Write customers.csv, orders.parquet, products.xlsx and checks.yml. Returns checks.yml."""
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "customers.csv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(ds.customers[0]))
        writer.writeheader()
        writer.writerows(ds.customers)
    pq.write_table(pa.Table.from_pylist(ds.orders), out / "orders.parquet")
    book = Workbook()
    sheet = book.active
    sheet.title = "Products"
    sheet.append(list(ds.products[0]))
    for row in ds.products:
        sheet.append(list(row.values()))
    book.save(out / "products.xlsx")
    files = {"customers": "customers.csv", "orders": "orders.parquet",
             "products": "products.xlsx"}
    checks = out / "checks.yml"
    checks.write_text(yaml.safe_dump(checks_config(ds, files), sort_keys=False, width=100),
                      encoding="utf-8")
    return checks


def write_planted(ds: Dataset, path: Path) -> None:
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["fault", "table", "key", "meant_to_be_caught_by"])
        for name, table, key in ds.planted:
            target = FAULTS_BY_NAME[name].target or "(no check covers this)"
            writer.writerow([name, table, key, target])


def make_samples(out: Path, seed: int = 7) -> Path:
    """Small sample set: two of each row-level fault, no table-level faults."""
    ds = make_dataset(customers=40, orders=150, products=12, seed=seed)
    rng = random.Random(seed)
    for fault in FAULTS:
        if fault.key:
            plant(ds, fault, 2, rng)
    checks = write_dataset(ds, out)
    write_planted(ds, out / "planted.csv")
    return checks


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path("samples"))
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args(argv)
    print(f"Wrote {make_samples(args.out, args.seed)}")


if __name__ == "__main__":
    main()
