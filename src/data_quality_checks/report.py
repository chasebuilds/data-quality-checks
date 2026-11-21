"""Write a suite result as report.json and report.html."""

from __future__ import annotations

import json
from html import escape
from pathlib import Path
from typing import Any

from . import __version__
from .runner import CheckResult, SuiteResult

STATUS_LABEL = {"pass": "PASS", "fail": "FAIL", "warn": "WARN", "error": "ERROR"}


def label(result: CheckResult) -> str:
    if result.status == "fail" and result.check.severity == "warn":
        return "warn"
    return result.status


def to_dict(suite: SuiteResult) -> dict[str, Any]:
    return {
        "tool": f"data-quality-checks {__version__}",
        "started": suite.started.isoformat(),
        "seconds": round(suite.seconds, 3),
        "passed": suite.passed,
        "counts": suite.counts(),
        "files": suite.files,
        "checks": [
            {
                "name": r.check.name,
                "type": r.check.type,
                "table": r.check.table,
                "severity": r.check.severity,
                "status": label(r),
                "failing_rows": r.failing_rows,
                "sql": r.sql,
                "error": r.error,
                "sample_columns": r.columns,
                "sample_rows": r.sample,
            }
            for r in suite.results
        ],
    }


def write_json(suite: SuiteResult, path: Path) -> Path:
    path.write_text(json.dumps(to_dict(suite), indent=2) + "\n", encoding="utf-8")
    return path


_CSS = """
body{font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;margin:0;
background:#f6f7f9;color:#1d2330}
main{max-width:1100px;margin:0 auto;padding:24px 16px 48px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:32px 0 8px}
.meta{color:#5b6475;margin:0 0 16px}
.verdict{display:inline-block;padding:6px 14px;border-radius:6px;font-weight:600;color:#fff}
.ok{background:#1f7a4d}.bad{background:#b3261e}
.tiles{display:flex;flex-wrap:wrap;gap:8px;margin:16px 0}
.tile{background:#fff;border:1px solid #dde1e8;border-radius:6px;padding:8px 14px;min-width:90px}
.tile b{display:block;font-size:20px}
table{border-collapse:collapse;width:100%;background:#fff;font-size:14px}
th,td{border:1px solid #dde1e8;padding:6px 8px;text-align:left;vertical-align:top}
th{background:#eef0f4}
.wrap{overflow-x:auto}
.badge{font-weight:600;font-size:12px;padding:2px 8px;border-radius:4px;color:#fff}
.b-pass{background:#1f7a4d}.b-fail{background:#b3261e}.b-warn{background:#a15c00}
.b-error{background:#5b2a86}
.num{text-align:right}
pre{background:#1d2330;color:#e8ebf1;padding:10px;border-radius:6px;overflow-x:auto;
font-size:13px}
details{margin:6px 0}
section.failure{background:#fff;border:1px solid #dde1e8;border-left:4px solid #b3261e;
border-radius:6px;padding:12px 14px;margin:12px 0}
section.failure.warn{border-left-color:#a15c00}
section.failure.error{border-left-color:#5b2a86}
.null{color:#8a92a3;font-style:italic}
"""


def _cell(value: Any) -> str:
    if value is None:
        return '<td class="null">null</td>'
    return f"<td>{escape(str(value))}</td>"


def _failure(r: CheckResult) -> str:
    state = label(r)
    parts = [f'<section class="failure {state}" id="{escape(_anchor(r))}">',
             f"<h3>{escape(r.check.name)} "
             f'<span class="badge b-{state}">{STATUS_LABEL[state]}</span></h3>']
    if r.error:
        parts.append(f"<p>The check could not run: <code>{escape(r.error)}</code></p>")
    else:
        parts.append(f"<p>{r.failing_rows:,} row(s) break this rule. "
                     f"First {len(r.sample)} shown.</p>")
        head = "".join(f"<th>{escape(c)}</th>" for c in r.columns)
        body = "".join("<tr>" + "".join(_cell(v) for v in row) + "</tr>" for row in r.sample)
        parts.append(f'<div class="wrap"><table><tr>{head}</tr>{body}</table></div>')
    parts.append(f"<details><summary>SQL</summary><pre>{escape(r.sql)}</pre></details>")
    parts.append("</section>")
    return "\n".join(parts)


def _anchor(r: CheckResult) -> str:
    return "c-" + "".join(ch if ch.isalnum() else "-" for ch in r.check.name)


def render_html(suite: SuiteResult) -> str:
    counts = suite.counts()
    verdict = ('<span class="verdict ok">PASSED</span>' if suite.passed
               else '<span class="verdict bad">FAILED</span>')
    tiles = "".join(f'<div class="tile"><b>{counts[k]}</b>{STATUS_LABEL[k].title()}</div>'
                    for k in ("pass", "fail", "warn", "error"))
    rows = []
    for r in suite.results:
        state = label(r)
        name = escape(r.check.name)
        if state != "pass":
            name = f'<a href="#{escape(_anchor(r))}">{name}</a>'
        rows.append(
            f'<tr><td><span class="badge b-{state}">{STATUS_LABEL[state]}</span></td>'
            f"<td>{name}</td><td>{escape(r.check.type)}</td>"
            f"<td>{escape(r.check.table or '')}</td><td>{escape(r.check.severity)}</td>"
            f'<td class="num">{r.failing_rows:,}</td></tr>'
        )
    files = "".join(f"<tr><td>{escape(k)}</td><td>{escape(v)}</td></tr>"
                    for k, v in suite.files.items())
    failures = [r for r in suite.results if r.status != "pass"]
    failure_html = "\n".join(_failure(r) for r in failures) or "<p>No failures.</p>"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Data quality report</title>
<style>{_CSS}</style>
</head>
<body>
<main>
<h1>Data quality report</h1>
<p class="meta">Run {escape(suite.started.isoformat(sep=" "))}, {suite.seconds:.2f} s,
{len(suite.results)} checks, data-quality-checks {__version__}</p>
{verdict}
<div class="tiles">{tiles}</div>
<h2>Checks</h2>
<div class="wrap"><table>
<tr><th>Status</th><th>Check</th><th>Type</th><th>Table</th><th>Severity</th>
<th class="num">Failing rows</th></tr>
{"".join(rows)}
</table></div>
<h2>Failures and samples of bad rows</h2>
{failure_html}
<h2>Files</h2>
<table><tr><th>Table</th><th>File</th></tr>{files}</table>
</main>
</body>
</html>
"""


def write_html(suite: SuiteResult, path: Path) -> Path:
    path.write_text(render_html(suite), encoding="utf-8")
    return path


def write_reports(suite: SuiteResult, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    return write_html(suite, out_dir / "report.html"), write_json(suite, out_dir / "report.json")
