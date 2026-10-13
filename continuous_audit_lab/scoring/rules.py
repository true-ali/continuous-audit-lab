"""Row-level SQL audit flags for one month, computed without any look-ahead.

Each row-level SQL test is run on the ledger truncated at the end of the scored month, so a
flag for month t can only depend on payments dated up to and including month t. This keeps
the scores honest when they are later evaluated on the same synthetic ledger.
"""

import itertools

import pandas as pd

from continuous_audit_lab.data.schema import TXN_ID_COLUMN
from continuous_audit_lab.scoring.stats import month_start
from continuous_audit_lab.tests_sql.runner import connect, run_test

ROW_LEVEL_TESTS = (
    "duplicate_payments",
    "threshold_splitting",
    "round_amounts",
    "off_hours",
    "dormant_reactivation",
    "reference_integrity",
)
DATE_COLUMN = "txn_date"
PERIOD_COLUMN = "period"
TXN_IDS_COLUMN = "txn_ids"
MONTHS_PER_PERIOD = 1


def rule_flags(payments: pd.DataFrame, month: pd.Timestamp) -> pd.DataFrame:
    """Return 0/1 flags for every payment of the month, one column per row-level SQL test.

    Each test runs on the ledger cut off at the end of the month so that no later payment can
    influence the result. Only findings whose period is this month count, and every transaction
    id they list that is dated inside the month is marked as flagged.
    """
    start = month_start(pd.Timestamp(month))
    end = start + pd.DateOffset(months=MONTHS_PER_PERIOD)
    visible = payments.loc[payments[DATE_COLUMN] < end]
    month_ids = visible.loc[visible[DATE_COLUMN] >= start, TXN_ID_COLUMN].to_numpy()
    flags = pd.DataFrame(
        0,
        index=pd.Index(month_ids, name=TXN_ID_COLUMN),
        columns=list(ROW_LEVEL_TESTS),
        dtype=int,
    )
    connection = connect(visible)
    for name in ROW_LEVEL_TESTS:
        findings = run_test(connection, name)
        flagged = _flagged_ids(findings, start)
        flags.loc[flags.index.intersection(flagged), name] = 1
    connection.close()
    return flags


def findings_txn_ids(findings: dict[str, pd.DataFrame], month: pd.Timestamp) -> dict[str, set[int]]:
    """Return, for each row-level test present in findings, the txn ids it lists for the month.

    Only findings whose period is the given month count, matching how rule_flags scores a month,
    so the ids returned here are exactly the ones that contribute to that month's risk score.
    """
    start = month_start(pd.Timestamp(month))
    return {
        name: set(_flagged_ids(findings[name], start))
        for name in ROW_LEVEL_TESTS
        if name in findings
    }


def _flagged_ids(findings: pd.DataFrame, start: pd.Timestamp) -> list[int]:
    """Collect the transaction ids listed by findings whose period is the given month start."""
    in_period = pd.to_datetime(findings[PERIOD_COLUMN]) == start
    listed = findings.loc[in_period, TXN_IDS_COLUMN]
    return [int(txn_id) for txn_id in itertools.chain.from_iterable(listed)]
