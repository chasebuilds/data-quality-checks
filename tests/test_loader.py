from datetime import date, datetime

import duckdb
import pytest
from openpyxl import Workbook

from data_quality_checks import ConfigError, TableSource
from data_quality_checks.loader import connect, read_excel


def test_reads_csv_parquet_and_excel(tmp_path, write_csv):
    write_csv("a.csv", ["id", "name"], [[1, "x"], [2, "y"]])
    duckdb.execute("COPY (SELECT 1 AS id UNION ALL SELECT 2) TO "
               f"'{(tmp_path / 'b.parquet').as_posix()}' (FORMAT PARQUET)")
    book = Workbook()
    sheet = book.active
    sheet.title = "Data"
    sheet.append(["id", "when", "mixed", "num"])
    sheet.append([1, datetime(2026, 1, 2), "a", 1])
    sheet.append([2, datetime(2026, 1, 3), 5, 2.5])
    sheet.append([None, None, None, None])
    book.save(tmp_path / "c.xlsx")

    con = connect({
        "a": TableSource("a", tmp_path / "a.csv"),
        "b": TableSource("b", tmp_path / "b.parquet"),
        "c": TableSource("c", tmp_path / "c.xlsx", sheet="Data"),
    })
    assert con.execute("SELECT count(*) FROM a").fetchone() == (2,)
    assert con.execute("SELECT sum(id) FROM b").fetchone() == (3,)
    rows = con.execute('SELECT id, "when", mixed, num FROM c ORDER BY id').fetchall()
    assert rows == [(1, datetime(2026, 1, 2), "a", 1.0), (2, datetime(2026, 1, 3), "5", 2.5)]


def test_excel_mixed_dates_become_text(tmp_path):
    book = Workbook()
    book.active.append(["d", None])
    book.active.append([date(2026, 1, 2), 1])
    book.active.append(["soon", 2])
    book.save(tmp_path / "d.xlsx")
    table = read_excel(tmp_path / "d.xlsx")
    assert table.column_names == ["d", "column_2"]
    assert table.column("d").to_pylist() == ["2026-01-02", "soon"]


def test_excel_errors(tmp_path):
    book = Workbook()
    book.save(tmp_path / "empty.xlsx")
    with pytest.raises(ConfigError, match="empty"):
        read_excel(tmp_path / "empty.xlsx")
    with pytest.raises(ConfigError, match="no sheet"):
        read_excel(tmp_path / "empty.xlsx", sheet="Nope")


def test_missing_or_unreadable_file(tmp_path):
    with pytest.raises(ConfigError, match="file not found"):
        connect({"a": TableSource("a", tmp_path / "nope.csv")})
    (tmp_path / "broken.parquet").write_bytes(b"not parquet")
    with pytest.raises(ConfigError, match="could not read"):
        connect({"p": TableSource("p", tmp_path / "broken.parquet")})
