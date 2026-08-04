#!/usr/bin/env python3
"""L4 — Signal extraction.

Consumes TRUSTED Sources from L3 and emits typed `Signal` records for
L5 to consolidate. Also passes through raw image-OCR text for L7's
OCR-override policy.

DOES: run the canonical field extractor (absorbed verbatim from
v1/solution.py:292-519 — regex extraction, fee triangulation, name
precedence, manual-correction overrides) over combined text, per-stream,
and per-image content; emit authority-weighted Signals; run the fuzzy
recovery passes (flags, sponsor digits, template labels) on image OCR.
DOES NOT: consolidate signals (L5), decide trust (L3 already did), or
make any adjudication decision (L6/L7).

The Signal record is the substrate for cross-reference at L5:
    - Same (type, key) from multiple sources → group, resolve, weight
    - Conflicts → detected, not silently overwritten
    - Provenance → every value traces back to a specific source

Signals come from source classes with different confidence:
  - Combined text-stream content: source_id='combined_text',
    confidence=1.0 (authoritative form data)
  - Per-stream / per-image: confidence set by the Field Manual authority
    level of the classified document (source_type.classify).

`_finding` (adjudicator finding) is intentionally NOT emitted from image
OCR here — L7's policy already checks image OCR for risk signals via
evidence.evaluate_ocr_signal(). Emitting duplicate finding signals from
images would risk over-firing on OCR noise.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date as Date

from difflib import SequenceMatcher
from typing import Iterable

from v4.acquire import Source, TEXT_STREAM, IMAGE
from v4.config import Config, CONFIG
from v4.normalize import value as _normalize_value
from v4.patterns import (
    AMOUNT_LABEL_RE,
    CORRECTION_RE,
    FEE_LABEL_RE,
    FINDING_RE,
    FLAG_LABEL_RE,
    ISO_DATE_RE,
    NAME_LABEL_RE,
    PLACEHOLDER_RE,
    PURPOSE_LABEL_RE,
    REGISTRY_NAME_RE,
    SPONSOR_ATTESTS_RE,
    SPONSOR_PURPOSE_RE,
    SPONSOR_RE,
    VISA_LABEL_RE,
    WAIVER_CODE_RE,
)
from v4.reocr import repair as _reocr_repair
from v4.source_type import classify as classify_source
from v4.vocab import (
    ALL_FLAGS,
    DISQUALIFYING_FLAGS,
    FEE_VALUES,
    HOME_WORLDS,
    REVIEW_ONLY_FLAGS,
    SPECIES,
    VISA_CLASSES,
)


def _norm(key: str, raw: str, config: Config = CONFIG) -> str:
    """Config-gated normalizer wrapper. When config.normalize_values, apply
    field-specific normalization; otherwise pass value through untouched."""
    if config.normalize_values and raw:
        return _normalize_value(key, raw)
    return raw


def _norm_and_repair(key: str, raw: str, source: "Source",
                     config: Config = CONFIG) -> str:
    """Normalize; if a structured field still fails validation, char-whitelist re-OCR."""
    normed = _norm(key, raw, config)
    if config.reocr_char_whitelist and key in ("sponsor_id", "arrival_date"):
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
      type       kind of signal — 'FIELD_VALUE' or 'ADJUDICATOR_FINDING'
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


# Signal type constants (avoid stringly-typed bugs).
# v3 also defined FLAG_DECLARATION — never emitted, never consumed; dropped
# in v4 (bucket-1 dead code, spec §6).
FIELD_VALUE = "FIELD_VALUE"
ADJUDICATOR_FINDING = "ADJUDICATOR_FINDING"


# ---------------------------------------------------------------------------
# Canonical field extraction (absorbed verbatim from v1/solution.py:292-519)
# ---------------------------------------------------------------------------


def _reject_placeholder(value: str) -> str:
    """Return "" if value is a bracketed placeholder, else the value unchanged."""
    if value and PLACEHOLDER_RE.match(value):
        return ""
    return value


def _first_match(pattern: re.Pattern, text: str) -> str:
    m = pattern.search(text)
    return _reject_placeholder(m.group(1).strip()) if m else ""


def _find_vocab(text: str, vocab: Iterable[str]) -> str:
    """Return the first vocab item found in text (word-boundary match)."""
    for item in vocab:
        # word-boundary is unreliable for hyphenated tokens, so escape-match
        pattern = re.compile(re.escape(item))
        if pattern.search(text):
            return item
    return ""


def _extract_flags(text: str) -> str:
    """Return pipe-delimited sorted flags found in text, or 'none'."""
    # Prefer explicit label line if present
    labeled = _first_match(FLAG_LABEL_RE, text)
    scan_source = labeled if labeled else text
    found = set()
    for flag in ALL_FLAGS:
        if re.search(rf"\b{re.escape(flag)}\b", scan_source, re.IGNORECASE):
            found.add(flag)
    if not found:
        # Explicit "none" in the labeled line is a positive negative
        if labeled and re.search(r"\bnone\b", labeled, re.IGNORECASE):
            return "none"
        # Otherwise: absence is not evidence — but for schema we must emit
        # something. Emit "none" so validator passes; the audit tag will
        # note when we're guessing.
        return "none"
    return "|".join(sorted(found))


def _extract_purpose(text: str) -> str:
    """Return declared_purpose from intake label, else the sponsor letter's
    "is expected on Earth for <purpose>." phrasing.

    Fallback-only: intake and sponsor never disagree in the training set
    (verified 542 both-agree cases, 0 disagreements). Sponsor recovery
    covers 148 cases where intake is missing but the sponsor letter is
    present in text.
    """
    intake = _first_match(PURPOSE_LABEL_RE, text)
    if intake:
        return intake
    m = SPONSOR_PURPOSE_RE.search(text)
    if m:
        # Normalize whitespace — the capture crosses newlines that were
        # artifacts of PDF text-op splitting, not real whitespace.
        return _reject_placeholder(re.sub(r"\s+", " ", m.group(1).strip()))
    return ""


def _extract_applicant_name(text: str) -> str:
    """Return applicant name using priority: Registry Name > Sponsor attests > Intake.

    Registry Name (Planetary Registry Extract, an external database) and
    Sponsor "attests that" (an independent third-party statement) are more
    trustworthy than the intake form Applicant field, which is
    applicant-submitted and turns out to be adversarial in ~17 training
    cases (name on form differs from Registry+Sponsor, truth matches
    Registry+Sponsor). Empirical test on 1000 training PDFs: 17 fixes,
    0 breakage of previously-correct extractions.

    Safety note: `applicant_name` is not used in any adjudication rule, so
    this change affects only extraction score — it cannot cause false
    approvals. If we later want a "packet is lying" rule triggered by
    intake/registry disagreement, that would be a NEW adjudication rule
    to validate separately.
    """
    registry = _first_match(REGISTRY_NAME_RE, text)
    if registry:
        return registry
    m = SPONSOR_ATTESTS_RE.search(text)
    if m:
        v = _reject_placeholder(m.group(1).strip())
        if v:
            return v
    return _first_match(NAME_LABEL_RE, text)


def _extract_fee(text: str) -> str:
    """Return fee_status triangulated from Amount + Waiver Code + stated label.

    The stated `Fee Status: X` label in the fee receipt is adversarial in
    parts of the dataset (23 training cases where stated value contradicts
    truth). The Amount and Waiver Code fields are honest. Triangulation
    rules (verified 31 fixes, 0 regressions on 1000 training PDFs):

        Amount > 0 + no waiver   → paid    (regardless of stated)
        Amount = 0 + waiver code → waived  (regardless of stated)
        Amount = 0 + no waiver:
            stated ∈ {unpaid, unknown} → stated (plausible; trust it)
            stated ∈ {paid, waived}    → unknown (impossible; label lying)
        Amount = 0, no waiver, no stated  → unknown
        Amount missing → fall back to stated only

    Returns "" if neither stated nor Amount are extractable.
    """
    stated = _first_match(FEE_LABEL_RE, text).lower()
    amount_m = AMOUNT_LABEL_RE.search(text)

    if amount_m is None:
        return stated  # no receipt Amount visible — trust stated (may be empty)

    try:
        amount = float(amount_m.group(1).replace(",", ""))
    except ValueError:
        return stated

    waiver_m = WAIVER_CODE_RE.search(text)
    waiver = (waiver_m.group(1).strip().upper() if waiver_m else "")
    has_waiver = bool(waiver) and waiver != "N/A"

    if amount > 0 and not has_waiver:
        return "paid"
    if amount == 0 and has_waiver:
        return "waived"
    if amount == 0 and not has_waiver:
        if stated in ("unpaid", "unknown"):
            return stated
        # stated is paid/waived/empty — none are plausible with $0 + N/A.
        return "unknown"
    # amount > 0 AND waiver present — contradictory, unseen in training.
    return "unknown"


def _extract_visa(text: str) -> str:
    """Return the visa class following a 'Visa Class' label if present;
    otherwise fall back to any visa token found in the text.

    Packets contain the form's radio-button label listing all visa classes.
    Preferring the labeled value avoids picking the first alphabetical match
    (which caused TRANSIT-7 to be misread as MED-3).
    """
    labeled = _first_match(VISA_LABEL_RE, text).upper()
    if labeled in VISA_CLASSES:
        return labeled
    return _find_vocab(text, VISA_CLASSES)


def _extract_finding(text: str) -> str:
    """Return 'APPROVED', 'DENIED', 'NEEDS_REVIEW', or '' if not present."""
    m = FINDING_RE.search(text)
    if not m:
        return ""
    return m.group(1).upper().replace(" ", "_")


def _apply_corrections(text: str, fields: dict) -> None:
    """Apply any 'Manual correction: X is Y' notes to fields in-place.

    Adjudicator corrections rank #1 in evidence precedence — they override
    the form-derived value even if extraction succeeded from the form.
    """
    for m in CORRECTION_RE.finditer(text):
        field_label = re.sub(r"\s+", "", m.group(1)).lower()
        value = m.group(2).strip()
        if field_label == "sponsor" and SPONSOR_RE.fullmatch(value):
            fields["sponsor_id"] = value
        elif field_label == "applicant":
            fields["applicant_name"] = value
        elif field_label == "visaclass":
            v = value.upper()
            if v in VISA_CLASSES:
                fields["visa_class"] = v
        elif field_label == "feestatus":
            v = value.lower()
            if v in FEE_VALUES:
                fields["fee_status"] = v


def extract_fields(text: str, case_id: str) -> dict:
    """Extract fields from decoded PDF text.

    Returns a dict of {field: value}. Values are "" when the field could
    not be positively confirmed from the text — callers must NOT treat empty
    strings as safe positives for approval decisions.

    Adds a synthetic `_finding` key with the adjudicator's stated decision
    if present (ranked #1 in FIELD_MANUAL evidence precedence).

    Applies 'Manual correction: X is Y' overrides after initial extraction
    so adjudicator corrections beat form values.
    """
    fields = {
        "case_id": case_id,
        "applicant_name": _extract_applicant_name(text),
        "species_code": _find_vocab(text, SPECIES),
        "home_world": _find_vocab(text, HOME_WORLDS),
        "visa_class": _extract_visa(text),
        "sponsor_id": (SPONSOR_RE.search(text).group(0)
                       if SPONSOR_RE.search(text) else ""),
        "arrival_date": _first_match(ISO_DATE_RE, text),
        "declared_purpose": _extract_purpose(text),
        "risk_flags": _extract_flags(text),
        "fee_status": _extract_fee(text),
        "_finding": _extract_finding(text),
    }
    _apply_corrections(text, fields)
    return fields


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


# Which extracted fields become FIELD_VALUE signals
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
# combined_text remains at 1.0 above the L1 floor — the canonical
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
# The exact flag detector requires exact `\bbiohazard_red\b`. OCR often
# mangles the separator: reads underscore as space, hyphen, em-dash, or
# drops it entirely. This detector relaxes the separator between flag
# components so "biohazard red", "biohazard-red", "biohazardred" all match.
# Only used for IMAGE sources; text streams keep strict matching.
_FUZZY_FLAG_CACHE: dict[str, "re.Pattern"] = {}


def fuzzy_flag_pattern(flag: str) -> "re.Pattern":
    """Compile a case-insensitive fuzzy pattern for a flag word.

    Public helper — used both here (per-image fuzzy flag detection at L4)
    and in v4.evidence (fuzzy risk-signal check at L7).

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
# Sponsor ID: strict SPN-#### format (belt-and-suspenders; the canonical
# extractor already enforces).
_SPONSOR_ID_RE = re.compile(r"^SPN-\d{4}$")

# --- Fuzzy sponsor_id repair for OCR digit-lookalike misreads ---
# OCR routinely substitutes letters for digits (O→0, l→1, B→8, etc.).
# The strict SPONSOR_RE requires exact SPN-\d{4}, so mangled reads produce
# no extraction. The regex below finds SPN- followed by exactly 4 chars that
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
    matches whose 4-char body is already all digits — the strict
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
    #
    # The prescreen is exact: quick_ratio() equals 2*M/(|window|+|label|)
    # where M is the character-multiset intersection, an UPPER BOUND on
    # ratio(). M is maintained incrementally as the window slides (O(1)
    # per position), so full ratio() runs only on windows whose bound
    # clears both the threshold and the running best — a window pruned by
    # the bound can never change the result. (2026-08-03: this sweep
    # dominated L4 runtime once fee_status joined the ladder; the rolling
    # prescreen cuts it ~50x, output-identical — equivalence-tested.)
    best_score = 0.0
    best_end = -1
    sm = SequenceMatcher(None, "", label_lo)
    lcount: dict[str, int] = {}
    for ch in label_lo:
        lcount[ch] = lcount.get(ch, 0) + 1
    n = len(text_lo)
    for wsize in range(max(3, llen - 2), llen + 3):
        if wsize > n:
            continue
        denom = wsize + llen
        # Initialize window counts + intersection for text_lo[0:wsize]
        wcount: dict[str, int] = {}
        M = 0
        for ch in text_lo[:wsize]:
            c = wcount.get(ch, 0) + 1
            wcount[ch] = c
            if c <= lcount.get(ch, 0):
                M += 1
        for i in range(0, n - wsize + 1):
            if i > 0:
                out_c = text_lo[i - 1]
                in_c = text_lo[i + wsize - 1]
                if out_c != in_c:
                    c = wcount[out_c]
                    if c <= lcount.get(out_c, 0):
                        M -= 1
                    wcount[out_c] = c - 1
                    c = wcount.get(in_c, 0) + 1
                    wcount[in_c] = c
                    if c <= lcount.get(in_c, 0):
                        M += 1
            q = 2.0 * M / denom
            if q < label_threshold or q <= best_score:
                continue
            sm.set_seq1(text_lo[i:i + wsize])
            score = sm.ratio()
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

    Also filters risk_flags='none' — that's the extractor's default meaning
    "no flag word found in this source," not "actively no flag." Emitting it
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

    # Other fields are enum-restricted by the extractors; accept as-is.
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
    config: Config,
) -> None:
    """Append fuzzy-label Signals for any labeled field the canonical
    extractor didn't extract.

    Runs the generic _fuzzy_label_extract for each entry in
    _FUZZY_LABEL_CONFIG. Only emits when the per-image extraction
    returned empty or "unknown" for that key — no point competing with
    a definitive per-image value.

    fee_status historically had NO fuzzy recovery (a v3 docstring
    referenced a "dedicated block" that never existed — the fossil was
    found 2026-08-03 via the L6 fallback census). Recovery is gated on
    config.fee_fuzzy_recovery pending measurement.
    """
    entries = _FUZZY_LABEL_CONFIG
    if config.fee_fuzzy_recovery:
        entries = entries + [("fee_status", "Fee Status", _FEE_ENUM)]
    for key, label, enum in entries:
        v1_value = img_fields.get(key, "")
        if v1_value and v1_value.strip().lower() not in ("", "unknown"):
            continue  # got a definitive value; don't add a fuzzy signal
        value = _fuzzy_label_extract(
            img_src.content, label, enum=enum,
        )
        if not value:
            continue
        if not _valid_field_value(key, value):
            continue
        signals.append(Signal(
            type=FIELD_VALUE, key=key,
            value=_norm_and_repair(key, value, img_src, config),
            source_id=img_src.id, confidence=conf,
            tag=f"image_ocr_fuzzy_{key}:{value[:20]}",
        ))


def extract_signals(sources: list[Source], config: Config = CONFIG) -> dict:
    """Bundle trusted-source content into signals + raw OCR pass-through.

    Returns:
        {
            'signals': list[Signal],   # typed evidence for L5 to consolidate
            'image_ocr': list[(source_id, ocr_text)],  # kept for L7 policy
            'combined_text': str,      # merged trusted text streams (L7)
            'any_illegibility_excluded': bool,  # L7 defensive-downgrade input
        }

    Signals come from two source classes with different confidence:
      - Combined text-stream content: extracted via extract_fields,
        source_id='combined_text', confidence=1.0 (authoritative form data)
      - Each stream/image: extracted via extract_fields, confidence by
        classified authority level (can misread, but fills gaps when text
        streams have no value for a field)

    L5 picks the highest-confidence Signal per key, so text-stream
    signals always win ties. Image OCR signals only affect output for fields
    where text streams have no value at all.
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

    # --- Primary source: combined text streams (canonical extractor) ---
    # Full logic including Manual correction overrides and applicant-name
    # precedence (Registry > Sponsor > Intake). This stays as the
    # AUTHORITATIVE value at conf 1.0 for definitive values.
    #
    # Exception: fee_status = "unknown" is a MISSING-EXTRACTION sentinel
    # (extract_fields returns "unknown" when it can't parse Amount + Waiver
    # + stated Fee Status). Emitting it at conf 1.0 blocks per-source fuzzy
    # signals that DID recover a real value from an OCR-mangled label.
    # Downgrade its confidence to 0.4 so a per-source paid/waived/unpaid
    # can win.
    v1_fields = extract_fields(combined_text, "PENDING")
    for key in _FIELD_KEYS:
        value = v1_fields.get(key, "")
        if not _valid_field_value(key, value):
            continue
        conf = 1.0
        if key == "fee_status" and value == "unknown":
            conf = 0.4
        signals.append(Signal(
            type=FIELD_VALUE, key=key, value=_norm(key, value, config),
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
                type=FIELD_VALUE, key=key,
                value=_norm_and_repair(key, value, txt_src, config),
                source_id=txt_src.id, confidence=conf,
                tag=f"text_stream_L{level}:{key}",
            ))

    # --- Per-image signals (Field Manual authority-weighted; same scheme) ---
    # Image OCR is treated at the SAME authority level as text streams when
    # it identifies as a visible document (L1..L5). Per FIELD_MANUAL what
    # a human SEES is what matters — text layer (L6) is the LOWEST tier.
    # The finding is intentionally NOT re-emitted here (L7 handles it).
    for img_src in image_sources:
        level = classify_source(img_src)
        conf = _LEVEL_CONFIDENCE[level]
        img_fields = extract_fields(img_src.content, "PENDING")
        for key in _FIELD_KEYS:
            value = img_fields.get(key, "")
            if not _valid_field_value(key, value):
                continue
            signals.append(Signal(
                type=FIELD_VALUE, key=key,
                value=_norm_and_repair(key, value, img_src, config),
                source_id=img_src.id, confidence=conf,
                tag=f"image_ocr_L{level}:{key}",
            ))

        # Fuzzy flag detection — catches OCR-mangled separators the exact
        # matcher misses. Emitted at lower confidence than exact match
        # (0.6 < 0.7) so an exact match on the same source wins tiebreaks.
        # Only relevant when the exact matcher didn't find anything
        # ('none' or empty).
        v1_flags = img_fields.get("risk_flags", "none")
        if v1_flags in ("", "none"):
            fuzzy = _fuzzy_flags_from_ocr(img_src.content)
            if fuzzy:
                signals.append(Signal(
                    type=FIELD_VALUE, key="risk_flags",
                    value=_norm_and_repair("risk_flags", fuzzy, img_src, config),
                    source_id=img_src.id, confidence=0.6,
                    tag=f"image_ocr_fuzzy_flag:{fuzzy}",
                ))

        # Fuzzy sponsor_id — restore SPN-#### from OCR digit-lookalike
        # misreads the strict SPN-\d{4} regex missed. Same tiebreak
        # discipline as fuzzy flags: 0.6 conf so a clean text-stream or
        # exact image extraction dominates.
        if not img_fields.get("sponsor_id"):
            fuzzy_spn = _fuzzy_extract_sponsor(img_src.content)
            if fuzzy_spn and _valid_field_value("sponsor_id", fuzzy_spn):
                signals.append(Signal(
                    type=FIELD_VALUE, key="sponsor_id",
                    value=_norm_and_repair("sponsor_id", fuzzy_spn, img_src, config),
                    source_id=img_src.id, confidence=0.6,
                    tag="image_ocr_fuzzy_sponsor",
                ))

        # Fuzzy label extraction for all remaining labeled fields.
        # Per FUZZY EXTRACTION DISCIPLINE: labels are template-generated
        # (safe to fuzzy-match every time); values snap to enum only for
        # Manual-listed fields, extract raw otherwise.
        #
        # Fires per field only when the strict extractor missed
        # (returned empty or "unknown"). Emits at level-based confidence
        # so consolidation still respects source authority. The generic
        # helper is _fuzzy_label_extract in this file.
        _fuzzy_label_signals(
            signals, img_src, img_fields, conf, config,
        )


    any_illegibility_excluded = any(
        not s.trusted and s.exclusion_reason and "Illegibility" in s.exclusion_reason
        for s in sources
    )
    return {
        "signals": signals,
        "image_ocr": image_ocr,
        "combined_text": combined_text,
        "any_illegibility_excluded": any_illegibility_excluded,
    }
