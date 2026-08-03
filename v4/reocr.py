#!/usr/bin/env python3
"""Char-whitelist re-OCR for structured fields.

When L4's initial extraction returns a value that fails the field's format
regex, this module re-OCRs the source image with a per-field character
whitelist. The whitelist prevents Tesseract from proposing letters where
only digits are valid (e.g. SPN-6O99 → SPN-6099, 2O26 → 2026).

Fires only when initial extraction failed format validation, so per-run
cost is bounded (~50-100 invocations on the full training set).
"""
from __future__ import annotations

import re

from v4.acquire import Source, IMAGE
from v4.extract import _tesseract


# Per-field: (character whitelist string, format regex to validate against)
_STRATEGIES: dict[str, tuple[str, re.Pattern]] = {
    "sponsor_id":  ("SPN-0123456789",  re.compile(r"^SPN-\d{4}$")),
    "arrival_date": ("-0123456789",     re.compile(r"^\d{4}-\d{2}-\d{2}$")),
}


def _current_valid(field: str, value: str) -> bool:
    strat = _STRATEGIES.get(field)
    return bool(strat and strat[1].match(value or ""))


def repair(source: Source, field: str, current_value: str) -> str | None:
    """Re-OCR `source` with the char whitelist for `field`. Return the
    repaired value if it matches the format regex, else None.

    - Returns None if the source is not an image
    - Returns None if the field has no whitelist strategy
    - Returns None if the current value already passes validation
    - Returns None if the re-OCR result also fails validation
    """
    if source.type != IMAGE:
        return None
    strat = _STRATEGIES.get(field)
    if strat is None:
        return None
    if _current_valid(field, current_value):
        return None
    whitelist, regex = strat
    text = _tesseract(
        source.raw,
        psm=6,
        extra_flags=["-c", f"tessedit_char_whitelist={whitelist}"],
    )
    # Take the first token that matches the format regex, scanning
    # whitespace-split tokens.
    for token in text.split():
        if regex.match(token):
            return token
    return None
