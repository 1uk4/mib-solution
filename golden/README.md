# Golden Output Files

Reference `predictions.jsonl` captured from the **v3 pipeline at commit `92eb104`**
(the frozen pre-rewrite baseline). These are the parity oracle for the v4 rewrite:
v4 must reproduce the matching golden **byte for byte**.

## Files

| File | Environment | Score | Cat FAs | Brier |
|------|-------------|-------|---------|-------|
| `native-92eb104-seed42-n1000.jsonl` | Host (macOS, tesseract 5.5.1 / leptonica 1.85.0), warm OCR cache | 118.08 / 150 | 15 | 0.1136 |
| `docker-92eb104-n1000.jsonl` | Container (`python:3.12-slim`, tesseract 5.5.0 / leptonica 1.84.1), cold cache | 117.98 / 150 | 15 | 0.1138 |

Both cover the same 1000 `case_id`s (verified identical id sets).

### Provenance

The Docker image `mib-submission:latest` was verified to contain exactly the code at
commit `92eb104` — `sha256` of `solution.py`, `v1/solution.py`, `v3/policy.py`,
`v3/signals.py` and `v3/extract.py` inside the image match `git show 92eb104:<file>`
for all five.

The native run used `./dev_score.sh 1000` with `MIB_SEED=42`. The train set is
exactly 1000 packets, so the seed-42 sample of 1000 is the whole set — the two runs
are directly comparable.

## Environments are NOT interchangeable

**145 of 1000 rows differ between the two files, with zero code change between them.**

- 140 rows differ in extracted field values
- 7 rows differ in final verdict

Field diffs by frequency:
`home_world` 47, `declared_purpose` 37, `applicant_name` 31, `species_code` 28,
`arrival_date` 12, `visa_class` 11, `sponsor_id` 8, `fee_status` 6, `risk_flags` 1.

Verdict changes (native → docker):

```
MIB-000096  DENIED       -> NEEDS_REVIEW   conf 0.96 -> 0.34
MIB-000293  NEEDS_REVIEW -> DENIED         conf 0.34 -> 0.99
MIB-000631  APPROVED     -> NEEDS_REVIEW   conf 0.69 -> 0.65
MIB-000758  DENIED       -> NEEDS_REVIEW   conf 0.96 -> 0.34
MIB-000772  APPROVED     -> NEEDS_REVIEW   conf 0.69 -> 0.65
MIB-000879  NEEDS_REVIEW -> DENIED         conf 0.34 -> 0.85
MIB-000969  NEEDS_REVIEW -> APPROVED       conf 0.65 -> 0.69
```

**Cause:** the two environments ship different OCR engines (tesseract 5.5.1 /
leptonica 1.85.0 vs 5.5.0 / 1.84.1). Slightly different character reads propagate
through L4 extraction into L6/L7 decisions.

The aggregate score barely moves (−0.10) because the differences largely cancel, which
is exactly why an aggregate score is an inadequate parity gate — 14.5% of the output
changed while the headline number stayed within 0.1 points.

**Rule: only ever diff like against like.** Compare a native v4 run to the native
golden, and a Docker v4 run to the Docker golden. Never cross-compare.

## Usage

```bash
# Native parity check (fast, run at every milestone)
./dev_score.sh 1000                      # writes /tmp/mib-dev-output/predictions.jsonl
diff <(sort /tmp/mib-dev-output/predictions.jsonl) \
     <(sort golden/native-92eb104-seed42-n1000.jsonl) && echo "PARITY OK"

# Docker parity check (~55 min, final gate only)
MIB_DOCKER=1 ./dev_score.sh 1000
diff <(sort /tmp/mib-output/predictions.jsonl) \
     <(sort golden/docker-92eb104-n1000.jsonl) && echo "DOCKER PARITY OK"
```

Rows are written in `case_id` order by `solution.py`, so a plain `diff` also works;
sorting guards against any future change in emission order.

## Runtime

The Docker run processed 1000 packets in **54m 54s cold-cache = 3.29 s/PDF**, against
a 6 s/PDF budget. Extrapolated to the 5000-packet eval set: ~16,450 s against a
30,000 s hard limit.
