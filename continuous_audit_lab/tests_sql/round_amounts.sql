-- PURPOSE: find payments in round amounts (exact multiples of a round unit) and suppliers paid
--          the same amount repeatedly in one month, which often indicates estimates or
--          unsupported lump sums.
-- RED FLAG: a single payment that is an exact multiple of round_modulus and at least
--           round_min_amount; or three or more payments to one supplier of the same amount at
--           or above round_min_amount in one calendar month.
-- PARAMETERS: round_modulus, round_min_amount, round_min_count
-- KNOWN FALSE POSITIVES: genuine fixed-price contracts, deposits, rent and retainers that are
--           naturally round; repeated fixed monthly fees for the same supplier; the round check
--           does not look at the supplier's share of round payments, so a supplier with mostly
--           round prices is flagged on every payment.
-- CLUSTERING: round findings are one row per payment. Repeated findings are one row per
--           supplier, amount and month.
-- FLAGGED VALUE: the amount of the round payment, or the total of the repeated payments.
WITH round_payments AS (
    SELECT
        txn_id,
        txn_date,
        supplier,
        amount,
        CAST(date_trunc('month', txn_date) AS DATE) AS period
    FROM payments
    WHERE amount >= $round_min_amount
      AND amount % $round_modulus = 0
),
repeated_groups AS (
    SELECT
        supplier,
        amount,
        CAST(date_trunc('month', txn_date) AS DATE) AS period,
        list(txn_id ORDER BY txn_date, txn_id) AS txn_ids,
        count(*) AS n_payments,
        sum(amount) AS total
    FROM payments
    WHERE amount >= $round_min_amount
    GROUP BY supplier, amount, CAST(date_trunc('month', txn_date) AS DATE)
    HAVING count(*) >= $round_min_count
),
round_findings AS (
    SELECT
        period,
        supplier,
        [txn_id] AS txn_ids,
        amount AS flagged_value,
        'round amount: ' || CAST(amount AS VARCHAR) || ' is a multiple of '
            || CAST($round_modulus AS VARCHAR) AS detail
    FROM round_payments
),
repeated_findings AS (
    SELECT
        period,
        supplier,
        txn_ids,
        total AS flagged_value,
        'repeated: ' || CAST(n_payments AS VARCHAR) || ' payments of ' || CAST(amount AS VARCHAR)
            || ' in one month' AS detail
    FROM repeated_groups
)
SELECT
    'round_amounts' AS test,
    period,
    supplier,
    CAST(txn_ids AS BIGINT[]) AS txn_ids,
    CAST(flagged_value AS DOUBLE) AS flagged_value,
    detail
FROM (
    SELECT * FROM round_findings
    UNION ALL
    SELECT * FROM repeated_findings
)
ORDER BY period, supplier
