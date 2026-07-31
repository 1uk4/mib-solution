# V2 Edge Cases — OCR pass

Building on top of V1 (see [../v1/EDGE_CASES.md](../v1/EDGE_CASES.md)).
This document catalogs edge cases specific to the OCR layer — populated as
we discover them.

## V2 scope statement

Minimal Architecture C from the design discussion:
- Selective OCR only on packets where V1 would APPROVE
- Tesseract engine, embedded JPEGs (no page rendering)
- No new adjudication rules — reuses V1 vocabulary and rules exactly
- OCR is a *safety guard*, not a primary extractor

Anything more elaborate (OCR-driven extraction, preprocessing, image-type
classification, cross-referencing) is deferred to V3+.

## Image taxonomy (confirmed by inspection + user description)

Three distinct image types appear in packets:

| Type | Typical size | Role | OCR usefulness |
|---|---|---|---|
| Profile photo | 512×512 RGB | biometric portrait of applicant | none (no text) |
| Stamp | 512×512 (RGB or grayscale) | risk signal (biohazard, denial) | valuable but stylized — Tesseract may need PSM tuning |
| Full page | 1224×1584 | form / sponsor letter / adjudicator note | high — 200 DPI letter-paper, readable at `--psm 6` |

Distribution across the 22 V1 catastrophic false approvals:
- 11 cases have letter-sized images (OCR-recoverable via current pipeline)
- 11 cases have only 512×512 images (portraits and/or stamps)
- 0 cases have zero images

Observed OCR quality on letter-sized images (Tesseract 5.5.1, `--psm 6`):
- Field text extractable but noisy: `illegible_biometrics` → `egible_biometrics`
- Enough for keyword-based `has_risk_signal` to trigger (regex is substring, not exact)
- Truncation happens (`DENIED` → `DENIE`) — our regex accounts for word-boundary

## Adversarial content on OCR-visible pages

User warning + V1 experience: full-page images may be planted to throw
off the system. Analogous to V1's prompt injection and fee-receipt label
lying. Design implications for V2:

- **We never trust OCR to confirm approval.** The V1 decision to APPROVE
  was based on positive text evidence; OCR is only a *veto*.
- `has_risk_signal` triggers on any risk keyword and downgrades to
  REVIEW (not DENY). Even if OCR fires a false positive (e.g., "no
  biohazard risk detected" contains the word "biohazard"), the cost is
  APPROVE (+8) → REVIEW (+2), not APPROVE (+8) → DENY (-4).
- Adversarial OCR content that says "APPROVED" cannot help us because
  we don't consult OCR when V1 already denied or reviewed.

## Open questions (populate as we go)

- How reliable is Tesseract on stylized 512×512 stamps (biohazard, denial)?
- Does the `has_risk_signal` regex ever fire on benign OCR text
  (false-downgrade rate)?
- Are the small-image-only false-approval cases fundamentally
  OCR-unreachable, or is the stamp text large enough that a different
  PSM mode would catch it?
- OCR latency per packet — within the 6s/PDF budget when we OCR ~190
  packets?
