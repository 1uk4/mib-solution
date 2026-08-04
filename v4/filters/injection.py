#!/usr/bin/env python3
"""InjectionSanitizer — removes injection lines from source content.

Source-level exclusion was too coarse: legitimate FORM I-8090 field
content and prompt-injection lines can appear in the same text stream
(e.g. MIB-000115), and dropping the whole source loses the real fields.

Line-level sanitization keeps the legitimate content while stripping only
the adversarial lines. The source stays TRUSTED because everything
post-sanitization is legitimate.

This is a distinct operation from Detector's "mark untrusted" pattern —
sanitizers mutate source content and never mark untrusted.
"""
from v4.acquire import Source
from v4.patterns import INJECTION_MARKERS


def sanitize_injection(source: Source) -> tuple[bool, str]:
    """Remove injection lines from source.content in place.

    Returns (should_exclude, audit_reason) — should_exclude is always
    False for a sanitizer; audit_reason is non-empty iff lines were
    dropped. Fires on ~98% of packets (SYSTEM: lines are ambient in this
    corpus), never on image OCR.
    """
    if not source.content:
        return False, ""
    kept: list[str] = []
    dropped = 0
    matched_markers: set[str] = set()
    for line in source.content.split("\n"):
        upper = line.upper()
        hit = next((mk for mk in INJECTION_MARKERS if mk in upper), None)
        if hit is None:
            kept.append(line)
        else:
            dropped += 1
            matched_markers.add(hit)
    if dropped == 0:
        return False, ""
    source.content = "\n".join(kept)
    return False, f"sanitized_lines:{dropped}:{','.join(sorted(matched_markers))}"
