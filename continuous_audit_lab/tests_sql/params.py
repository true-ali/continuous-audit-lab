"""Documented audit-test parameters. All values are assumptions, not any organisation's real limits."""

PARAMETER_VERSION = "v1"

DEFAULT_PARAMS: dict[str, float | int | str] = {
    "approval_limit_1": 1000.0,
    "approval_limit_2": 5000.0,
    "approval_limit_3": 10000.0,
    "split_window_days": 7,
    "split_min_payments": 3,
    "split_lower_ratio": 0.80,
    "duplicate_window_days": 5,
    "round_modulus": 1000.0,
    "round_min_amount": 1000.0,
    "round_min_count": 3,
    "dormant_months": 9,
    "dormant_history_quantile": 0.95,
    "dormant_min_history": 5,
    "business_hour_start": 7,
    "business_hour_end": 19,
    "benford_min_rows": 300,
    "split_excess_ratio": 4.0,
}
