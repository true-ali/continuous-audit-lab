import math

import pandas as pd
import pytest

from continuous_audit_lab.data.schema import TXN_ID_COLUMN
from continuous_audit_lab.data.synthetic import generate
from continuous_audit_lab.scoring.evaluate import (
    AMOUNT_RANKER,
    RANDOM_RANKER,
    RISK_RANKER,
    evaluate_synthetic,
    precision_at_k,
    recall_at_k,
    summarise,
)
from continuous_audit_lab.scoring.risk import WEIGHTS, score_month
from continuous_audit_lab.scoring.rules import DATE_COLUMN, ROW_LEVEL_TESTS, rule_flags
from continuous_audit_lab.scoring.stats import month_start

TEST_MONTH_OFFSET = 20
MONTH_STEP = pd.DateOffset(months=1)
SCORE_COLUMNS = [*ROW_LEVEL_TESTS, "z_expense", "z_supplier", "iforest", "risk_score", "rank"]
EVAL_K = 50
EXPECTED_FLAG_COUNT_FLOOR = 1


def _test_month(payments: pd.DataFrame) -> pd.Timestamp:
    """Return the start of the month used for the leakage and scoring checks."""
    start = payments[DATE_COLUMN].min()
    return month_start(start) + TEST_MONTH_OFFSET * MONTH_STEP


def _month_end(month: pd.Timestamp) -> pd.Timestamp:
    """Return the first instant after the given month."""
    return month + MONTH_STEP


def test_precision_at_k_counts_hits_over_k() -> None:
    """Precision@k divides the anomalies in the top k by k."""
    ranked = [True, False, True, False, False]
    assert precision_at_k(ranked, 2) == pytest.approx(0.5)
    assert precision_at_k(ranked, 3) == pytest.approx(2 / 3)


def test_recall_at_k_divides_by_total_and_is_nan_without_anomalies() -> None:
    """Recall@k divides the anomalies in the top k by the month's total anomalies."""
    ranked = [True, False, True, False, False]
    assert recall_at_k(ranked, 3, total_anomalies=4) == pytest.approx(0.5)
    assert math.isnan(recall_at_k(ranked, 3, total_anomalies=0))


def test_rule_flags_do_not_use_future_rows() -> None:
    """Flags for a month are unchanged by deleting later rows or inflating their amounts."""
    payments = generate()
    month = _test_month(payments)
    end = _month_end(month)
    base = rule_flags(payments, month)
    assert base.to_numpy().sum() >= EXPECTED_FLAG_COUNT_FLOOR
    without_future = payments[payments[DATE_COLUMN] < end]
    pd.testing.assert_frame_equal(rule_flags(without_future, month), base)
    inflated = payments.copy()
    later = inflated[DATE_COLUMN] >= end
    inflated.loc[later, "amount"] = inflated.loc[later, "amount"] * 1000
    pd.testing.assert_frame_equal(rule_flags(inflated, month), base)


def test_score_month_returns_expected_columns_and_unique_ranks() -> None:
    """score_month returns the flags, statistical scores and a unique rank from 1 to n."""
    payments = generate()
    scores = score_month(payments, _test_month(payments))
    assert list(scores.columns) == [*SCORE_COLUMNS, "risk_reasons"]
    assert scores.index.name == TXN_ID_COLUMN
    ranks = sorted(scores["rank"].tolist())
    assert ranks == list(range(1, len(scores) + 1))


def test_weights_cover_all_row_level_flags() -> None:
    """Every row-level SQL flag has a documented weight."""
    assert set(ROW_LEVEL_TESTS) <= set(WEIGHTS)
    assert all(weight >= 0 for weight in WEIGHTS.values())


def test_risk_ranking_beats_baselines_on_synthetic_ledger() -> None:
    """Risk-score precision@50 beats both the random and the amount-only baselines on average."""
    per_month = evaluate_synthetic(generate(), k=EVAL_K)
    summary = summarise(per_month)
    risk = summary.loc[RISK_RANKER, "mean_precision_at_k"]
    amount = summary.loc[AMOUNT_RANKER, "mean_precision_at_k"]
    random = summary.loc[RANDOM_RANKER, "mean_precision_at_k"]
    print(f"\nmean precision@{EVAL_K}: risk={risk:.3f} amount={amount:.3f} random={random:.3f}")
    assert risk > random
    assert risk > amount
