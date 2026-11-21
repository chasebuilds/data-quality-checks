# Benchmark

Written by `benchmark/run_benchmark.py` on 2026-11-21 from a real run.
No number on this page was typed by hand.

Command: `python -m benchmark.run_benchmark --seed 20261121 --trials 20 --per-trial 10`
Python 3.11.15 on Windows, total run time 146 s.

## Method

1. Generate a clean shop dataset: 2,000 customers (CSV),
   10,000 orders (Parquet) and 200 products (Excel).
2. Plant one kind of fault in a fresh copy: 10 rows per run for row-level
   faults, or one whole-file fault (truncated file, stale export).
3. Run the same 11 checks as `samples/checks.yml` and read back every row each check flags.
4. A planted row counts as caught when its key is among the rows the intended check flags.
5. Repeat for 20 seeds per fault type. Separately, run the checks on
   20 clean datasets to count false alarms.

Two fault types have no check meant to catch them. They are in the table on purpose, to show
what declarative checks miss.

## Result

| Planted fault | Intended check | Planted | Caught by intended check | Catch rate |
|---|---|---:|---:|---:|
| blank_email | customer email present | 200 | 200 | **100.0%** |
| duplicate_customer | customer id unique | 200 | 200 | **100.0%** |
| bad_email | customer email format | 200 | 184 | **92.0%** |
| bad_status | order status known | 200 | 200 | **100.0%** |
| bad_quantity | order quantity 1 to 50 | 200 | 200 | **100.0%** |
| orphan_customer | order customer exists | 200 | 200 | **100.0%** |
| orphan_sku | order product exists | 200 | 200 | **100.0%** |
| ship_before_order | ship date not before order date | 200 | 200 | **100.0%** |
| bad_price | product price 0.01 to 10000 | 200 | 200 | **100.0%** |
| truncated_orders | orders not truncated | 20 runs | 20 | **100.0%** |
| stale_orders | orders updated in last 3 days | 20 runs | 20 | **100.0%** |
| plausible_quantity | (none) | 200 | 0 | **0.0%** |
| plausible_email | (none) | 200 | 0 | **0.0%** |

**Row-level faults with an intended check: 1,784 of 1,800 caught
(99.1%).**

**False alarms on clean data: 0 failing checks out of
220 check runs (0 rows flagged) across
20 clean datasets.**

## Misses

- **bad_email**: 16 of 200 planted not caught by `customer email format`.

The `bad_email` misses are addresses whose domain was cut from `mail.example.net` to
`mail.example`. That still matches the email pattern, so no format rule can flag it; it is the
same limit as the plausible-value faults below. A test
(`test_email_format_misses_a_domain_cut_short`) pins this behavior.

## Detail

"Extra rows flagged" are rows the intended check flagged whose key was not planted. Rows are
matched by key (customer_id, order_id, sku), so for `duplicate_customer` the original row and
its copy count as one. "Caught by any check" counts planted rows flagged by any check on
the same table.

| Planted fault | What was changed | Extra rows flagged by intended check | Caught by any check | Other checks that also failed (runs) |
|---|---|---:|---:|---|
| blank_email | email set to null, empty or spaces | 0 | 100.0% | customer email format (20) |
| duplicate_customer | customer row exported twice | 0 | 100.0% | none |
| bad_email | email missing @, has a space, @@ or a trailing dot, or domain cut short | 0 | 92.0% | none |
| bad_status | status miscased, misspelled, trailing space or unknown | 0 | 100.0% | none |
| bad_quantity | quantity 0, negative or far too large | 0 | 100.0% | none |
| orphan_customer | order points at a customer id that does not exist | 0 | 100.0% | none |
| orphan_sku | order SKU missing, miscased, short or padded | 0 | 100.0% | none |
| ship_before_order | ship date 1 to 30 days before order date | 0 | 100.0% | none |
| bad_price | price zero, negative or too large | 0 | 100.0% | none |
| truncated_orders | orders file cut to 30% of its rows | n/a | 100.0% | none |
| stale_orders | every order date shifted 4 to 60 days back | n/a | 100.0% | none |
| plausible_quantity | quantity changed to a different value from 1 to 20 | 0 | 0.0% | none |
| plausible_email | email changed to another valid-looking address | 0 | 0.0% | none |

## Speed

One run of all 11 checks on 100,000 customers, 1,000,000
orders and 500 products (clean data, files on local disk):

| Measure | Value |
|---|---:|
| Load files and run 11 checks | 0.43 s |
| Slowest check | order customer exists (0.11 s) |
| Checks passed / failed | 11 / 0 |
| Time to generate and write the files (not part of the tool) | 3.4 s |

## Limits of this benchmark

- The data and the faults are generated. The faults are the kinds the checks were written
  for, so high catch rates here show the checks do what they say, not that they will find
  every problem in a real file.
- Each run plants one fault type. Real files carry several at once, and one bad value can
  trip more than one check (see the detail table).
- The plausible-value faults show the main limit: a wrong value that still looks valid
  passes every rule-based check.
- Timing is one run on one machine.
