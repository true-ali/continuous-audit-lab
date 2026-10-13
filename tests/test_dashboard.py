from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from continuous_audit_lab.dashboard import data
from continuous_audit_lab.data.schema import LABEL_COLUMN, TXN_ID_COLUMN
from continuous_audit_lab.data.synthetic import generate
from continuous_audit_lab.monitor.runner import run_months

APP_PATH = str(Path(__file__).resolve().parents[1] / "continuous_audit_lab/dashboard/app.py")
N_MONTHS = 18
PAYMENTS_PER_MONTH = 100
RAG_STATUSES = {"green", "amber", "red", "baseline", "n/a"}
TILE_COUNT = 7


@pytest.fixture(scope="module")
def run_files(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    tmp_path = tmp_path_factory.mktemp("dashboard")
    payments = generate(n_months=N_MONTHS, payments_per_month=PAYMENTS_PER_MONTH)
    csv_path = tmp_path / "payments.csv"
    payments.to_csv(csv_path, index=False)
    db_path = tmp_path / "audit.duckdb"
    run_months(payments, out_path=str(db_path), source="synthetic")
    return db_path, csv_path


def test_latest_tiles_cover_every_kri(run_files: tuple[Path, Path]) -> None:
    db_path, _ = run_files
    tiles = data.latest_tiles(path=str(db_path))
    assert len(tiles) == TILE_COUNT
    assert {tile["rag"] for tile in tiles} <= RAG_STATUSES


def test_load_alerts_sorted_with_parsed_lists(run_files: tuple[Path, Path]) -> None:
    db_path, _ = run_files
    alerts = data.load_alerts(path=str(db_path))
    assert not alerts.empty
    assert alerts["risk_score"].is_monotonic_decreasing
    first = alerts.iloc[0]
    assert isinstance(first["tests_fired"], list)
    assert isinstance(first["involved_txn_ids"], list)
    assert int(first["txn_id"]) in first["involved_txn_ids"]


def test_transactions_for_alert_contains_alert_txn(run_files: tuple[Path, Path]) -> None:
    db_path, csv_path = run_files
    alert = data.load_alerts(path=str(db_path)).iloc[0]
    transactions = data.transactions_for_alert(alert, str(csv_path))
    assert int(alert["txn_id"]) in transactions[TXN_ID_COLUMN].tolist()
    assert LABEL_COLUMN not in transactions.columns


def test_app_renders_without_exception(run_files: tuple[Path, Path], monkeypatch) -> None:
    db_path, _ = run_files
    monkeypatch.setenv(data.DB_ENV_VAR, str(db_path))
    app = AppTest.from_file(APP_PATH).run(timeout=60)
    assert not app.exception
