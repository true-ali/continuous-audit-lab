"""Seeded synthetic payment ledger with labelled audit anomalies, for development and tests."""

import itertools
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from continuous_audit_lab.data.schema import CANONICAL_COLUMNS, LABEL_COLUMN, TXN_ID_COLUMN

DUPLICATE_PAYMENT = "duplicate_payment"
THRESHOLD_SPLITTING = "threshold_splitting"
ROUND_AMOUNT = "round_amount"
OFF_HOURS = "off_hours"
DORMANT_REACTIVATION = "dormant_reactivation"
STRUCTURING_BURST = "structuring_burst"
PASS_THROUGH = "pass_through"

ANOMALY_TYPES = (
    DUPLICATE_PAYMENT,
    THRESHOLD_SPLITTING,
    ROUND_AMOUNT,
    OFF_HOURS,
    DORMANT_REACTIVATION,
    STRUCTURING_BURST,
    PASS_THROUGH,
)

N_SUPPLIERS = 60
EXPENSE_TYPES = (
    "Office Supplies",
    "Travel",
    "Software Licences",
    "Professional Services",
    "Utilities",
    "Facilities Maintenance",
    "Marketing",
    "Training",
)
DEPARTMENTS = ("Finance", "Operations", "Sales", "Marketing", "IT", "Human Resources")
ZIPF_EXPONENT = 1.1
LOG_MEAN_RANGE = (4.5, 7.5)
LOG_SIGMA_RANGE = (0.3, 0.8)
REFERENCE_FORMAT = "INV-{:06d}"

BASELINE_MONTHS = 12
DORMANT_SUPPLIER_INDICES = (9, 19, 29)
DORMANT_LAST_ACTIVE_MONTH = 7
REACTIVATION_EARLIEST_MONTH = 17
DORMANT_MULTIPLE_RANGE = (10.5, 13.0)

HOURS_PER_DAY = 24
MINUTES_PER_HOUR = 60
BUSINESS_START_HOUR = 8
BUSINESS_END_HOUR = 18
BUSINESS_START_MINUTE = BUSINESS_START_HOUR * MINUTES_PER_HOUR
BUSINESS_END_MINUTE = BUSINESS_END_HOUR * MINUTES_PER_HOUR
WEEKEND_FIRST_WEEKDAY = 5
NIGHT_HOURS = (22, 23, 0, 1, 2, 3, 4)
OFF_HOURS_WEEKEND_PROBABILITY = 0.5

DUPLICATE_COUNT = 30
ROUND_AMOUNT_COUNT = 30
OFF_HOURS_COUNT = 30
THRESHOLD_EVENT_COUNT = 8
STRUCTURING_EVENT_COUNT = 4
PASS_THROUGH_EVENT_COUNT = 15

DUPLICATE_WINDOW_HOURS = 5 * HOURS_PER_DAY
SPLIT_PAYMENT_COUNT = (3, 5)
SPLIT_SHARE_RANGE = (0.85, 0.99)
SPLIT_WINDOW_HOURS = 7 * HOURS_PER_DAY
BURST_PAYMENT_COUNT = (8, 15)
BURST_AMOUNT_RANGE = (100.0, 400.0)
BURST_WINDOW_HOURS = 3 * HOURS_PER_DAY
ROUND_UNIT = 1000.0
ROUND_MAX_MULTIPLE = 50
PASS_IN_AMOUNT_RANGE = (20000.0, 60000.0)
PASS_OUT_SHARE_RANGE = (0.95, 0.99)
PASS_WINDOW_HOURS = 3 * HOURS_PER_DAY


@dataclass(frozen=True)
class _Suppliers:
    names: tuple[str, ...]
    log_means: np.ndarray
    log_sigmas: np.ndarray
    expense_types: tuple[str, ...]
    zipf_weights: np.ndarray
    dormant: tuple[int, ...]
    injectable_ids: np.ndarray


@dataclass
class _Context:
    rng: np.random.Generator
    suppliers: _Suppliers
    month_starts: pd.DatetimeIndex
    references: Iterator[int]
    approval_limits: Sequence[float]

    def new_reference(self) -> str:
        return REFERENCE_FORMAT.format(next(self.references))


def generate(
    seed: int = 42,
    n_months: int = 36,
    start: str = "2018-04-01",
    payments_per_month: int = 400,
    approval_limits: tuple[float, ...] = (1000.0, 5000.0, 10000.0),
) -> pd.DataFrame:
    """Build a deterministic ledger of clean payments plus labelled anomalies.

    Anomalies are placed after the first baseline year so that detectors have history to compare
    against; the label column is empty for clean rows and txn_id is assigned after sorting.
    """
    if n_months <= REACTIVATION_EARLIEST_MONTH:
        raise ValueError(f"n_months must exceed {REACTIVATION_EARLIEST_MONTH}")
    rng = np.random.default_rng(seed)
    month_starts = pd.date_range(pd.Timestamp(start), periods=n_months, freq="MS")
    suppliers = _make_suppliers(rng)
    clean_count = n_months * payments_per_month
    ctx = _Context(rng, suppliers, month_starts, itertools.count(clean_count + 1), approval_limits)

    clean = _clean_payments(ctx, payments_per_month)
    clean["reference"] = [REFERENCE_FORMAT.format(number) for number in range(1, clean_count + 1)]

    anomalies = [
        *_duplicate_payments(ctx, clean, DUPLICATE_COUNT),
        *_threshold_splitting(ctx, THRESHOLD_EVENT_COUNT),
        *_round_amounts(ctx, ROUND_AMOUNT_COUNT),
        *_off_hours(ctx, OFF_HOURS_COUNT),
        *_dormant_reactivations(ctx),
        *_structuring_bursts(ctx, STRUCTURING_EVENT_COUNT),
        *_pass_throughs(ctx, PASS_THROUGH_EVENT_COUNT),
    ]
    frame = pd.concat([clean, pd.DataFrame(anomalies)], ignore_index=True)
    frame = frame.sort_values("txn_date", kind="stable", ignore_index=True)
    frame[TXN_ID_COLUMN] = np.arange(len(frame))
    return frame[[*CANONICAL_COLUMNS, TXN_ID_COLUMN, LABEL_COLUMN]]


def write_synthetic(out_path: str | Path = "data/processed/synthetic.csv", **kwargs) -> Path:
    """Generate the synthetic ledger and write it as CSV, creating parent folders as needed."""
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    generate(**kwargs).to_csv(path, index=False)
    return path


def _make_suppliers(rng: np.random.Generator) -> _Suppliers:
    """Draw supplier profiles; Zipf weights make a few suppliers dominate activity."""
    zipf = np.arange(1, N_SUPPLIERS + 1, dtype=float) ** -ZIPF_EXPONENT
    injectable = [index for index in range(N_SUPPLIERS) if index not in DORMANT_SUPPLIER_INDICES]
    return _Suppliers(
        names=tuple(f"Supplier {index + 1:03d}" for index in range(N_SUPPLIERS)),
        log_means=rng.uniform(*LOG_MEAN_RANGE, size=N_SUPPLIERS),
        log_sigmas=rng.uniform(*LOG_SIGMA_RANGE, size=N_SUPPLIERS),
        expense_types=tuple(
            EXPENSE_TYPES[index] for index in rng.integers(len(EXPENSE_TYPES), size=N_SUPPLIERS)
        ),
        zipf_weights=zipf / zipf.sum(),
        dormant=DORMANT_SUPPLIER_INDICES,
        injectable_ids=np.array(injectable),
    )


def _active_weights(suppliers: _Suppliers, month_index: int) -> np.ndarray:
    """Return payment probabilities per supplier, silencing dormant ones after their last month."""
    weights = suppliers.zipf_weights.copy()
    if month_index > DORMANT_LAST_ACTIVE_MONTH:
        weights[list(suppliers.dormant)] = 0.0
    return weights / weights.sum()


def _clean_payments(ctx: _Context, payments_per_month: int) -> pd.DataFrame:
    """Generate business-hours payments for every month, sorted by posting time."""
    rng = ctx.rng
    suppliers = ctx.suppliers
    frames = []
    for month_index, month_start in enumerate(ctx.month_starts):
        supplier_ids = rng.choice(
            N_SUPPLIERS, size=payments_per_month, p=_active_weights(suppliers, month_index)
        )
        frames.append(
            pd.DataFrame(
                {
                    "txn_date": _business_timestamps(rng, month_start, payments_per_month),
                    "supplier": [suppliers.names[index] for index in supplier_ids],
                    "amount": np.round(
                        rng.lognormal(
                            suppliers.log_means[supplier_ids], suppliers.log_sigmas[supplier_ids]
                        ),
                        2,
                    ),
                    "expense_type": [suppliers.expense_types[index] for index in supplier_ids],
                    "department": [
                        DEPARTMENTS[index]
                        for index in rng.integers(len(DEPARTMENTS), size=payments_per_month)
                    ],
                    LABEL_COLUMN: "",
                }
            )
        )
    return pd.concat(frames, ignore_index=True).sort_values(
        "txn_date", kind="stable", ignore_index=True
    )


def _month_days(month_start: pd.Timestamp) -> pd.DatetimeIndex:
    """Return every calendar day of the month that starts at month_start."""
    return pd.date_range(month_start, month_start + pd.offsets.MonthEnd(0), freq="D")


def _business_timestamps(
    rng: np.random.Generator, month_start: pd.Timestamp, count: int
) -> pd.DatetimeIndex:
    """Draw weekday timestamps between business start and end hours."""
    days = _month_days(month_start)
    weekdays = days[days.weekday < WEEKEND_FIRST_WEEKDAY]
    chosen_days = weekdays[rng.integers(len(weekdays), size=count)]
    minutes = rng.integers(BUSINESS_START_MINUTE, BUSINESS_END_MINUTE, size=count)
    return chosen_days + pd.to_timedelta(minutes, unit="min")


def _business_timestamp(rng: np.random.Generator, month_start: pd.Timestamp) -> pd.Timestamp:
    """Draw one weekday business-hours timestamp inside the month."""
    return _business_timestamps(rng, month_start, 1)[0]


def _random_month_start(ctx: _Context, first_index: int) -> pd.Timestamp:
    """Pick a month start at or after first_index so anomalies never touch the baseline year."""
    index = int(ctx.rng.integers(first_index, len(ctx.month_starts)))
    return ctx.month_starts[index]


def _random_offset(rng: np.random.Generator, low_hours: int, high_hours: int) -> pd.Timedelta:
    """Draw an inclusive whole-hour offset used to space related payments in time."""
    return pd.Timedelta(hours=int(rng.integers(low_hours, high_hours + 1)))


def _randint(rng: np.random.Generator, bounds: tuple[int, int]) -> int:
    """Draw an integer from an inclusive (low, high) pair."""
    low, high = bounds
    return int(rng.integers(low, high + 1))


def _random_supplier_id(ctx: _Context) -> int:
    """Pick a supplier for injected rows, never one of the dormant suppliers."""
    return int(ctx.rng.choice(ctx.suppliers.injectable_ids))


def _draw_amount(rng: np.random.Generator, suppliers: _Suppliers, supplier_id: int) -> float:
    """Draw a supplier-typical amount from its lognormal profile."""
    return float(rng.lognormal(suppliers.log_means[supplier_id], suppliers.log_sigmas[supplier_id]))


def _row(
    ctx: _Context,
    supplier_id: int,
    timestamp: pd.Timestamp,
    amount: float,
    label: str,
    reference: str,
) -> dict:
    """Build one ledger row for an injected payment."""
    return {
        "txn_date": timestamp,
        "supplier": ctx.suppliers.names[supplier_id],
        "amount": round(amount, 2),
        "expense_type": ctx.suppliers.expense_types[supplier_id],
        "department": DEPARTMENTS[int(ctx.rng.integers(len(DEPARTMENTS)))],
        "reference": reference,
        LABEL_COLUMN: label,
    }


def _duplicate_payments(ctx: _Context, clean: pd.DataFrame, count: int) -> list[dict]:
    """Copy existing payments with the same reference and amount a few days later."""
    candidates = clean[clean["txn_date"] >= ctx.month_starts[BASELINE_MONTHS]]
    rows = []
    for position in ctx.rng.choice(len(candidates), size=count, replace=False):
        source = candidates.iloc[position].to_dict()
        timestamp = source["txn_date"] + _random_offset(ctx.rng, 0, DUPLICATE_WINDOW_HOURS)
        rows.append({**source, "txn_date": timestamp, LABEL_COLUMN: DUPLICATE_PAYMENT})
    return rows


def _threshold_splitting(ctx: _Context, count: int) -> list[dict]:
    """Split one purchase into several payments just under an approval limit within a week."""
    rows = []
    for _ in range(count):
        supplier_id = _random_supplier_id(ctx)
        limit = float(ctx.approval_limits[int(ctx.rng.integers(len(ctx.approval_limits)))])
        base = _business_timestamp(ctx.rng, _random_month_start(ctx, BASELINE_MONTHS))
        for _ in range(_randint(ctx.rng, SPLIT_PAYMENT_COUNT)):
            share = float(ctx.rng.uniform(*SPLIT_SHARE_RANGE))
            timestamp = base + _random_offset(ctx.rng, 0, SPLIT_WINDOW_HOURS)
            rows.append(
                _row(
                    ctx,
                    supplier_id,
                    timestamp,
                    limit * share,
                    THRESHOLD_SPLITTING,
                    ctx.new_reference(),
                )
            )
    return rows


def _round_amounts(ctx: _Context, count: int) -> list[dict]:
    """Post payments whose amount is an exact multiple of the round unit."""
    rows = []
    for _ in range(count):
        supplier_id = _random_supplier_id(ctx)
        timestamp = _business_timestamp(ctx.rng, _random_month_start(ctx, BASELINE_MONTHS))
        amount = ROUND_UNIT * float(ctx.rng.integers(1, ROUND_MAX_MULTIPLE + 1))
        rows.append(_row(ctx, supplier_id, timestamp, amount, ROUND_AMOUNT, ctx.new_reference()))
    return rows


def _off_hours(ctx: _Context, count: int) -> list[dict]:
    """Post payments on weekends or in the late night and early morning."""
    rows = []
    for _ in range(count):
        supplier_id = _random_supplier_id(ctx)
        days = _month_days(_random_month_start(ctx, BASELINE_MONTHS))
        if ctx.rng.random() < OFF_HOURS_WEEKEND_PROBABILITY:
            weekend_days = days[days.weekday >= WEEKEND_FIRST_WEEKDAY]
            day = weekend_days[int(ctx.rng.integers(len(weekend_days)))]
            hour = int(ctx.rng.integers(BUSINESS_START_HOUR, BUSINESS_END_HOUR))
        else:
            weekdays = days[days.weekday < WEEKEND_FIRST_WEEKDAY]
            day = weekdays[int(ctx.rng.integers(len(weekdays)))]
            hour = int(ctx.rng.choice(NIGHT_HOURS))
        minute = int(ctx.rng.integers(MINUTES_PER_HOUR))
        timestamp = day + pd.Timedelta(hours=hour, minutes=minute)
        amount = _draw_amount(ctx.rng, ctx.suppliers, supplier_id)
        rows.append(_row(ctx, supplier_id, timestamp, amount, OFF_HOURS, ctx.new_reference()))
    return rows


def _dormant_reactivations(ctx: _Context) -> list[dict]:
    """Pay each dormant supplier one large amount after a gap of at least nine months."""
    rows = []
    for supplier_id in ctx.suppliers.dormant:
        month_start = _random_month_start(ctx, REACTIVATION_EARLIEST_MONTH)
        typical = float(np.exp(ctx.suppliers.log_means[supplier_id]))
        amount = typical * float(ctx.rng.uniform(*DORMANT_MULTIPLE_RANGE))
        timestamp = _business_timestamp(ctx.rng, month_start)
        rows.append(
            _row(ctx, supplier_id, timestamp, amount, DORMANT_REACTIVATION, ctx.new_reference())
        )
    return rows


def _structuring_bursts(ctx: _Context, count: int) -> list[dict]:
    """Post many small payments to one supplier within a few days."""
    rows = []
    for _ in range(count):
        supplier_id = _random_supplier_id(ctx)
        base = _business_timestamp(ctx.rng, _random_month_start(ctx, BASELINE_MONTHS))
        for _ in range(_randint(ctx.rng, BURST_PAYMENT_COUNT)):
            amount = float(ctx.rng.uniform(*BURST_AMOUNT_RANGE))
            timestamp = base + _random_offset(ctx.rng, 0, BURST_WINDOW_HOURS)
            rows.append(
                _row(ctx, supplier_id, timestamp, amount, STRUCTURING_BURST, ctx.new_reference())
            )
    return rows


def _pass_throughs(ctx: _Context, count: int) -> list[dict]:
    """Receive a large amount and forward nearly all of it to another supplier shortly after."""
    rows = []
    for _ in range(count):
        supplier_in, supplier_out = (
            int(index)
            for index in ctx.rng.choice(ctx.suppliers.injectable_ids, size=2, replace=False)
        )
        inflow_time = _business_timestamp(ctx.rng, _random_month_start(ctx, BASELINE_MONTHS))
        inflow = round(float(ctx.rng.uniform(*PASS_IN_AMOUNT_RANGE)), 2)
        outflow = inflow * float(ctx.rng.uniform(*PASS_OUT_SHARE_RANGE))
        outflow_time = inflow_time + _random_offset(ctx.rng, 1, PASS_WINDOW_HOURS)
        rows.append(_row(ctx, supplier_in, inflow_time, inflow, PASS_THROUGH, ctx.new_reference()))
        rows.append(
            _row(ctx, supplier_out, outflow_time, outflow, PASS_THROUGH, ctx.new_reference())
        )
    return rows
