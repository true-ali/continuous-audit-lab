"""Combines row-level audit flags and statistical outliers into one ranked risk score per payment.

The score is a plain weighted sum. Every component is first scaled to the range 0 to 1, then
multiplied by its weight, so the weight is the largest amount that component can add:

- Rule flags (0 or 1) come from the row-level SQL tests. Duplicate payments, threshold splitting
  and dormant reactivation are the strongest signals and carry weight 3.0. Reference integrity
  carries 2.0. Round amounts and off-hours postings are weak on their own and carry 1.0.
- The statistical term 'z' uses the larger absolute z-score of expense type and supplier. It is
  capped at Z_CAP standard deviations and divided by Z_CAP, so a z-score of Z_CAP or more adds
  the full 0.5.
- The isolation-forest score is already between 0 and 1 and adds up to 1.0.

These weights are judgement-based assumptions chosen to reflect how strong each red flag is in
audit practice. They were not fitted to labelled data, so they are not a calibrated probability.
Rank 1 is the riskiest payment; ties are broken by txn_id so the ordering is deterministic.
"""

import numpy as np
import pandas as pd

from continuous_audit_lab.data.schema import TXN_ID_COLUMN
from continuous_audit_lab.scoring.rules import ROW_LEVEL_TESTS, rule_flags
from continuous_audit_lab.scoring.stats import DEFAULT_WINDOW_MONTHS, stat_scores

Z_CAP = 6.0
Z_COMPONENT = "z"
IFOREST_COMPONENT = "iforest"
IFOREST_COLUMN = "iforest"
Z_COLUMNS = ("z_expense", "z_supplier")
REASON_MIN_CONTRIBUTION = 0.5
SCORE_COLUMN = "risk_score"
RANK_COLUMN = "rank"
REASONS_COLUMN = "risk_reasons"
WEIGHTS: dict[str, float] = {
    "duplicate_payments": 3.0,
    "threshold_splitting": 3.0,
    "dormant_reactivation": 3.0,
    "reference_integrity": 2.0,
    "round_amounts": 1.0,
    "off_hours": 1.0,
    Z_COMPONENT: 0.5,
    IFOREST_COMPONENT: 1.0,
}


def score_month(
    payments: pd.DataFrame,
    month: pd.Timestamp,
    window_months: int = DEFAULT_WINDOW_MONTHS,
    seed: int = 0,
) -> pd.DataFrame:
    """Score every payment of the month and rank them from riskiest (1) to least risky.

    Rule flags are computed without look-ahead, and the statistical scores come from the same
    month. The result keeps each raw flag and score so that the ranking can be audited, and it
    lists in risk_reasons which components added enough weight to matter.
    """
    flags = rule_flags(payments, month)
    stats = stat_scores(payments, month, window_months=window_months, seed=seed)
    scores = flags.join(stats[[*Z_COLUMNS, IFOREST_COLUMN]], how="inner")
    contributions = _contributions(scores)
    scores[SCORE_COLUMN] = contributions.sum(axis=1)
    scores[RANK_COLUMN] = _ranks(scores)
    scores[REASONS_COLUMN] = [_reasons(row) for _, row in contributions.iterrows()]
    scores.index.name = TXN_ID_COLUMN
    return scores.sort_values(RANK_COLUMN)


def _contributions(scores: pd.DataFrame) -> pd.DataFrame:
    """Return each component's weighted contribution, with one column per WEIGHTS key."""
    components = scores[list(ROW_LEVEL_TESTS)].astype(float)
    components[Z_COMPONENT] = _z_component(scores)
    components[IFOREST_COMPONENT] = scores[IFOREST_COLUMN].clip(0.0, 1.0)
    return components[list(WEIGHTS)].mul(pd.Series(WEIGHTS), axis="columns")


def _z_component(scores: pd.DataFrame) -> pd.Series:
    """Scale the larger absolute z-score of expense type and supplier into the range 0 to 1."""
    largest = np.maximum(scores[Z_COLUMNS[0]].abs(), scores[Z_COLUMNS[1]].abs())
    return np.minimum(largest, Z_CAP) / Z_CAP


def _ranks(scores: pd.DataFrame) -> np.ndarray:
    """Rank rows by descending risk score, breaking ties by ascending txn_id."""
    txn_ids = scores.index.to_numpy()
    order = np.lexsort((txn_ids, -scores[SCORE_COLUMN].to_numpy()))
    ranks = np.empty(len(order), dtype=int)
    ranks[order] = np.arange(1, len(order) + 1)
    return ranks


def _reasons(contribution: pd.Series) -> str:
    """List, comma-separated, the components whose weighted contribution is material."""
    material = contribution[contribution >= REASON_MIN_CONTRIBUTION]
    return ", ".join(material.index)
