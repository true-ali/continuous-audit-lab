"""Canonical schema shared by the loader, the synthetic generator and all audit tests."""

CANONICAL_COLUMNS = ["txn_date", "supplier", "amount", "expense_type", "department", "reference"]
LABEL_COLUMN = "anomaly_type"  # synthetic data only; "" for clean rows
TXN_ID_COLUMN = "txn_id"
