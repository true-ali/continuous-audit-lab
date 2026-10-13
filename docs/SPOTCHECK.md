# Spot-check of the top 20 real-data alerts

**Status: intentionally empty.** This template is for the owner to fill in by hand after running the
pipeline on the real council data. No row has been filled, and no verdict has been recorded by the
code or by anyone else. Nothing in this file is a finding.

## How to produce the list

From the repository root:

```
make data
python -m continuous_audit_lab.cli run --source real
```

`make data` needs network access and the `.venv` from `make install`. The run writes
`runs/audit.duckdb`. Then, to list the highest-scoring alerts across all months:

```
python -c "import duckdb; print(duckdb.connect('runs/audit.duckdb', read_only=True).sql('SELECT period, rank, txn_id, risk_score, tests_fired, amount FROM alerts ORDER BY risk_score DESC LIMIT 20').df().to_string())"
```

The same query works in the DuckDB command-line tool if it is installed.

Notes on the columns:

- `rank` in the `alerts` table is the rank **within its month** (1 is the riskiest that month), not
  across the whole run. Write the `period` in the note column so the row can be traced.
- `supplier` is not stored in `alerts`. Look it up by `txn_id` in `data/processed/payments.csv`, which
  is the prepared ledger the run read.
- `tests_fired` is a list of the row-level tests that flagged the payment. An empty list means the
  payment was flagged only by the statistical components.
- For each payment, `risk_reasons` and `evidence_hash` are in the same row, and
  `reperform` in `continuous_audit_lab/monitor/evidence.py` can re-check the evidence.

## Verdict scale

- **explainable**: the flagged reason makes sense for this payment once looked at against the source
  record (for example, a genuine repeat order or a contract with that price).
- **needs follow-up**: the payment should be checked with the source documents, the approver or the
  supplier before any conclusion is drawn.
- **unclear**: the source record is not enough to decide, and more information is needed.

Record the verdict in your own words. "explainable" is not a finding of no problem, and "needs
follow-up" is not a finding of a problem.

## Table

| rank | txn_id | supplier | amount | tests_fired | my verdict (explainable / needs follow-up / unclear) | note |
|---|---|---|---|---|---|---|
| 1 | | | | | | |
| 2 | | | | | | |
| 3 | | | | | | |
| 4 | | | | | | |
| 5 | | | | | | |
| 6 | | | | | | |
| 7 | | | | | | |
| 8 | | | | | | |
| 9 | | | | | | |
| 10 | | | | | | |
| 11 | | | | | | |
| 12 | | | | | | |
| 13 | | | | | | |
| 14 | | | | | | |
| 15 | | | | | | |
| 16 | | | | | | |
| 17 | | | | | | |
| 18 | | | | | | |
| 19 | | | | | | |
| 20 | | | | | | |

## Summary (owner to complete)

- Date of the run and `run_id`:
- Number of rows marked explainable / needs follow-up / unclear:
- Patterns seen in the false positives (which tests, which suppliers or expense types):
- Anything the rules missed that you noticed from the source records:
- Changes you would consider to the tests, parameters or weights, and why:
- Limits of this check (sample size, time spent per row, access to source documents):
