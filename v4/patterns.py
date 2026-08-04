#!/usr/bin/env python3
"""Compiled patterns — the regexes and word lists shared across layers.

Single-consumer patterns stay with their consumer (redaction/illegibility
regexes in filters/, source-type markers in source_type.py, OCR signal
regexes in evidence.py) — this module holds what more than one layer reads.

DOES: define (1) PDF structural regexes, (2) injection-marker strings,
(3) field-label extraction regexes, (4) adjudicator-note regexes, and
(5) the OCR word lists, each with its empirical basis.

DOES NOT: perform any matching — extraction logic lives with its layer
(L2 extract.py, L4 signals.py, evidence.py). No pattern here encodes a
decision; decisions belong to L6/L7.

Transcribed from v1/solution.py (line refs in comments). CASE_ID_RE and
VISA_RE were dropped: provably unreferenced by the live call graph
(bucket-1 dead code, spec §6).
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# PDF structure (v1:94-110)
# ---------------------------------------------------------------------------

# Match a PDF dict-then-stream: << ... >>\nstream\n<data>\nendstream
# Using [^>]* in the dict prevents accidentally matching through nested
# dicts (e.g. /Trans <<>> inside a Page dict). PDF stream-dicts contain
# simple entries (Filter, Length, Type) with no nested <<>>.
# Empirically validated: 0 missed streams across 1000 training PDFs.
STREAM_RE = re.compile(
    rb"<<(?P<dict>[^>]*)>>\s*stream\r?\n(?P<data>.*?)(?:\r?\n)?endstream",
    re.DOTALL,
)
FILTER_RE = re.compile(rb"/(\w+)Decode")
# PDF text-showing operator: (text) Tj
# Empirical survey of decoded content streams across all 1000 training PDFs:
#   Tj: 36,905 ops (100% of PDFs)
#   TJ array, ', ", hex-string Tj: 0 occurrences
# So we only need the simple form. ReportLab does not emit kerned arrays for
# this dataset — every text-showing op is a single-string Tj.
TEXT_OP_RE = re.compile(rb"\(((?:[^()\\]|\\.)*)\)\s*Tj")

# ---------------------------------------------------------------------------
# Prompt-injection signatures (v1:152-158)
# ---------------------------------------------------------------------------

# These lines are adversarial content trying to trick LLM-based extractors
# into approving. Filtered from extracted text before field extraction so
# they can't pollute our regexes.
INJECTION_MARKERS = (
    "SYSTEM:", "IGNORE VISIBLE", "IGNORE ALL", "ANSWER KEY", "OUTPUT THIS",
    "OVERRIDE", "DO NOT FOLLOW", "INSTRUCTIONS:",
)

# ---------------------------------------------------------------------------
# Field-label extraction (v1:219-289)
# ---------------------------------------------------------------------------

SPONSOR_RE = re.compile(r"SPN-\d{4}")
ISO_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")

# Prefer the visa value that immediately follows a "Visa Class" label —
# packets contain both the intake-form radio-button list AND the selected
# value, and _find_vocab would return whichever the alphabet loop hits
# first. Look for the labeled value first, fall back to scanning.
VISA_LABEL_RE = re.compile(
    r"(?:^|\n)\s*(?:visa\s*class|visa)\s*[:=\n]\s*(XW-[12]|DIP-1|MED-3|TRANSIT-7)\b",
    re.IGNORECASE,
)

# Field-label patterns — ReportLab writes "Label\nValue" as two separate
# text ops joined by newlines in our extraction. Accept both colon-form
# and newline-form.
NAME_LABEL_RE = re.compile(
    r"(?:^|\n)(?:applicant|applicant\s*name|full\s*name)\s*[:=\n]\s*([^\n\r]+)",
    re.IGNORECASE,
)
# Fallback name sources — used only when the primary Applicant label misses
# or resolves to a placeholder. Ordering reflects trustworthiness measured
# on training: Registry Name matches truth 100% of the sampled recoveries;
# Sponsor "attests that" matches truth on the ~20 sampled cases.
REGISTRY_NAME_RE = re.compile(
    r"(?:^|\n)\s*Registry\s*Name\s*[:\n]\s*([^\n\r]+)",
    re.IGNORECASE,
)
SPONSOR_ATTESTS_RE = re.compile(
    r"Sponsor\s+(?:SPN-\d{4}|\[SPONSOR\s+ID\s+BLANK\])\s+attests\s+that\s+"
    r"([A-Z][\w'-]+(?:\s+[A-Z][\w'-]+){1,3})",
)
PURPOSE_LABEL_RE = re.compile(
    r"(?:^|\n)(?:declared\s*purpose|purpose)\s*[:=\n]\s*([^\n\r]+)",
    re.IGNORECASE,
)
# Sponsor-letter fallback: "is expected on Earth for <purpose>."
# DOTALL so multi-word purposes can span newlines in the extracted text
# (ReportLab's layout often splits "reactor maintenance" across two text
# ops). Capture stops at the sentence-boundary period.
SPONSOR_PURPOSE_RE = re.compile(
    r"is\s+expected\s+on\s+Earth\s+for\s+([^.]+?)\s*\.",
    re.IGNORECASE | re.DOTALL,
)
FEE_LABEL_RE = re.compile(
    r"(?:^|\n)(?:fee\s*status|fee)\s*[:=\n]\s*(paid|waived|unpaid|unknown)\b",
    re.IGNORECASE,
)
# Fee receipt cross-reference fields — Amount and Waiver Code triangulate
# against the (adversarial) stated Fee Status label. See _extract_fee.
AMOUNT_LABEL_RE = re.compile(
    r"(?:^|\n)\s*Amount\s*[:\n]\s*\$?([\d,]+(?:\.\d+)?)",
    re.IGNORECASE,
)
WAIVER_CODE_RE = re.compile(
    r"(?:^|\n)\s*Waiver\s*Code\s*[:\n]\s*([^\n\r]+)",
    re.IGNORECASE,
)
FLAG_LABEL_RE = re.compile(
    r"(?:^|\n)(?:risk\s*flags?|flags)\s*[:=\n]\s*([^\n\r]+)",
    re.IGNORECASE,
)

# Bracketed redaction placeholders that appear in the text stream — e.g.
# "[NAME CUT OUT]", "[PURPOSE ILLEGIBLE]", "[SPONSOR ID BLANK]". These are
# not data values; they mean "this field was intentionally removed". Treat
# them as extraction failure (return ""), matching the decision-safety
# principle: no positive evidence should be manufactured from a placeholder.
PLACEHOLDER_RE = re.compile(r"^\s*\[[^\]]*\]\s*$")

# ---------------------------------------------------------------------------
# Adjudicator notes (v1:442-457)
# ---------------------------------------------------------------------------

# Adjudicator finding — evidence precedence #1 per FIELD_MANUAL. When
# present, this is the ground truth for the case's adjudication.
FINDING_RE = re.compile(
    r"Finding:\s*(APPROVED|DENIED|NEEDS[_\s]REVIEW)",
    re.IGNORECASE,
)

# Adjudicator manual correction — overrides the corresponding form field.
# Observed phrasings: "Manual correction: sponsor is SPN-####",
# "...applicant is <name>", "...visa class is <value>", "...fee status is <value>"
CORRECTION_RE = re.compile(
    r"Manual\s*correction:\s*"
    r"(sponsor|applicant|visa\s*class|fee\s*status)\s+is\s+"
    r"([^\n\r.]+)",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# OCR word lists — TWO lists, deliberately NOT merged
# ---------------------------------------------------------------------------

# The brief asked for "one canonical set", but these are divergent, not
# duplicated (spec §4.4). DECISION_* stems are UPPERCASE substrings matched
# against uppercased OCR text to make L7 verdicts (v3/ocr_signal.py:82-83);
# DIAGNOSTIC_PROBES are lowercase whole-ish words matched against lowercased
# OCR text to emit boolean features for dev analysis only
# (v3/dev/analysis/extract_features.py:45-49). Different casing, different
# matching semantics, different consumers — merging them changes what fires.
# Do not unify.

# Stems split by semantic weight: stems naming a specific disqualifying risk
# category route to DENIED (presence in OCR is direct evidence of that
# risk); stems that could appear in adjudicator prose stay at REVIEW.
# DENIE promoted to the DENY side: ocr_has_denied=True → 100% truth-DENIED
# on 45 training cases.
DECISION_DENY_STEMS = ("BIOHAZ", "EMBARG", "DENIE")
DECISION_REVIEW_STEMS = ("REVOK", "RESCIN")

# Dev-only diagnostic probes (kept here so the bucket-2 tooling port has
# one canonical home for them; no production consumer).
DIAGNOSTIC_PROBES = (
    "biohazard", "tampering", "warrant", "embargo", "illegible",
    "identity", "sponsor mismatch", "rescinded", "denied", "review",
    "revoked", "finding", "denial", "manual", "adjudicator",
)
