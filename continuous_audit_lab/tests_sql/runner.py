"""Runs the readable .sql audit tests against a `payments` table in DuckDB.

Each .sql file reads the table `payments` (canonical columns plus txn_id) and uses `$name`
placeholders filled from DEFAULT_PARAMS. Row-level tests return one row per finding with the
columns in FINDING_COLUMNS; aggregate tests (benford, concentration) return per-period metrics.
"""

import re
from pathlib import Path

import duckdb
import pandas as pd

from continuous_audit_lab.tests_sql.params import DEFAULT_PARAMS

SQL_DIR = Path(__file__).parent
FINDING_COLUMNS = ["test", "period", "supplier", "txn_ids", "flagged_value", "detail"]
PLACEHOLDER = re.compile(r"\$([a-z_0-9]+)")


def list_tests() -> list[str]:
    """Return the names of all .sql test files (file name without extension)."""
    return sorted(path.stem for path in SQL_DIR.glob("*.sql"))


def connect(payments: pd.DataFrame) -> duckdb.DuckDBPyConnection:
    """Create an in-memory DuckDB connection exposing the dataframe as table `payments`."""
    con = duckdb.connect()
    con.register("payments", payments)
    return con


def sql_text(name: str) -> str:
    """Read the SQL text of one test."""
    return (SQL_DIR / f"{name}.sql").read_text()


def used_params(name: str, params: dict) -> dict:
    """Keep only the parameters the test actually references (DuckDB rejects unused ones)."""
    wanted = set(PLACEHOLDER.findall(sql_text(name)))
    return {key: value for key, value in params.items() if key in wanted}


def run_test(con: duckdb.DuckDBPyConnection, name: str, params: dict | None = None) -> pd.DataFrame:
    """Execute one named test and return its findings as a dataframe."""
    merged = {**DEFAULT_PARAMS, **(params or {})}
    return con.execute(sql_text(name), used_params(name, merged)).df()


def run_all(
    payments: pd.DataFrame, params: dict | None = None, names: list[str] | None = None
) -> dict[str, pd.DataFrame]:
    """Run every (or the named) test on the payments dataframe."""
    con = connect(payments)
    return {name: run_test(con, name, params) for name in (names or list_tests())}
