#!/usr/bin/env python3
"""L4 — Signal extraction.

Consumes TRUSTED Sources from L3 and emits typed `Signal` records for
L5 to consolidate. Also passes through raw image-OCR text for L7's
OCR-override policy (kept for backward compatibility until L7 is
refactored to consume Signals too).

The Signal record is the substrate for cross-reference at L5:
    - Same (type, key) from multiple sources → group, resolve, weight
    - Conflicts → detected, not silently overwritten
    - Provenance → every value traces back to a specific source

Phase D-1 (this file): minimal refactor. V1's `extract_fields` still runs
on the combined trusted text and its results are wrapped as Signals.
Behavior is byte-identical to the previous L4. This locks in the
Signal-based interface without changing extraction logic.

Later phases:
    D-2: emit Signals per-source (each text stream and each image
         individually) so L5 can see conflicts.
    D-3: L5 does real cross-reference / trust weighting.
    D-4: field-extraction Signals from image OCR (currently only OCR
         used at L7 for risk-signal check).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date as Date

from difflib import SequenceMatcher

import os

from v1.solution import extract_fields, DISQUALIFYING_FLAGS, REVIEW_ONLY_FLAGS
from v3.acquire import Source, TEXT_STREAM, IMAGE
from v3.normalize import value as _normalize_value
from v3.reocr import repair as _reocr_repair
from v3.source_type import classify as classify_source

_NORMALIZE_ENABLED = os.environ.get("MIB_NORMALIZE_VALUES", "1") != "0"
_REOCR_ENABLED = os.environ.get("MIB_CHAR_WHITELIST_REOCR", "1") != "0"


def _norm(key: str, raw: str) -> str:
    """Env-gated normalizer wrapper. When MIB_NORMALIZE_VALUES=1, apply
    field-specific normalization; otherwise pass value through untouched."""
    if _NORMALIZE_ENABLED and raw:
        return _normalize_value(key, raw)
    return raw


def _norm_and_repair(key: str, raw: str, source: "Source") -> str:
    """Normalize; if a structured field still fails validation, char-whitelist re-OCR."""
    normed = _norm(key, raw)
    if _REOCR_ENABLED and key in ("sponsor_id", "arrival_date"):
        repaired = _reocr_repair(source, key, normed)
        if repaired:
            return repaired
    return normed


# ---------------------------------------------------------------------------
# Signal record
# ---------------------------------------------------------------------------


@dataclass
class Signal:
    """A typed piece of evidence extracted from one source.

    Fields:
      type       kind of signal — 'FIELD_VALUE', 'ADJUDICATOR_FINDING',
                 'FLAG_DECLARATION', 'RAW_TEXT' etc.
      key        for FIELD_VALUE: field name ('applicant_name' etc.)
                 for other types: empty or type-specific
      value      the extracted content
      source_id  ID of the Source this came from (e.g. 'text_stream_2')
      confidence in [0, 1]; used by L5 for weighting conflicts
      tag        optional audit string (which extractor / pattern hit)
    """
    type: str
    key: str
    value: str
    source_id: str
    confidence: float = 1.0
    tag: str = ""


# Signal type constants (avoid stringly-typed bugs)
FIELD_VALUE = "FIELD_VALUE"
ADJUDICATOR_FINDING = "ADJUDICATOR_FINDING"
FLAG_DECLARATION = "FLAG_DECLARATION"


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


# Which V1-extracted fields become FIELD_VALUE signals
_FIELD_KEYS = (
    "applicant_name", "species_code", "home_world", "visa_class",
    "sponsor_id", "arrival_date", "declared_purpose", "risk_flags",
    "fee_status",
)


# Per-source Signal confidence by Field Manual authority level. Higher
# authority (lower level number) = higher confidence, so at L5 consolidation
# a higher-authority source outranks a lower one when they disagree.
#
# This encodes FIELD_MANUAL.md "Trusted Evidence" precedence:
#     L1 adjudicator note / manual correction    (highest)
#     L2 intake form
#     L3 biometric slip
#     L4 sponsor attestation
#     L5 registry extract
#     L6 unrecognized text (default)             (lowest)
#
# combined_text remains at 1.0 above the L1 floor — V1's canonical
# extractor runs on the merged text and already applies cross-stream
# "Manual correction:" overrides that per-source signals can't replicate
# alone. Per-source signals only outrank combined_text if BOTH is L1 AND
# combined_text extracted something inconsistent, which is rare.
_LEVEL_CONFIDENCE: dict[int, float] = {
    1: 0.99,
    2: 0.90,
    3: 0.85,
    4: 0.80,
    5: 0.75,
    6: 0.70,
}


# --- Fuzzy flag matching for OCR-derived risk_flags ---
# V1's flag detector requires exact `\bbiohazard_red\b`. OCR often mangles
# the separator: reads underscore as space, hyphen, em-dash, or drops it
# entirely. This detector relaxes the separator between flag components so
# "biohazard red", "biohazard-red", "biohazardred" all match.
# Only used for IMAGE sources; text streams keep V1's strict matching.
_FUZZY_FLAG_CACHE: dict[str, "re.Pattern"] = {}


def fuzzy_flag_pattern(flag: str) -> "re.Pattern":
    """Compile a case-insensitive fuzzy pattern for a flag word.

    Public helper — used both here (per-image fuzzy flag detection at L4)
    and in v3.ocr_signal (fuzzy risk-signal check at L7).

    Replaces the underscore separator with a character class that also
    accepts space, hyphen, em-dash, or nothing at all. So "biohazard_red"
    matches "biohazard_red", "biohazard red", "biohazard-red", and
    "biohazardred" — the common OCR-mangled variants.

    Word-boundary anchors on both sides prevent partial-word matches
    like "biohazard_red_flag" (which shouldn't count as clean evidence).

    Note: `re.escape` doesn't escape underscore in Python 3.7+, so we
    replace the literal `_` directly.
    """
    if flag not in _FUZZY_FLAG_CACHE:
        escaped = re.escape(flag).replace("_", r"[_ \-—]?")
        _FUZZY_FLAG_CACHE[flag] = re.compile(
            rf"\b{escaped}\b", re.IGNORECASE
        )
    return _FUZZY_FLAG_CACHE[flag]


def _fuzzy_flags_from_ocr(text: str) -> str:
    """Return pipe-delimited sorted flags found via fuzzy match, or ''."""
    if not text:
        return ""
    found: set[str] = set()
    for flag in DISQUALIFYING_FLAGS | REVIEW_ONLY_FLAGS:
        if fuzzy_flag_pattern(flag).search(text):
            found.add(flag)
    return "|".join(sorted(found)) if found else ""


# Whole-string anchored regexes — reject values with any trailing garbage.
# Names (alien Firstname Lastname): 2+ capitalized words, letters only.
_NAME_RE = re.compile(r"^[A-Z][A-Za-z]+(?: [A-Z][A-Za-z]+)+$")
# Purposes: 1+ lowercase words separated by single spaces.
_PURPOSE_RE = re.compile(r"^[a-z]+(?: [a-z]+)*$")
# Sponsor ID: strict SPN-#### format (belt-and-suspenders; V1 already enforces).
_SPONSOR_ID_RE = re.compile(r"^SPN-\d{4}$")

# --- Fuzzy sponsor_id repair for OCR digit-lookalike misreads ---
# OCR routinely substitutes letters for digits (O→0, l→1, B→8, etc.).
# V1's SPONSOR_RE requires exact SPN-\d{4}, so mangled reads produce no
# extraction. The regex below finds SPN- followed by exactly 4 chars that
# could plausibly be digits; the translation table restores them.
#
# Universal-rule discipline: no reference to any known-good SPN list. The
# corrected value only needs to fit the SPN-\d{4} shape — the 4-digit space
# is 10000 and only 864 SPN values appeared in training, so restricting to
# the seen set would silently overfit and drop valid unseen sponsors.
_SPONSOR_DIGIT_SUBST = str.maketrans({
    "O": "0", "o": "0", "D": "0", "Q": "0",
    "I": "1", "l": "1", "i": "1", "|": "1",
    "Z": "2", "z": "2",
    "S": "5", "s": "5",
    "G": "6", "b": "6",
    "T": "7",
    "B": "8",
    "g": "9", "q": "9",
})
# Separator tolerance: ASCII hyphen, en-dash, em-dash (OCR sometimes emits
# a Unicode dash where a hyphen was printed). The 4-char capture group
# accepts any digit or digit-lookalike letter; the trailing negative
# lookahead prevents grabbing part of a longer alphanumeric run.
_FUZZY_SPN_RE = re.compile(r"\bSPN[-–—]([0-9A-Za-z|]{4})(?![0-9A-Za-z])")


def _fuzzy_extract_sponsor(text: str) -> str:
    """Restore SPN-#### from OCR text whose digits were read as letters.

    Returns the first restorable SPN-#### found, or '' if none. Skips
    matches whose 4-char body is already all digits — V1's strict
    extractor would have caught those, so re-emitting adds nothing.
    """
    if not text:
        return ""
    for match in _FUZZY_SPN_RE.finditer(text):
        raw = match.group(1)
        if raw.isdigit():
            continue
        corrected = raw.translate(_SPONSOR_DIGIT_SUBST)
        if corrected.isdigit():
            return f"SPN-{corrected}"
    return ""


# --- Generic fuzzy-label extractor ---
# Labels in this dataset are rendered from a fixed template — every packet
# has the exact same set of label strings ("Fee Status:", "Visa Class:",
# "Sponsor ID:", ...). OCR routinely mangles the labels ("Fee Status" →
# "“ee Status", "lee Status") but the intent is always the same field.
# Fuzzy-matching on the KNOWN template label is safe: no ambiguity about
# what field is being labeled.
#
# Value handling is field-specific and up to the caller:
#   * enum passed  → snap value to closest enum entry; return "" if no
#                    entry matches at `enum_threshold` (rejects garbage
#                    rather than emitting a bad value)
#   * enum omitted → return raw value verbatim (safe for open-ended
#                    fields; caller decides what to do with it)
#
# NEVER pass an enum for a field that isn't explicitly enumerated in the
# Field Manual (home_world, applicant_name, sponsor_id) — snapping open
# values to a training-set list is overfitting; see the "Fuzzy extraction
# discipline" memory note.


def _fuzzy_label_extract(
    text: str,
    label: str,
    enum: list[str] | None = None,
    label_threshold: float = 0.72,
    enum_threshold: float = 0.72,
) -> str:
    """Find `label` fuzzily in text, extract the value that follows.

    Slides a window of length near `label` through the text, scoring each
    against `label` via SequenceMatcher. If the best window scores at least
    `label_threshold`, extracts the substring after that window (skipping
    a colon/dash separator) up to the next line break as the value.

    If `enum` is provided, snaps the value to the closest enum entry when
    similarity meets `enum_threshold`. Returns "" if no enum entry matches
    (rejects garbage rather than passing through a mangled value).

    Returns the extracted (and optionally snapped) value, or "" if nothing
    was found.
    """
    if not text:
        return ""
    label_lo = label.lower()
    llen = len(label_lo)
    text_lo = text.lower()

    # Locate best label position — sweep windows sized near |label|.
    best_score = 0.0
    best_end = -1
    for wsize in range(max(3, llen - 2), llen + 3):
        for i in range(0, len(text) - wsize + 1):
            window = text_lo[i:i + wsize]
            score = SequenceMatcher(None, window, label_lo).ratio()
            if score > best_score:
                best_score = score
                best_end = i + wsize

    if best_score < label_threshold or best_end < 0:
        return ""

    # Grab the value following the label (up to 60 chars, one line).
    rest = text[best_end:best_end + 80]
    m = re.match(r"[\s:.\-—]*([^\n\r|\[\]]{1,60})", rest)
    if not m:
        return ""
    value = m.group(1).strip()
    if not value:
        return ""

    if enum is None:
        return value

    # Snap value to closest enum entry.
    value_lo = value.lower()
    # Exact substring hit wins immediately.
    for e in enum:
        if e.lower() in value_lo:
            return e
    # Otherwise fuzzy-match; require score ≥ enum_threshold.
    best_enum_score = 0.0
    best_enum = ""
    for e in enum:
        score = SequenceMatcher(None, value_lo, e.lower()).ratio()
        if score > best_enum_score:
            best_enum_score = score
            best_enum = e
    return best_enum if best_enum_score >= enum_threshold else ""




def _valid_field_value(key: str, value: str) -> bool:
    """Reject syntactically-formatted but semantically-invalid values.

    Runs at signal-emission time. Values failing validation don't get
    emitted as Signals — the field ends up empty (default) rather than
    populated with garbage. This is the "reject rather than sanitize"
    strategy: safer than trying to strip trailing OCR garbage.

    Applied to both text-stream AND image-OCR extractions. Legitimate
    text-stream values should always pass these checks; if they don't,
    we'd rather lose the extraction than propagate mangled data.

    Also filters risk_flags='none' — that's V1's default meaning "no
    flag word found in this source," not "actively no flag." Emitting it
    would create spurious conflicts at L5 when other sources DO report
    flags (this source's silence isn't a disagreement, just missing info).

    Fields not covered here have extraction patterns that are already
    strict enums or format-constrained (fee_status, visa_class,
    species_code, home_world), so their extractor output is guaranteed
    well-formed by construction.
    """
    if not value:
        return False
    v = value.strip()
    if not v:
        return False

    # Silent-absence sentinel for risk_flags — don't treat as evidence.
    if key == "risk_flags" and v == "none":
        return False

    if key == "arrival_date":
        try:
            Date.fromisoformat(v)
        except (ValueError, TypeError):
            return False
        return True

    if key == "applicant_name":
        return bool(_NAME_RE.match(v))

    if key == "declared_purpose":
        return bool(_PURPOSE_RE.match(v))

    if key == "sponsor_id":
        return bool(_SPONSOR_ID_RE.match(v))

    # Other fields are enum-restricted by V1's extractors; accept as-is.
    return True


# --- Per-field fuzzy label config ---
# Every field with a template-generated label gets a fuzzy-recovery pass.
# `enum` is populated ONLY when the Field Manual explicitly enumerates the
# value set. Fields whose values are open or format-strict (sponsor_id,
# arrival_date) pass enum=None — _fuzzy_label_extract returns the raw
# value, which then goes through _valid_field_value for format checking.
#
# sponsor_id is handled separately (see _fuzzy_extract_sponsor above) —
# its OCR failure mode is digit-lookalike character substitution, which
# needs a specialized restorer beyond generic label extraction.
_VISA_ENUM = ["XW-1", "XW-2", "DIP-1", "MED-3", "TRANSIT-7"]
_FEE_ENUM = ["paid", "waived", "unpaid"]
_FLAG_ENUM = sorted(DISQUALIFYING_FLAGS | REVIEW_ONLY_FLAGS)

_FUZZY_LABEL_CONFIG: list[tuple[str, str, list[str] | None]] = [
    # (field_key, template_label, enum_or_None)
    ("visa_class",       "Visa Class",       _VISA_ENUM),
    ("home_world",       "Home World",       None),  # open per Manual
    ("species_code",     "Species Code",     None),  # open per Manual
    ("applicant_name",   "Applicant",        None),  # open (alien names)
    ("arrival_date",     "Arrival Date",     None),  # format-validated
    ("declared_purpose", "Declared Purpose", None),  # open
    ("risk_flags",       "Risk Flags",       _FLAG_ENUM),
]


def _fuzzy_label_signals(
    signals: list["Signal"],
    img_src: Source,
    img_fields: dict,
    conf: float,
    level: int,
) -> None:
    """Append fuzzy-label Signals for any labeled field V1 didn't extract.

    Runs the generic _fuzzy_label_extract for each entry in
    _FUZZY_LABEL_CONFIG. Only emits when V1's per-image extraction
    returned empty or "unknown" for that key — no point competing with
    a definitive per-image value.

    Fee_status has its own dedicated block above (special empty/unknown
    condition) so it's not repeated here.
    """
    for key, label, enum in _FUZZY_LABEL_CONFIG:
        v1_value = img_fields.get(key, "")
        if v1_value and v1_value.strip().lower() not in ("", "unknown"):
            continue  # V1 got a definitive value; don't add a fuzzy signal
        value = _fuzzy_label_extract(
            img_src.content, label, enum=enum,
        )
        if not value:
            continue
        if not _valid_field_value(key, value):
            continue
        signals.append(Signal(
            type=FIELD_VALUE, key=key, value=_norm_and_repair(key, value, img_src),
            source_id=img_src.id, confidence=conf,
            tag=f"image_ocr_fuzzy_{key}:{value[:20]}",
        ))


def extract_signals(sources: list[Source]) -> dict:
    """Bundle trusted-source content into signals + raw OCR pass-through.

    Returns:
        {
            'signals': list[Signal],   # typed evidence for L5 to consolidate
            'image_ocr': list[(source_id, ocr_text)],  # kept for L7 policy
        }

    Phase D-2: Signals come from two source classes with different confidence:
      - Combined text-stream content: extracted via V1's extract_fields,
        source_id='combined_text', confidence=1.0 (authoritative form data)
      - Each image's OCR text: extracted via V1's extract_fields, source_id
        =image_N, confidence=0.7 (OCR-derived; can misread, but fills gaps
        when text streams have no value for a field)

    L5 currently picks the highest-confidence Signal per key, so text-stream
    signals always win ties. Image OCR signals only affect output for fields
    where text streams have no value at all.

    `_finding` (adjudicator finding) is intentionally NOT emitted from image
    OCR here — L7's policy already checks image OCR for risk signals via
    evaluate_ocr_signal(). Emitting duplicate finding signals from images
    would risk over-firing on OCR noise.
    """
    text_parts: list[str] = []
    text_sources: list[Source] = []
    image_ocr: list[tuple[str, str]] = []
    image_sources: list[Source] = []
    for src in sources:
        if not src.trusted:
            continue
        if src.type == TEXT_STREAM:
            text_parts.append(src.content)
            if src.content.strip():
                text_sources.append(src)
        elif src.type == IMAGE:
            image_ocr.append((src.id, src.content))
            if src.content.strip():
                image_sources.append(src)

    combined_text = "\n".join(text_parts)

    signals: list[Signal] = []

    # --- Primary source: combined text streams (V1's proven extractor) ---
    # Uses V1's full logic including Manual correction overrides and
    # applicant-name precedence (Registry > Sponsor > Intake). This stays
    # as the AUTHORITATIVE value at conf 1.0 for definitive values.
    #
    # Exception: fee_status = "unknown" is a MISSING-EXTRACTION sentinel
    # (V1 returns "unknown" when it can't parse Amount + Waiver + stated
    # Fee Status). Emitting it at conf 1.0 blocks per-source fuzzy signals
    # that DID recover a real value from an OCR-mangled label. Downgrade
    # its confidence to 0.4 so a per-source paid/waived/unpaid can win.
    v1_fields = extract_fields(combined_text, "PENDING")
    for key in _FIELD_KEYS:
        value = v1_fields.get(key, "")
        if not _valid_field_value(key, value):
            continue
        conf = 1.0
        if key == "fee_status" and value == "unknown":
            conf = 0.4
        signals.append(Signal(
            type=FIELD_VALUE, key=key, value=_norm(key, value),
            source_id="combined_text", confidence=conf,
            tag=f"v1_extract_fields:{key}",
        ))
    finding = v1_fields.get("_finding", "")
    if finding:
        signals.append(Signal(
            type=ADJUDICATOR_FINDING, key="", value=finding,
            source_id="combined_text", confidence=0.99,
            tag="v1_finding_regex",
        ))

    # --- Per-text-stream signals (Field Manual authority-weighted) ---
    # Each stream's Signal confidence is set by its classified authority
    # level (L1..L6). At L5 consolidation an L1 intake-adjudicator note
    # outranks an L5 registry extract when they disagree.
    for txt_src in text_sources:
        level = classify_source(txt_src)
        conf = _LEVEL_CONFIDENCE[level]
        stream_fields = extract_fields(txt_src.content, "PENDING")
        for key in _FIELD_KEYS:
            value = stream_fields.get(key, "")
            if not _valid_field_value(key, value):
                continue
            signals.append(Signal(
                type=FIELD_VALUE, key=key, value=_norm_and_repair(key, value, txt_src),
                source_id=txt_src.id, confidence=conf,
                tag=f"text_stream_L{level}:{key}",
            ))

    # --- Per-image signals (Field Manual authority-weighted; same scheme) ---
    # Image OCR is treated at the SAME authority level as text streams when
    # it identifies as a visible document (L1..L5). Per FIELD_MANUAL what
    # a human SEES is what matters — text layer (L6) is the LOWEST tier.
    # V1's _finding is intentionally NOT re-emitted here (L7 handles it).
    for img_src in image_sources:
        level = classify_source(img_src)
        conf = _LEVEL_CONFIDENCE[level]
        img_fields = extract_fields(img_src.content, "PENDING")
        for key in _FIELD_KEYS:
            value = img_fields.get(key, "")
            if not _valid_field_value(key, value):
                continue
            signals.append(Signal(
                type=FIELD_VALUE, key=key, value=_norm_and_repair(key, value, img_src),
                source_id=img_src.id, confidence=conf,
                tag=f"image_ocr_L{level}:{key}",
            ))

        # Fuzzy flag detection — catches OCR-mangled separators V1 misses.
        # Emitted at lower confidence than exact match (0.6 < 0.7) so an
        # exact match on the same source wins tiebreaks. Only relevant when
        # V1's exact matcher didn't find anything ('none' or empty).
        v1_flags = img_fields.get("risk_flags", "none")
        if v1_flags in ("", "none"):
            fuzzy = _fuzzy_flags_from_ocr(img_src.content)
            if fuzzy:
                signals.append(Signal(
                    type=FIELD_VALUE, key="risk_flags", value=_norm_and_repair("risk_flags", fuzzy, img_src),
                    source_id=img_src.id, confidence=0.6,
                    tag=f"image_ocr_fuzzy_flag:{fuzzy}",
                ))

        # Fuzzy sponsor_id — restore SPN-#### from OCR digit-lookalike
        # misreads V1's strict SPN-\d{4} regex missed. Same tiebreak
        # discipline as fuzzy flags: 0.6 conf so a clean text-stream or
        # exact image extraction dominates.
        if not img_fields.get("sponsor_id"):
            fuzzy_spn = _fuzzy_extract_sponsor(img_src.content)
            if fuzzy_spn and _valid_field_value("sponsor_id", fuzzy_spn):
                signals.append(Signal(
                    type=FIELD_VALUE, key="sponsor_id", value=_norm_and_repair("sponsor_id", fuzzy_spn, img_src),
                    source_id=img_src.id, confidence=0.6,
                    tag="image_ocr_fuzzy_sponsor",
                ))

        # Fuzzy label extraction for all remaining labeled fields.
        # Per FUZZY EXTRACTION DISCIPLINE: labels are template-generated
        # (safe to fuzzy-match every time); values snap to enum only for
        # Manual-listed fields, extract raw otherwise.
        #
        # Fires per field only when V1's strict extractor missed
        # (returned empty or "unknown"). Emits at level-based confidence
        # so consolidation still respects source authority. The generic
        # helper is _fuzzy_label_extract in this file.
        _fuzzy_label_signals(
            signals, img_src, img_fields, conf, level,
        )


    return {"signals": signals, "image_ocr": image_ocr}
