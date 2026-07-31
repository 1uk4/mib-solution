# V3 Edge Cases — Ensemble

Building on V1 and V2 (see [../v1/EDGE_CASES.md](../v1/EDGE_CASES.md),
[../v2/EDGE_CASES.md](../v2/EDGE_CASES.md)). V3 is architecturally
distinct: V1 and V2 become feature-extraction libraries; V3's ensemble
is the sole decider.

## V3 scope

**Discovery-first methodology.** V1 and V2 built rules from hypotheses
about what mattered. V3 measures every feature against training truth
via a correlator, then adds only signals that discriminate.

Initial ensemble mirrors V2 exactly for zero-regression baseline. Each
new signal is added only after the correlator proves it moves the needle
against training truth.

## Signals to enumerate and measure

Grouped by source. Every entry is a hypothesis until the correlator
tags it with a lift score. Prune ruthlessly — a rule that doesn't earn
its keep gets removed.

### Structural
- file_bytes, stream_count, page count
- image_count, letter_image_count, small_image_count
- presence of specific documents (fee receipt, sponsor letter,
  adjudicator note, biometric slip) — needs image type classification
- number of pages that ONLY contain a header (empty rest of page)

### V1 text (already computed)
- v1_rule_tag — is R_A1_paid_clean strongly wrong more than others?
- Specific field combinations (e.g., visa=MED-3 + species=X + home=Y)

### V2 OCR (already computed)
- ocr_signal (adjudication, confidence, tag)
- OCR char count per image (silence pattern)
- Text-vs-OCR cross-references — do stated fields match OCR-visible ones?

### Image-content (per image)
- Dimensions bucket (square-small, letter, other)
- Mean brightness (photos vs mostly-white pages)
- Text density (OCR char count / area)
- Color palette (grayscale vs colored) — stamps are often colored

## Attempted filters that DIDN'T work (documented so we don't retry)

### ArchivalDetector (source-level exclusion by COPY/FILED/ARCHIVE/INTAKE)

Intuition: archival stamps mark documents as superseded, so exclude
their content from adjudication.

Empirical result: FORM B-13 Biometric Scan Slips (MIB-000078 image_3)
contain BOTH the authoritative `Observed flags: biohazard_red` risk field
AND archival stamps (ARCHIVE, FILED, INTAKE) on the same page. Excluding
those sources loses the real risk signal → APPROVE regressions on cases
V2 correctly denied.

**Correct interpretation**: archival stamps mark a document as "processed
/ closed" — not "content is invalid." Content on archived pages can still
be authoritative. Any archival-awareness feature belongs at L5 (cross-
reference weighting) not L3 (source exclusion).

### Source-level InjectionDetector

Intuition: mark whole source untrusted if it contains any injection marker.

Empirical result: text streams like MIB-000115 text_stream_4 contain
BOTH FORM I-8090 legitimate field data AND SYSTEM: injection lines in
the same source. Whole-source exclusion loses the fields → extraction
regression.

**Correct fix**: line-level sanitizer (matches V1's `_filter_injection`).
The Sanitizer removes only injection lines, source remains trusted, real
content survives.

## Open questions to answer via inspector + correlator

1. Do the 15 content-invisible catastrophic FAs have any structural
   feature that discriminates them from clean truth-APPROVED cases?
2. Are the R→A errors (36 total, V1-APPROVED but truth=REVIEW)
   discriminable via any signal V1 didn't use?
3. Does file_bytes / image_count correlate with truth adjudication?
4. Are there packet-level artifacts (specific dimensions, byte-size
   patterns) from how the challenge was generated that leak the truth?
