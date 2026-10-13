-- PURPOSE: Flags payments posted outside normal working time: on a Saturday or Sunday, or
--   before $business_hour_start or at/after $business_hour_end. One finding per transaction.
-- RED FLAG: Payments released when no approver or reviewer is normally working can indicate
--   unauthorised or concealed disbursements.
-- PARAMETERS: $business_hour_start, $business_hour_end
-- KNOWN FALSE POSITIVES: Public holidays are not modelled, so postings on a holiday weekday are
--   not flagged. Genuine out-of-hours work (month-end close, overseas suppliers, automated
--   batch runs) is flagged too. If every txn_date is date-only (time 00:00:00), the hour check
--   is skipped and only weekends are flagged.
WITH grain AS (
    SELECT coalesce(bool_and(txn_date = date_trunc('day', txn_date)), true) AS date_only
    FROM payments
),
checked AS (
    SELECT
        payments.*,
        isodow(txn_date) >= 6 AS is_weekend,
        NOT grain.date_only AND (
            hour(txn_date) < $business_hour_start OR hour(txn_date) >= $business_hour_end
        ) AS outside_hours
    FROM payments
    CROSS JOIN grain
)
SELECT
    'off_hours' AS test,
    CAST(date_trunc('month', txn_date) AS DATE) AS period,
    supplier,
    [CAST(txn_id AS BIGINT)] AS txn_ids,
    CAST(amount AS DOUBLE) AS flagged_value,
    CASE
        WHEN is_weekend AND outside_hours THEN 'weekend and outside business hours'
        WHEN is_weekend THEN 'weekend posting'
        ELSE 'outside business hours'
    END || ' at ' || strftime(txn_date, '%a %Y-%m-%d %H:%M') AS detail
FROM checked
WHERE is_weekend OR outside_hours
ORDER BY txn_date, txn_id
