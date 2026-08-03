#!/usr/bin/env python3
"""IllegibilityDetector — flags images whose OCR output is mostly garbage.

Purpose: pages that are physically degraded (heavy fading, coffee stains,
low contrast) produce OCR text that is jumbled non-words. Trusting that
content risks feeding false OCR signals to L7. Excluding it costs nothing
because there's no real content to lose.

Detection: real-word ratio. Each OCR token is classified as recognized
(matches domain vocabulary, or is a date / ID pattern) or unrecognized.
If ratio < 0.25 with at least 5 tokens, the source is untrusted.

Vocabulary is small and domain-specific — form labels, closed-vocabulary
enums (species, home worlds, visa classes, flags), common English function
words. NOT a general English dictionary — that would over-match on random
character sequences and defeat the detector.

Applies only to IMAGE sources. Text streams have controlled vocabulary
(legitimate form content or injection payloads — both known patterns).

Empirical thresholds validated on:
- MIB-000787 image_3 (heavily degraded sponsor letter): 12% real → flag
- MIB-000078 image_3 (legit FORM B-13 with archival stamps): 60%+ real → keep
- MIB-000115 image_2 (legit adjudicator note): 40%+ real → keep
"""
import re

from v4.acquire import Source, IMAGE
from v4.vocab import (
    SPECIES,
    HOME_WORLDS,
    VISA_CLASSES,
    FEE_VALUES,
    DISQUALIFYING_FLAGS,
    REVIEW_ONLY_FLAGS,
)


def _build_vocabulary() -> set[str]:
    """Assemble the domain vocabulary used to judge OCR legibility."""
    v: set[str] = set()

    # Form-field labels (words that appear in printed labels)
    v.update({
        "applicant", "species", "code", "home", "world", "visa", "class",
        "sponsor", "id", "arrival", "date", "declared", "purpose", "observed",
        "flags", "flag", "fee", "status", "amount", "waiver", "case", "name",
        "registry", "extract", "primary", "intake", "record", "passport",
        "image", "scan", "confidence", "match", "finding", "reason", "note",
        "adjudicator", "manual", "correction", "form", "notes", "biometric",
    })

    # Document titles / template chrome
    v.update({
        "mib", "form", "planetary", "receipt", "attestation", "letter",
        "eyes", "only", "synthetic", "hiring", "challenge", "document",
        "packet", "page", "extraterrestrial", "work", "authorization",
        "i-8090", "b-13", "cleared", "clear", "pending",
    })

    # Closed-vocab enums (lowercased for case-insensitive matching)
    for s in SPECIES:
        v.add(s.lower())
    for h in HOME_WORLDS:
        for word in h.lower().replace("-", " ").split():
            v.add(word)
    for c in VISA_CLASSES:
        v.add(c.lower())
    for fv in FEE_VALUES:
        v.add(fv.lower())
    for f in DISQUALIFYING_FLAGS | REVIEW_ONLY_FLAGS:
        v.add(f.lower())
        for part in f.replace("_", " ").split():
            v.add(part)

    # Adjudication vocabulary (including OCR-truncated variants)
    v.update({
        "approved", "denied", "denie", "needs", "review",
        "reject", "rejected", "revoked", "rescinded",
    })

    # Common English function words that appear in prose OCR
    v.update({
        "the", "and", "of", "at", "for", "is", "in", "to", "a", "on",
        "with", "not", "no", "or", "from", "by", "was", "be", "this",
        "has", "have", "that", "as", "an", "are", "will",
    })

    # Archival / status words we've observed
    v.update({
        "copy", "filed", "archive", "archived", "void", "redacted",
        "casework", "blank", "illegible",
    })

    return v


_VOCAB = _build_vocabulary()
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_ID_RE = re.compile(r"(?:MIB|SPN)-\d+")

_MIN_TOKENS = 5           # below this: too little content to judge
# 30% threshold: MIB-000787 image_3 (heavily degraded, 27.78% real) flagged;
# MIB-000078 image_3 (65% real), MIB-000115 image_2 (73% real) safely kept.
# Empirically validated below 30% never contains extractable field values.
_MIN_REAL_RATIO = 0.30


def detect_illegibility(source: Source) -> tuple[bool, str]:
    """Return (True, reason) if OCR output is dominated by non-vocabulary tokens."""
    if source.type != IMAGE or not source.content:
        return False, ""

    text = source.content
    tokens = _TOKEN_RE.findall(text)
    n_dates = len(_DATE_RE.findall(text))
    n_ids = len(_ID_RE.findall(text))
    total = len(tokens) + n_dates + n_ids

    if total < _MIN_TOKENS:
        return False, ""  # too little content to judge

    recognized = n_dates + n_ids
    for tok in tokens:
        if tok.lower() in _VOCAB:
            recognized += 1

    ratio = recognized / total if total else 0
    if ratio < _MIN_REAL_RATIO:
        return True, f"real_word_ratio:{ratio:.2f} ({recognized}/{total})"
    return False, ""
