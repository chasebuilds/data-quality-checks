window.DEMO = {
  repo: "data-quality-checks",
  title: "data-quality-checks",
  tagline: "Describe the rules your data should follow in a small YAML file (not null, unique, format, allowed values, ranges, foreign keys, freshness). Every check runs as SQL in DuckDB and failing rows are shown.",
  pkg: "data_quality_checks",
  packages: ["openpyxl", "pyyaml", "duckdb", "pyarrow"],
  inputs: [
    { id: "data", label: "Data files the checks file names (CSV, XLSX, Parquet)", accept: ".csv,.xlsx,.parquet", multiple: true, optional: true,
      sample: ["samples/customers.csv", "samples/orders.parquet", "samples/products.xlsx"] },
    { id: "checks", label: "Checks file (.yml or .toml)", accept: ".yml,.yaml,.toml", optional: true, sample: "samples/checks.yml" },
  ],
  options: [],
  sampleNote: 'The sample is <a href="samples/checks.yml">checks.yml</a> run against three synthetic files (<a href="samples/customers.csv">customers.csv</a>, <a href="samples/orders.parquet">orders.parquet</a>, <a href="samples/products.xlsx">products.xlsx</a>) with two planted problems of each kind, listed in <a href="samples/planted.csv">planted.csv</a>. Sample dates are shifted to today at build: every date moves by the same number of days so the newest order is one day before the build date (see <a href="build-date.txt">build-date.txt</a>). The tool and its checks are unchanged. Any box left empty uses the sample file.<br>DuckDB and pyarrow both have official Pyodide builds, so the same SQL runs here as on a desktop.',
  python: `
import shutil
from pathlib import Path
from data_quality_checks.cli import main as cli_main

def run(paths, opts):
    checks = Path(paths["checks"][0])
    for p in paths["data"]:
        if Path(p).parent != checks.parent:
            shutil.copy(p, checks.parent / Path(p).name)  # checks files name tables relative to themselves
    out = Path("/work/report")
    code, log = run_cli(cli_main, ["run", str(checks), "-o", str(out)])  # exit 1 means a check failed
    report = out / "report.json"
    if not report.exists():
        return {"ok": False, "log": log, "sheets": [], "download": None}
    data = json.loads(report.read_text(encoding="utf-8"))
    head = ["check", "table", "type", "status", "failing_rows", "sql"]
    rows = [head] + [[c["name"], str(c.get("table") or ""), c["type"], c["status"],
                      str(c.get("failing_rows") or 0), c.get("sql") or ""] for c in data["checks"]]
    colors = {"fail": "#e2583a", "pass": "#1f9a72", "warn": "#c8821e", "error": "#e2583a"}
    fills = [[None] * len(head)] + [[None, None, None, colors.get(c["status"]), None, None] for c in data["checks"]]
    sheets = [{"name": "Checks", "rows": rows, "fills": fills, "total": len(rows) - 1}]
    for c in data["checks"]:
        if c.get("sample_rows"):
            body = [[("" if v is None else str(v)) for v in r] for r in c["sample_rows"]]
            sheets.append({"name": c["name"], "rows": [c["sample_columns"]] + body, "fills": None, "total": len(body)})
    return {"ok": True, "log": log, "sheets": sheets, "download": zip_dir(str(out), "/work/dqc-report.zip")}
`,
};
