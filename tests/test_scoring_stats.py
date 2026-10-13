import time

import numpy as np
import pandas as pd
import pytest

from continuous_audit_lab.data.schema import LABEL_COLUMN, TXN_ID_COLUMN
from continuous_audit_lab.data.synthetic import generate
from continuous_audit_lab.scoring.stats import (
    DEFAULT_WINDOW_MONTHS,
    IFOREST,
    MONTH_STEP,
    Z_EXPENSE,
    Z_SUPPLIER,
    month_start,
    robust_z,
    stat_scores,
)

START = pd.Timestamp("2018-04-01")
BASELINE_MONTHS = DEFAULT_WINDOW_MONTHS
LAST_MONTH_INDEX = 35
LEAKAGE_MONTH_INDEX = 15
SCORE_COLUMNS = [Z_EXPENSE, Z_SUPPLIER, IFOREST]
SCALED_AMOUNT_FACTOR = 1000.0
RUNTIME_LIMIT_SECONDS = 10.0
HAND_VALUES = [1.0, 2.0, 3.0]
HAND_MEDIAN = 2.0
HAND_MAD = 1.0
HAND_EXPECTED = [-0.6745, 0.0, 0.6745]


@pytest.fixture(scope="module")
def payments() -> pd.DataFrame:
    """Provide the seeded synthetic ledger shared by the scoring tests."""
    return generate()


def _month_at(index: int) -> pd.Timestamp:
    """Return the first day of the month that sits index months after the ledger start."""
    return month_start(START + index * MONTH_STEP)


def test_robust_z_matches_hand_example() -> None:
    """Check robust_z against a hand-computed median and MAD example."""
    z = robust_z(pd.Series(HAND_VALUES), median=HAND_MEDIAN, mad=HAND_MAD)
    np.testing.assert_allclose(z.to_numpy(), HAND_EXPECTED)


def test_stat_scores_returns_one_row_per_transaction_in_month(payments: pd.DataFrame) -> None:
    """Check that one scored row exists per payment in the month, with the three score columns."""
    month = _month_at(BASELINE_MONTHS)
    in_month = payments[
        (payments["txn_date"] >= month) & (payments["txn_date"] < month + MONTH_STEP)
    ]
    scores = stat_scores(payments, month)
    assert list(scores.columns) == SCORE_COLUMNS
    assert sorted(scores.index) == sorted(in_month[TXN_ID_COLUMN])


def test_stat_scores_ignores_rows_dated_from_next_month(payments: pd.DataFrame) -> None:
    """Check that scores for a month are unchanged by any row dated in or after the next month."""
    month = _month_at(LEAKAGE_MONTH_INDEX)
    next_month = month + MONTH_STEP
    full = stat_scores(payments, month)
    truncated = stat_scores(payments[payments["txn_date"] < next_month], month)
    future_scaled = payments.copy()
    future_scaled.loc[future_scaled["txn_date"] >= next_month, "amount"] *= SCALED_AMOUNT_FACTOR
    pd.testing.assert_frame_equal(truncated, full)
    pd.testing.assert_frame_equal(stat_scores(future_scaled, month), full)


def test_injected_anomalies_score_higher_than_clean_rows(payments: pd.DataFrame) -> None:
    """Check that labelled anomalies score higher on average than clean rows."""
    scored = pd.concat(
        stat_scores(payments, _month_at(index))
        for index in range(BASELINE_MONTHS, LAST_MONTH_INDEX + 1)
    )
    labels = payments.set_index(TXN_ID_COLUMN)[LABEL_COLUMN]
    scored = scored.join(labels)
    scored["max_abs_z"] = scored[[Z_EXPENSE, Z_SUPPLIER]].abs().max(axis=1)
    is_anomaly = scored[LABEL_COLUMN] != ""
    assert scored.loc[is_anomaly, IFOREST].mean() > scored.loc[~is_anomaly, IFOREST].mean()
    assert scored.loc[is_anomaly, "max_abs_z"].mean() > scored.loc[~is_anomaly, "max_abs_z"].mean()


def test_stat_scores_runs_within_time_budget(payments: pd.DataFrame) -> None:
    """Check that one monthly scoring call finishes within the runtime budget."""
    started = time.perf_counter()
    stat_scores(payments, _month_at(LEAKAGE_MONTH_INDEX))
    assert time.perf_counter() - started < RUNTIME_LIMIT_SECONDS
