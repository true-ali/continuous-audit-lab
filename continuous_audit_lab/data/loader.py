import csv
import io
import json
import logging
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from continuous_audit_lab.data.schema import CANONICAL_COLUMNS, TXN_ID_COLUMN

logger = logging.getLogger(__name__)

HEADER_ALIASES: dict[str, list[str]] = {
    "txn_date": ["date", "paymentdate", "transactiondate"],
    "supplier": ["supplier", "suppliername", "beneficiary"],
    "amount": ["amount", "netamount", "value", "total"],
    "expense_type": ["expensetype", "expensedescription", "expenditurecategory"],
    "department": ["service", "department", "directorate"],
    "reference": ["reference", "invoicenumber", "transactionnumber", "voucher"],
}
REQUIRED_COLUMNS = ("txn_date", "supplier", "amount")
OPTIONAL_COLUMNS = ("expense_type", "department", "reference")
SOURCE_COLUMN = "source_file"
HEADER_SEARCH_LINES = 15
EXCEL_EPOCH = datetime(1899, 12, 30)
EXCEL_SERIAL_RANGE = (30000, 60000)
DATE_FORMATS = ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d-%b-%Y", "%d %b %Y", "%d-%B-%Y", "%d %B %Y")
DEFAULT_RAW_DIR = "data/raw/southlakeland"
DEFAULT_OUT_PATH = "data/processed/payments.csv"
DEFAULT_REPORT_PATH = "data/processed/quality_report.json"


@dataclass
class FileReport:
    file: str
    mapped: bool = False
    rows_read: int = 0
    rows_dropped: dict[str, int] = field(default_factory=dict)
    amount_parse_failures: int = 0
    date_parse_failures: int = 0
    unmapped_reason: str = ""


def load_file(path: str | Path) -> tuple[pd.DataFrame, FileReport]:
    """Parse one council payments CSV into canonical columns, keeping every data row.

    Rows that fail amount or date parsing keep NaN/NaT and are counted in the report,
    so no source row disappears without a trace.
    """
    source = Path(path)
    report = FileReport(file=str(source))
    rows = list(csv.reader(io.StringIO(_read_text(source), newline="")))
    found = _find_header(rows)
    if found is None:
        return _unmapped(report, "no header row with date, supplier and amount columns")
    header_index, mapping = found

    body = rows[header_index + 1 :]
    populated = [row for row in body if any(cell.strip() for cell in row)]
    dropped = len(body) - len(populated)
    report.rows_read = len(body)
    report.rows_dropped = {"empty_row": dropped} if dropped else {}

    frame = pd.DataFrame(
        {
            column: [_cell(row, mapping.get(column)) for row in populated]
            for column in CANONICAL_COLUMNS
        }
    )
    frame["amount"] = frame["amount"].map(parse_amount).astype("float64")
    frame["txn_date"] = pd.to_datetime(frame["txn_date"].map(parse_date))
    frame[SOURCE_COLUMN] = source.name

    report.mapped = True
    report.amount_parse_failures = int(frame["amount"].isna().sum())
    report.date_parse_failures = int(frame["txn_date"].isna().sum())
    return frame[CANONICAL_COLUMNS + [SOURCE_COLUMN]], report


def load_directory(raw_dir: str | Path) -> tuple[pd.DataFrame, list[FileReport]]:
    """Load every CSV in a directory in filename order and concatenate the rows."""
    paths = sorted(Path(raw_dir).glob("*.csv"), key=lambda path: path.name)
    if not paths:
        raise FileNotFoundError(f"no CSV files in {raw_dir}")
    frames: list[pd.DataFrame] = []
    reports: list[FileReport] = []
    for path in paths:
        frame, report = load_file(path)
        frames.append(frame)
        reports.append(report)
    non_empty = [frame for frame in frames if not frame.empty]
    combined = pd.concat(non_empty, ignore_index=True) if non_empty else _empty_frame()
    return combined, reports


def quality_report(reports: list[FileReport]) -> dict:
    """Summarise load reports into totals for the audit trail."""
    dropped: Counter[str] = Counter()
    for report in reports:
        dropped.update(report.rows_dropped)
    rows_read = sum(report.rows_read for report in reports)
    return {
        "files": len(reports),
        "files_mapped": sum(report.mapped for report in reports),
        "files_unmapped": sum(not report.mapped for report in reports),
        "rows_read": rows_read,
        "rows_kept": rows_read - sum(dropped.values()),
        "rows_dropped": dict(dropped),
        "amount_parse_failures": sum(report.amount_parse_failures for report in reports),
        "date_parse_failures": sum(report.date_parse_failures for report in reports),
        "unmapped_files": [
            {"file": report.file, "reason": report.unmapped_reason}
            for report in reports
            if not report.mapped
        ],
    }


def write_quality_report(reports: list[FileReport], path: str | Path) -> None:
    """Write the quality summary as JSON so every run leaves a reviewable record."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(quality_report(reports), indent=2), encoding="utf-8")


def prepare(
    raw_dir: str | Path = DEFAULT_RAW_DIR,
    out_path: str | Path = DEFAULT_OUT_PATH,
    report_path: str | Path = DEFAULT_REPORT_PATH,
) -> dict:
    """Load raw payments, assign a stable txn_id, write the table and quality report."""
    frame, reports = load_directory(raw_dir)
    frame.insert(0, TXN_ID_COLUMN, list(range(len(frame))))
    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    _write_frame(frame, target)
    write_quality_report(reports, report_path)
    return quality_report(reports)


def parse_amount(text: str) -> float:
    """Turn a council amount such as '£1,234.50' or '(£20.00)' into a signed float.

    Parentheses denote a negative amount. Anything unparseable returns NaN.
    """
    stripped = text.strip()
    negative = stripped.startswith("(") and stripped.endswith(")")
    digits = re.sub(r"[£,\s()]", "", stripped)
    try:
        value = float(digits)
    except ValueError:
        return math.nan
    if not math.isfinite(value):
        return math.nan
    return -value if negative else value


def parse_date(text: str) -> datetime | None:
    """Parse a payment date against the known layouts, returning None when none match.

    Formats are tried explicitly because pandas dayfirst parsing misreads ISO dates. Some
    files hold Excel serial day numbers, which are converted from the 1899-12-30 epoch.
    """
    stripped = text.strip()
    if stripped.isdigit() and EXCEL_SERIAL_RANGE[0] <= int(stripped) <= EXCEL_SERIAL_RANGE[1]:
        return EXCEL_EPOCH + timedelta(days=int(stripped))
    for date_format in DATE_FORMATS:
        try:
            return datetime.strptime(stripped, date_format)
        except ValueError:
            continue
    return None


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def _normalise(cell: str) -> str:
    return re.sub(r"[^a-z0-9]", "", cell.lower())


def _map_columns(header: list[str]) -> dict[str, int]:
    normalised = [_normalise(cell) for cell in header]
    mapping: dict[str, int] = {}
    for column, aliases in HEADER_ALIASES.items():
        for index, name in enumerate(normalised):
            if name in aliases:
                mapping[column] = index
                break
    return mapping


def _find_header(rows: list[list[str]]) -> tuple[int, dict[str, int]] | None:
    for index, row in enumerate(rows[:HEADER_SEARCH_LINES]):
        mapping = _map_columns(row)
        if all(column in mapping for column in REQUIRED_COLUMNS):
            return index, mapping
    return None


def _cell(row: list[str], index: int | None) -> str:
    if index is None or index >= len(row):
        return ""
    return row[index].strip()


def _empty_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "txn_date": pd.Series(dtype="datetime64[ns]"),
            "supplier": pd.Series(dtype="object"),
            "amount": pd.Series(dtype="float64"),
            "expense_type": pd.Series(dtype="object"),
            "department": pd.Series(dtype="object"),
            "reference": pd.Series(dtype="object"),
            SOURCE_COLUMN: pd.Series(dtype="object"),
        }
    )


def _unmapped(report: FileReport, reason: str) -> tuple[pd.DataFrame, FileReport]:
    report.unmapped_reason = reason
    logger.warning("Skipping unmapped payments file %s: %s", report.file, reason)
    return _empty_frame(), report


def _write_frame(frame: pd.DataFrame, target: Path) -> None:
    frame.to_csv(target.with_suffix(".csv"), index=False)
