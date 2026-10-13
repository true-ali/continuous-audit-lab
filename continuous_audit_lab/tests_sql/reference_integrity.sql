-- PURPOSE: Two checks on the reference field. (1) Reused references: a non-empty reference
--   used by different suppliers, or by one supplier with different amounts; one finding per
--   reference. (2) Sequence gaps: for references of the form PREFIX-NNNNNN, missing numbers
--   between consecutive sequence numbers of the same prefix; one finding per prefix.
-- RED FLAG: A reused reference can hide a second payment under an existing invoice; missing
--   sequence numbers can indicate voided, deleted or unrecorded invoices.
-- PARAMETERS: none
-- KNOWN FALSE POSITIVES: Exact repeats (same supplier and amount) are not reported here and are
--   left to the duplicate payment test. Sequence gaps are only meaningful if the reference is a
--   single sequential series; references from several numbering systems sharing one prefix,
--   or cancelled numbers never posted to the ledger, will show as gaps.
WITH refs AS (
    SELECT txn_id, supplier, txn_date, amount, reference
    FROM payments
    WHERE reference IS NOT NULL AND reference <> ''
),
reused AS (
    SELECT
        reference,
        CASE WHEN count(DISTINCT supplier) = 1 THEN min(supplier) END AS supplier,
        list(CAST(txn_id AS BIGINT) ORDER BY txn_id) AS txn_ids,
        sum(amount) AS flagged_value,
        max(txn_date) AS latest,
        count(*) AS n_payments,
        count(DISTINCT supplier) AS n_suppliers,
        count(DISTINCT amount) AS n_amounts
    FROM refs
    GROUP BY reference
    HAVING count(DISTINCT supplier) > 1 OR count(DISTINCT amount) > 1
),
reused_findings AS (
    SELECT
        'reference_integrity' AS test,
        CAST(date_trunc('month', latest) AS DATE) AS period,
        supplier,
        txn_ids,
        CAST(flagged_value AS DOUBLE) AS flagged_value,
        printf(
            'reused reference %s: %d payments, %d suppliers, %d distinct amounts',
            reference, n_payments, n_suppliers, n_amounts
        ) AS detail
    FROM reused
),
numbered AS (
    SELECT
        regexp_extract(reference, '^(.*)-([0-9]{6})$', 1) AS prefix,
        CAST(regexp_extract(reference, '^(.*)-([0-9]{6})$', 2) AS BIGINT) AS seq,
        txn_date
    FROM refs
    WHERE regexp_matches(reference, '^.*-[0-9]{6}$')
),
series AS (
    SELECT DISTINCT prefix, seq FROM numbered
),
steps AS (
    SELECT prefix, seq - prev_seq - 1 AS missing
    FROM (
        SELECT prefix, seq, lag(seq) OVER (PARTITION BY prefix ORDER BY seq) AS prev_seq
        FROM series
    )
    WHERE prev_seq IS NOT NULL AND seq - prev_seq > 1
),
gap_summary AS (
    SELECT prefix, CAST(sum(missing) AS BIGINT) AS missing_total, CAST(count(*) AS BIGINT) AS gap_count
    FROM steps
    GROUP BY prefix
),
gap_findings AS (
    SELECT
        'reference_integrity' AS test,
        CAST(date_trunc('month', max(numbered.txn_date)) AS DATE) AS period,
        CAST(NULL AS VARCHAR) AS supplier,
        CAST([] AS BIGINT[]) AS txn_ids,
        CAST(0 AS DOUBLE) AS flagged_value,
        printf(
            '%s sequence: %d missing numbers in %d gaps',
            gap_summary.prefix, gap_summary.missing_total, gap_summary.gap_count
        ) AS detail
    FROM gap_summary
    JOIN numbered ON numbered.prefix = gap_summary.prefix
    GROUP BY gap_summary.prefix, gap_summary.missing_total, gap_summary.gap_count
)
SELECT * FROM reused_findings
UNION ALL
SELECT * FROM gap_findings
ORDER BY period, detail
