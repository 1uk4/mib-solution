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

Almost certainly several distinct populations sharing one tag and one
confidence value. Splitting it is a **calibration** win before it is ever a
classification win (Brier is scored per-case; a well-split bucket lowers
Brier even if no verdict changes).

*Counterfactual to measure:* partition by which extraction failed
(visa-only / fee-only / both / other) and measure per-partition accuracy on
`/tmp/mib-features.jsonl` after regenerating features against v4.

*Where:* `v4/rules.py` (emission), `v4/confidence.py` (values).

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
