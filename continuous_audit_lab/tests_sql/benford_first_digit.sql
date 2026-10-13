-- PURPOSE: Aggregate Benford's Law test on the leading digit of payment amounts. For each month
--   and for each expense type, it compares the observed first-digit distribution (digits 1-9)
--   with the expected distribution log10(1 + 1/d) and reports the mean absolute deviation (MAD).
--   Output is one row per (period, scope) and is not a finding list.
-- RED FLAG: A MAD above the acceptable band (conformity 'marginal' or 'nonconforming') means the
--   amounts in that month or expense type do not follow the natural first-digit pattern. This
--   can indicate fabricated or manipulated amounts, or a pricing or approval-driven pattern
--   that warrants a sample. It is a prompt for investigation, not proof of fraud.
-- BANDS: Nigrini (2012), Benford's Law: Applications for Forensic Accounting, Auditing, and
--   Fraud Detection, first-digit MAD bands: <=0.006 close, <=0.012 acceptable, <=0.015 marginal,
--   above 0.015 nonconforming. These are rules of thumb with no analytic critical value. The
--   upper 0.015 nonconformity cut-off was not independently verified in this repository; it is
--   taken from the cited source as recalled and should be checked against it before reliance.
-- WHY MAD AND NOT CHI-SQUARE: A chi-square test almost always rejects Benford conformity on a
--   large ledger, because its power grows with n, so trivial deviations become statistically
--   significant. MAD measures the size of the deviation and does not scale with n, which is why
--   it is used here for conformity grading.
-- PARAMETERS: $benford_min_rows is the minimum number of positive amounts in a group before a
--   conformity grade is given; smaller groups report 'insufficient_data' and a NULL mad.
-- KNOWN FALSE POSITIVES / WHERE IT DOES NOT APPLY: Bounded amounts (for example all amounts
--   between 5,000 and 5,999), fixed or assigned fees and subscriptions, amounts capped at an
--   approval limit, amounts that are mostly round numbers or rounded to a unit, small samples
--   (see $benford_min_rows), and months or types dominated by one recurring supplier. Expected
--   distributions do not hold for these populations, so a nonconforming grade there is weak
--   evidence on its own.
WITH positive AS (
    SELECT CAST(date_trunc('month', txn_date) AS DATE) AS period,
           expense_type,
           CAST(floor(amount / power(10, floor(log10(amount)))) AS INTEGER) AS first_digit
    FROM payments
    WHERE amount > 0
),
grouped AS (
    SELECT 'all' AS scope, period, first_digit FROM positive
    UNION ALL
    SELECT expense_type AS scope, period, first_digit FROM positive
),
group_sizes AS (
    SELECT scope, period, COUNT(*) AS n
    FROM grouped
    GROUP BY scope, period
),
digit_counts AS (
    SELECT scope, period, first_digit, COUNT(*) AS c
    FROM grouped
    GROUP BY scope, period, first_digit
),
digit_deviation AS (
    SELECT g.scope,
           g.period,
           g.n,
           ABS(COALESCE(dc.c, 0) * 1.0 / g.n - LOG10(1 + 1.0 / d.digit)) AS deviation
    FROM group_sizes AS g
    CROSS JOIN (SELECT UNNEST(RANGE(1, 10)) AS digit) AS d
    LEFT JOIN digit_counts AS dc
           ON dc.scope = g.scope
          AND dc.period = g.period
          AND dc.first_digit = d.digit
),
metrics AS (
    SELECT scope, period, n, AVG(deviation) AS deviation_mad
    FROM digit_deviation
    GROUP BY scope, period, n
)
SELECT 'benford_first_digit' AS test,
       period,
       scope,
       n,
       CASE WHEN n >= $benford_min_rows THEN deviation_mad END AS mad,
       CASE
           WHEN n < $benford_min_rows THEN 'insufficient_data'
           WHEN deviation_mad <= 0.006 THEN 'close'
           WHEN deviation_mad <= 0.012 THEN 'acceptable'
           WHEN deviation_mad <= 0.015 THEN 'marginal'
           ELSE 'nonconforming'
       END AS conformity
FROM metrics
ORDER BY period, scope;
