#!/usr/bin/env python3
"""L4 helper — post-OCR value normalization for extracted field values.

Called by v4.signals right before a FIELD_VALUE signal is emitted. Fixes
common OCR artifacts that make otherwise-correct extractions fail the
scorer's exact-string match.

Per `extraction_fuzzy_discipline.md`: fuzzy-snap VALUES only for closed
enums confirmed in the Field Manual (visa_class, fee_status). Never snap
open-ended fields (home_world, applicant_name, species_code, declared_purpose)
to a memorized training-set list — the eval may introduce unseen values.

Per `vocab_audit_discipline.md`: enum tables here are derived from the
Field Manual + training-set distribution, not from memory.
"""
from __future__ import annotations

import re
from datetime import date


# --- Enum tables (verified against FIELD_MANUAL.md + train_labels.csv) ---

# fee_status: Field Manual §Fee Rules, training-set distribution:
# paid=664, waived=242, unpaid=50, unknown=44 (n=1000)
_FEE_STATUS_ENUM = {"paid", "waived", "unpaid", "unknown"}

# visa_class: Field Manual §Visa Classes
_VISA_CLASS_ENUM = {"MED-3", "XW-1", "XW-2", "DIP-1", "TRANSIT-7"}


# --- Sponsor ID: SPN-####
_SPONSOR_ID_RE = re.compile(r"^SPN-\d{4}$")


def _normalize_sponsor_id(raw: str) -> str:
    if not raw:
        return ""
    s = raw.strip().rstrip(".,;:")
    # Collapse internal whitespace (e.g. 'SPN- 6099' → 'SPN-6099')
    s = re.sub(r"\s+", "", s)
    return s  # validation happens in _valid_field_value downstream


# --- Home world: letter-digit tokens with spurious spaces
_HOME_SPACE_RE = re.compile(r"^([A-Za-z][A-Za-z\-]+-\d+)\s+(\d*[a-z]?)$")


def _normalize_home_world(raw: str) -> str:
    if not raw:
        return ""
    s = raw.strip().rstrip(".,;:")
    # If pattern 'Word-N MoreN' (letter-dash-digits, space, more digits/letter),
    # collapse the space. Example: 'Wolf-106 1c' → 'Wolf-1061c'.
    m = _HOME_SPACE_RE.match(s)
    if m:
        s = m.group(1) + m.group(2)
    return s


# --- Arrival date: YYYY-MM-DD with year in 2020-2030
_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_YEAR_MIN, _YEAR_MAX = 2020, 2030


def _try_valid_date(y: int, m: int, d: int) -> bool:
    if not (_YEAR_MIN <= y <= _YEAR_MAX and 1 <= m <= 12 and 1 <= d <= 31):
        return False
    try:
        date(y, m, d)
        return True
    except ValueError:
        return False


def _normalize_arrival_date(raw: str) -> str:
    if not raw:
        return ""
    s = raw.strip().strip(".,;:")
    m = _DATE_RE.match(s)
    if not m:
        return s
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if _try_valid_date(y, mo, d):
        return s  # already valid
    # Try single-digit repair on year: substitute each position with 0-9
    year_str = m.group(1)
    for pos in range(4):
        for digit in "0123456789":
            candidate = year_str[:pos] + digit + year_str[pos+1:]
            cy = int(candidate)
            if _try_valid_date(cy, mo, d):
                return f"{candidate}-{m.group(2)}-{m.group(3)}"
    return s  # give up, keep raw


# --- Enum snappers
def _snap_enum(raw: str, enum: set[str], casefold: bool = False) -> str:
    if not raw:
        return ""
    s = raw.strip().rstrip(".,;:")
    if casefold:
        s = s.lower()
    if s in enum:
        return s
    # Try replacing common OCR separator variants
    for sep in ("_", " ", "."):
        alt = s.replace(sep, "-")
        if alt in enum:
            return alt
    # Case-insensitive match
    for e in enum:
        if s.upper() == e.upper():
            return e
    return raw.strip()  # unknown, keep raw (trimmed)


def _normalize_visa_class(raw: str) -> str:
    return _snap_enum(raw, _VISA_CLASS_ENUM)


def _normalize_fee_status(raw: str) -> str:
    return _snap_enum(raw, _FEE_STATUS_ENUM, casefold=True)


# --- Free-form: strip whitespace only, no snap
def _normalize_freeform(raw: str) -> str:
    return raw.strip() if raw else ""


# --- Dispatch
_NORMALIZERS = {
    "sponsor_id": _normalize_sponsor_id,
    "home_world": _normalize_home_world,
    "arrival_date": _normalize_arrival_date,
    "visa_class": _normalize_visa_class,
    "fee_status": _normalize_fee_status,
    "applicant_name": _normalize_freeform,
    "species_code": _normalize_freeform,
    "declared_purpose": _normalize_freeform,
}


def value(field_name: str, raw: str) -> str:
    """Normalize a raw extracted value for the given field name.

    Returns normalized value or raw if the field has no normalizer.
    Never raises — falls through to raw on any internal failure.
    """
    if not raw:
        return ""
    fn = _NORMALIZERS.get(field_name)
    if fn is None:
        return raw
    try:
        return fn(raw)
    except Exception:
        return raw
