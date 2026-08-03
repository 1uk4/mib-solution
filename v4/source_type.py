#!/usr/bin/env python3
"""Source authority classifier — Field Manual precedence tier per Source.

FIELD_MANUAL.md section "Trusted Evidence" defines this precedence for
resolving conflicts between documents in a packet:

    L1  visible MIB adjudicator stamp or signed manual note   (highest)
    L2  visible intake form fields
    L3  visible biometric slip
    L4  visible sponsor attestation
    L5  visible registry extract
    L6  machine-readable text layer                           (lowest)

L1-L5 are what a human SEES on the rendered page — reachable through image
OCR AND through the underlying text stream that renders those documents.
L6 is text present only in the machine-readable layer, without a visible
document behind it (typically packet titles, page-N headers, hidden decoys).

`classify(source)` returns the level integer 1..6. Higher authority = lower
integer. Detection is marker-based: each level has a set of unambiguous
title / phrase markers that a real document of that type contains.

Design principle: markers must be EMPIRICALLY VALIDATED (see
v3/dev/analysis/source_type_audit.py) before the level is used to weight
Signal confidence. Adding new markers without audit risks silently
elevating wrong sources.

Extended one level at a time, starting from L1. Levels not yet implemented
here return L6 (safe default — treats unclassified as lowest authority).
"""
from __future__ import annotations

import re

from v4.acquire import Source


# ---------------------------------------------------------------------------
# Level markers — case-insensitive; OCR-tolerant where possible.
# Every marker set has been audited empirically on the 1000-PDF training
# set via v3/dev/analysis/source_type_audit.py before being added here.
# ---------------------------------------------------------------------------

# L1: adjudicator stamp / signed manual note
#   "Manual Adjudicator Note"  — title seen 232× (plus ~40 OCR-mangled variants)
#   "Manual correction:"       — V1 already treats these as field override
#                                 (see _apply_manual_corrections in v1/solution.py)
#   "Finding: <verdict>"       — 100% precision in ocr_signal_audit (n=77)
# OCR tolerance for the title uses a fuzzy substring of "Adjudicator" which
# has enough unique letters to remain distinctive even with typical
# Tesseract errors ("Adjt", "Adudcator", "Adjudicetor" all match).
_L1_TITLE_RE = re.compile(r"adjudi[a-z]{0,3}[ct][a-z]{0,3}or\s+note", re.IGNORECASE)
_L1_INLINE_RE = re.compile(
    r"\bmanual\s+correction\s*:|"
    r"\bfinding\s*[:.\-—]?\s*(APPROVED|DENIED|DENIE|NEEDS[_\s\-—]?REVIEW|REJECT)",
    re.IGNORECASE,
)

# L2: intake form (FORM I-8090)
#   "FORM I-8090: Extraterrestrial Work Authorization Intake" — 474 clean +
#   ~35 OCR variants where "I" is misread ("|", "1", "[", "l"). The phrase
#   "Extraterrestrial Work Authorization" is uniquely distinctive and
#   survives OCR far better than the form-number prefix.
_L2_MARKER_RE = re.compile(
    r"extraterrestrial\s+work\s+authorization|"
    r"form\s*[|1I\[l]\s*[-–—]?\s*8090",
    re.IGNORECASE,
)

# L3: biometric slip (FORM B-13)
#   "FORM B-13: Biometric Scan Slip" — 361 clean matches
#   OCR variant "Blometric" (i→l) — very common Tesseract error on "Bi"
_L3_MARKER_RE = re.compile(
    r"b[il]ometric\s+scan\s+slip|form\s+b[-–—]?13",
    re.IGNORECASE,
)

# L4: sponsor attestation
#   "Sponsor Attestation Letter" — 364 clean + 21 with a stray leading quote
_L4_MARKER_RE = re.compile(r"sponsor\s+attestation", re.IGNORECASE)

# L5: planetary registry extract
#   "Planetary Registry Extract" — 525 clean matches
_L5_MARKER_RE = re.compile(r"planetary\s+registry\s+extract", re.IGNORECASE)


def classify(source: Source) -> int:
    """Return the Field Manual authority level (1..6) for a Source.

    Precedence check runs top-down (L1 first): the highest-authority marker
    that fires wins. A document containing both intake-form fields and an
    inline "Manual correction:" is classified L1 because the correction is
    the authoritative statement in it. Returns 6 when no marker fires —
    the safe default treats unrecognized sources as lowest authority.
    """
    text = source.content or ""
    if not text.strip():
        return 6

    if _L1_TITLE_RE.search(text) or _L1_INLINE_RE.search(text):
        return 1
    if _L2_MARKER_RE.search(text):
        return 2
    if _L3_MARKER_RE.search(text):
        return 3
    if _L4_MARKER_RE.search(text):
        return 4
    if _L5_MARKER_RE.search(text):
        return 5

    return 6
