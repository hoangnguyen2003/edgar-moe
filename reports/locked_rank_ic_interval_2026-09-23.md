# Conditional rank-IC intervals for the frozen v1 study

Date: 2026-09-23. This is a **derived companion analysis**, not a revision of
the [frozen research report](authenticated_research_report.md), a new model
selection, or a new locked-test opening.

| Cohort | Frozen rank IC | 95% block-bootstrap interval | Events |
| --- | ---: | ---: | ---: |
| 2023–2024 development (validation-count-weighted mean of two fold ICs) | 0.0632 | [0.0173, 0.1181] | 2,305 |
| 2025–2026 locked test (single pooled IC) | 0.0316 | [-0.0114, 0.0702] | 1,794 |

The locked-test interval includes zero. It does **not** establish positive
out-of-sample ranking skill, and the frozen portfolio's negative after-cost
result remains the economic conclusion. The development point estimate was
used in model selection and its interval is **conditional on the selected
champion**; it does not correct for searching 33 candidates. The two rows use
the frozen study's different aggregation definitions: the development figure
is a validation-count-weighted mean of within-year Spearman correlations,
whereas the locked figure is one Spearman correlation across all test events.
The difference between rows is descriptive, not a formally estimated
performance-decay interval.

## Method and provenance

- Recompute Spearman rank IC from archived out-of-fold predictions, locked
  scores, targets, and filing acceptance months. Before resampling, fail if
  dataset, selection, score-archive hashes, counts, champion identity, or any
  frozen point estimate disagree. No raw filing, return, or prediction is
  published in this note or the site.
- Group **all filings in each calendar month**, including empty months in the
  calendar span. Draw overlapping moving blocks of **two consecutive calendar
  months** with replacement and truncate to each cohort's original month span.
  This keeps contemporaneous filing clusters together and preserves some
  cross-month dependence from overlapping 20-trading-day outcomes. Resample
  2023 and 2024 separately, then combine their ICs with the original frozen
  fold counts (1,152 and 1,153). The locked test is resampled as one cohort.
- Use **5,000** draws and percentile **2.5%/97.5%** limits; NumPy generator
  seed **20260923** for development and **20260924** for locked test. The
  development folds each span 11 months with eligible events; the locked test
  spans 19 months. These are short time series, so the limits are approximate
  and sensitive to the block-length and stationarity assumptions. They do not
  account for model selection, regime changes, issuer-level dependence beyond
  the time blocks, or data-provider revisions.
- The procedure is a calendar-cluster adaptation of the moving-block
  bootstrap for dependent observations described by
  [Künsch (1989)](https://projecteuclid.org/journals/annals-of-statistics/volume-17/issue-3/The-Jackknife-and-the-Bootstrap-for-General-Stationary-Observations/10.1214/aos/1176347265.full).

As a block-length sensitivity check, the same code with 2,000 draws per
setting (same seeds) gives:

| Consecutive calendar months per block | Development 95% interval | Locked-test 95% interval |
| ---: | ---: | ---: |
| 1 | [0.0009, 0.1077] | [-0.0252, 0.0776] |
| 2 (primary; 5,000 draws above) | [0.0173, 0.1181] | [-0.0114, 0.0702] |
| 3 | [0.0210, 0.1215] | [-0.0149, 0.0723] |

The locked interval includes zero in every setting; the endpoints should not
be treated as precisely estimated with only 19 test months.

Reproduce locally with authenticated data (not included in the public repo):

```bash
.venv/bin/python scripts/locked_rank_ic_interval.py
```

Pinned identities:

| Artifact | SHA-256 / identity |
| --- | --- |
| Dataset | `research-2026-07-31-3553e7ad78dd` |
| Selection | `0bce6d674607af4e6f8e0930332923d1ab8f9c5409c63634c678ad4b67f1f906` |
| Locked result | `9caf4c4dfd12ec8d1981342cd190195e2c45db0b2f1ea751c3b0bcedf3e62987` |
| OOF predictions | `25a78eca1df14b8bf04d1863618972569e656e011fe621fc8da8ead787f71bf3` |
| Locked scores | `f6b18472d43d2b5e40df90b8a200ef162a1c6dd865ae47688220c9b0c3b55ddd` |
