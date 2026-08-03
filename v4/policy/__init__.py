#!/usr/bin/env python3
"""L7 — Decision policy: the staged dispatcher.

DOES: sequence the nine stages — 3 upgrades, the APPROVED gate, 1 trust
bypass, 5 guards — and expose the public apply_policy with the same
signature the pipeline has always called.
DOES NOT: contain stage logic (upgrades.py / bypasses.py / guards.py) or
value objects (context.py).

Sequencing semantics (load-bearing — see spec §5):

  * A firing upgrade returns IMMEDIATELY: its result deliberately skips
    every guard. "Apply upgrades then validate with guards" would silently
    change verdicts.
  * Upgrades run BEFORE the gate because two of them promote non-APPROVED
    verdicts (a REVIEW and a DENIED respectively).
  * The fallback-OCR upgrade falls through (returns None) when the packet
    has no usable OCR signal — the case then exits at the gate with its
    fallback verdict unchanged.
  * The bypass sits AFTER the gate, matching the live v3 ordering; the
    brief's sketch had it before, which is observationally identical.
  * Guards run in fixed order, first hit wins; only APPROVED verdicts
    that no bypass exempted ever reach them.

Two stages (upgrade_unpaid_waiver, guard_defensive_downgrade) are disabled
by default via Config — insurance paths, see v4/config.py.
"""
from __future__ import annotations

from v4.config import Config, CONFIG
from v4.policy.bypasses import BYPASSES, bypass_adjudicator_finding
from v4.policy.context import PolicyContext, Verdict, make_ctx
from v4.policy.guards import (
    GUARDS,
    guard_defensive_downgrade,
    guard_field_conflict,
    guard_missing_required,
    guard_ocr_only,
    guard_ocr_risk_override,
)
from v4.policy.upgrades import (
    UPGRADES,
    upgrade_biometric_clean,
    upgrade_fallback_ocr,
    upgrade_unpaid_waiver,
)

__all__ = [
    "apply_policy", "PolicyContext", "Verdict", "make_ctx",
    "UPGRADES", "BYPASSES", "GUARDS",
    "upgrade_biometric_clean", "upgrade_unpaid_waiver", "upgrade_fallback_ocr",
    "bypass_adjudicator_finding",
    "guard_ocr_risk_override", "guard_ocr_only", "guard_field_conflict",
    "guard_missing_required", "guard_defensive_downgrade",
]


def apply_policy(
    fields: dict,
    adj: str, conf: float, tag: str,
    signals: dict,
    config: Config = CONFIG,
) -> Verdict:
    """Return final (adj, conf, tag). May override L6's decision.

    Public signature unchanged from v3 — the PolicyContext is an internal
    construction, so all existing call sites and tests work untouched.
    Verdict is a NamedTuple and unpacks as a plain 3-tuple.
    """
    ctx = PolicyContext(fields=fields, adj=adj, conf=conf, tag=tag,
                        signals=signals, config=config)
    for stage in UPGRADES:
        if (r := stage(ctx)) is not None:
            return r
    if ctx.adj != "APPROVED":
        return ctx.verdict
    for stage in BYPASSES:
        if (r := stage(ctx)) is not None:
            return r
    for stage in GUARDS:
        if (r := stage(ctx)) is not None:
            return r
    return ctx.verdict
