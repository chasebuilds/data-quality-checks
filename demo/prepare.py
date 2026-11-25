"""Shift the published sample's dates so the newest order is always one day before the build date.

Only the copies in _site/samples change; the repo's samples and the tool code do not. Every date
moves by the same number of days, so the planted problems (ship date before order date, etc.)
stay exactly as they were. The freshness check's as_of moves to the build date.
"""

from __future__ import annotations

import csv
import datetime as dt
import os
import re
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

DAYS_BEFORE_BUILD = 1  # newest order_date, counted back from the build date


def build_date() -> dt.date:
    forced = os.environ.get("DEMO_BUILD_DATE")
    return dt.date.fromisoformat(forced) if forced else dt.date.today()


def prepare(site: Path) -> None:
    samples = site / "samples"
    today = build_date()
    orders_path = samples / "orders.parquet"
    orders = pq.read_table(orders_path)
    newest = pc.max(orders["order_date"]).as_py()
    delta = (today - dt.timedelta(days=DAYS_BEFORE_BUILD)) - newest
    for name in ("order_date", "ship_date"):
        col = orders[name]
        shifted = pa.array(
            [None if d is None else d + delta for d in col.to_pylist()], type=col.type
        )
        orders = orders.set_column(orders.schema.get_field_index(name), name, shifted)
    pq.write_table(orders, orders_path)

    customers_path = samples / "customers.csv"
    with customers_path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    i = rows[0].index("signup_date")
    for r in rows[1:]:
        if r[i].strip():
            r[i] = (dt.date.fromisoformat(r[i].strip()) + delta).isoformat()
    with customers_path.open("w", newline="", encoding="utf-8") as fh:
        csv.writer(fh, lineterminator="\n").writerows(rows)

    checks_path = samples / "checks.yml"
    text = checks_path.read_text(encoding="utf-8")
    checks_path.write_text(
        re.sub(r"as_of: '\d{4}-\d{2}-\d{2}'", f"as_of: '{today.isoformat()}'", text),
        encoding="utf-8",
    )
    (site / "build-date.txt").write_text(f"{today.isoformat()} shift {delta.days} days\n")
    print(f"sample dates shifted by {delta.days} days; as_of {today}")
