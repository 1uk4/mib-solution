#!/usr/bin/env python3
"""L3 — Adversarial filter (the trust boundary).

DOES: run Sanitizers (mutate content, remove adversarial substrings) then
Detectors (mark whole sources untrusted) over every source. Trust is
decided HERE, exactly once — downstream layers never re-litigate it.
DOES NOT: extract fields or interpret content beyond adversarial checks.

Two families of L3 operations on Sources:

    Sanitizers modify a source's content in place, removing adversarial
    substrings (e.g. injection payload lines) while keeping legitimate
    surrounding content. Never mark the source untrusted.

    Detectors decide whether the WHOLE source is adversarial. If yes,
    the source is marked untrusted and downstream layers exclude it
    entirely. First-hit wins for audit clarity.

Sanitizers run first (on all sources), then Detectors. This ordering
lets a stream with mixed content (some injection, some legitimate) be
sanitized to clean content and then evaluated on its remaining merit.

Both share the same signature `Source -> (should_exclude, reason)` for
uniformity. Sanitizers always return `should_exclude=False` and put
their sanitization report in the reason. Detectors return
`should_exclude=True` when they fire.
"""
from __future__ import annotations

from typing import Callable

from v4.acquire import Source
from v4.filters.injection import sanitize_injection
from v4.filters.redaction import sanitize_redactions
from v4.filters.illegibility import detect_illegibility

# Sanitizer signature: modifies source.content, returns (False, audit_reason).
Sanitizer = Callable[[Source], tuple[bool, str]]
# Detector signature: reads source, returns (is_adversarial, reason_if_yes).
Detector = Callable[[Source], tuple[bool, str]]

SANITIZERS: list[tuple[str, Sanitizer]] = [
    # Order matters: Injection first (may remove lines whose only content is
    # a marker; then Redaction runs on what remains).
    ("Injection", sanitize_injection),
    ("Redaction", sanitize_redactions),
]

DETECTORS: list[tuple[str, Detector]] = [
    # First-hit wins. Detectors run after Sanitizers, so mixed sources
    # have already been cleaned.
    ("Illegibility", detect_illegibility),
]


def apply_filters(sources: list[Source]) -> list[Source]:
    """Run sanitizers then detectors against every source (mutates in place)."""
    for src in sources:
        # Sanitize first — modifies content
        for _name, sanitizer in SANITIZERS:
            sanitizer(src)
        # Then detect — may mark untrusted
        for name, detector in DETECTORS:
            is_adversarial, reason = detector(src)
            if is_adversarial:
                src.trusted = False
                src.exclusion_reason = f"{name}:{reason}"
                break
    return sources
