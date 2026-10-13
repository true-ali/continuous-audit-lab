import json
import logging
from datetime import datetime
from pathlib import Path

import pandas as pd

from continuous_audit_lab.data.loader import (
    load_directory,
    load_file,
    parse_date,
    quality_report,
    write_quality_report,
)
from continuous_audit_lab.data.schema import CANONICAL_COLUMNS

LAYOUT_A = (
    "Southlake Council - payments over £500\n"
    "\n"
    "Payment Date,Supplier Name,Expenditure Category,Directorate,Transaction Number,Net Amount\n"
    '05/01/2024,Acme Ltd,Consultancy,Finance,INV-1,"£1,234.50"\n'
    '2024-01-09,Beta Plc,Software,IT,INV-2,"(£20.00)"\n'
    ",,,,,\n"
)

LAYOUT_B = (
    "Transaction Date,Beneficiary,Expense Description,Department,Voucher,Total (£)\n"
    '05-Feb-2024,Café Ltd,Repairs,Highways,V-9,"1,000"\n'
    "not a date,Gamma Ltd,Repairs,Highways,V-10,abc\n"
)

UNMAPPABLE = "foo,bar\n1,2\n"


def _write(path: Path, text: str, encoding: str = "utf-8") -> Path:
    path.write_bytes(text.encode(encoding))
    return path


def test_load_file_maps_title_rows_and_parses_amounts_and_dates(tmp_path):
    frame, report = load_file(_write(tmp_path / "a.csv", LAYOUT_A, encoding="utf-8-sig"))

    assert report.mapped is True
    assert report.rows_read == 3
    assert report.rows_dropped == {"empty_row": 1}
    assert report.date_parse_failures == 0
    assert report.amount_parse_failures == 0
    assert list(frame.columns) == CANONICAL_COLUMNS + ["source_file"]
    assert frame["amount"].tolist() == [1234.50, -20.00]
    assert frame["txn_date"].tolist() == [pd.Timestamp("2024-01-05"), pd.Timestamp("2024-01-09")]
    assert frame["department"].tolist() == ["Finance", "IT"]
    assert frame["source_file"].unique().tolist() == ["a.csv"]


def test_load_file_keeps_bad_rows_as_nan_and_counts_failures(tmp_path):
    frame, report = load_file(_write(tmp_path / "b.csv", LAYOUT_B, encoding="latin-1"))

    assert report.mapped is True
    assert report.rows_read == 2
    assert report.rows_dropped == {}
    assert report.date_parse_failures == 1
    assert report.amount_parse_failures == 1
    assert len(frame) == 2
    assert frame["supplier"].tolist() == ["Café Ltd", "Gamma Ltd"]
    assert frame["txn_date"].isna().tolist() == [False, True]
    assert frame["amount"].iloc[0] == 1000.0
    assert pd.isna(frame["amount"].iloc[1])
    assert frame["expense_type"].tolist() == ["Repairs", "Repairs"]


def test_load_directory_reports_every_row_and_flags_unmappable_file(tmp_path, caplog):
    _write(tmp_path / "a.csv", LAYOUT_A)
    _write(tmp_path / "b.csv", LAYOUT_B, encoding="latin-1")
    _write(tmp_path / "c.csv", UNMAPPABLE)

    with caplog.at_level(logging.WARNING):
        frame, reports = load_directory(tmp_path)

    assert [report.file.rsplit("/", 1)[-1] for report in reports] == ["a.csv", "b.csv", "c.csv"]
    assert reports[2].mapped is False
    assert reports[2].unmapped_reason
    assert "c.csv" in caplog.text
    for report in reports:
        kept = int((frame["source_file"] == Path(report.file).name).sum())
        assert report.rows_read == kept + sum(report.rows_dropped.values())
    assert list(frame.columns) == CANONICAL_COLUMNS + ["source_file"]
    assert len(frame) == 4


def test_quality_report_totals_and_json_output(tmp_path):
    _write(tmp_path / "a.csv", LAYOUT_A)
    _write(tmp_path / "b.csv", LAYOUT_B, encoding="latin-1")
    _write(tmp_path / "c.csv", UNMAPPABLE)
    _, reports = load_directory(tmp_path)

    summary = quality_report(reports)
    assert summary["files"] == 3
    assert summary["files_mapped"] == 2
    assert summary["files_unmapped"] == 1
    assert summary["rows_read"] == 5
    assert summary["rows_kept"] == 4
    assert summary["rows_dropped"] == {"empty_row": 1}
    assert summary["amount_parse_failures"] == 1
    assert summary["date_parse_failures"] == 1
    assert summary["unmapped_files"][0]["file"].endswith("c.csv")

    report_path = tmp_path / "out" / "quality_report.json"
    write_quality_report(reports, report_path)
    assert json.loads(report_path.read_text(encoding="utf-8")) == summary


def test_parse_date_handles_dash_and_excel_serial_dates() -> None:
    assert parse_date("10-01-2018") == datetime(2018, 1, 10)
    assert parse_date("42121") == datetime(2015, 4, 27)
