"""Runs the audit one month at a time, as if each month arrived fresh, and stores the results.

Month t is processed with only the rows dated before the start of month t+1, so nothing a later
month contains can change what month t reports. Results go to a DuckDB file that is rebuilt on
every run, which keeps repeated runs on the same data identical apart from the creation time.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from continuous_audit_lab.data.schema import TXN_ID_COLUMN
from continuous_audit_lab.monitor.evidence import (
    build_evidence,
    canonical_hash,
    canonical_rows,
)
from continuous_audit_lab.monitor.kri import (
    KRI_NAMES,
    build_thresholds,
    compute_month_kris,
    rag_table,
)
from continuous_audit_lab.scoring.risk import WEIGHTS, score_month
from continuous_audit_lab.scoring.rules import DATE_COLUMN, findings_txn_ids
from continuous_audit_lab.scoring.stats import DEFAULT_WINDOW_MONTHS, MONTH_STEP, month_start
from continuous_audit_lab.tests_sql.params import DEFAULT_PARAMS, PARAMETER_VERSION
from continuous_audit_lab.tests_sql.runner import run_all

DEFAULT_OUT_PATH = "runs/audit.duckdb"
DEFAULT_ALERT_K = 50
DEFAULT_WARMUP_MONTHS = 12
DATA_QUALITY_PATH = "data/processed/quality_report.json"
RUN_ID_PREFIX = "run-"
RUN_ID_LENGTH = 12
PERIOD_TEXT_FORMAT = "%Y-%m-%d"
PERIOD_COLUMN = "period"
KEY_COLUMN = "key"
VALUE_COLUMN = "value"
WAL_SUFFIX = ".wal"
ALERT_LIST_COLUMNS = ("tests_fired", "involved_txn_ids")
ALERT_COLUMNS = (
    "alert_id",
    "run_id",
    "period",
    "txn_id",
    "risk_score",
    "rank",
    "risk_reasons",
    "tests_fired",
    "involved_txn_ids",
    "params_json",
    "evidence_hash",
    "amount",
)
RUN_LOG_COLUMNS = (
    "run_id",
    "period",
    "rows",
    "tests_executed",
    "alert_count",
    "rules_flagged_txns",
    "parameter_version",
    "data_hash",
    "created_at",
)
CREATED_AT_FORMAT = "%Y-%m-%dT%H:%M:%S"
SCORING_FLAGGED_COUNT_COLUMN = "rules_flagged_txns"


def run_months(
    payments: pd.DataFrame,
    out_path: str = DEFAULT_OUT_PATH,
    alert_k: int = DEFAULT_ALERT_K,
    warmup_months: int = DEFAULT_WARMUP_MONTHS,
    window_months: int = DEFAULT_WINDOW_MONTHS,
    source: str = "synthetic",
) -> dict[str, Any]:
    """Run every month in order, store the results in DuckDB and return a short summary.

    Each month sees only rows dated before the start of the following month. Alerts are the top
    alert_k payments by risk score with a positive score. Thresholds are set from the first
    warmup_months KRI values, and the RAG table labels every month against them.
    """
    frame = _with_parsed_dates(payments)
    run_id = _run_id(frame, alert_k, source)
    months = sorted(frame[DATE_COLUMN].map(month_start).unique())
    run_log: list[dict[str, Any]] = []
    alerts: list[dict[str, Any]] = []
    kri_values: list[dict[str, float]] = []
    for month in months:
        visible = frame[frame[DATE_COLUMN] < month + MONTH_STEP]
        findings = run_all(visible)
        scores = score_month(visible, month, window_months=window_months)
        month_alerts = _month_alerts(visible, month, findings, scores, run_id, alert_k)
        alerts.extend(month_alerts)
        run_log.append(_run_log_row(visible, month, findings, run_id, len(month_alerts)))
        kri_values.append(compute_month_kris(visible, month, findings))
    kri_history = pd.DataFrame(
        kri_values, index=pd.DatetimeIndex(months, name=PERIOD_COLUMN), columns=list(KRI_NAMES)
    )
    thresholds = build_thresholds(kri_history, baseline_months=warmup_months)
    rag = rag_table(kri_history, thresholds, baseline_months=warmup_months)
    tables = {
        "run_log": _frame_of(run_log, RUN_LOG_COLUMNS),
        "alerts": _alerts_table(alerts),
        "kris": _reset_period(kri_history),
        "kri_thresholds": thresholds,
        "kri_rag": _reset_period(rag),
        "data_quality": _data_quality_table(DATA_QUALITY_PATH),
        "run_meta": _run_meta_table(run_id, source, alert_k),
    }
    _write_tables(Path(out_path), tables)
    return {
        "run_id": run_id,
        "months": len(months),
        "alerts": len(alerts),
        "out_path": str(out_path),
    }


def _with_parsed_dates(payments: pd.DataFrame) -> pd.DataFrame:
    """Return a copy of the payments with txn_date as datetime, the type every test expects."""
    frame = payments.copy()
    frame[DATE_COLUMN] = pd.to_datetime(frame[DATE_COLUMN])
    return frame


def _run_id(frame: pd.DataFrame, alert_k: int, source: str) -> str:
    """Derive the run id from the whole ledger and the settings, so equal inputs give equal ids."""
    digest = canonical_hash(
        {
            "data": canonical_hash(canonical_rows(frame)),
            "parameter_version": PARAMETER_VERSION,
            "alert_k": alert_k,
            "source": source,
        }
    )
    return RUN_ID_PREFIX + digest[:RUN_ID_LENGTH]


def _month_alerts(
    visible: pd.DataFrame,
    month: pd.Timestamp,
    findings: dict[str, pd.DataFrame],
    scores: pd.DataFrame,
    run_id: str,
    alert_k: int,
) -> list[dict[str, Any]]:
    """Build the evidence record for each of the month's top alert_k positively scored payments."""
    top = scores[scores["risk_score"] > 0].head(alert_k)
    return [
        build_evidence(
            visible,
            month,
            txn_id,
            findings,
            DEFAULT_PARAMS,
            WEIGHTS,
            run_id,
            row,
        )
        for txn_id, row in top.iterrows()
    ]


def _run_log_row(
    visible: pd.DataFrame,
    month: pd.Timestamp,
    findings: dict[str, pd.DataFrame],
    run_id: str,
    alert_count: int,
) -> dict[str, Any]:
    """Summarise one month's run: rows, tests executed, alerts, flagged payments and data hash."""
    in_month = visible[visible[DATE_COLUMN] >= month]
    month_ids = set(in_month[TXN_ID_COLUMN].astype(int))
    listed = findings_txn_ids(findings, month)
    flagged = set().union(*listed.values()) & month_ids
    return {
        "run_id": run_id,
        "period": month.strftime(PERIOD_TEXT_FORMAT),
        "rows": len(in_month),
        "tests_executed": ",".join(sorted(findings)),
        "alert_count": alert_count,
        SCORING_FLAGGED_COUNT_COLUMN: len(flagged),
        "parameter_version": PARAMETER_VERSION,
        "data_hash": canonical_hash(canonical_rows(in_month)),
        "created_at": datetime.now(UTC).strftime(CREATED_AT_FORMAT),
    }


def _frame_of(records: list[dict[str, Any]], columns: tuple[str, ...]) -> pd.DataFrame:
    """Build a dataframe with a fixed column order, even when there are no records."""
    return pd.DataFrame(records, columns=list(columns))


def _alerts_table(alerts: list[dict[str, Any]]) -> pd.DataFrame:
    """Build the alerts table, storing list-valued fields as JSON text."""
    table = _frame_of(alerts, ALERT_COLUMNS)
    for column in ALERT_LIST_COLUMNS:
        table[column] = table[column].map(json.dumps)
    return table


def _reset_period(frame: pd.DataFrame) -> pd.DataFrame:
    """Move the period index into a text column named period, formatted as ISO dates."""
    table = frame.reset_index()
    table[PERIOD_COLUMN] = table[PERIOD_COLUMN].map(
        lambda value: pd.Timestamp(value).strftime(PERIOD_TEXT_FORMAT)
    )
    return table


def _data_quality_table(path: str) -> pd.DataFrame:
    """Flatten the quality report JSON into key and value rows, or return an empty table."""
    empty = pd.DataFrame({KEY_COLUMN: pd.Series(dtype=str), VALUE_COLUMN: pd.Series(dtype=str)})
    report_path = Path(path)
    if not report_path.exists():
        return empty
    report = json.loads(report_path.read_text())
    rows = [{KEY_COLUMN: key, VALUE_COLUMN: _as_text(value)} for key, value in _flatten(report)]
    return _frame_of(rows, (KEY_COLUMN, VALUE_COLUMN))


def _flatten(node: Any, prefix: str = "") -> list[tuple[str, Any]]:
    """Return (dotted key, leaf value) pairs for every leaf of a nested JSON object."""
    if not isinstance(node, dict):
        return [(prefix, node)]
    pairs: list[tuple[str, Any]] = []
    for key, value in node.items():
        dotted = f"{prefix}.{key}" if prefix else str(key)
        pairs.extend(_flatten(value, dotted))
    return pairs


def _as_text(value: Any) -> str:
    """Render a leaf value as text, leaving strings as they are and JSON-encoding anything else."""
    return value if isinstance(value, str) else json.dumps(value, default=str)


def _run_meta_table(run_id: str, source: str, alert_k: int) -> pd.DataFrame:
    """Record the settings of the run so that the report can read them back."""
    return pd.DataFrame(
        [
            {
                "run_id": run_id,
                "source": source,
                "alert_k": alert_k,
                "parameter_version": PARAMETER_VERSION,
            }
        ]
    )


def _write_tables(out_path: Path, tables: dict[str, pd.DataFrame]) -> None:
    """Replace the DuckDB file with a fresh one holding exactly the given tables."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.unlink(missing_ok=True)
    Path(f"{out_path}{WAL_SUFFIX}").unlink(missing_ok=True)
    connection = duckdb.connect(str(out_path))
    try:
        for name, table in tables.items():
            connection.register("staged", table)
            connection.execute(f"CREATE TABLE {name} AS SELECT * FROM staged")
            connection.unregister("staged")
    finally:
        connection.close()
