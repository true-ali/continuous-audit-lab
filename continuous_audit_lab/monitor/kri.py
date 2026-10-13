"""Monthly key risk indicators (KRIs) built from audit test findings, with leakage-safe thresholds.

The seven KRIs cover the main families of audit signal: how much activity is flagged (share and
value), duplicate payments, payments structured just under approval limits, the shape of the amount
distribution (Benford), how concentrated spend is among suppliers, and suppliers that resume after
long silence. Each is a single number per month so that a reviewer can watch it over time.

Thresholds use a robust baseline. The median is the typical level of a KRI and the median absolute
deviation (MAD) measures its typical spread; both are barely moved by one unusual month, unlike
mean and standard deviation. For normally distributed data, 1.4826 times the MAD estimates the
standard deviation, so the scaled MAD is comparable to a familiar z-score. Amber is set at two
scaled MADs above the median and red at three, a common control-chart convention. A floor per KRI
keeps a flat baseline from turning every small positive value red.

These cut-offs are assumptions, not validated limits. They are calibrated only on the first
baseline months (12 by default) and should be reviewed against real investigation outcomes.

Leakage safety: build_thresholds reads only the first baseline_months rows. Later months are scored
against those fixed thresholds and never change them, so the monitor cannot learn from the period
it is judging.
"""

import numpy as np
import pandas as pd

KRI_NAMES = (
    "flagged_share",
    "flagged_value",
    "duplicate_value",
    "near_threshold_clusters",
    "benford_mad",
    "supplier_hhi",
    "dormant_reactivations",
)
ROW_LEVEL_TESTS = (
    "duplicate_payments",
    "threshold_splitting",
    "round_amounts",
    "off_hours",
    "dormant_reactivation",
    "reference_integrity",
)
BENFORD_TEST = "benford_first_digit"
BENFORD_SCOPE = "all"
CONCENTRATION_TEST = "supplier_concentration"
DUPLICATE_TEST = "duplicate_payments"
SPLITTING_TEST = "threshold_splitting"
DORMANT_TEST = "dormant_reactivation"
PERIOD_COLUMN = "period"
TXN_ID_COLUMN = "txn_id"
AMOUNT_COLUMN = "amount"
FLAGGED_VALUE_COLUMN = "flagged_value"
TXN_IDS_COLUMN = "txn_ids"
SCOPE_COLUMN = "scope"
MAD_COLUMN = "mad"
HHI_COLUMN = "hhi"
WEIGHT_COLUMN = "total_value"
MONTH_FREQUENCY = "M"
MAD_TO_SIGMA = 1.4826
AMBER_MULTIPLE = 2.0
RED_MULTIPLE = 3.0
DEFAULT_BASELINE_MONTHS = 12
THRESHOLD_COLUMNS = ("kri", "median", "scaled_mad", "amber", "red")
STATUS_GREEN = "green"
STATUS_AMBER = "amber"
STATUS_RED = "red"
STATUS_NOT_AVAILABLE = "n/a"
STATUS_BASELINE = "baseline"
MIN_SCALE: dict[str, float] = {
    "flagged_share": 0.01,
    "flagged_value": 1000.0,
    "duplicate_value": 1000.0,
    "near_threshold_clusters": 1.0,
    "benford_mad": 0.002,
    "supplier_hhi": 0.01,
    "dormant_reactivations": 1.0,
}


def compute_month_kris(
    payments: pd.DataFrame, month: pd.Timestamp, findings: dict[str, pd.DataFrame]
) -> dict[str, float]:
    """Compute the seven KRIs for one calendar month from the payments and audit findings.

    Only findings whose period falls in the month are used, so the result is the month's own
    signal. Missing findings give zeros, and an absent Benford grade gives NaN.
    """
    period = _to_period(month)
    month_payment_count = int(_payment_periods(payments).eq(period).sum())
    flagged_ids = _flagged_txn_ids(findings, period)
    flagged_value = _amount_of(payments, flagged_ids)
    return {
        "flagged_share": _ratio(len(flagged_ids), month_payment_count),
        "flagged_value": flagged_value,
        "duplicate_value": _sum_flagged_value(findings, DUPLICATE_TEST, period),
        "near_threshold_clusters": float(_count_findings(findings, SPLITTING_TEST, period)),
        "benford_mad": _benford_mad(findings, period),
        "supplier_hhi": _value_weighted_hhi(findings, period),
        "dormant_reactivations": float(_count_findings(findings, DORMANT_TEST, period)),
    }


def build_thresholds(
    kri_history: pd.DataFrame, baseline_months: int = DEFAULT_BASELINE_MONTHS
) -> pd.DataFrame:
    """Derive median and amber/red limits for each KRI from the first baseline months only.

    Uses only the earliest baseline_months rows so that later months cannot move the thresholds.
    Returns one row per KRI with the columns in THRESHOLD_COLUMNS.
    """
    baseline = kri_history.sort_index().iloc[:baseline_months]
    rows = [_threshold_row(name, baseline[name]) for name in KRI_NAMES]
    return pd.DataFrame(rows, columns=list(THRESHOLD_COLUMNS))


def rag_status(value: float, amber: float, red: float) -> str:
    """Classify one KRI value as green, amber or red; any missing input gives n/a."""
    if any(pd.isna(number) for number in (value, amber, red)):
        return STATUS_NOT_AVAILABLE
    if value >= red:
        return STATUS_RED
    if value >= amber:
        return STATUS_AMBER
    return STATUS_GREEN


def rag_table(
    kri_history: pd.DataFrame,
    thresholds: pd.DataFrame,
    baseline_months: int = DEFAULT_BASELINE_MONTHS,
) -> pd.DataFrame:
    """Label every KRI month green, amber or red, marking the baseline months as 'baseline'.

    The baseline months are the ones that were used to set the thresholds, so their status is not
    a judgement and is labelled separately.
    """
    ordered = kri_history.sort_index()
    limits = thresholds.set_index("kri")
    statuses = pd.DataFrame(
        {
            name: [
                rag_status(value, limits.at[name, "amber"], limits.at[name, "red"])
                for value in ordered[name]
            ]
            for name in KRI_NAMES
        },
        index=ordered.index,
    )
    statuses.iloc[:baseline_months] = STATUS_BASELINE
    return statuses


def _to_period(value: pd.Timestamp) -> pd.Period:
    """Convert a timestamp to its calendar-month period."""
    return pd.Timestamp(value).to_period(MONTH_FREQUENCY)


def _payment_periods(payments: pd.DataFrame) -> pd.Series:
    """Return the calendar-month period of every payment's transaction date."""
    return pd.to_datetime(payments["txn_date"]).dt.to_period(MONTH_FREQUENCY)


def _findings_for(findings: dict[str, pd.DataFrame], test: str, period: pd.Period) -> pd.DataFrame:
    """Return the rows of one test's findings that fall in the given month."""
    frame = findings.get(test, pd.DataFrame())
    if frame.empty:
        return frame
    periods = pd.to_datetime(frame[PERIOD_COLUMN]).dt.to_period(MONTH_FREQUENCY)
    return frame[periods == period]


def _flagged_txn_ids(findings: dict[str, pd.DataFrame], period: pd.Period) -> set[int]:
    """Collect the distinct transaction ids flagged by any row-level test in the month."""
    flagged: set[int] = set()
    for test in ROW_LEVEL_TESTS:
        frame = _findings_for(findings, test, period)
        if frame.empty:
            continue
        for txn_list in frame[TXN_IDS_COLUMN]:
            flagged.update(int(txn_id) for txn_id in txn_list)
    return flagged


def _amount_of(payments: pd.DataFrame, txn_ids: set[int]) -> float:
    """Sum the amounts of the given transactions, which is zero when there are none."""
    if not txn_ids:
        return 0.0
    return float(payments.loc[payments[TXN_ID_COLUMN].isin(txn_ids), AMOUNT_COLUMN].sum())


def _ratio(numerator: int, denominator: int) -> float:
    """Divide two counts, returning zero when the denominator is zero."""
    if denominator == 0:
        return 0.0
    return numerator / denominator


def _count_findings(findings: dict[str, pd.DataFrame], test: str, period: pd.Period) -> int:
    """Count one test's findings in the month."""
    return len(_findings_for(findings, test, period))


def _sum_flagged_value(findings: dict[str, pd.DataFrame], test: str, period: pd.Period) -> float:
    """Sum the flagged value of one test's findings in the month."""
    frame = _findings_for(findings, test, period)
    if frame.empty:
        return 0.0
    return float(frame[FLAGGED_VALUE_COLUMN].sum())


def _benford_mad(findings: dict[str, pd.DataFrame], period: pd.Period) -> float:
    """Return the first-digit MAD for all payments in the month, NaN when no grade exists."""
    frame = _findings_for(findings, BENFORD_TEST, period)
    overall = frame[frame[SCOPE_COLUMN] == BENFORD_SCOPE]
    if overall.empty:
        return float("nan")
    return float(overall[MAD_COLUMN].iloc[0])


def _value_weighted_hhi(findings: dict[str, pd.DataFrame], period: pd.Period) -> float:
    """Average the per-expense-type HHI in the month, weighted by each type's paid value."""
    frame = _findings_for(findings, CONCENTRATION_TEST, period)
    if frame.empty:
        return 0.0
    return float(np.average(frame[HHI_COLUMN], weights=frame[WEIGHT_COLUMN]))


def _threshold_row(name: str, series: pd.Series) -> dict[str, float | str]:
    """Build the median, scaled MAD and limits for one KRI from its baseline values."""
    values = series.dropna().to_numpy(dtype=float)
    if values.size == 0:
        return {"kri": name, "median": np.nan, "scaled_mad": np.nan, "amber": np.nan, "red": np.nan}
    median = float(np.median(values))
    scaled = _scaled_mad(values, median, MIN_SCALE[name])
    return {
        "kri": name,
        "median": median,
        "scaled_mad": scaled,
        "amber": median + AMBER_MULTIPLE * scaled,
        "red": median + RED_MULTIPLE * scaled,
    }


def _scaled_mad(values: np.ndarray, median: float, floor: float) -> float:
    """Return 1.4826 times the MAD around the median, never below the per-KRI floor."""
    mad = float(np.median(np.abs(values - median)))
    return max(MAD_TO_SIGMA * mad, floor)
