# Version 2 — Iteration Log

## Score progression

| Iter | Score | Δ | Notes |
|---:|---:|---:|---|
| V1 close | 103.48 | — | starting point |
| 0 | 104.43 | +0.95 | selective OCR + stem matching + finding regex |
| 1 | **104.98** | +0.55 | strong/weak OCR signal split — strong → DENIED |

## Iteration 0: scaffolding + selective OCR safety guard

### Goal
Prove the concept: does OCR-when-V1-wants-to-approve reduce catastrophic
false approvals without breaking too many correct APPROVEs?

### What's in
- `v2/solution.py`: imports V1, adds `extract_jpegs`, `ocr_jpeg`,
  `has_risk_signal`, and a `predict_case` that only OCRs when V1's
  adjudication is APPROVED
- `dev_score.sh` at repo root: random-sample scorer for fast dev iteration
  (default 100 PDFs, seeded)
- Dockerfile: tesseract-ocr + pytesseract installed, `v2/` copied
- Root `solution.py` dispatcher: switched to `from v2.solution import`

### What's out
Everything else. See EDGE_CASES.md scope statement.

### How to test
```bash
./dev_score.sh 20    # tiny debug run
./dev_score.sh 100   # standard dev sample
./score.sh           # full 1000 (only when we think we're done)
```

### What happened in iter 0

**Image extraction (adjusted mid-iter):** Original plan was "extract only
JPEG streams." Testing revealed ~50% of packets (including many false-
approval cases) have Flate-compressed raw-pixel images instead of JPEGs.
Added Pillow to Docker and added raw-pixel reconstruction path
(`extract_images` supports both DCT-JPEG and Flate-raw-RGB/L streams).

**Direct test on 22 known catastrophic FAs:** OCR successfully catches
7-8 depending on stem list tuning. Two categories:
- 11 cases have letter-sized (1224×1584) images — sponsor letters,
  adjudicator notes, biometric slips. Tesseract at `--psm 6` reads them
  well enough for keyword detection. Most catches come from here.
- 11 cases have only 512×512 images (portraits + stamps). Tesseract at
  default settings returns 0-5 chars. **These need image preprocessing
  (thresholding, upscaling) — deferred to V3.**

**Refinement 1: adjudicator-finding regex in OCR.** V1's
`R_ADJUDICATOR_FINDING` matches "Finding: DENIED" in text streams. Ported
to OCR: `OCR_FINDING_RE` catches the same pattern in OCR text, tolerating
Tesseract truncation ("DENIED" → "DENIE"). Caught MIB-000115 (adjudicator
note image OCR'd cleanly enough).

**Refinement 2: stem matching for risk keywords.** Tesseract truncates
stylized text ("BIOHAZARD" → "BIOHAZ"). Substring matching against stems
(`BIOHAZ`, `EMBARG`, `DENIE`, `REVOK`, `RESCIN`) tolerates this while
staying specific.

**Refinement 3: pruned ambiguous stems.** Initial stem list included
`BIOMETRIC` and `MEMORY`, which triggered on benign form titles
("Biometric Scan Slip" appears on every biometric-related packet). This
caused 6+ false downgrades on the first 500 packets. Removed those
stems; also removed `WARRAN` / `TAMPER` / `REJECT` which could match
"warranty" / "temper" / "rejection" in benign prose. Kept only stems
that essentially never appear outside the risk context.

### Remaining ceiling
Same as V1 for the 14 Category-A packets (small-image-only, needs
preprocessing). Everything else is either caught by OCR or was already
handled by V1's clean text pipeline.

---

## Iteration 1: strong / weak OCR signal split

### Motivation
Iter 0 downgraded every OCR-caught risk to NEEDS_REVIEW with confidence
0.65. For truth=DENIED cases that's +2 raw (conservative-review credit);
routing to DENIED would earn +8 raw when we have high-confidence signal.
Difference: **+6 raw per case correctly caught → +42 raw for the 7 catches
= ~+3.15 total.**

### Design
Replaced `has_risk_signal(text) -> tag_or_none` with
`evaluate_ocr_signal(text) -> (adjudication, confidence, tag) or None`.

**Strong signals (→ DENIED, conf 0.90-0.95):**
- Adjudicator "Finding: DENIED" (regex `OCR_FINDING_DENIED_RE`). V1 proved
  this 100% accurate on 162 text-stream findings; OCR variant inherits
  the trust for OCR-readable adjudicator notes.
- Exact underscored disqualifying flag labels (`biohazard_red`,
  `memory_tampering`, `active_warrant`, `planetary_embargo`). These are
  the machine enum values — not something a human writes in prose. If OCR
  reads them, a form/stamp literally rendered them.
- Revoked sponsor SPN visible in OCR — same principle as V1's R4 rule.
- Hard-embargoed home world in OCR — same as V1's R0 rule.

**Medium signals (→ REVIEW, conf 0.85):**
- Adjudicator "Finding: NEEDS_REVIEW"
- Exact review-only flag labels

**Weak signals (→ REVIEW, conf 0.65):**
- Stem matches (`DENIE`, `REVOK`, `RESCIN`, `BIOHAZ`, `EMBARG`)

### Adversarial safety analysis
No adversary trying to get their packet APPROVED would plant a "Finding:
DENIED" or an exact disqualifying flag label — those actively work
against their goal. The attack surface for OCR-based routing to DENIED
is therefore near-zero. Confirmed empirically:
- 132 truth=APPROVED V1-APPROVE cases → 0 false-DENIES from V2
- 36 truth=REVIEW V1-APPROVE cases → 0 false-DENIES from V2
- 22 truth=DENIED V1-APPROVE cases → 7 correct-DENIES from V2

### Impact
- Same 7 cases caught as iter 0 (identical extraction/OCR — only routing
  differs)
- Cases moved from D→R (iter 0) to D→D (iter 1) — best possible outcome
- Zero regressions on non-DENIED truth
- Score: **104.43 → 104.98 (+0.55)**
- Classification: +0.42 (from 42 raw / 8000 max × 80 scale)
- Calibration: +0.13 (Brier improvement on confident correct DENIES)

### Prediction miscalibration note
Initially predicted +3.15. Actual +0.55. The mistake was thinking in raw
points instead of the normalized score. Correct math: classification
score = 80 × raw / (cases × 8) = raw/100 per case. So 42 raw → +0.42
score, not +3.15.

Real leverage per iteration is small when we're already at 100/150 —
each catch is worth ~0.06 points on classification alone. Only preprocessing
(Direction B) can materially move the total from here.
