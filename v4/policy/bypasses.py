#!/usr/bin/env python3
"""L7 trust bypasses — exempt specific APPROVED tags from the guards.

DOES: hold the single trust bypass. A bypass returns ctx.verdict
UNCHANGED to fire (dispatcher returns it, skipping all guards) or None.
Runs after the APPROVED gate, so bypasses only ever see approvals —
matching the live v3 ordering (the brief sketched the bypass before the
gate; both orderings are observationally identical, and v4 matches the
code, not the sketch).
DOES NOT: modify the verdict — a bypass is a pass-through exemption,
never a promotion or demotion.
"""
from __future__ import annotations

from v4.policy.context import PolicyContext, Verdict


def bypass_adjudicator_finding(ctx: PolicyContext) -> Verdict | None:
    """Adjudicator finding is Evidence Precedence #1 (per FIELD_MANUAL) and
    is 100% accurate across all 162 training cases with a Finding line.
    It is emitted only from trusted text streams (never from image OCR —
    see v4/signals.py), so it has already passed injection/redaction
    filtering. The form-derived guards exist to protect R_A1_* approvals
    from being fooled by form data; they should not second-guess a signed
    manual note that outranks form data by design."""
    if ctx.config.trust_finding and ctx.tag.startswith("R_ADJUDICATOR_FINDING"):
        return ctx.verdict
    return None


BYPASSES = [bypass_adjudicator_finding]
