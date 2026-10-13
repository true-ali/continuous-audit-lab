import pandas as pd
import pytest

from continuous_audit_lab.data.schema import LABEL_COLUMN, TXN_ID_COLUMN
from continuous_audit_lab.data.synthetic import DORMANT_REACTIVATION, OFF_HOURS, generate
from continuous_audit_lab.tests_sql.params import DEFAULT_PARAMS
from continuous_audit_lab.tests_sql.runner import (
    FINDING_COLUMNS,
    connect,
    run_test,
    sql_text,
    used_params,
)

OFF_HOURS_RECALL_MINIMUM = 0.90
DORMANT_RECALL_MINIMUM = 2
SQL_TESTS = ("off_hours", "dormant_reactivation", "reference_integrity")


@pytest.fixture(scope="module")
def frame() -> pd.DataFrame:
    return generate()


@pytest.fixture(scope="module")
def clean_frame(frame: pd.DataFrame) -> pd.DataFrame:
    return frame[frame[LABEL_COLUMN] == ""]


def flagged_ids(findings: pd.DataFrame) -> set[int]:
    return {int(ids[0]) for ids in findings["txn_ids"] if len(ids)}


def labelled_ids(frame: pd.DataFrame, label: str) -> set[int]:
    return set(frame.loc[frame[LABEL_COLUMN] == label, TXN_ID_COLUMN].astype(int))


def test_off_hours_recall_on_synthetic(frame: pd.DataFrame) -> None:
    labelled = labelled_ids(frame, OFF_HOURS)
    recall = len(flagged_ids(run_test(connect(frame), "off_hours")) & labelled) / len(labelled)
    print(f"off_hours recall: {recall:.3f}")
    assert recall >= OFF_HOURS_RECALL_MINIMUM


def test_dormant_reactivation_recall_on_synthetic(frame: pd.DataFrame) -> None:
    labelled = labelled_ids(frame, DORMANT_REACTIVATION)
    findings = run_test(connect(frame), "dormant_reactivation")
    hits = len(flagged_ids(findings) & labelled)
    print(f"dormant_reactivation recall: {hits}/{len(labelled)}")
    assert hits >= DORMANT_RECALL_MINIMUM


def test_reference_integrity_runs_on_synthetic(frame: pd.DataFrame) -> None:
    findings = run_test(connect(frame), "reference_integrity")
    assert list(findings.columns) == FINDING_COLUMNS


@pytest.mark.parametrize("name", ["off_hours", "dormant_reactivation"])
def test_clean_data_has_no_findings(clean_frame: pd.DataFrame, name: str) -> None:
    assert run_test(connect(clean_frame), name).empty


def test_clean_data_has_no_reused_reference_findings(clean_frame: pd.DataFrame) -> None:
    findings = run_test(connect(clean_frame), "reference_integrity")
    assert not [detail for detail in findings["detail"] if detail.startswith("reused")]


@pytest.mark.parametrize("name", SQL_TESTS)
def test_output_has_exact_columns_and_types(frame: pd.DataFrame, name: str) -> None:
    con = connect(frame)
    result = con.execute(sql_text(name), used_params(name, DEFAULT_PARAMS))
    assert [column[0] for column in result.description] == FINDING_COLUMNS
    assert [column[1] for column in result.description] == [
        "VARCHAR",
        "DATE",
        "VARCHAR",
        "BIGINT[]",
        "DOUBLE",
        "VARCHAR",
    ]


def test_dormant_reactivation_ignores_later_rows() -> None:
    dates = pd.date_range("2020-01-15", periods=12, freq="MS") + pd.Timedelta(days=14)
    dates = list(dates) + [
        pd.Timestamp("2022-01-15 10:00"),
        pd.Timestamp("2022-02-15 10:00"),
        pd.Timestamp("2022-03-15 10:00"),
    ]
    amounts = [100.0 + index for index in range(12)] + [5000.0, 90.0, 20000.0]
    frame = pd.DataFrame(
        {
            "txn_date": dates,
            "supplier": "Supplier 001",
            "amount": amounts,
            "expense_type": "Travel",
            "department": "Finance",
            "reference": [f"INV-{index + 1:06d}" for index in range(len(dates))],
            TXN_ID_COLUMN: range(len(dates)),
        }
    )
    cutoff_id = 12
    full = run_test(connect(frame), "dormant_reactivation")
    truncated = run_test(connect(frame[frame[TXN_ID_COLUMN] <= cutoff_id]), "dormant_reactivation")
    before_cutoff = full[full["txn_ids"].map(lambda ids: ids[0] <= cutoff_id)]
    assert flagged_ids(before_cutoff) == {cutoff_id}
    pd.testing.assert_frame_equal(before_cutoff.reset_index(drop=True), truncated)
