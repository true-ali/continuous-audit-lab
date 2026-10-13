import numpy as np
import pandas as pd
import pytest

from continuous_audit_lab.data.synthetic import generate
from continuous_audit_lab.tests_sql.runner import connect, run_test

BENFORD = "benford_first_digit"
CONCENTRATION = "supplier_concentration"
MONTH_DATE = pd.Timestamp("2024-03-15")
SAMPLE_SIZE = 5000
SMALL_SAMPLE_SIZE = 100


def _ledger(amounts, suppliers=None, expense_types=None) -> pd.DataFrame:
    count = len(amounts)
    return pd.DataFrame(
        {
            "txn_date": MONTH_DATE,
            "supplier": suppliers or ["Supplier A"] * count,
            "amount": amounts,
            "expense_type": expense_types or ["Travel"] * count,
        }
    )


def _run(frame: pd.DataFrame, name: str) -> pd.DataFrame:
    return run_test(connect(frame), name)


def _overall(result: pd.DataFrame) -> pd.Series:
    return result[result["scope"] == "all"].iloc[0]


def test_lognormal_amounts_conform_to_benford() -> None:
    amounts = np.random.default_rng(7).lognormal(mean=7, sigma=2, size=SAMPLE_SIZE)
    assert _overall(_run(_ledger(amounts), BENFORD))["mad"] <= 0.015


def test_uniform_amounts_in_narrow_band_are_nonconforming() -> None:
    amounts = np.random.default_rng(7).uniform(5000, 5999, size=SAMPLE_SIZE)
    assert _overall(_run(_ledger(amounts), BENFORD))["conformity"] == "nonconforming"


def test_small_month_is_insufficient_data() -> None:
    amounts = np.random.default_rng(7).lognormal(mean=7, sigma=2, size=SMALL_SAMPLE_SIZE)
    assert _overall(_run(_ledger(amounts), BENFORD))["conformity"] == "insufficient_data"


def test_two_equal_suppliers_give_hhi_half() -> None:
    frame = _ledger([100.0, 100.0], suppliers=["Supplier A", "Supplier B"])
    row = _run(frame, CONCENTRATION).iloc[0]
    assert row["hhi"] == pytest.approx(0.5)
    assert row["top_share"] == pytest.approx(0.5)
    assert row["n_suppliers"] == 2


def test_single_supplier_gives_hhi_one() -> None:
    row = _run(_ledger([100.0, 250.0]), CONCENTRATION).iloc[0]
    assert row["hhi"] == pytest.approx(1.0)
    assert row["top_share"] == pytest.approx(1.0)


def test_both_tests_run_on_generated_ledger() -> None:
    frame = generate()
    assert not _run(frame, BENFORD).empty
    assert not _run(frame, CONCENTRATION).empty
