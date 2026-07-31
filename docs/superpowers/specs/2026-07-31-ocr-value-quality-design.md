# OCR Value Quality — Design Spec

**Date:** 2026-07-31
**Author:** Luka Flores (with Claude)
**Status:** Draft — awaiting review

## Problem

Full-run baseline on 1000 training PDFs (2026-07-30):
- Total score: **103.48 / 150**
- Extraction: **34.46 / 50** (69%) — **~15 pts on the table**
- Classification: **56.12 / 80** (70%)
- Calibration: **12.90 / 20** (65%)

The extraction leak is concentrated in a **231-packet cohort with poor extraction** (80 packets extract 0/9 fields, 151 extract 1–3/9). Systematic inspection identified three sub-cohorts:

| Cohort | Est. size | Root cause |
|---|---|---|
| **A. Small OCR value errors** | ~120 | Character-level misreads (`Annax`↔`Arinax`), spurious whitespace and punctuation (`Wolf-106 1c.` vs truth `Wolf-1061c`) |
| **B. Structurally under-determined** | ~80 | No readable form content in any OCR variant (MIB-000003 class); per challenge maintainer, `NEEDS_REVIEW` is the correct output |
| **C. Multi-image compositional loss** | ~30 | Per-embedded-image OCR is already at native resolution; alternative approaches (page render) empirically regress |

The scorer uses **exact string equality after whitespace-collapse + case-fold** (`scripts/evaluate.py:field_match`). A single-character OCR error or spurious punctuation causes a "missed" result even when the extraction is semantically correct.

This spec targets **Cohort A**. Cohorts B and C are out of scope.

## Non-goals

- **Page rendering as a source.** Measured on 20 poor-extract packets: page rendering at 200 DPI recovered 7 unique field labels but *lost 41* compared to per-embedded-image OCR. Per-image OCR at native resolution outperforms composed-page OCR because each embedded document region gets Tesseract's full attention without inter-region compression or text-stream overlay noise.
- **Higher-DPI upscaling beyond 2×.** Measured: 400 DPI regressed all test cases; excess upscaling degrades small-font recognition.
- **Alternative OCR engines (EasyOCR / PaddleOCR).** Docker size budget and 6-sec/PDF runtime constraint make swap risky; deferred until baseline exhausts.
- **Fuzzy value-snapping against training-set enumerations for open fields** (per `extraction_fuzzy_discipline.md`): labels can be fuzzy-matched, but values like `home_world`, `applicant_name` must not be snapped to a memorized training set — the eval may introduce unseen values.

## Design

Four additive components, each independently gated for measurement and rollback.

### 1. Sharpen (unsharp mask) as third OCR pass

Add a third pass to `_ocr_image_dual` in `v3/extract.py`:

```
baseline  = tesseract(image, psm=6)
upscaled  = tesseract(upscale_2x(image), psm=3)
sharpened = tesseract(unsharp(image, radius=2, percent=150), psm=6)
result    = "\n".join([baseline, upscaled, sharpened])
```

**Evidence:** on MIB-000032, sharpen recovered `Applicant: Asinax Qommora` where baseline read `Annax Qormora` — a character-level improvement toward truth `Arinax Qormora`. The union across passes lets any field regex catch whichever pass read that field correctly.

**Gate:** applied only when `_looks_like_document(source)` is true (existing dual-pass gate — bright ≥80, dim ≥800). No change to non-document images.

**Runtime cost:** ~+0.5s per doc-shaped image. Cache key updated to `"_triple"` to invalidate existing `_dual` entries.

### 2. Post-extraction value normalization at L4

Add a `_normalize_field_value(field, value)` helper in `v3/signals.py`, applied after every raw field extraction (both text-stream and OCR paths). Field-specific rules:

| Field | Normalization |
|---|---|
| `sponsor_id` | Strip whitespace and punctuation; validate `^SPN-\d{4}$`; if 5 digits with an OCR-spurious space (`SPN-60 99`), collapse before validation |
| `home_world` | Strip trailing `.` `,` `;` `:`; if the value matches the pattern `[A-Za-z]+-\d+\s+\d*[a-z]?` (letter+dash+digits with a spurious space before final chars), collapse to `[A-Za-z]+-\d+[a-z]?`. Example: `Wolf-106 1c.` → `Wolf-1061c`. Do not apply to values without the pattern (avoids breaking legitimate multi-word planet names) |
| `arrival_date` | Strip surrounding punctuation; validate `^\d{4}-\d{2}-\d{2}$`; digit-substitution repair (`2526-02-13` where year not in 2020..2030 → try `2026-02-13`, but only if the substitution produces a valid date within a plausible corridor) |
| `visa_class` | Snap to closed enum (`MED-3`, `XW-1`, `XW-2`, `DIP-1`, etc.) — already a Manual-listed enum |
| `fee_status` | Snap to closed enum (`paid`, `waived`, `unpaid`, `unknown`) — verified from Field Manual §Fee Rules and training-set distribution (n=1000: paid=664, waived=242, unpaid=50, unknown=44) |
| Free-form (`applicant_name`, `declared_purpose`, `species_code`) | Strip surrounding whitespace only; NO enum snap |

Only normalizations that are lossless-under-repair are applied. Where a repair would change semantics (e.g., collapsing `Wolf 1061c` where the space might be intentional), the normalizer leaves it alone and the extractor reports the raw value.

### 3. Char-whitelist re-OCR for structured fields

After L4 raw extraction, if a structured field (`sponsor_id`, `arrival_date`) fails its format regex, re-OCR the source that emitted the value with a per-field character allowlist:

```
tesseract image - --psm 6 -c tessedit_char_whitelist=SPN-0123456789   # for sponsor_id
tesseract image - --psm 6 -c tessedit_char_whitelist=-0123456789      # for arrival_date
```

**Rationale:** the whitelist prevents Tesseract from proposing letters where only digits are valid, fixing errors like `SPN-6O99` → `SPN-6099` and `2O26-02-13` → `2026-02-13`.

**Region granularity:** for the first version, re-OCR the entire source image (not a bounding-box crop). Bounding-box crop would require Tesseract HOCR output parsing — a future improvement.

**Cost:** only fires when a field failed initial extraction. Estimated ~50-100 invocations per full run; +50s total.

**Gate:** rolled out behind an env var `MIB_CHAR_WHITELIST_REOCR=1` for the first measurement pass so we can quantify impact independently.

### 4. `user-words` dictionary for closed-enum tokens

Add a `v3/data/tesseract_user_words.txt` file containing all closed-enum tokens from the Field Manual:

```
MED-3
XW-1
XW-2
DIP-1
TRANSIT-7
paid
waived
unpaid
unknown
TRIANGULAN
...  # (full list to be audited against Field Manual + training-set distribution before commit)
```

**Vocab audit (blocking step before implementation):** Before writing `tesseract_user_words.txt`, enumerate every value in every closed-enum field from `train_labels.csv` and cross-reference against the Field Manual. Values that appear only in training but not in the Manual should be flagged — including them biases OCR toward training-only tokens, which violates the fuzzy-extraction discipline for open fields.

Pass to Tesseract via `--user-words path/to/file`. This biases Tesseract's language model toward valid tokens without hard-snapping (i.e., Tesseract still outputs whatever it sees, but ties break toward these tokens).

**Safety:** every word in the file is a Field Manual enum value — no training-set-only tokens. Adding an eval-only visa class would not confuse Tesseract; it would simply not receive the same bias.

## Data flow

```
L1 acquire      → Source(TEXT_STREAM | IMAGE, raw, metadata)
L2 extract      → source.content populated
                   TEXT_STREAM: PDF text-op decode
                   IMAGE:
                     if _looks_like_document(source):
                       triple-pass OCR (baseline + upscale + SHARPEN)  [NEW]
                     else:
                       single-pass OCR
L3 filters      → injection sanitize, illegibility mark, redaction mark
L4 signals      → per-source field extraction
                   after extraction:
                     value = _normalize_field_value(field, raw_value)  [NEW]
                     if structured field & value fails format:
                       value = _char_whitelist_reocr(source, field)     [NEW]
L5 consolidate  → agreement + source_class per field
L6 rules        → V1 rule battery
L7 policy       → safety guards
```

Tesseract invocations in L2 use `--user-words` [NEW] pointing at the enum-tokens file.

## Testing plan

Each component gates on an env var so we can measure impact independently:

| Component | Env var | Default |
|---|---|---|
| Sharpen pass | `MIB_OCR_SHARPEN=1` | on after validation |
| Value normalization | `MIB_NORMALIZE_VALUES=1` | on after validation |
| Char-whitelist re-OCR | `MIB_CHAR_WHITELIST_REOCR=1` | on after validation |
| `user-words` dictionary | `MIB_USER_WORDS=1` | on after validation |

**Measurement protocol** (per component):
1. Baseline: full-run score with all components OFF.
2. Enable component alone: full-run, compare.
3. Component is kept if it improves total score AND does not increase `catastrophic_false_approvals` beyond baseline+1.

**Regression checks:**
- Cat-FA count must not exceed 24 (baseline 22 + tolerance 2).
- Runtime must complete inside 6000-sec docker timeout.
- Per-field extraction points must not regress on any field (`applicant_name`, `species_code`, `visa_class`, `sponsor_id`, `home_world`, `arrival_date`, `declared_purpose`, `fee_status`, `risk_flags`).

**Unit tests to add in `v3/tests/`:**
- `test_normalize.py`: `_normalize_field_value("home_world", "Wolf-106 1c.")` → `"Wolf-1061c"`; `_normalize_field_value("sponsor_id", "SPN- 6099 ")` → `"SPN-6099"`; date OCR repair; free-form no-snap.
- `test_ocr_triple_pass.py`: sharpen pass runs; union contains baseline text; cache key uses `_triple` suffix.
- `test_char_whitelist_reocr.py`: fake source with garbage OCR + valid image bytes → re-OCR with digit-only allowlist recovers a digit sequence.

## Runtime budget accounting

Contract: **6 sec/PDF avg, 30,000 sec hard limit for 5,000-PDF validation set**.

| Component | Per-PDF cost (avg) | Full 5000-run cost |
|---|---|---|
| Sharpen pass | +0.5s × ~2 doc-images = +1s | +5,000s |
| Value normalization | ~0ms | negligible |
| Char-whitelist re-OCR | +0.1s (fires ~5% of packets) | +500s |
| `user-words` load | +0ms per invocation | negligible |
| **Added total** | **+1.1s/PDF** | **+5,500s** |

Baseline current runtime is ~2-4s/PDF on 4 vCPU. Adding +1.1s/PDF brings avg to 3-5s — inside the 6s budget with margin.

## Rollback strategy

Each component is behind an env var default-off during measurement. After validation, defaults flip to on. If a component causes regression on the private test set, we can toggle it off by editing `run.sh` without a rebuild.

## Out of scope (separate follow-up work)

- **Calibration fix** — `427` predictions at conf 0.5 achieve only 36% accuracy. Simple fix: lower default `NEEDS_REVIEW` confidence from 0.5 to 0.35. ~4 pts. Independent design.
- **Defensive downgrade for cat-FAs** — 22 catastrophic FAs all share fingerprint `conf=0.94, 7-8/9 fields matched, no adjudicator note`. Structural downgrade rule. ~3 pts. Independent design.
- **Cohort B under-determined packets** — per maintainer, `NEEDS_REVIEW` is the correct output. No action.

## Success criteria

- Extraction score improves by ≥5 pts (target range 5-8 pts).
- No regression in catastrophic FA count (≤22).
- Full-run completes inside docker 6000-sec timeout.
- Each component's contribution measured independently and documented in `v3/dev/docs/RULE_AUDIT.md`.
