"""Evidence records for alerts: each alert is bound by a hash to the rows and tests that raised it.

The evidence hash covers the tests that fired, the parameters in force and the canonical fields of
every transaction involved. Re-performance re-reads those rows from the source ledger, recomputes
the hash and re-runs the fired tests on the ledger as it stood at the alert's month, so an edited
source row or a test that no longer flags the transaction is detected.
"""

import hashlib
import json
from typing import Any

import pandas as pd

from continuous_audit_lab.data.schema import TXN_ID_COLUMN
from continuous_audit_lab.scoring.rules import DATE_COLUMN, ROW_LEVEL_TESTS, findings_txn_ids
from continuous_audit_lab.scoring.stats import MONTH_STEP, month_start
from continuous_audit_lab.tests_sql.params import PARAMETER_VERSION
from continuous_audit_lab.tests_sql.runner import run_all

EVIDENCE_ROW_FIELDS = (
    TXN_ID_COLUMN,
    DATE_COLUMN,
    "supplier",
    "amount",
    "expense_type",
    "department",
    "reference",
)
AMOUNT_COLUMN = "amount"
PERIOD_TEXT_FORMAT = "%Y-%m-%d"
ALERT_ID_TEMPLATE = "{run_id}:{period}:{txn_id}"
PARAMETERS_KEY = "parameters"


def clean_value(value: Any) -> Any:
    """Convert one cell to a JSON-safe value: missing becomes None, timestamps become ISO text.

    Numpy scalars are turned into plain Python numbers so that the canonical JSON is identical
    whether a value came from pandas, DuckDB or the raw CSV.
    """
    if pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    return value


def canonical_rows(frame: pd.DataFrame) -> list[dict[str, Any]]:
    """Return the canonical fields of every row, ordered by txn_id, as plain dictionaries.

    Ordering by txn_id makes the result independent of the order the rows arrived in.
    """
    ordered = frame.sort_values(TXN_ID_COLUMN)
    records = ordered[list(EVIDENCE_ROW_FIELDS)].to_dict("records")
    return [{key: clean_value(value) for key, value in record.items()} for record in records]


def canonical_hash(payload: Any) -> str:
    """Return the SHA-256 hex digest of the payload as canonical JSON with sorted keys.

    Canonical JSON means fixed key order and no optional whitespace, so the same content always
    hashes to the same digest.
    """
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def evidence_hash(tests_fired: list[str], params: dict, rows: list[dict[str, Any]]) -> str:
    """Hash the fired tests, the parameters and the involved rows into one evidence digest."""
    return canonical_hash({"tests_fired": tests_fired, "params": params, "rows": rows})


def build_evidence(
    payments_upto_month: pd.DataFrame,
    month: pd.Timestamp,
    txn_id: int,
    findings: dict[str, pd.DataFrame],
    params: dict,
    weights: dict[str, float],
    run_id: str,
    score_row: pd.Series,
) -> dict[str, Any]:
    """Build the evidence record for one alert from the month's findings and scoring row.

    The tests fired are the row-level tests that list this transaction for the month. The
    involved transactions are every id those tests list, always including txn_id itself. The
    score_row supplies risk_score, rank and risk_reasons from score_month for the same month.
    """
    period = month_start(pd.Timestamp(month)).strftime(PERIOD_TEXT_FORMAT)
    listed = findings_txn_ids(findings, month)
    fired = sorted(name for name in ROW_LEVEL_TESTS if int(txn_id) in listed.get(name, set()))
    involved = sorted({int(txn_id)}.union(*(listed[name] for name in fired)))
    rows = involved_rows(payments_upto_month, involved)
    amount = next(row[AMOUNT_COLUMN] for row in rows if row[TXN_ID_COLUMN] == int(txn_id))
    params_payload = {
        PARAMETERS_KEY: params,
        "parameter_version": PARAMETER_VERSION,
        "weights": weights,
    }
    return {
        "alert_id": ALERT_ID_TEMPLATE.format(run_id=run_id, period=period, txn_id=int(txn_id)),
        "run_id": run_id,
        "period": period,
        "txn_id": int(txn_id),
        "risk_score": float(score_row["risk_score"]),
        "rank": int(score_row["rank"]),
        "risk_reasons": str(score_row["risk_reasons"]),
        "tests_fired": fired,
        "involved_txn_ids": involved,
        "params_json": json.dumps(params_payload, sort_keys=True),
        "evidence_hash": evidence_hash(fired, params, rows),
        "amount": amount,
    }


def involved_rows(payments: pd.DataFrame, txn_ids: list[int]) -> list[dict[str, Any]]:
    """Return the canonical rows of the given transaction ids, found in the payments frame."""
    return canonical_rows(payments[payments[TXN_ID_COLUMN].isin(txn_ids)])


def reperform(record: dict[str, Any], payments: pd.DataFrame) -> bool:
    """Return True only if the source rows still hash to the record and its tests still flag it.

    The involved rows are re-read from payments and hashed with the stored tests and parameters.
    Then the fired tests are re-run on the ledger up to the alert's month. An alert that fired no
    row-level test has no test to re-run, so only the hash is checked for it.
    """
    stored = json.loads(record["params_json"])[PARAMETERS_KEY]
    involved = [int(txn_id) for txn_id in record["involved_txn_ids"]]
    rows = involved_rows(payments, involved)
    if len(rows) != len(involved):
        return False
    if evidence_hash(record["tests_fired"], stored, rows) != record["evidence_hash"]:
        return False
    return _fired_tests_still_flag(record, payments, stored)


def _fired_tests_still_flag(record: dict[str, Any], payments: pd.DataFrame, params: dict) -> bool:
    """Re-run the fired tests on the ledger up to the alert's month and check the txn is listed."""
    fired = record["tests_fired"]
    if not fired:
        return True
    month = pd.Timestamp(record["period"])
    visible = payments[payments[DATE_COLUMN] < month + MONTH_STEP]
    findings = run_all(visible, params=params, names=fired)
    listed = findings_txn_ids(findings, month)
    return all(int(record["txn_id"]) in listed.get(name, set()) for name in fired)
