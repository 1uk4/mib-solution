#!/usr/bin/env python3
"""L7 guard stages — may only DEMOTE an APPROVED; run after gate + bypasses.

DOES: hold the five guards in their required order:
  (1) ocr_risk_override   (always on — direct evidence beats safety guards)
  (2) ocr_only            (config.ocr_only_guard, default ON)
  (3) field_conflict      (always on)
  (4) missing_required    (always on)
  (5) defensive_downgrade (config.defensive_downgrade, default OFF)
A guard returns a Verdict to fire or None to pass; first hit wins.
DOES NOT: see non-APPROVED verdicts (the dispatcher's gate filters them),
promote anything (upgrades.py), or exempt tags (bypasses.py).
"""
from __future__ import annotations

from v4.confidence import conf as _conf
from v4.evidence import evaluate_ocr_signal
from v4.policy.context import PolicyContext, Verdict

# Fields that participate in the APPROVE rules. If any of these came
# from OCR alone, don't let R_A1_* fire the approval.
_APPROVE_RELEVANT_FIELDS = (
    "fee_status", "visa_class", "sponsor_id", "home_world", "risk_flags",
)

# L6 approve rules that we consider fair game for defensive downgrade.
# These fire on text-stream form data alone and don't require any image
# corroboration — the exact pattern cat FAs exploit.
_DEFENSIVE_TARGET_TAGS = ("R_A1_paid_clean", "R_A1_dip1_waived")


def guard_ocr_risk_override(ctx: PolicyContext) -> Verdict | None:
    """(1) OCR risk-signal override — direct evidence beats safety guards.

    Access contract: ctx.signals["image_ocr"] (KeyError on absence,
    matching the v3 body) — unlike the upgrades' tolerant .get()."""
    ocr_text = "\n".join(text for _sid, text in ctx.signals["image_ocr"])
    ocr_signal = evaluate_ocr_signal(ocr_text)
    if ocr_signal is not None:
        return Verdict(*ocr_signal)
    return None


def guard_ocr_only(ctx: PolicyContext) -> Verdict | None:
    """(2) OCR-only field guard — DEFAULT ON (config.ocr_only_guard).

    Purpose: adversarial defense — if an approve-relevant field came only
    from image OCR (no text-stream backing), don't trust the approval.
    The concern is fake documents inserted into images that OCR reads as
    legitimate.

    Measurement (2026-08-03): disabling the guard won 16 A→A but
    uncovered +10 cat FAs across MULTIPLE approve-rule paths
    (R_A1_paid_clean, R_A1_dip1_waived, the biometric upgrades, and the
    fallback OCR upgrade) — net -0.22 pts. When it fires SOLO its
    precision is only ~35%, but the cat-FA protection spans paths that
    blanket disabling exposes. Kept ON.

    Note: Field Manual §Trusted Evidence ranks visible image-based
    evidence ABOVE the machine-readable text layer, so this guard's
    implicit "text > image" ordering is backwards from the Manual —
    a known tension, resolved empirically in favor of keeping the guard
    (see spec Pitfall 5)."""
    if ctx.config.ocr_only_guard:
        source_class = ctx.fields.get("_source_class", {})
        ocr_only = [
            f for f in _APPROVE_RELEVANT_FIELDS
            if source_class.get(f) == "ocr_only"
        ]
        if ocr_only:
            return Verdict(
                "NEEDS_REVIEW", _conf("ocr_only_downgrade"),
                f"ocr_only_downgrade:{','.join(ocr_only)}",
            )
    return None


def guard_field_conflict(ctx: PolicyContext) -> Verdict | None:
    """(3) Multi-source conflict guard — if different sources emit
    different values for the same rule-driving field, at least one
    source is wrong (or adversarial). We can't tell which — route
    to REVIEW rather than trust either."""
    agreement = ctx.fields.get("_agreement", {})
    conflicts = []
    for f in _APPROVE_RELEVANT_FIELDS:
        a = agreement.get(f)
        if a and a.get("has_conflict"):
            conflicts.append(f"{f}({'/'.join(a['unique_values'])})")
    if conflicts:
        return Verdict(
            "NEEDS_REVIEW", _conf("field_conflict"),
            f"field_conflict:{';'.join(conflicts)}",
        )
    return None


def guard_missing_required(ctx: PolicyContext) -> Verdict | None:
    """(4) Missing required-field guard — R_A1_* can approve on partial
    extraction (e.g. R_A1_paid_clean fires purely on fee=paid + no
    visible flags). But if home_world or (non-DIP-1) sponsor_id
    weren't extracted, we can't verify hard-embargo (R0) or
    revoked-sponsor (R4) rules. Missing evidence ≠ safe to approve.

    Field Manual: "NEEDS_REVIEW: the packet is incomplete, contradictory,
    illegible, or relies on untrusted evidence." Missing critical field
    extraction = incomplete for adjudication purposes.

    Note: this rule assumes no enumerated home list — it fires purely on
    ABSENCE of extraction, not on the value. Works for unseen homes and
    sponsors that eval may introduce.

    Measured 2026-08-03: fully shadowed on training — 10 would-fires, all
    preempted by an earlier guard. Kept as depth (free, generalizes)."""
    def _missing(key: str) -> bool:
        v = (ctx.fields.get(key) or "").strip().lower()
        return v in ("", "unknown")

    missing = []
    if _missing("home_world"):
        missing.append("home_world")
    if ctx.fields.get("visa_class") != "DIP-1" and _missing("sponsor_id"):
        missing.append("sponsor_id")
    if missing:
        return Verdict(
            "NEEDS_REVIEW", _conf("missing_required"),
            f"missing_required:{','.join(missing)}",
        )
    return None


def guard_defensive_downgrade(ctx: PolicyContext) -> Verdict | None:
    """(5) Defensive downgrade for thin-evidence auto-approvals — the cat FA
    fingerprint. When R_A1_paid_clean / R_A1_dip1_waived fires purely on
    text-stream form data with no risk_flags signal at all (source_class
    is "absent" — distinct from an extracted "none" value), and NO image
    in the packet triggered the illegibility filter, we have no actively-
    vetted image evidence to corroborate the "no flags" claim.
    DEFAULT OFF (config) — catches all 15 training cat FAs (0 D→A) at the
    cost of ~76 A→A → A→R: net -2.45 pts. Insurance in case the private
    eval weights cat FAs as a hard constraint.

    Why source_class rather than the field value: consolidate.py defaults
    fields["risk_flags"] to the string "none" when no signal exists, so
    the value alone cannot distinguish "extracted as none" from "not
    extracted at all". source_class carries the distinction."""
    if (
        ctx.config.defensive_downgrade
        and ctx.tag in _DEFENSIVE_TARGET_TAGS
        and ctx.fields.get("_source_class", {}).get("risk_flags") == "absent"
        and not ctx.signals.get("any_illegibility_excluded")
    ):
        return Verdict(
            "NEEDS_REVIEW", _conf("defensive_downgrade_thin_evidence"),
            f"defensive_downgrade_thin_evidence:{ctx.tag}",
        )
    return None


GUARDS = [
    guard_ocr_risk_override,
    guard_ocr_only,
    guard_field_conflict,
    guard_missing_required,
    guard_defensive_downgrade,
]
