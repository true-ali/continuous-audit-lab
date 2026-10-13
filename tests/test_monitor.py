import json
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from continuous_audit_lab.data.schema import TXN_ID_COLUMN
from continuous_audit_lab.data.synthetic import generate
from continuous_audit_lab.monitor.evidence import reperform
from continuous_audit_lab.monitor.runner import run_months
from continuous_audit_lab.scoring.rules import DATE_COLUMN

N_MONTHS = 20
PAYMENTS_PER_MONTH = 150
TRUNCATED_MONTHS = 14
LAST_ALTERED_MONTHS = 3
ALTERED_AMOUNT_FACTOR = 2.0
CREATED_AT = "created_at"
ALERT_RUN_COLUMNS = ("alert_id", "run_id")
JSON_ALERT_COLUMNS = ("tests_fired", "involved_txn_ids")
MONTH_STEP = pd.DateOffset(months=1)


@pytest.fixture(scope="module")
def payments() -> pd.DataFrame:
    return generate(n_months=N_MONTHS, payments_per_month=PAYMENTS_PER_MONTH)


@pytest.fixture(scope="module")
def base_run(payments: pd.DataFrame, tmp_path_factory: pytest.TempPathFactory) -> Path:
    out_path = tmp_path_factory.mktemp("base") / "audit.duckdb"
    run_months(payments, out_path=str(out_path), source="synthetic")
    return out_path


def _table(path: Path, name: str) -> pd.DataFrame:
    with duckdb.connect(str(path), read_only=True) as connection:
        return connection.execute(f"SELECT * FROM {name}").df()


def _alerts(path: Path) -> pd.DataFrame:
    alerts = _table(path, "alerts")
    for column in JSON_ALERT_COLUMNS:
        alerts[column] = alerts[column].map(json.loads)
    return alerts


def test_repeated_runs_give_identical_run_log(
    payments: pd.DataFrame, base_run: Path, tmp_path: Path
) -> None:
    second = tmp_path / "second.duckdb"
    run_months(payments, out_path=str(second), source="synthetic")
    first_log = _table(base_run, "run_log").drop(columns=[CREATED_AT])
    second_log = _table(second, "run_log").drop(columns=[CREATED_AT])
    pd.testing.assert_frame_equal(first_log, second_log)


def test_run_log_has_one_row_per_month_with_its_row_count(
    payments: pd.DataFrame, base_run: Path
) -> None:
    run_log = _table(base_run, "run_log")
    expected = payments[DATE_COLUMN].dt.to_period("M").value_counts().sort_index()
    assert len(run_log) == len(expected)
    assert run_log["rows"].tolist() == expected.tolist()


def test_every_alert_reperforms_and_detects_a_changed_amount(
    payments: pd.DataFrame, base_run: Path
) -> None:
    alerts = _alerts(base_run)
    assert not alerts.empty
    assert all(alerts["evidence_hash"].str.len() == 64)
    records = alerts.to_dict("records")
    assert all(reperform(record, payments) for record in records)
    record = next(record for record in records if record["tests_fired"])
    changed = payments.copy()
    changed.loc[changed[TXN_ID_COLUMN] == record["txn_id"], "amount"] *= ALTERED_AMOUNT_FACTOR
    assert not reperform(record, changed)


def test_thresholds_ignore_the_last_months_of_data(
    payments: pd.DataFrame, base_run: Path, tmp_path: Path
) -> None:
    last_start = payments[DATE_COLUMN].max().normalize().replace(day=1)
    last_start -= (LAST_ALTERED_MONTHS - 1) * MONTH_STEP
    altered = payments.copy()
    tail = altered[DATE_COLUMN] >= last_start
    altered.loc[tail, "amount"] *= ALTERED_AMOUNT_FACTOR
    altered_path = tmp_path / "altered.duckdb"
    run_months(altered, out_path=str(altered_path), source="synthetic")
    pd.testing.assert_frame_equal(
        _table(base_run, "kri_thresholds"), _table(altered_path, "kri_thresholds")
    )


def test_alerts_of_a_month_do_not_depend_on_later_months(
    payments: pd.DataFrame, base_run: Path, tmp_path: Path
) -> None:
    cutoff = payments[DATE_COLUMN].min().normalize().replace(day=1) + TRUNCATED_MONTHS * MONTH_STEP
    earlier = payments[payments[DATE_COLUMN] < cutoff]
    earlier_path = tmp_path / "earlier.duckdb"
    run_months(earlier, out_path=str(earlier_path), source="synthetic")
    full = _alerts(base_run).drop(columns=list(ALERT_RUN_COLUMNS))
    truncated = _alerts(earlier_path).drop(columns=list(ALERT_RUN_COLUMNS))
    full_before = full[pd.to_datetime(full["period"]) < cutoff].reset_index(drop=True)
    pd.testing.assert_frame_equal(full_before, truncated.reset_index(drop=True))
