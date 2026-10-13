"""Causal statistical scores for audit payments, computed one month at a time.

Every score for month t is built only from rows dated before the first day of month t (the
baseline), plus, for per-transaction history features, rows earlier than that transaction. A score
is therefore what an auditor could have seen at the time, with no peeking at later payments.

Feature set for the Isolation Forest, and why each feature is included:
- log_amount: payment size on a log scale, so lognormal spend profiles become roughly symmetric.
- days_since_last: gap since the same supplier was last paid. Payments after long dormancy and
  rapid repeat payments both stand out.
- count_30d: payments the same supplier received in the 30 days before this one. Bursts of small
  payments, a structuring pattern, show up here.
- weekday: 0 (Monday) to 6 (Sunday). Weekend postings are a classic off-hours red flag.
- z_expense: how far the amount sits from its expense category's normal range, as a robust
  z-score. It gives the forest category context that raw size alone does not.

z_expense and z_supplier use medians and the median absolute deviation (MAD) instead of means and
standard deviations. A few large payments would otherwise inflate the spread and hide themselves.
"""

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

from continuous_audit_lab.data.schema import TXN_ID_COLUMN

MIN_BASELINE_ROWS = 20
MIN_SUPPLIER_HISTORY = 5
DEFAULT_WINDOW_MONTHS = 12
MAD_SCALE = 0.6745
MAD_FLOOR = 1e-9
MIN_AMOUNT = 0.01
NO_HISTORY_DAYS = 365.0
SUPPLIER_WINDOW_DAYS = 30
IFOREST_ESTIMATORS = 100
MONTH_STEP = pd.DateOffset(months=1)

DATE_COLUMN = "txn_date"
SUPPLIER_COLUMN = "supplier"
EXPENSE_COLUMN = "expense_type"
AMOUNT_COLUMN = "amount"
LOG_AMOUNT = "log_amount"
DAYS_SINCE_LAST = "days_since_last"
COUNT_30D = "count_30d"
WEEKDAY = "weekday"
Z_EXPENSE = "z_expense"
Z_SUPPLIER = "z_supplier"
IFOREST = "iforest"
IFOREST_FEATURES = [LOG_AMOUNT, DAYS_SINCE_LAST, COUNT_30D, WEEKDAY, Z_EXPENSE]


def robust_z(values: pd.Series, median: float | pd.Series, mad: float | pd.Series) -> pd.Series:
    """Scale deviations from a median by the MAD so that 1.0 is roughly one standard deviation.

    The MAD is floored at a tiny epsilon so a zero-spread group yields finite scores instead of
    a division by zero.
    """
    return MAD_SCALE * (values - median) / np.maximum(mad, MAD_FLOOR)


def month_start(ts: pd.Timestamp | str) -> pd.Timestamp:
    """Return the first day of the calendar month containing ts."""
    return pd.Timestamp(ts).normalize().replace(day=1)


def transaction_features(payments: pd.DataFrame) -> pd.DataFrame:
    """Build per-transaction features that depend only on the transaction and earlier rows.

    Rows are ordered by posting date and then txn_id, so every history feature looks strictly
    backwards in that order. The result is indexed by txn_id.
    """
    ordered = payments.sort_values([DATE_COLUMN, TXN_ID_COLUMN], ignore_index=True)
    days_since, counts = _supplier_history(ordered)
    features = pd.DataFrame(
        {
            LOG_AMOUNT: _log_amount(ordered[AMOUNT_COLUMN]).to_numpy(),
            DAYS_SINCE_LAST: days_since,
            COUNT_30D: counts,
            WEEKDAY: ordered[DATE_COLUMN].dt.weekday.to_numpy(),
        },
        index=ordered[TXN_ID_COLUMN].to_numpy(),
    )
    features.index.name = TXN_ID_COLUMN
    return features


def stat_scores(
    payments: pd.DataFrame,
    month: pd.Timestamp,
    window_months: int = DEFAULT_WINDOW_MONTHS,
    seed: int = 0,
) -> pd.DataFrame:
    """Score every payment dated in the given month using only rows dated before that month.

    The expense baseline covers the previous window_months months, the supplier history covers all
    earlier months, and the Isolation Forest is fitted on the expense baseline alone.
    """
    start = month_start(month)
    end = start + MONTH_STEP
    baseline_start = start - pd.DateOffset(months=window_months)
    scoring_rows = _scoring_frame(payments)
    target = scoring_rows[(scoring_rows[DATE_COLUMN] >= start) & (scoring_rows[DATE_COLUMN] < end)]
    history = scoring_rows[scoring_rows[DATE_COLUMN] < start]
    baseline = history[history[DATE_COLUMN] >= baseline_start]
    z_expense = _keyed_robust_z(target, baseline, EXPENSE_COLUMN, MIN_BASELINE_ROWS)
    z_supplier = _keyed_robust_z(target, history, SUPPLIER_COLUMN, MIN_SUPPLIER_HISTORY)
    iforest = _iforest_scores(baseline, target, z_expense, seed)
    return pd.DataFrame(
        {Z_EXPENSE: z_expense, Z_SUPPLIER: z_supplier, IFOREST: iforest}, index=target.index
    )


def _log_amount(amount: pd.Series) -> pd.Series:
    """Return the natural log of amounts, clipping non-positive values to a small floor."""
    return np.log(amount.clip(lower=MIN_AMOUNT))


def _supplier_history(ordered: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Compute days since each supplier's previous payment and its count in the prior 30 days.

    Each supplier's rows are already in time order, so the previous row and the window start are
    found by position, and nothing from the row itself or later rows is used.
    """
    times = ordered[DATE_COLUMN].to_numpy(dtype="datetime64[ns]")
    days_since = np.full(len(ordered), NO_HISTORY_DAYS)
    counts = np.zeros(len(ordered), dtype=int)
    window = np.timedelta64(SUPPLIER_WINDOW_DAYS, "D")
    one_day = np.timedelta64(1, "D")
    for positions in ordered.groupby(SUPPLIER_COLUMN, sort=False).indices.values():
        supplier_times = times[positions]
        days_since[positions[1:]] = np.diff(supplier_times) / one_day
        window_starts = np.searchsorted(supplier_times, supplier_times - window, side="left")
        counts[positions] = np.arange(len(positions)) - window_starts
    return days_since, counts


def _scoring_frame(payments: pd.DataFrame) -> pd.DataFrame:
    """Join the grouping columns with the causal features, indexed by txn_id."""
    details = payments.set_index(TXN_ID_COLUMN)[[DATE_COLUMN, SUPPLIER_COLUMN, EXPENSE_COLUMN]]
    return details.join(transaction_features(payments))


def _group_stats(history: pd.DataFrame, key: str) -> pd.DataFrame:
    """Return median, MAD and row count of log amount for each group in the history."""
    medians = history.groupby(key)[LOG_AMOUNT].median()
    deviations = (history[LOG_AMOUNT] - history[key].map(medians)).abs()
    mads = deviations.groupby(history[key]).median()
    counts = history.groupby(key).size()
    return pd.DataFrame({"median": medians, "mad": mads, "count": counts}, dtype=float)


def _keyed_robust_z(
    target: pd.DataFrame, history: pd.DataFrame, key: str, min_rows: int
) -> pd.Series:
    """Score each target amount against its group's history, or 0.0 if the group is too small."""
    stats = _group_stats(history, key)
    groups = target[key]
    z = robust_z(target[LOG_AMOUNT], groups.map(stats["median"]), groups.map(stats["mad"]))
    enough_history = groups.map(stats["count"]).fillna(0) >= min_rows
    return z.where(enough_history, 0.0)


def _iforest_matrix(rows: pd.DataFrame, z_expense: pd.Series) -> np.ndarray:
    """Stack the Isolation Forest features in their fixed column order."""
    return rows.assign(**{Z_EXPENSE: z_expense})[IFOREST_FEATURES].to_numpy()


def _iforest_scores(
    baseline: pd.DataFrame, target: pd.DataFrame, z_expense: pd.Series, seed: int
) -> np.ndarray:
    """Fit an Isolation Forest on the baseline and return each target's anomaly percentile.

    The percentile is the share of baseline rows that look less anomalous than the target, so it
    is 0 for the most ordinary rows and close to 1 for the most unusual ones. Returns zeros when
    the baseline is too small to learn from.
    """
    if target.empty or len(baseline) < MIN_BASELINE_ROWS:
        return np.zeros(len(target))
    baseline_z = _keyed_robust_z(baseline, baseline, EXPENSE_COLUMN, MIN_BASELINE_ROWS)
    baseline_matrix = _iforest_matrix(baseline, baseline_z)
    model = IsolationForest(n_estimators=IFOREST_ESTIMATORS, random_state=seed)
    model.fit(baseline_matrix)
    baseline_anomaly = np.sort(-model.score_samples(baseline_matrix))
    target_anomaly = -model.score_samples(_iforest_matrix(target, z_expense))
    return np.searchsorted(baseline_anomaly, target_anomaly, side="left") / len(baseline_anomaly)
