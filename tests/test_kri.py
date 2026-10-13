import numpy as np
import pandas as pd
import pytest

from continuous_audit_lab.data.schema import LABEL_COLUMN
from continuous_audit_lab.data.synthetic import DUPLICATE_PAYMENT, generate
from continuous_audit_lab.monitor.kri import (
    KRI_NAMES,
    MIN_SCALE,
    build_thresholds,
    compute_month_kris,
    rag_status,
    rag_table,
)
from continuous_audit_lab.tests_sql.runner import run_all

SERIES = np.arange(1, 13, dtype=float)
MAD_TO_SIGMA = 1.4826


def _history(values: np.ndarray, periods: int) -> pd.DataFrame:
    """Build a KRI history where every KRI column carries the same values."""
    index = pd.date_range("2020-01-01", periods=periods, freq="MS")
    return pd.DataFrame({name: values for name in KRI_NAMES}, index=index)


@pytest.fixture(scope="module")
def ledger() -> pd.DataFrame:
    """Return the seeded synthetic ledger shared by the module's tests."""
    return generate()


def test_thresholds_match_hand_computed_values() -> None:
    """Median, scaled MAD and limits must match a hand calculation on 1..12."""
    thresholds = build_thresholds(_history(SERIES, 12)).set_index("kri")
    scaled = MAD_TO_SIGMA * 3.0
    row = thresholds.loc["dormant_reactivations"]
    assert row["median"] == pytest.approx(6.5)
    assert row["scaled_mad"] == pytest.approx(scaled)
    assert row["amber"] == pytest.approx(6.5 + 2 * scaled)
    assert row["red"] == pytest.approx(6.5 + 3 * scaled)


def test_scaled_mad_is_floored_for_flat_baseline() -> None:
    """A constant baseline has zero MAD, so the scale must fall back to the KRI floor."""
    thresholds = build_thresholds(_history(np.full(12, 0.01), 12)).set_index("kri")
    assert thresholds.loc["benford_mad", "scaled_mad"] == pytest.approx(MIN_SCALE["benford_mad"])


def test_rag_status_boundaries_and_missing_values() -> None:
    """Values at amber or red thresholds take that colour; NaN anywhere gives n/a."""
    assert rag_status(4.9, 5.0, 10.0) == "green"
    assert rag_status(5.0, 5.0, 10.0) == "amber"
    assert rag_status(10.0, 5.0, 10.0) == "red"
    assert rag_status(np.nan, 5.0, 10.0) == "n/a"
    assert rag_status(1.0, np.nan, 10.0) == "n/a"


def test_rag_table_labels_baseline_months() -> None:
    """The first baseline months are labelled baseline and later spikes are coloured red."""
    values = np.concatenate([SERIES, [1e6]])
    history = _history(values, 13)
    table = rag_table(history, build_thresholds(history))
    assert (table.iloc[:12] == "baseline").all().all()
    assert set(table.iloc[12]) == {"red"}


def test_thresholds_ignore_months_after_baseline() -> None:
    """Changing months after the baseline must leave the thresholds exactly unchanged."""
    history = _history(np.concatenate([SERIES, SERIES]), 24)
    altered = history.copy()
    altered.iloc[12:] = 1e6
    pd.testing.assert_frame_equal(build_thresholds(history), build_thresholds(altered))


def test_compute_month_kris_returns_finite_values_for_late_month(ledger: pd.DataFrame) -> None:
    """A late month yields all seven KRIs as finite floats."""
    month = pd.Timestamp("2020-10-01")
    upto = ledger[ledger["txn_date"] < month + pd.offsets.MonthBegin(1)]
    kris = compute_month_kris(upto, month, run_all(upto))
    assert set(kris) == set(KRI_NAMES)
    assert all(isinstance(value, float) and np.isfinite(value) for value in kris.values())


def test_duplicate_label_month_has_positive_duplicate_value(ledger: pd.DataFrame) -> None:
    """A month containing a labelled duplicate payment must report a positive duplicate value."""
    labelled = ledger.loc[ledger[LABEL_COLUMN] == DUPLICATE_PAYMENT, "txn_date"]
    month = labelled.dt.to_period("M").min().to_timestamp()
    upto = ledger[ledger["txn_date"] < month + pd.offsets.MonthBegin(1)]
    kris = compute_month_kris(upto, month, run_all(upto))
    assert kris["duplicate_value"] > 0
