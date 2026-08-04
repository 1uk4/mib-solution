# MIB Doc Challenge — Technical Memo

**Training set: 118.56 / 150** (official `evaluate.py`, all 1,000 labeled
packets). Train/val split (800/200, fixed seed): **118.46 / 118.94** —
the held-out fifth scores *higher* than the fitted portion. Catastrophic
false approvals: **15**, unchanged across every configuration ever
shipped. Runtime: **3.5 s/PDF measured under the full grading harness**
(one container, `--cpus 4 --memory 8g --read-only`, tmpfs `/tmp`) against
the 6 s budget — ~17,500 s projected for the 5,000-PDF validation set vs
the 30,000 s hard limit. Fully offline: Python stdlib + Pillow +
Tesseract. No models, no network, no LLM/VLM anywhere.

## Architecture: establish what the packet says, then decide

Seven single-responsibility layers. **L1–L5 establish what the packet
says; L6–L7 decide what to do about it.** Trust is decided exactly once,
at L3, and never re-litigated.

L1 enumerates PDF sources (text streams + images) with stdlib regex
parsing (validated: 0 missed streams). L2 decodes text and OCRs images
with a *pooled* multi-pass strategy — baseline psm 6, 2× upscale psm 3,
sharpened, sparse-text psm 11 — whose outputs are concatenated, so
whichever rendering read a field legibly wins at extraction. Geometry
passes (whole-page rotation, projection-profile deskew) exist behind
evidence triggers: a thumbnail ink-profile must first give a *reason*
(column-dominant profile = sideways text; variance peak at ±N° = real
tilt) before any Tesseract call is spent. L3 is the trust boundary:
sanitizers strip prompt-injection lines (ambient — 98% of packets carry
`SYSTEM:` payloads) and redaction placeholders; a domain-vocabulary
detector excludes illegible OCR (19% of images). L4 runs the canonical
field extractor over combined text, per-stream, and per-image content,
emitting typed Signals weighted by the Field Manual's evidence hierarchy
(adjudicator note → intake → biometric → attestation → registry → bare
text layer), with format-validated fuzzy recovery only where strict
extraction missed. L5 consolidates: highest-authority signal wins;
disagreements and per-field provenance (text / OCR-only / absent) are
preserved for the decision layers.

L6 is a strict-priority rule chain: a signed adjudicator Finding trumps
everything (162 cases, 100% correct); standalone hard-denies fire on
minimum sufficient evidence *before* the extraction-failure fallback, so
a half-readable packet can still be denied on the readable half. L7
applies nine named policy stages — three upgrades (better evidence
overturns a verdict: a cleanly-read biometric slip, an explicit Finding
in image OCR), one trust bypass (human findings are exempt from
form-derived suspicion), five guards (demote APPROVED→REVIEW only; the
system cannot manufacture a denial from suspicion). Every confidence is
a lookup in one registry whose values are measured per-rule accuracies —
Brier-optimal by construction — and a completeness test makes an
uncalibrated confidence unshippable.

## Decisions shaped by reading the scorer

The −4 false-approval penalty makes NEEDS_REVIEW the correct hedge more
often than intuition suggests; every rule's emitted action was verified
EV-optimal against its measured truth distribution. Every packet always
emits a schema-valid record (the missing-case penalty is never worth
taking). Confidence equals measured bucket accuracy — including honest
low values (0.34 on the extraction-failure fallback) where the evidence
is genuinely absent.

## Measurement discipline

The system was rebuilt as a standalone package and proven **byte-identical**
to its predecessor on all 1,000 packets before any improvement was
attempted — aggregate-score parity provably hides compensating errors
(the same code produces 145 different rows under two Tesseract versions
while the score moves 0.10). Every candidate improvement since ran the
same gauntlet: full-set measurement, fixed 800/200 split (never tuned on
val), cat-FA count, and per-case diff attribution. Accepted: the psm 11
pool pass (+0.37 train / +0.90 val — val outgaining train). Rejected and
retained as documented negatives: fee-label fuzzy recovery (train +0.11 /
val −0.09, and 83% of fee-missing packets carry no fee text at all),
whole-page rotation and deskew pool passes (measured at their ungated
ceilings: +0.02 and −0.01 — the evidence isn't there), threshold changes
to safety guards, and every rule keyed on absence of evidence. The dominant residual loss is measured to be *evidence-absent*:
risk panels and fee receipts removed by the generator (the two fields are
95% of extraction loss), and ~15 denials whose disqualifying flag appears
nowhere visible — priced honestly rather than guessed at.

## Known limits

Cross-engine OCR drift (Tesseract 5.5.0 vs 5.5.1) moves 145/1000 rows
and 7 verdicts; the container is the canonical artifact. 6.1% of images
sit within ±0.05 of the illegibility threshold — the main driver of that
drift. The ambiguous middle (fallback bucket, 251 cases at 34%) is
bounded by evidence, not calibration.

## Reproducibility

`git clone` → `docker build` → run; entrypoint is the contract's
`<input_dir> <output_path>`. 155 unit tests; committed golden outputs
with a byte-parity gate (`parity.sh`); every measurement in the memo has
a committed script under `v3/dev/analysis/` and a dated entry in
`v4/OBSERVATIONS.md` — accepted and rejected experiments alike.
