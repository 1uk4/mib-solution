# V4 Observations Ledger — Bucket 2 / Bucket 3

The rewrite's second deliverable: everything found during transcription that
**would change behavior** and was therefore NOT acted on (three-bucket rule,
spec §2). Bucket 2 = measurable candidates for the next phase, ordered by
expected impact. Bucket 3 = rejected on sight (unmeasurable / keys on
absence of evidence — brief Pitfall 1).

Measurement protocol for any B2 item: change one thing; run
`./dev_score.sh 1000`; run `v3/dev/analysis/split_score.py`; report BOTH
split scores (never tune on val); compare per-case against
`golden/native-92eb104-seed42-n1000.jsonl` to attribute every flipped row.

---

## B2-1 — The `FALLBACK_extraction_fail` bucket (top priority)

**Fires on 251 of 1000 packets at 34% accuracy.** A quarter of all decisions
land in one undifferentiated tag that is wrong roughly two times in three,
emitted at confidence 0.34. Every L7 guard the brief debates concerns tens
of cases; this concerns 251.

**Measured 2026-08-03 (L6 census) — the obvious split axis is dead:**
partitioning by which extraction failed gives fee-only-missing 163 @ 35%,
both-missing 53 @ 28%, visa-only-missing 35 @ 26% — statistically
indistinguishable from the pooled 32%; sub-tagging would move Brier by
almost nothing. The original "distinct populations" hypothesis is NOT
supported along this axis.

What the decomposition found instead: **65% of the bucket is
fee-only-missing, and 44% of those are truth-APPROVED** — approvable
packets whose fee receipt didn't extract while everything else read
fine. Inverse signal: visa-missing packets skew DENIED (43%).

**CLOSED 2026-08-03 — measured end-to-end and structurally
unrecoverable.** Built `fee_fuzzy_recovery` (fee_status joins the fuzzy
label ladder; it was the one labeled field without recovery — the v3
docstring's "dedicated block" never existed). Full measurement
(`v3/dev/analysis/fee_recovery_measure.py`): 28 fees recovered, ZERO
verdicts changed (+0.12 extraction, −0.05 calibration, +0.06 total);
split: train +0.105 / **val −0.090** — the overfit pattern the split
discipline exists to catch. Root cause diagnostic: of 354 fee-empty
cases, **83% contain no fee text anywhere in trusted content** — the
evidence is not in the packet (Pitfall 9's shape; this is the eval's
designed extraction-failure cohort, the fee-side sibling of Cohort B).
**Flag stays default-OFF**, kept as a documented measured experiment.
The fallback bucket's 0.34 is the honest confidence for a genuinely
undecidable population.

## B2-2 — Crash path reuses extraction-failure confidence

`v4/solution.py` `main()` assigns `conf("FALLBACK_extraction_fail")` (0.34)
to packets that **raised an exception** — annotated in-code. A crashed
packet and an under-extracted packet are different populations; sharing a
calibration bucket has no measured justification.

*Counterfactual:* count exception-path emissions on training (expected:
near zero — which itself argues for a distinct high-uncertainty constant)
and measure that cohort's empirical accuracy if nonempty.

## B2-3 — `R_A1_non_dip_waived` at 0.37

A rule asserting it is wrong 63% of the time. Brier-optimal *for that
bucket*, which is the tell that the bucket wants splitting rather than
tuning. The L7 biometric upgrade already partitions it (clean-slip cases
leave at 0.80); the residue's composition is unexamined.

*Counterfactual:* split residue by biometric-slip presence (present-but-
noisy vs absent) and measure per-split accuracy.

## B2-4 — OCR engine version sensitivity

**145 of 1000 rows change between tesseract 5.5.1 (host) and 5.5.0
(container) with zero code change; 7 verdicts flip** (see
`golden/README.md`). The grader's container is authoritative and we don't
control its image. Two of the seven flips fall from `R3_unpaid` @ 0.96 into
`FALLBACK_extraction_fail` @ 0.34 — direct evidence for B2-1: the fallback
bucket is where degraded extraction lands.

*Counterfactual:* measure how many packets sit within one character-read of
a rule boundary (e.g. fee_status flips between paid/unpaid across engines).
Bounds how much host-measured tuning can be trusted.

## B2-6 — Brightness statistics: measured, found decisionless, REMOVED

Measured 2026-08-03 over all 4,079 training images, then acted on
(the first bucket-2 item resolved by measurement):

- **The `_should_ocr` blank-canvas rule never fired** — zero blank
  canvases in the corpus (and zero sub-100px images for the tiny rule).
- **The doc gate's brightness veto never fired** — every ≥800px image is
  bright. On this corpus `_looks_like_document` is EXACTLY "is a JPEG"
  (1,956 = the DCTDecode count; 0 mismatches on an 846-image sample):
  documents ship as big JPEG scans, photos/stamps as small Flate-raws.
  The dimension test alone does all discrimination.
- Brightness stats forced a full pixel decode per image: ~8.4 ms/image
  ≈ 34 ms/PDF ≈ 1% of the container budget, for zero decisions.

**Decision (user call): removed.** `_image_meta` now reads header
dimensions only; both gates are dimension-only, each carrying a short
"existed through v3, measured 0 fires / 4,079, removed" note — the record
lives in the code where the next reader will look. The costless tiny-image
rule stays (a decoy icon would waste an OCR call, not lose evidence).

Eval-set delta accepted with eyes open: a hypothetical blank canvas now
gets OCR'd (wasted ~0.3s, empty text, no extraction impact) and a
hypothetical dark ≥800px photo now gets triple-pass instead of single
(more compute, strictly more OCR text). Both fail toward MORE evidence;
runtime headroom is 2×. Verified training-identical: the removed branches
had zero fires, mini-parity clean.

## B2-7 — OCR pass composition, measured at pass granularity (2026-08-03)

120-packet composition experiment (passes OCR'd separately, pipeline
replayed per composition, fields scored vs truth; n=1,080 field slots).
Complements the 2026-07-31 bundle sweep, which measured features at
full-score granularity:

- **Union order matters per-case but is net-neutral**: reordering flips
  9–16 fields, net within ±3 (noise). Baseline-first stays — now a
  measured choice, not an inherited accident.
- **The 2× upscale pass is the workhorse**: removing it costs −21 fields.
  **1.5× retains almost all value** (−2 vs 2×) at ~44% less upscale
  compute — the pressure-relief lever if eval runtime ever binds.
- **Sharpen's extraction contribution is ≈ 0 (−4±noise)** — its measured
  +10.27 total (RULE_AUDIT 2026-07-31) was classification-side: readable
  risk stems feeding the L7 OCR-signal path, plus cat FA 22→15. The two
  measurements compose: sharpen buys evidence, not field values.

*Counterfactual queued:* full-score run with `Config(ocr_sharpen=False)`
(the retained DUAL insurance path) to decompose sharpen's classification
value directly. No code change this phase — every variant is ≤ noise or
a lever we don't need; parity preserved.

## B2-8 — Illegibility threshold: 6.1% of images sit on the knife edge

Census 2026-08-03 (4,079 images): 780 excluded at ratio < 0.30; **249
images (6.1%) fall within ±0.05 of the cutoff**. OCR-engine version drift
(B2-4) moves ratios slightly, so borderline images flip exclusion across
environments — cascading into signal counts, field values, and the
`any_illegibility_excluded` bit L7 reads. Likely a real component of the
145-row host/docker delta.

Not actionable (brief Rule 4: filter thresholds are off-limits) — logged
as the known sensitivity behind cross-environment drift, for the debrief.
Also measured: injection sanitizer fires on 98% of packets (SYSTEM: lines
are ambient); redaction fires mostly via image OCR (197/268); L4's
placeholder check is genuine defense-in-depth (9 post-sanitization
catches — mixed-case brackets redaction deliberately ignores);
`SPONSOR_ATTESTS_RE`'s `[SPONSOR ID BLANK]` alternative: 0 matches
post-sanitization (vestigial from pre-L3 v1, harmless).

## B2-9 — The per-stream signal tier: 47% of emissions, 0.3% win rate

Census 2026-08-03 (16,960 signals, 6,978 filled fields): per-stream
signals are the largest emission family (7,997) and win 24 fields —
**23 of which are exactly the fee_status unknown@0.4 sentinel rescues**
(sentinel emitted 23×, outvoted 23× — the design works every time), plus
one applicant_name. Structural cause: combined_text is a superset of
every stream at conf 1.0, so per-stream can only win where combined
deliberately under-bids.

Not removable — losing candidates feed L5's `_agreement` (the L7
conflict guard's input) and corroboration counts. Reframed, not removed:
the tier is a *metadata and sentinel-rescue* mechanism, not a value
source. *Counterfactual if simplification is ever wanted:* measure
emission narrowed to fee_status + conflict-relevant fields.

## B2-10 — Char-whitelist re-OCR: fully shadowed by its bundle-mates

Same census: 2,572 repair invocations, **1** reached Tesseract (0.2 s
total), **0 repairs succeeded**. The 2026-07-31 sweep's +9.64 was
measured against the pre-bundle baseline; with user-words + sharpen +
normalize on, every value re-OCR used to fix arrives valid. Kept as-is
(inert and free; Rules 2/8): it is *shadowed insurance* that would
reactivate exactly when upstream OCR degrades — e.g. the container's
different tesseract (B2-4), the one environment we cannot cheaply
measure from the host.

## B2-5 — Guard confidences are shared, not measured per-guard

`ocr_only_downgrade`, `field_conflict`, `missing_required`, and
`defensive_downgrade_thin_evidence` all emit 0.65 — a hand-set value
predating calibration (the brief itself flags "multiple rules share 0.65
with no principled distinction"). Preserved exactly this phase.

*Counterfactual:* per-guard empirical accuracy from regenerated features;
`ocr_only_downgrade`'s measured 43% precision suggests its Brier-optimal
confidence is materially below 0.65.

---

## Bucket 3 — rejected (do not build)

- **Any rule firing on invisible-evidence cohorts** (Cohort B packets:
  DENIED truth, flag word absent from all visible content). ~15/1000 cat
  FAs are structurally undetectable; proxy-signal rules downgrade ~5×
  as many correct approvals as they catch (brief Pitfall 1).
- **MED-3 + missing biohazard-clearance gating** — 2× enrichment is real,
  but the packets don't contain the clearance info needed to act on it;
  every tested gate lost more than it saved (brief Pitfall 9).
- **Boilerplate-as-signal** (`DIP-WAIVER` waiver codes, `Registry Status:
  CLEAR`, bare `Observed flags: none` without the all-reads-clean
  discipline) — appears on both clean and dirty packets at similar rates
  (brief Pitfall 6).
