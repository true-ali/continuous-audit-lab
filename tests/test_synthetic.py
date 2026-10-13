import time

import pandas as pd
import pytest

from continuous_audit_lab.data.schema import CANONICAL_COLUMNS, LABEL_COLUMN, TXN_ID_COLUMN
from continuous_audit_lab.data.synthetic import ANOMALY_TYPES, generate

BASELINE_END = pd.Timestamp("2018-04-01") + pd.DateOffset(months=12)
RUNTIME_LIMIT_SECONDS = 5.0


@pytest.fixture(scope="module")
def frame() -> pd.DataFrame:
    return generate()


def test_same_seed_produces_identical_frame(frame: pd.DataFrame) -> None:
    pd.testing.assert_frame_equal(frame, generate(seed=42))


def test_different_seed_produces_different_frame(frame: pd.DataFrame) -> None:
    assert not frame.equals(generate(seed=43))


def test_columns_include_canonical_id_and_label(frame: pd.DataFrame) -> None:
    assert list(frame.columns) == [*CANONICAL_COLUMNS, TXN_ID_COLUMN, LABEL_COLUMN]


def test_txn_id_is_sequential_from_zero(frame: pd.DataFrame) -> None:
    assert frame[TXN_ID_COLUMN].tolist() == list(range(len(frame)))


@pytest.mark.parametrize("anomaly_type", ANOMALY_TYPES)
def test_every_anomaly_type_is_injected(frame: pd.DataFrame, anomaly_type: str) -> None:
    assert (frame[LABEL_COLUMN] == anomaly_type).sum() > 0


def test_no_anomalies_in_baseline_year(frame: pd.DataFrame) -> None:
    anomalies = frame[frame[LABEL_COLUMN] != ""]
    assert (anomalies["txn_date"] >= BASELINE_END).all()


def test_clean_references_are_unique(frame: pd.DataFrame) -> None:
    clean = frame[frame[LABEL_COLUMN] == ""]
    assert clean["reference"].is_unique


def test_default_generation_is_fast() -> None:
    started = time.perf_counter()
    generate()
    assert time.perf_counter() - started < RUNTIME_LIMIT_SECONDS
