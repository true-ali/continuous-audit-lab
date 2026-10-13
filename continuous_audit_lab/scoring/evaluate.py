"""Backtests the risk ranking against the labelled synthetic ledger and two simple baselines.

For every month after the warm-up, each ranker orders the month's payments and the top k are
checked against the synthetic labels. Precision@k is hits divided by k. Recall@k is hits divided
by the month's anomaly count, and it is undefined (NaN) for months with no anomalies, so those
months are left out of recall averages rather than counted as zero.
"""

import math
from collections.abc import Sequence

import numpy as np
import pandas as pd

from continuous_audit_lab.data.schema import LABEL_COLUMN, TXN_ID_COLUMN
from continuous_audit_lab.scoring.risk import score_month
from continuous_audit_lab.scoring.rules import DATE_COLUMN
from continuous_audit_lab.scoring.stats import DEFAULT_WINDOW_MONTHS, month_start

RISK_RANKER = "risk_score"
AMOUNT_RANKER = "amount_only"
RANDOM_RANKER = "random"
RANKERS = (RISK_RANKER, AMOUNT_RANKER, RANDOM_RANKER)
DEFAULT_K = 50
DEFAULT_WARMUP_MONTHS = 12
RANDOM_BASELINE_SEED = 0
AMOUNT_COLUMN = "amount"
MONTH_COLUMN = "month_start"
PER_MONTH_COLUMNS = ("month", "ranker", "n_txn", "n_anomalies", "precision_at_k", "recall_at_k")
SUMMARY_COLUMNS = (
    "mean_precision_at_k",
    "mean_recall_at_k",
    "months",
    "months_with_anomalies",
)
MISSING_TEXT = "n/a"
DECIMAL_PLACES = 3


def precision_at_k(ranked_is_anomaly: Sequence[bool], k: int) -> float:
    """Return the share of the top k ranked items that are anomalies, with k as denominator."""
    if k <= 0:
        raise ValueError("k must be positive")
    return _hits(ranked_is_anomaly, k) / k


def recall_at_k(ranked_is_anomaly: Sequence[bool], k: int, total_anomalies: int) -> float:
    """Return the share of all anomalies found in the top k; NaN when there are no anomalies."""
    if k <= 0:
        raise ValueError("k must be positive")
    if total_anomalies == 0:
        return math.nan
    return _hits(ranked_is_anomaly, k) / total_anomalies


def _hits(ranked_is_anomaly: Sequence[bool], k: int) -> int:
    """Count the anomalies among the first k ranked items."""
    return sum(bool(flag) for flag in list(ranked_is_anomaly)[:k])


def evaluate_synthetic(
    payments: pd.DataFrame,
    k: int = DEFAULT_K,
    warmup_months: int = DEFAULT_WARMUP_MONTHS,
    window_months: int = DEFAULT_WINDOW_MONTHS,
) -> pd.DataFrame:
    """Score each month after the warm-up with all three rankers and return one row per ranker-month.

    The warm-up months are skipped so that every evaluated month has a full history window. The
    random baseline uses one seeded generator, so repeated runs give identical numbers.
    """
    frame = payments.set_index(TXN_ID_COLUMN)
    frame[MONTH_COLUMN] = frame[DATE_COLUMN].map(month_start)
    is_anomaly = frame[LABEL_COLUMN].fillna("").ne("")
    months = sorted(frame[MONTH_COLUMN].unique())
    rng = np.random.default_rng(RANDOM_BASELINE_SEED)
    rows: list[dict] = []
    for month in months[warmup_months:]:
        month_frame = frame.loc[frame[MONTH_COLUMN] == month]
        month_ids = month_frame.index.to_numpy()
        anomaly_count = int(is_anomaly.loc[month_ids].sum())
        rankings = _rankings(payments, month, window_months, month_frame, is_anomaly, rng)
        for ranker, ranked in rankings.items():
            rows.append(
                {
                    "month": month,
                    "ranker": ranker,
                    "n_txn": len(month_ids),
                    "n_anomalies": anomaly_count,
                    "precision_at_k": precision_at_k(ranked, k),
                    "recall_at_k": recall_at_k(ranked, k, anomaly_count),
                }
            )
    return pd.DataFrame(rows, columns=list(PER_MONTH_COLUMNS))


def summarise(per_month: pd.DataFrame) -> pd.DataFrame:
    """Average precision and recall per ranker; recall averages skip months with no anomalies."""
    with_anomalies = per_month[per_month["n_anomalies"] > 0]
    summary = pd.DataFrame(
        {
            "mean_precision_at_k": per_month.groupby("ranker")["precision_at_k"].mean(),
            "mean_recall_at_k": with_anomalies.groupby("ranker")["recall_at_k"].mean(),
            "months": per_month.groupby("ranker").size(),
            "months_with_anomalies": with_anomalies.groupby("ranker").size(),
        }
    )
    return summary.reindex(list(RANKERS))[list(SUMMARY_COLUMNS)]


def format_results_table(summary: pd.DataFrame) -> str:
    """Render the per-ranker summary as a markdown table."""
    header = "| ranker | " + " | ".join(SUMMARY_COLUMNS) + " |"
    divider = "|" + "---|" * (len(SUMMARY_COLUMNS) + 1)
    lines = [header, divider]
    for ranker, row in summary.iterrows():
        cells = [_format_cell(row[column]) for column in SUMMARY_COLUMNS]
        lines.append("| " + " | ".join([str(ranker), *cells]) + " |")
    return "\n".join(lines)


def _format_cell(value: object) -> str:
    """Format one summary value, showing missing values as n/a and numbers to fixed decimals."""
    if pd.isna(value):
        return MISSING_TEXT
    if isinstance(value, float):
        return f"{value:.{DECIMAL_PLACES}f}"
    return str(value)


def _rankings(
    payments: pd.DataFrame,
    month: pd.Timestamp,
    window_months: int,
    month_frame: pd.DataFrame,
    is_anomaly: pd.Series,
    rng: np.random.Generator,
) -> dict[str, list[bool]]:
    """Return each ranker's anomaly flags in ranked order for one month."""
    scores = score_month(payments, month, window_months=window_months)
    month_ids = month_frame.index.to_numpy()
    amount_order = _amount_order(month_frame)
    random_order = rng.permutation(month_ids)
    return {
        RISK_RANKER: is_anomaly.loc[scores.index].tolist(),
        AMOUNT_RANKER: is_anomaly.loc[amount_order].tolist(),
        RANDOM_RANKER: is_anomaly.loc[random_order].tolist(),
    }


def _amount_order(month_frame: pd.DataFrame) -> np.ndarray:
    """Order a month's txn ids by amount descending, breaking ties by ascending txn_id."""
    txn_ids = month_frame.index.to_numpy()
    amounts = month_frame[AMOUNT_COLUMN].to_numpy()
    return txn_ids[np.lexsort((txn_ids, -amounts))]
