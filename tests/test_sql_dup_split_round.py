import itertools

import pandas as pd
import pytest

from continuous_audit_lab.data.schema import LABEL_COLUMN, TXN_ID_COLUMN
from continuous_audit_lab.data.synthetic import (
    DUPLICATE_PAYMENT,
    ROUND_AMOUNT,
    THRESHOLD_SPLITTING,
    generate,
)
from continuous_audit_lab.tests_sql.runner import FINDING_COLUMNS, connect, run_test

RECALL_TARGET = 0.8
CLEAN_ROUND_FLAG_LIMIT = 0.01
CLEAN_SPLIT_FINDING_LIMIT = 5
CASES = [
    ("duplicate_payments", DUPLICATE_PAYMENT),
    ("threshold_splitting", THRESHOLD_SPLITTING),
    ("round_amounts", ROUND_AMOUNT),
]
NAMES = [name for name, _ in CASES]


@pytest.fixture(scope="module")
def frame() -> pd.DataFrame:
    return generate()


@pytest.fixture(scope="module")
def clean(frame: pd.DataFrame) -> pd.DataFrame:
    return frame[frame[LABEL_COLUMN] == ""].reset_index(drop=True)


def flagged_ids(findings: pd.DataFrame) -> set[int]:
    return {int(txn_id) for txn_id in itertools.chain.from_iterable(findings["txn_ids"])}


@pytest.mark.parametrize(("name", "label"), CASES)
def test_recall_on_labelled_anomalies(frame: pd.DataFrame, name: str, label: str) -> None:
    labelled = set(frame.loc[frame[LABEL_COLUMN] == label, TXN_ID_COLUMN].astype(int))
    findings = run_test(connect(frame), name)
    recall = len(labelled & flagged_ids(findings)) / len(labelled)
    assert recall >= RECALL_TARGET


def test_clean_data_has_no_duplicate_findings(clean: pd.DataFrame) -> None:
    assert run_test(connect(clean), "duplicate_payments").empty


def test_clean_data_threshold_false_positives_are_few(clean: pd.DataFrame) -> None:
    assert len(run_test(connect(clean), "threshold_splitting")) <= CLEAN_SPLIT_FINDING_LIMIT


def test_clean_data_round_flags_are_rare(clean: pd.DataFrame) -> None:
    findings = run_test(connect(clean), "round_amounts")
    assert len(flagged_ids(findings)) / len(clean) < CLEAN_ROUND_FLAG_LIMIT


@pytest.mark.parametrize("name", NAMES)
def test_output_has_exactly_the_finding_columns(frame: pd.DataFrame, name: str) -> None:
    assert list(run_test(connect(frame), name).columns) == FINDING_COLUMNS
