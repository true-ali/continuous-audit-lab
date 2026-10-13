-- PURPOSE: find payments that were posted twice, either as exact copies or as near copies
--          (same supplier and amount, different or missing reference, close in time).
-- RED FLAG: the same amount paid to the same supplier twice within a short period, or with an
--           identical invoice reference posted more than once.
-- PARAMETERS: duplicate_window_days
-- KNOWN FALSE POSITIVES: recurring fixed-fee invoices (rent, licences, subscriptions) paid
--           twice within the window for legitimate reasons; a genuine repeat order of the same
--           price by the same department; reference numbers reused by a supplier across
--           unrelated invoices.
-- CLUSTERING: near clusters chain consecutive payments with the same supplier, amount, expense
--           type and department whose gap is at most the window. Requiring the same expense type
--           and department removes coincidental equal cent amounts in unrelated payments. A cluster made only of one non-empty reference is an exact cluster and
--           is reported once by the exact branch, not by the near branch.
-- FLAGGED VALUE: amount of every redundant copy (cluster size minus one times the amount).
WITH base AS (
    SELECT txn_id, txn_date, supplier, amount, expense_type, department,
           coalesce(reference, '') AS reference
    FROM payments
    WHERE amount > 0
),
exact_groups AS (
    SELECT supplier, amount, list(txn_id ORDER BY txn_date, txn_id) AS txn_ids,
           max(txn_date) AS last_date, count(*) AS copies, 'exact' AS kind
    FROM base
    WHERE reference <> ''
    GROUP BY supplier, amount, reference
    HAVING count(*) >= 2
),
chained AS (
    SELECT *,
           CASE
               WHEN lag(txn_date) OVER (PARTITION BY supplier, amount, expense_type, department ORDER BY txn_date, txn_id)
                    IS NULL
                    OR txn_date - lag(txn_date) OVER (
                        PARTITION BY supplier, amount, expense_type, department ORDER BY txn_date, txn_id
                    ) > to_days($duplicate_window_days)
               THEN 1 ELSE 0
           END AS starts_cluster
    FROM base
),
clustered AS (
    SELECT *,
           sum(starts_cluster) OVER (
               PARTITION BY supplier, amount, expense_type, department ORDER BY txn_date, txn_id
               ROWS UNBOUNDED PRECEDING
           ) AS cluster_no
    FROM chained
),
near_groups AS (
    SELECT supplier, amount, list(txn_id ORDER BY txn_date, txn_id) AS txn_ids,
           max(txn_date) AS last_date, count(*) AS copies, 'near' AS kind
    FROM clustered
    GROUP BY supplier, amount, expense_type, department, cluster_no
    HAVING count(*) >= 2
       AND NOT (min(reference) = max(reference) AND min(reference) <> '')
),
findings AS (
    SELECT * FROM exact_groups
    UNION ALL
    SELECT * FROM near_groups
)
SELECT
    'duplicate_payments' AS test,
    CAST(date_trunc('month', last_date) AS DATE) AS period,
    supplier,
    CAST(txn_ids AS BIGINT[]) AS txn_ids,
    CAST(amount * (copies - 1) AS DOUBLE) AS flagged_value,
    kind || ' duplicate: ' || CAST(copies AS VARCHAR) || ' payments of the same amount'
        || CASE WHEN kind = 'exact' THEN ' and reference'
                ELSE ' within ' || CAST($duplicate_window_days AS VARCHAR) || ' days' END
        AS detail
FROM findings
ORDER BY period, supplier
