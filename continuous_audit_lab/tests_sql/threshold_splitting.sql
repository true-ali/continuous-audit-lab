-- PURPOSE: find purchases split into several payments that each sit just under an approval limit,
--          so that no single payment needs the higher approval.
-- RED FLAG: three or more payments to one supplier within a short window, each between
--           split_lower_ratio and 100% of the same approval limit, whose total reaches that limit,
--           and that are well above the supplier's usual rate of payments in that band.
-- PARAMETERS: approval_limit_1, approval_limit_2, approval_limit_3, split_lower_ratio,
--           split_min_payments, split_window_days, split_excess_ratio
-- EXPECTED RATE: the band share is the supplier's band payments divided by all its payments over
--           the whole ledger. A cluster must hold at least split_excess_ratio times the band
--           payments expected in its window. Without this check, high-volume suppliers whose
--           ordinary invoices fall in the band below a limit fire on clean data.
-- CLUSTERING: each band payment with no earlier band payment of the same supplier and limit
--           within the window becomes an anchor. The cluster is the anchor plus all later band
--           payments within the window, so each cluster is reported once.
-- KNOWN FALSE POSITIVES: instalments billed on purpose just below a limit (milestone billing);
--           a low-volume supplier with a genuine burst of invoices in the band; the band share
--           is measured on the full ledger, so a supplier that split heavily before is harder to
--           flag.
-- FLAGGED VALUE: total amount of the cluster.
WITH limits AS (
    SELECT unnest([$approval_limit_1, $approval_limit_2, $approval_limit_3]) AS limit_amount
),
banded AS (
    SELECT p.txn_id, p.txn_date, p.supplier, p.amount, l.limit_amount
    FROM payments p
    JOIN limits l
      ON p.amount < l.limit_amount
     AND p.amount >= $split_lower_ratio * l.limit_amount
),
anchors AS (
    SELECT b.*
    FROM banded b
    WHERE NOT EXISTS (
        SELECT 1
        FROM banded e
        WHERE e.supplier = b.supplier
          AND e.limit_amount = b.limit_amount
          AND e.txn_id <> b.txn_id
          AND (e.txn_date < b.txn_date OR (e.txn_date = b.txn_date AND e.txn_id < b.txn_id))
          AND e.txn_date >= b.txn_date - to_days($split_window_days)
    )
),
band_rates AS (
    SELECT t.supplier, coalesce(b.band_n, 0) * 1.0 / t.total_n AS band_rate
    FROM (SELECT supplier, count(*) AS total_n FROM payments GROUP BY supplier) t
    LEFT JOIN (
        SELECT supplier, count(DISTINCT txn_id) AS band_n FROM banded GROUP BY supplier
    ) b ON b.supplier = t.supplier
),
anchor_context AS (
    SELECT a.*,
           r.band_rate * (
               SELECT count(*)
               FROM payments q
               WHERE q.supplier = a.supplier
                 AND q.txn_date >= a.txn_date
                 AND q.txn_date <= a.txn_date + to_days($split_window_days)
           ) AS expected_band_payments
    FROM anchors a
    JOIN band_rates r ON r.supplier = a.supplier
),
clusters AS (
    SELECT a.supplier, a.limit_amount, a.expected_band_payments,
           list(m.txn_id ORDER BY m.txn_date, m.txn_id) AS txn_ids,
           count(*) AS n_payments,
           sum(m.amount) AS total,
           max(m.txn_date) AS last_date
    FROM anchor_context a
    JOIN banded m
      ON m.supplier = a.supplier
     AND m.limit_amount = a.limit_amount
     AND (m.txn_date > a.txn_date OR (m.txn_date = a.txn_date AND m.txn_id >= a.txn_id))
     AND m.txn_date <= a.txn_date + to_days($split_window_days)
    GROUP BY a.txn_id, a.supplier, a.limit_amount, a.expected_band_payments
    HAVING count(*) >= $split_min_payments
       AND sum(m.amount) >= a.limit_amount
       AND count(*) >= $split_excess_ratio * a.expected_band_payments
)
SELECT
    'threshold_splitting' AS test,
    CAST(date_trunc('month', last_date) AS DATE) AS period,
    supplier,
    CAST(txn_ids AS BIGINT[]) AS txn_ids,
    CAST(total AS DOUBLE) AS flagged_value,
    CAST(n_payments AS VARCHAR) || ' payments just below approval limit '
        || CAST(limit_amount AS VARCHAR) || ' within '
        || CAST($split_window_days AS VARCHAR) || ' days, total ' || CAST(total AS VARCHAR)
        AS detail
FROM clusters
ORDER BY period, supplier
