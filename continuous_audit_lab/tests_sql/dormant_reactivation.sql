-- PURPOSE: Flags a payment to a supplier that had been dormant: the supplier's previous payment
--   was at least $dormant_months months earlier, the supplier had at least $dormant_min_history
--   earlier payments, and the amount exceeds the $dormant_history_quantile quantile of that
--   supplier's own earlier payments. Only earlier rows are used, so no future data leaks in.
--   One finding per reactivating payment.
-- RED FLAG: A long-silent supplier suddenly receiving an unusually large payment may be a
--   fictitious or hijacked vendor account (bank detail change, invoice fraud).
-- PARAMETERS: $dormant_months, $dormant_min_history, $dormant_history_quantile
-- KNOWN FALSE POSITIVES: Contracts that resume after a seasonal or project pause, annual or
--   milestone payments, and suppliers with few earlier payments in the window that were
--   dormant before the ledger starts (their first payment in the ledger has no history).
WITH ordered AS (
    SELECT
        txn_id,
        supplier,
        txn_date,
        amount,
        lag(txn_date) OVER (PARTITION BY supplier ORDER BY txn_date, txn_id) AS prev_date,
        count(*) OVER earlier AS earlier_count,
        quantile_cont(amount, $dormant_history_quantile) OVER earlier AS earlier_quantile
    FROM payments
    WINDOW earlier AS (
        PARTITION BY supplier
        ORDER BY txn_date, txn_id
        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
    )
)
SELECT
    'dormant_reactivation' AS test,
    CAST(date_trunc('month', txn_date) AS DATE) AS period,
    supplier,
    [CAST(txn_id AS BIGINT)] AS txn_ids,
    CAST(amount AS DOUBLE) AS flagged_value,
    printf(
        'resumed after %d days; amount %.2f above %.2f (earlier %.0f%% quantile of %d payments)',
        datediff('day', prev_date, txn_date),
        amount,
        earlier_quantile,
        100 * $dormant_history_quantile,
        earlier_count
    ) AS detail
FROM ordered
WHERE prev_date IS NOT NULL
    AND txn_date >= prev_date + to_months($dormant_months)
    AND earlier_count >= $dormant_min_history
    AND amount > earlier_quantile
ORDER BY txn_date, txn_id
