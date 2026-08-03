# MIB Document Adjudication Pipeline — Technical Debrief

**System:** `v4/` — a seven-layer, trust-boundaried document adjudication
pipeline. Python 3.12 stdlib + Pillow + Tesseract; no network, no GPU, no
models beyond the OCR engine.
**Measured:** 118.08 / 150 on the 1000-packet training set (117.98 under the
submission container — the difference is OCR engine versions, §6). 15
catastrophic false approvals. Brier 0.1136. Train/val split 118.088 / 118.041
— essentially no overfitting.

This document explains how the system decides, why it is built the way it
is, and what we deliberately did not do.

---

## 1. The decision problem

Each packet is a multi-page PDF mixing structured forms, free-form letters,
embedded document scans, and — sometimes — adversarial content: hidden
instruction text, fake "sample denial" watermarks, redacted fields,
injected directives. The system must extract 9 fields, decide
APPROVED / DENIED / NEEDS_REVIEW, and report a calibrated confidence.

Three properties of the scoring shaped everything:

1. **Catastrophic false approvals (D→A) cost −4 raw** but a correct deny
   is +8 and a defensive deferral only +2 — so blanket defensiveness is a
   net loss unless a rule catches more than it spends (§5).
2. **Confidence is scored by Brier**, a proper scoring rule — minimized
   when reported confidence equals the rule's empirical accuracy, so every
   confidence in the system is a *measured frequency*, not a sentiment (§4).
3. Some denials are **structurally undetectable** — the disqualifying flag
   appears nowhere in the packet's visible content. The correct output
   there is NEEDS_REVIEW, and no rule should pretend otherwise (§5).

## 2. Architecture: establish what the packet says, then decide

```
                  ┌─────────────────────────────────────────┐
   foundation ──▶ │ config.py — every feature flag, one file │
                  │ confidence.py — every confidence, one    │
                  │   table, empirically calibrated          │
                  │ vocab.py — closed enums w/ provenance    │
                  │ patterns.py — every regex / word list    │
                  └─────────────────────────────────────────┘
                        │ read by every layer below
   ┌────────────────────┴────────────────────────────────────┐
   │ L1 acquire      PDF bytes  → list[Source]               │
   │ L2 extract      Source     → .content  (text / OCR)     │
   │ L3 filters      Source     → .trusted  ◄— TRUST BOUNDARY│
   │ L4 signals      Sources    → typed, weighted evidence   │
   │ L5 consolidate  evidence   → one value per field        │
   │ L6 rules        fields     → (verdict, confidence, tag) │
   │ L7 policy       verdict    → final verdict              │
   └─────────────────────────────────────────────────────────┘
                        │
                 predictions.jsonl
```

**L1–L5 establish what the packet says; L6–L7 decide what to do about it.**
Trust is decided exactly once, at L3, and never re-litigated: sanitizers
strip adversarial substrings (injection payload lines, redaction markers)
while keeping legitimate content; detectors exclude whole sources whose OCR
is garbage (real-word ratio < 30%). Everything downstream sees only trusted
content, so no rule needs its own adversarial defenses.

### The journey of one packet

1. **L1** walks the PDF's streams: text streams kept as raw bytes + filter
   chain; images decoded to OCR-ready bytes with cheap brightness stats.
2. **L2** decodes text streams (stdlib Flate/ASCII85 + `Tj` operator
   extraction — validated: 0 missed streams, 36,905/36,905 text ops across
   training) and OCRs images. Documents get **triple-pass OCR** (baseline
   psm 6, 2× upscale psm 3, unsharp-mask sharpen; union of outputs,
   +10.27 pts measured); icons and blank canvases are skipped only when
   provably contentless.
3. **L3** applies the trust boundary (above).
4. **L4** runs the canonical field extractor over the combined text, each
   stream, and each image, emitting **typed Signals weighted by the Field
   Manual's evidence hierarchy** (adjudicator note L1 → intake form L2 →
   biometric slip L3 → sponsor attestation L4 → registry extract L5 →
   bare text layer L6). Fuzzy recovery passes (separator-tolerant flag
   matching, digit-lookalike sponsor repair, template-label fuzzy
   extraction, char-whitelist re-OCR) fire only where strict extraction
   failed, at lower confidence, so they can fill gaps but never outvote a
   clean read.
5. **L5** consolidates: highest-confidence signal wins per field;
   disagreements are recorded (`_agreement`), and each field's provenance
   class (`text` / `ocr_only` / `absent`) is kept for L7.
6. **L6** runs the ordered rule chain (§3).
7. **L7** applies nine policy stages (§3) and emits the final verdict.

## 3. The decision layers

**L6 is a strict-priority rule chain** — highest-authority evidence first:

| Priority | Rule | Verdict | Measured accuracy (fires) |
|---|---|---|---|
| 1 | Adjudicator `Finding:` line | as stated | 100% (162) |
| 2 | Hard-embargoed home world | DENIED | 100% (34) |
| 3 | TRANSIT-7 visa | DENIED | 97% (37) |
| 4 | Disqualifying risk flag | DENIED | 100% (56) |
| 5 | Revoked sponsor (non-DIP) | DENIED | 98% (62) |
| 6 | Soft-embargoed home (non-DIP) | DENIED | 100% (11) |
| 7 | Unpaid fee | DENIED | 96% (27) |
| 8 | Stale arrival (non-DIP) | DENIED | 96% (24) |
| 9 | Review-only flag | NEEDS_REVIEW | 94% (70) |
| 10 | Visa/fee unextractable | NEEDS_REVIEW | 34% (251) |
| 11 | Missing arrival | NEEDS_REVIEW | 46% (13) |
| 12 | Unknown fee | NEEDS_REVIEW | 100% (24) |
| 13 | Paid + clean | APPROVED | 69% (168) |
| 14 | DIP-1 waived | APPROVED | 79% (22) |
| 15 | Non-DIP waived | NEEDS_REVIEW | 37% (43) |

Hard-deny rules that need only a single field fire **before** the
extraction-failure fallback, so a packet whose fee is unreadable can still
be denied on a readable embargoed home world.

**L7 is nine named stages** in three families, dispatched in a fixed order:

- **3 upgrades** (may promote any verdict; a firing upgrade skips every
  guard): promote a defensively-reviewed waived case when a biometric slip
  reads cleanly "Observed flags: none" in *every* fragment (0 cat-FA risk
  measured); promote extraction-failure fallbacks when image OCR carries an
  explicit adjudicator Finding, a named risk category, or a deny stem
  (+14 correct denials, 0 cat FAs); and a default-off unpaid-waiver
  variant (§5).
- **1 trust bypass**: adjudicator-finding approvals are exempt from the
  guards — a signed manual note outranks form-derived suspicion by design,
  and is 100% accurate on training.
- **5 guards** (may only demote an APPROVED; first hit wins): OCR risk
  override (direct evidence beats safety heuristics), OCR-only fields
  guard, multi-source conflict guard, missing-required-field guard, and a
  default-off defensive downgrade (§5).

## 4. Calibration: confidence = measured frequency

Every confidence the system can emit lives in one registry
(`v4/confidence.py`, 31 entries), each recording its fire count and
measured accuracy. Values *are* the measured accuracies, capped at 0.99
for eval-set tail headroom. This replaced hand-picked values that were
systematically overconfident on the approve rules (0.94 asserted vs 0.69
measured) — the recalibration is worth roughly 3 points of Brier-derived
score on its own. A registry-completeness test enforces that every tag the
pipeline can emit resolves to an entry, so an uncalibrated confidence
cannot ship.

The honest consequence: the system reports 0.34 on its extraction-failure
fallback and 0.37 on non-DIP waived reviews, because that is how often
those buckets are right. Low confidence on genuinely uncertain cohorts is
what proper calibration looks like.

## 5. Restraint as a design decision

Three empirical findings governed what we refused to build:

- **Undetectable denials exist** (~15/1000): packets whose disqualifying
  flag appears in no visible content. Rules firing on proxy signals (thin
  OCR, missing slips) catch some — and downgrade ~5× as many correct
  approvals. We accept these cat FAs deliberately.
- **The defensive downgrade is built, tested, and OFF.** It catches all 15
  training cat FAs at the cost of 76 correct approvals — net −2.45 points
  under the published scorer. It ships as a one-line config flip in case
  the private eval weights cat FAs as a hard constraint (binding at
  >~0.15 pt per cat FA).
- **The OCR-only guard stays ON despite looking wrong.** Its solo precision
  is ~35%, and its text-over-image skepticism inverts the Field Manual's
  evidence hierarchy — but disabling it uncovered +10 cat FAs across four
  distinct approve paths for a net −0.22. Measured behavior beat both
  intuition and doctrine; we kept the guard and documented the tension.

## 6. Verification: byte-identity, not score-matching

The v4 architecture is a ground-up restructuring of a working system, and
its behavior preservation is proven at the strongest available standard:

- **Golden oracle.** The pre-rewrite system's outputs on all 1000 training
  packets are committed (`golden/`, provenance-verified by SHA-256 of the
  pipeline files inside the submission image). v4's output is
  **byte-identical** — same rows, same order, same digits. Not "same
  score": same file.
- **Why that standard matters:** the same code produces **145 different
  rows** under the container's tesseract 5.5.0 vs the host's 5.5.1 — while
  the aggregate score moves only 0.10. A ±0.3-score gate would have called
  that "parity." Byte-identity (compared like-for-like per environment)
  cannot be fooled by compensating errors.
- **Function-level differentials:** the rule chain was replayed against
  its predecessor on 126,000 field combinations and the policy layer on
  15,000+ input combinations — zero mismatches — before the end-to-end
  gate ever ran.
- **Determinism** was verified separately (an unchanged rerun reproduces
  the golden SHA-256 exactly), so any future diff is a real defect, never
  noise.
- 141 unit tests, including per-stage isolation tests for all nine policy
  stages and both default-off insurance paths.

Runtime: 3.29 s/PDF cold-cache in the container against a 6 s budget
(~16,450 s extrapolated for the 5000-packet eval vs the 30,000 s limit).

## 7. What did NOT change (deliberately)

The rewrite preserved, verbatim: the output schema; the OCR pipeline and
its cache-key format; all L3 filter thresholds and adversarial logic; every
rule, every confidence value, every feature default; the layer separation;
and the error handling (a crashing packet emits a safe NEEDS_REVIEW rather
than failing the batch). The only removals were provably dead: an unused
signal type, two unreferenced regexes, a duplicate injection filter, and a
legacy standalone pipeline (~96 lines) — plus 10 environment variables
whose values had never varied in any production run, now literal defaults
in one config module. Both default-off experimental features remain in the
code, reachable by flipping one field.

## 8. Migration map (v3 → v4)

| v3/v1 location | v4 home |
|---|---|
| 11 env vars across `extract` / `signals` / `policy` | `config.py` (10 → literals; `MIB_OCR_CACHE_DIR` kept) |
| `v1 CONFIDENCE` + 6 policy literals + 10 evidence literals | `confidence.py` (31 entries + tag-mapping) |
| `v1` vocab/rule tables | `vocab.py` |
| `v1` PDF + field regexes, OCR stem lists | `patterns.py` |
| `v1 _decode_stream` / `_pdf_unescape` | `extract.py` |
| `v1 extract_fields` + 10 helpers | `signals.py` |
| `v1 adjudicate` + helpers | `rules.py` |
| `v3/ocr_signal.py` + biometric reader from `v3/policy.py` | `evidence.py` |
| `v3/policy.py` (200-line function) | `policy/` — 9 stages + 12-line dispatcher |
| `v1 _default_field`, crash fallback | `solution.py` |
| Dead: `v2/`*, `extract_text`, `predict_case`, `_filter_injection`, `CASE_ID_RE`, `VISA_RE`, `FLAG_DECLARATION` | not carried (*frozen in repo, not shipped in image) |

The submission image ships `v4/` alone — it building and running without
`v1`–`v3` present is the standing proof that the pipeline is
self-contained.
