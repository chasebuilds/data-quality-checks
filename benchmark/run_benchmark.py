"""Plant known faults in generated files, run the checks, and write BENCHMARK.md from the result.

    python -m benchmark.run_benchmark

Nothing in BENCHMARK.md is typed by hand. The same run refreshes the result block in
README.md between the benchmark markers.
"""

from __future__ import annotations

import argparse
import platform
import random
import re
import tempfile
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from benchmark.generate import FAULTS, KEYS, Dataset, Fault, make_dataset, plant, write_dataset
from data_quality_checks import compile_check, load_suite, run_suite
from data_quality_checks.loader import connect
from data_quality_checks.sql import quote

ROOT = Path(__file__).resolve().parent.parent
MARK_START, MARK_END = "<!-- benchmark:start -->", "<!-- benchmark:end -->"
TABLE_LEVEL = "(table)"


def flagged(ds: Dataset, work: Path) -> dict[str, set[Any]]:
    """Run every check on the dataset and return, per check, the keys of every row it flagged.

    Row-level checks give the key column of their table. Table-level checks (row_count,
    freshness) give {TABLE_LEVEL} when they fail. The custom SQL check reads orders.
    """
    suite = load_suite(write_dataset(ds, work))
    con = connect(suite.tables)
    out: dict[str, set[Any]] = {}
    try:
        for check in suite.checks:
            sql = compile_check(check)
            if check.type in ("row_count", "freshness"):
                row = con.execute(f"SELECT count(*) FROM ({sql}) AS q").fetchone()
                out[check.name] = {TABLE_LEVEL} if row and row[0] else set()
                continue
            key = quote(KEYS[check.table or "orders"])
            rows = con.execute(f"SELECT {key} FROM ({sql}) AS q").fetchall()
            out[check.name] = {r[0] for r in rows}
    finally:
        con.close()
    return out


@dataclass
class FaultScore:
    fault: Fault
    planted: int = 0
    caught_by_target: int = 0
    caught_by_any: int = 0
    extra_rows_target: int = 0
    other_checks: Counter[str] = field(default_factory=Counter)


def score_fault(fault: Fault, trials: int, per_trial: int, sizes: dict[str, int], seed: int,
                work: Path) -> FaultScore:
    result = FaultScore(fault)
    for t in range(trials):
        ds = make_dataset(seed=seed + t, **sizes)
        rng = random.Random(seed * 1000 + t)
        keys = set(plant(ds, fault, per_trial, rng))
        hits = flagged(ds, work)
        if not fault.key:
            result.planted += 1
            result.caught_by_target += TABLE_LEVEL in hits.get(fault.target or "", set())
            result.caught_by_any += any(hits.values())
            other = {name for name, found in hits.items() if found and name != fault.target}
        else:
            result.planted += len(keys)
            target = hits.get(fault.target or "", set())
            result.caught_by_target += len(keys & target)
            result.extra_rows_target += len(target - keys)
            any_hit: set[Any] = set()
            for name, found in hits.items():
                if _same_table(fault, name):
                    any_hit |= found
            result.caught_by_any += len(keys & any_hit)
            other = {name for name, found in hits.items() if found and name != fault.target}
        result.other_checks.update(other)
    return result


CHECK_TABLES = {
    "customer email present": "customers", "customer id unique": "customers",
    "customer email format": "customers", "product price 0.01 to 10000": "products",
}


def _same_table(fault: Fault, check_name: str) -> bool:
    return CHECK_TABLES.get(check_name, "orders") == fault.table


def score_clean(trials: int, sizes: dict[str, int], seed: int, work: Path) -> dict[str, int]:
    checks_run = failing = rows = 0
    for t in range(trials):
        hits = flagged(make_dataset(seed=seed + t, **sizes), work)
        checks_run += len(hits)
        failing += sum(bool(v) for v in hits.values())
        rows += sum(len(v) for v in hits.values())
    return {"trials": trials, "checks_run": checks_run, "failing": failing, "rows": rows}


def time_large(sizes: dict[str, int], seed: int, work: Path) -> dict[str, Any]:
    started = time.perf_counter()
    ds = make_dataset(seed=seed, **sizes)
    checks = write_dataset(ds, work)
    generate_s = time.perf_counter() - started
    started = time.perf_counter()
    result = run_suite(load_suite(checks), sample_rows=5)
    run_s = time.perf_counter() - started
    return {"generate_s": generate_s, "run_s": run_s, "counts": result.counts(),
            "slowest": max(result.results, key=lambda r: r.seconds)}


def _pct(part: int, whole: int) -> str:
    return f"{100 * part / whole:.1f}%" if whole else "n/a"


def result_table(scores: list[FaultScore]) -> str:
    lines = [
        "| Planted fault | Intended check | Planted | Caught by intended check | Catch rate |",
        "|---|---|---:|---:|---:|",
    ]
    for s in scores:
        unit = "" if s.fault.key else " runs"
        lines.append(
            f"| {s.fault.name} | {s.fault.target or '(none)'} | {s.planted:,}{unit} | "
            f"{s.caught_by_target:,} | **{_pct(s.caught_by_target, s.planted)}** |"
        )
    return "\n".join(lines)


def report(scores: list[FaultScore], clean: dict[str, int], large: dict[str, Any],
           args: argparse.Namespace, sizes: dict[str, int], large_sizes: dict[str, int],
           elapsed: float) -> str:
    covered = [s for s in scores if s.fault.target]
    planted = sum(s.planted for s in covered if s.fault.key)
    caught = sum(s.caught_by_target for s in covered if s.fault.key)
    detail = ["| Planted fault | What was changed | Extra rows flagged by intended check "
              "| Caught by any check | Other checks that also failed (runs) |",
              "|---|---|---:|---:|---|"]
    for s in scores:
        others = ", ".join(f"{n} ({c})" for n, c in sorted(s.other_checks.items())) or "none"
        extra = f"{s.extra_rows_target:,}" if s.fault.key else "n/a"
        detail.append(f"| {s.fault.name} | {s.fault.description} | {extra} | "
                      f"{_pct(s.caught_by_any, s.planted)} | {others} |")
    counts = large["counts"]
    slow = large["slowest"]
    misses = [s for s in covered if s.caught_by_target < s.planted]
    miss_lines = "\n".join(
        f"- **{s.fault.name}**: {s.planted - s.caught_by_target:,} of {s.planted:,} planted "
        f"not caught by `{s.fault.target}`." for s in misses) or "- None."
    return f"""# Benchmark

Written by `benchmark/run_benchmark.py` on {date.today().isoformat()} from a real run.
No number on this page was typed by hand.

Command: `python -m benchmark.run_benchmark --seed {args.seed} --trials {args.trials} \
--per-trial {args.per_trial}`
Python {platform.python_version()} on {platform.system()}, total run time {elapsed:.0f} s.

## Method

1. Generate a clean shop dataset: {sizes['customers']:,} customers (CSV),
   {sizes['orders']:,} orders (Parquet) and {sizes['products']:,} products (Excel).
2. Plant one kind of fault in a fresh copy: {args.per_trial} rows per run for row-level
   faults, or one whole-file fault (truncated file, stale export).
3. Run the same 11 checks as `samples/checks.yml` and read back every row each check flags.
4. A planted row counts as caught when its key is among the rows the intended check flags.
5. Repeat for {args.trials} seeds per fault type. Separately, run the checks on
   {clean['trials']} clean datasets to count false alarms.

Two fault types have no check meant to catch them. They are in the table on purpose, to show
what declarative checks miss.

## Result

{result_table(scores)}

**Row-level faults with an intended check: {caught:,} of {planted:,} caught
({_pct(caught, planted)}).**

**False alarms on clean data: {clean['failing']} failing checks out of
{clean['checks_run']:,} check runs ({clean['rows']} rows flagged) across
{clean['trials']} clean datasets.**

## Misses

{miss_lines}

## Detail

"Extra rows flagged" are rows the intended check flagged whose key was not planted. Rows are
matched by key (customer_id, order_id, sku), so for `duplicate_customer` the original row and
its copy count as one. "Caught by any check" counts planted rows flagged by any check on
the same table.

{chr(10).join(detail)}

## Speed

One run of all 11 checks on {large_sizes['customers']:,} customers, {large_sizes['orders']:,}
orders and {large_sizes['products']:,} products (clean data, files on local disk):

| Measure | Value |
|---|---:|
| Load files and run 11 checks | {large['run_s']:.2f} s |
| Slowest check | {slow.check.name} ({slow.seconds:.2f} s) |
| Checks passed / failed | {counts['pass']} / {counts['fail'] + counts['error']} |
| Time to generate and write the files (not part of the tool) | {large['generate_s']:.1f} s |

## Limits of this benchmark

- The data and the faults are generated. The faults are the kinds the checks were written
  for, so high catch rates here show the checks do what they say, not that they will find
  every problem in a real file.
- Each run plants one fault type. Real files carry several at once, and one bad value can
  trip more than one check (see the detail table).
- The plausible-value faults show the main limit: a wrong value that still looks valid
  passes every rule-based check.
- Timing is one run on one machine.
"""


def write_outputs(text: str, table: str) -> None:
    (ROOT / "BENCHMARK.md").write_text(text, encoding="utf-8")
    readme = ROOT / "README.md"
    if readme.is_file():
        body = readme.read_text(encoding="utf-8")
        pattern = re.compile(re.escape(MARK_START) + ".*?" + re.escape(MARK_END), re.S)
        if pattern.search(body):
            body = pattern.sub(lambda _: f"{MARK_START}\n{table}\n{MARK_END}", body)
            readme.write_text(body, encoding="utf-8")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Score the checks against planted faults.")
    parser.add_argument("--seed", type=int, default=20261121)
    parser.add_argument("--trials", type=int, default=20)
    parser.add_argument("--per-trial", type=int, default=10)
    parser.add_argument("--large-orders", type=int, default=1_000_000)
    parser.add_argument("--no-write", action="store_true", help="print only")
    args = parser.parse_args(argv)
    sizes = {"customers": 2000, "orders": 10_000, "products": 200}
    large_sizes = {"customers": args.large_orders // 10, "orders": args.large_orders,
                   "products": 500}

    started = time.perf_counter()
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        scores = []
        for fault in FAULTS:
            scores.append(score_fault(fault, args.trials, args.per_trial, sizes, args.seed,
                                      work / fault.name))
            print(f"{fault.name}: {scores[-1].caught_by_target}/{scores[-1].planted}")
        clean = score_clean(args.trials, sizes, args.seed, work / "clean")
        large = time_large(large_sizes, args.seed, work / "large")
    elapsed = time.perf_counter() - started

    text = report(scores, clean, large, args, sizes, large_sizes, elapsed)
    table = result_table(scores) + (
        f"\n\nFalse alarms on clean data: {clean['failing']} failing checks in "
        f"{clean['checks_run']:,} check runs. All 11 checks on {large_sizes['orders']:,} orders: "
        f"{large['run_s']:.2f} s."
    )
    if args.no_write:
        print(text)
    else:
        write_outputs(text, table)
        print("Wrote BENCHMARK.md")


if __name__ == "__main__":
    main()
