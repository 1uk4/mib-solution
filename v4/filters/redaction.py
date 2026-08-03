#!/usr/bin/env python3
"""RedactionSanitizer — strips redaction markers from source content.

Redaction markers are placeholder tokens that indicate "this content was
deliberately removed / illegible / blank." They must never be extracted
as field values (a value of `[NAME CUT OUT]` is not a name).

The extraction-time `_reject_placeholder` (v4/signals.py) handles this at
extraction time by returning "" when a matched value equals a bracketed
placeholder. This sanitizer does the same job at L3, one layer earlier —
content that reaches L4 is already free of these markers, so all
downstream extractors and signal emitters don't need to know about them.

Observed markers in the training set:
    [NAME CUT OUT]         placeholder for redacted names
    [PURPOSE ILLEGIBLE]    placeholder for redacted purposes
    [SPONSOR ID BLANK]     placeholder for missing sponsors
    [BLANK]                generic placeholder
    REDACTED?              standalone highlighter overlay marker

The `\\[[A-Z][A-Z ]+\\]` pattern catches all bracketed all-caps markers,
including any we haven't explicitly enumerated. Legitimate values are
never bracketed all-caps (species codes / home worlds aren't in brackets;
mixed-case brackets like `[MIB Eyes Only]` in headers don't match).
"""
import re

from v4.acquire import Source


_REDACTION_RE = re.compile(
    r"\[[A-Z][A-Z ]+\]"       # bracketed all-caps placeholders
    r"|REDACTED\??",          # REDACTED marker, optional trailing '?'
)


def sanitize_redactions(source: Source) -> tuple[bool, str]:
    """Remove redaction markers from source.content in place.

    Returns (was_modified, reason) — matches Sanitizer signature.
    Source stays trusted; only content changes.
    """
    if not source.content:
        return False, ""
    original = source.content
    cleaned = _REDACTION_RE.sub("", original)
    if cleaned == original:
        return False, ""
    source.content = cleaned
    return False, f"sanitized_redactions:{len(original) - len(cleaned)}_chars"
