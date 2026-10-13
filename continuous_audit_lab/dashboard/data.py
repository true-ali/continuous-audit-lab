"""Read-only data access for the dashboard: DuckDB audit tables and the source ledger.

Every loader opens the DuckDB file read-only, so the dashboard can never change a finished run.
The path comes from the AUDIT_DB environment variable when set, otherwise from the default run
file. Alert list fields are stored as JSON text and are parsed here so the UI works with lists.
"""

import json
import os
from typing import Any

import duckdb
import pandas as pd

from continuous_audit_lab.data.schema import LABEL_COLUMN, TXN_ID_COLUMN
from continuous_audit_lab.monitor.kri import KRI_NAMES
from continuous_audit_lab.scoring.rules import DATE_COLUMN

DEFAULT_DB_PATH = "runs/audit.duckdb"
DB_ENV_VAR = "AUDIT_DB"
PAYMENT_PATHS = {
    "synthetic": "data/processed/synthetic.csv",
    "real": "data/processed/payments.csv",
}
ALERT_LIST_COLUMNS = ("tests_fired", "involved_txn_ids")
PERIOD_COLUMN = "period"
RAG_COLUMNS = list(KRI_NAMES)


def db_path() -> str:
    """Return the audit database path, from AUDIT_DB when it is set and non-empty."""
    return os.environ.get(DB_ENV_VAR) or DEFAULT_DB_PATH


def payments_path_for(source: str) -> str:
    """Return the source ledger CSV that a run with the given source was built from."""
    return PAYMENT_PATHS[source]


def _query(sql: str, path: str | None, params: list[Any] | None = None) -> pd.DataFrame:
    """Run one read-only query against the audit database and return the rows as a dataframe."""
    with duckdb.connect(path or db_path(), read_only=True) as connection:
        return connection.execute(sql, params or []).df()


def load_run_meta(path: str | None = None) -> dict[str, Any]:
    """Return the settings of the run: run id, source, alert count k and parameter version."""
    return _query("SELECT * FROM run_meta", path).iloc[0].to_dict()


def load_kris(path: str | None = None) -> pd.DataFrame:
    """Return the monthly KRI values, one row per period in ascending order."""
    return _query("SELECT * FROM kris ORDER BY period", path)


def load_thresholds(path: str | None = None) -> pd.DataFrame:
    """Return the median, scaled MAD, amber and red limit of each KRI."""
    return _query("SELECT * FROM kri_thresholds", path)


def load_rag(path: str | None = None) -> pd.DataFrame:
    """Return the monthly RAG status of each KRI, one row per period in ascending order."""
    return _query("SELECT * FROM kri_rag ORDER BY period", path)


def latest_period(path: str | None = None) -> str:
    """Return the most recent period in the KRI table, as ISO text."""
    return str(load_kris(path)[PERIOD_COLUMN].max())


def latest_tiles(period: str | None = None, path: str | None = None) -> list[dict[str, Any]]:
    """Return one tile per KRI for the given period (latest by default) with value and limits.

    Each tile holds kri, value, rag (green, amber, red, baseline or n/a), amber and red. A period
    with no KRI row gives an empty list.
    """
    chosen = period or latest_period(path)
    kri_row = _row_for(load_kris(path), chosen)
    rag_row = _row_for(load_rag(path), chosen)
    limits = load_thresholds(path).set_index("kri")
    if kri_row is None or rag_row is None:
        return []
    return [
        {
            "kri": name,
            "value": float(kri_row[name]),
            "rag": str(rag_row[name]),
            "amber": float(limits.at[name, "amber"]),
            "red": float(limits.at[name, "red"]),
        }
        for name in RAG_COLUMNS
    ]


def _row_for(frame: pd.DataFrame, period: str) -> pd.Series | None:
    """Return the row of the given period, or None when the period is not in the table."""
    matches = frame[frame[PERIOD_COLUMN] == period]
    if matches.empty:
        return None
    return matches.iloc[0]


def load_alerts(period: str | None = None, path: str | None = None) -> pd.DataFrame:
    """Return the alerts, optionally for one period, sorted by risk score from highest down.

    The tests_fired and involved_txn_ids fields are parsed from JSON text into lists.
    """
    if period is None:
        frame = _query("SELECT * FROM alerts ORDER BY risk_score DESC", path)
    else:
        frame = _query(
            "SELECT * FROM alerts WHERE period = ? ORDER BY risk_score DESC", path, [period]
        )
    for column in ALERT_LIST_COLUMNS:
        frame[column] = frame[column].map(json.loads)
    return frame


def load_payments(payments_path: str) -> pd.DataFrame:
    """Read the source ledger CSV, with txn_id from row order when absent and no label column.

    The anomaly label is dropped here, so no part of the dashboard can show ground truth.
    """
    frame = pd.read_csv(payments_path, parse_dates=[DATE_COLUMN])
    if TXN_ID_COLUMN not in frame.columns:
        frame[TXN_ID_COLUMN] = range(len(frame))
    return frame.drop(columns=[LABEL_COLUMN], errors="ignore")


def transactions_for_alert(
    alert_row: pd.Series | dict[str, Any], payments_path: str
) -> pd.DataFrame:
    """Return the source rows of the transactions involved in one alert, ordered by txn_id.

    The involved ids come from the alert's involved_txn_ids list, which always includes the alert's
    own transaction.
    """
    payments = load_payments(payments_path)
    involved = [int(txn_id) for txn_id in alert_row["involved_txn_ids"]]
    selected = payments[payments[TXN_ID_COLUMN].isin(involved)]
    return selected.sort_values(TXN_ID_COLUMN).reset_index(drop=True)


def load_data_quality(path: str | None = None) -> pd.DataFrame:
    """Return the data-quality report as key and value rows; empty for a synthetic run."""
    return _query("SELECT key, value FROM data_quality ORDER BY key", path)
