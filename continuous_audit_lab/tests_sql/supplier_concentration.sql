-- PURPOSE: Aggregate supplier concentration per (month, expense type). For each group it reports
--   total positive value, the number of suppliers paid, the largest supplier's share of value,
--   and the Herfindahl-Hirschman Index HHI = sum(share^2) on a 0-1 scale, where share is each
--   supplier's value divided by the group total. Output is one row per (period, expense_type).
-- RED FLAG: A high top_share or HHI means a small number of suppliers receives most of a
--   category's spend. Combined with a sudden change from the prior months, or a new dominant
--   supplier, this can indicate supplier steering, a shell or related-party vendor, or a
--   split of spend that bypasses competitive sourcing.
-- HHI READING: HHI ranges from 1/n (equal shares across n suppliers) to 1.0 (one supplier).
--   A category with only two or three suppliers naturally has a high HHI, so the number of
--   suppliers (n_suppliers) must be read alongside it. Compare a category with its own history
--   rather than with other categories.
-- PARAMETERS: None.
-- KNOWN FALSE POSITIVES / WHERE IT DOES NOT APPLY: Categories with genuinely few sources such as
--   utilities, software licences from a single vendor, or specialist professional services;
--   months with very few payments, where one invoice moves the shares; and seasonal or
--   project-driven spend where one large contractor is expected. Negative amounts (credits and
--   reversals) are excluded, so a reversal does not change a supplier's share.
WITH supplier_totals AS (
    SELECT CAST(date_trunc('month', txn_date) AS DATE) AS period,
           expense_type,
           supplier,
           SUM(amount) AS supplier_value
    FROM payments
    WHERE amount > 0
    GROUP BY period, expense_type, supplier
),
shares AS (
    SELECT period,
           expense_type,
           supplier_value,
           supplier_value / SUM(supplier_value) OVER (PARTITION BY period, expense_type) AS share
    FROM supplier_totals
)
SELECT 'supplier_concentration' AS test,
       period,
       expense_type,
       SUM(supplier_value) AS total_value,
       COUNT(*) AS n_suppliers,
       MAX(share) AS top_share,
       SUM(share * share) AS hhi
FROM shares
GROUP BY period, expense_type
ORDER BY period, expense_type;
