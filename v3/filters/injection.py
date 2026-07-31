#!/usr/bin/env python3
"""InjectionSanitizer — removes injection lines from source content.

Source-level exclusion was too coarse: legitimate FORM I-8090 field
content and prompt-injection lines can appear in the same text stream
(e.g. MIB-000115), and dropping the whole source loses the real fields.

Line-level sanitization (matching V1's `_filter_injection`) keeps the
legitimate content while stripping only the adversarial lines. The
source stays TRUSTED because everything post-sanitization is legitimate.

This is a distinct operation from Detector's "mark untrusted" pattern —
sanitizers mutate source content and never mark untrusted.
"""
from v1.solution import INJECTION_MARKERS
from v3.acquire import Source


def sanitize_injection(source: Source) -> tuple[bool, str]:
    """Remove injection lines from source.content in place.

    Returns (was_modified, reason) for audit — matches Detector signature
    so it can share the same registration mechanism in filters/__init__.py.
    Even when modified, the source stays trusted.
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
