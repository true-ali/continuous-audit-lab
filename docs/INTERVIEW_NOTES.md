# Interview notes

Short answers grounded in this repository. Each one says what the code does and where it is, and
states the limit. Numbers are the measured synthetic results or code constants.

### 1. Why test the full population instead of sampling?

Because the data is already in the ledger and each test is a query. Every payment is checked, so there
is no sampling error to explain away. Sampling is still useful for the human review step, which is what
`SPOTCHECK.md` is for. The trade-off is that a full-population test fires on every rule violation,
including harmless ones, so the false-positive handling matters more than coverage.

### 2. Deterministic rules versus statistical scoring: why both?

The SQL tests (`tests_sql/*.sql`) are readable, explainable rules. An auditor can read the SQL and say
exactly why a payment was flagged, which is what evidence needs. The statistical scores
(`scoring/stats.py`) catch things no rule was written for, such as an amount that is unusual for its
category. The risk score combines both. The rules are the explanation; the statistics are the net for
what the rules miss.

### 3. Why median and MAD rather than mean and standard deviation?

A few large payments would inflate the mean and standard deviation and hide themselves. Median and MAD
are barely moved by outliers. `robust_z` in `scoring/stats.py` uses `0.6745 * (x - median) / MAD`, which
reads like a z-score. The same logic sets the KRI thresholds in `monitor/kri.py`, with the scaled MAD
(1.4826 x MAD) as the spread. The cost is that MAD can be 0 on flat data, so the code uses a floor.

### 4. Why an Isolation Forest?

It needs no labels and handles several features together without assuming a distribution. The features
(`IFOREST_FEATURES`: log amount, days since last payment, 30-day count, weekday, expense z-score) each have a
stated reason in the module docstring. The output is turned into an empirical percentile of the baseline,
so it is between 0 and 1 and comparable month to month. Honest limit: its reference percentile is computed
in-sample on the rows the forest was fitted on. I have not measured its separate contribution to the risk score, so I should not claim how much it adds.

### 5. How do you prevent leakage?

Four places, each with a test. Rule flags run on the ledger cut off at the end of the scored month. Statistical
baselines use only earlier rows. The dormancy window uses `ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING`.
KRI thresholds come from the first 12 months only and never move. Tests such as
`test_alerts_of_a_month_do_not_depend_on_later_months` and `test_thresholds_ignore_the_last_months_of_data`
check that changing later months does not change earlier results. A known gap: a standalone call of a SQL
test on the full file is not causal, because `threshold_splitting` uses whatever ledger it is given.

### 6. Precision at k versus recall at k. Why k = 50?

Precision@k is hits in the top k divided by k. Recall@k is hits divided by the month's total anomalies. With
a fixed review capacity, precision@k is the number that matters to a team, because it says how many of the
50 reviews are worth doing. Recall@k says how much of the problem those reviews reach. On the synthetic data,
the risk score gives precision@50 0.153 and recall@50 0.946, against 0.077 / 0.471 for amount alone and 0.021 /
0.117 for random. About 8.4 anomalies a month means precision@50 cannot exceed about 0.17, so the value is
read against that ceiling. k = 50 is the monitor's default alert budget (`DEFAULT_ALERT_K`), an assumption,
not a figure from the data.

### 7. Why transparent weights instead of a fitted model?

There are no labels in the real data, so a fitted model would have nothing honest to fit on. Fixed weights
are visible, each component's contribution is shown in `risk_reasons`, and an auditor can argue with a weight
rather than with a black box. The weights are judgement-based and the code says so. They are not calibrated
probabilities. With real labels, the same structure could be re-fitted and tested, which is the next step.

### 8. Why MAD for Benford rather than chi-square?

Chi-square's power grows with the sample size, so on a large ledger almost any real population is rejected.
MAD measures how far the first-digit distribution is from Benford regardless of n, so it can grade conformity
(`benford_first_digit.sql`). The bands (0.006, 0.012, 0.015) are rules of thumb. Honest caveat: I checked the
first three against a source, but the 0.015 "nonconforming" cut-off is not independently verified in this
repo, and I say so in the file header. Also, Benford does not fit bounded or assigned amounts, so the test
can mislead on those.

### 9. How do the KRIs and RAG thresholds work?

Seven monthly numbers (`monitor/kri.py`), each one a count, a value, a share or a dispersion. Thresholds come
from the first 12 months: the median, the scaled MAD with a per-KRI floor, amber at median + 2 scaled MAD and
red at median + 3 scaled MAD. Later months are labelled against those fixed limits. The limits are assumptions.
A real artefact showed up: on the synthetic baseline the clean median is 0, so the floor sets the limits, and
`flagged_value` is red in all 24 post-baseline months. I treat that as a calibration problem, not a finding.

### 10. What is the evidence trail, and what does re-performance check?

Each alert record holds the tests that fired, the involved transaction ids, the parameters and an evidence hash
over those fields and the canonical rows (`monitor/evidence.py`). Re-performance re-reads the rows from the
source, recomputes the hash and re-runs the fired tests on the ledger as it was at the alert's month. It catches
an edited source amount or a test that no longer flags the payment. The gap I would name first: an alert with no
row-level test is checked by hash only, and the weights and the score are not part of the hash.

### 11. What would change if you had real labels?

Three things. First, I could measure precision and recall on real data, not only on synthetic anomalies. Second,
I could fit or check the weights and the KRI limits against outcomes, and test them on a time-split holdout.
Third, I would re-estimate the expected band rates and thresholds for the split test on real data. The labels
would have to come from investigation outcomes, and the labelling process would need its own review, because
un-investigated payments are not known to be clean.

### 12. How do you handle false positives?

Each test header lists its known false positives, so a reviewer knows what a finding can mean. The clean-data
tests in `tests/test_sql_dup_split_round.py` and `tests/test_sql_hours_dormant_refs.py` set limits. Threshold
splitting still produces 4 findings on clean synthetic data, inside the test's limit of 5. The design response is
to rank rather than to flag as a conclusion, to show the reasons, and to send the top 50 to a person. The
spot-check is the step where false positives get recorded.

### 13. How reliable are the synthetic results?

Not very, for the reason the data gives. The anomalies follow the same shapes as the SQL tests, so recall is
optimistic. Only 3 dormant events exist, so that recall is not meaningful alone. Two injected types (structuring
bursts and pass-through pairs) have no dedicated test. The synthetic data is a development and testing tool that
shows the code works and that the tests fire where intended. It does not show how the method performs on real fraud.

### 14. Walk me through what happens when you run the pipeline.

`make data` downloads the council CSVs, checks a manifest, and runs `loader.prepare`, which maps headers, parses
amounts and dates, and writes `payments.csv` and a quality report. `python -m continuous_audit_lab.cli run --source real`
(or `make run` for synthetic) loads the ledger and runs `monitor/runner.py`. For each month, in order, it runs every
SQL test on the cut-off ledger, scores the month's payments with `score_month`, writes the top 50 positive scores as
alerts with evidence records, and computes the KRIs. It then sets thresholds from the first 12 months and labels every
month. Everything goes to `runs/audit.duckdb`, which is rebuilt on each run. `cli report` prints the latest KRIs.

### 15. How would you scale this?

The current design re-runs all SQL tests on a cut-off ledger for every month, which is simple and causal but repeats
work. For more data I would move to an incremental design: keep per-supplier state, run each test only on the new
month plus the rows it needs for history, and push the heavy grouping into a database. The statistical step already
works month by month. Before doing any of that I would measure runtime on the real file, which has not been done here.
Scaling is untested; the timing test covers only the statistics step.

### 16. What does the tool not do?

It does not establish compliance with any anti-money-laundering rule or audit standard. It does not check holidays,
name variants or related parties. It does not validate its limits on real outcomes. Its output is a ranked list of
payments to look at, and it must not be used as the basis for a decision about a supplier or a person without a
human review and a documented outcome.

### 17. Why DuckDB and SQL?

SQL is readable by auditors and can be reviewed line by line, which matters when the code has to be defended.
DuckDB runs analytic window functions on dataframes in process without a server. It also stores the run output in
a single file that can be queried later.
