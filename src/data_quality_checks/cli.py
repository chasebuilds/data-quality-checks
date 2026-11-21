"""Command line: dqc run checks.yml"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from . import __version__
from .checks import compile_check
from .config import ConfigError, load_suite
from .report import STATUS_LABEL, label, write_reports
from .runner import run_suite

EXIT_OK, EXIT_FAILED, EXIT_CONFIG = 0, 1, 2


def _date(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("use YYYY-MM-DD") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dqc", description="Run declarative data-quality checks as DuckDB SQL.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run every check in a checks file")
    run.add_argument("checks", type=Path, help="checks file (.yml, .yaml or .toml)")
    run.add_argument("-o", "--out", type=Path, default=Path("dqc-report"),
                     help="folder for report.html and report.json (default: dqc-report)")
    run.add_argument("--sample", type=int, default=5, metavar="N",
                     help="bad rows to keep per failing check (default: 5)")
    run.add_argument("--show-sql", action="store_true", help="print the SQL for each check")
    run.add_argument("--no-report", action="store_true", help="print results only")
    run.add_argument("--strict", action="store_true",
                     help="treat warn-severity failures as failures for the exit code")
    run.add_argument("--as-of", type=_date, metavar="YYYY-MM-DD",
                     help="date that freshness checks measure from (default: today)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.sample < 0:
        print("error: --sample must be 0 or more", file=sys.stderr)
        return EXIT_CONFIG
    try:
        suite = load_suite(args.checks)
        if args.show_sql:
            for check in suite.checks:
                print(f"-- {check.name}\n{compile_check(check, args.as_of)};\n")
        result = run_suite(suite, sample_rows=args.sample, today=args.as_of)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_CONFIG

    width = max(len(r.check.name) for r in result.results)
    for r in result.results:
        state = label(r)
        detail = r.error if r.error else (f"{r.failing_rows:,} row(s)" if r.failing_rows else "")
        print(f"{STATUS_LABEL[state]:<5}  {r.check.name:<{width}}  {detail}".rstrip())
    counts = result.counts()
    print(f"\n{counts['pass']} passed, {counts['fail']} failed, {counts['warn']} warned, "
          f"{counts['error']} errors in {result.seconds:.2f} s")
    if not args.no_report:
        html_path, json_path = write_reports(result, args.out)
        print(f"Wrote {html_path} and {json_path}")

    failed = not result.passed or (args.strict and counts["warn"] > 0)
    return EXIT_FAILED if failed else EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
