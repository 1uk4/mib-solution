#!/usr/bin/env python3
"""L7 — Decision policy.

Safety guards applied on top of L6's rule output. Non-APPROVE decisions
pass through untouched. APPROVE decisions face two checks (order matters):

    1. OCR risk-signal override — V2's evaluate_ocr_signal on image OCR
       text. If risk keywords present, override to DENIED/REVIEW per the
       signal's severity.

    2. OCR-only field guard — if V1's approval was driven by fields
       populated ONLY from image OCR (no text-stream backing), downgrade
       to NEEDS_REVIEW. This enforces the challenge thesis principle:
       don't approve based on OCR-derived clean-looking data. OCR can
       fill gaps for extraction score, but approval requires text-stream
       corroboration.

Guard #2 is inherently conservative — it will downgrade some correct
APPROVEs that happened to derive from OCR alone. The safety argument is
that generalizing to unseen adversarial packets requires this rule
regardless of training-set score impact.
"""
from __future__ import annotations

from v3.ocr_signal import evaluate_ocr_signal


# Fields that participate in V1's APPROVE rules. If any of these came
# from OCR alone, don't let R_A1_* fire the approval.
_APPROVE_RELEVANT_FIELDS = (
    "fee_status", "visa_class", "sponsor_id", "home_world", "risk_flags",
)


def apply_policy(
    fields: dict,
    adj: str, conf: float, tag: str,
    signals: dict,
) -> tuple[str, float, str]:
    """Return final (adj, conf, tag). May override L6's decision."""
    if adj != "APPROVED":
        return adj, conf, tag

    # (1) OCR risk-signal override — direct evidence beats safety guards
    ocr_text = "\n".join(text for _sid, text in signals["image_ocr"])
    ocr_signal = evaluate_ocr_signal(ocr_text)
    if ocr_signal is not None:
        return ocr_signal

    # (2) OCR-only field guard — no rule-driving field may be OCR-only
    source_class = fields.get("_source_class", {})
    ocr_only = [
        f for f in _APPROVE_RELEVANT_FIELDS
        if source_class.get(f) == "ocr_only"
    ]
    if ocr_only:
        return (
            "NEEDS_REVIEW", 0.65,
            f"ocr_only_downgrade:{','.join(ocr_only)}",
        )

    # (3) Multi-source conflict guard — if different sources emit
    # different values for the same rule-driving field, at least one
    # source is wrong (or adversarial). We can't tell which — route
    # to REVIEW rather than trust either.
    agreement = fields.get("_agreement", {})
    conflicts = []
    for f in _APPROVE_RELEVANT_FIELDS:
        a = agreement.get(f)
        if a and a.get("has_conflict"):
            conflicts.append(f"{f}({'/'.join(a['unique_values'])})")
    if conflicts:
        return (
            "NEEDS_REVIEW", 0.65,
            f"field_conflict:{';'.join(conflicts)}",
        )

    # (4) Missing required-field guard — R_A1_* can approve on partial
    # extraction (e.g. R_A1_paid_clean fires purely on fee=paid + no
    # visible flags). But if home_world or (non-DIP-1) sponsor_id
    # weren't extracted, we can't verify hard-embargo (R0) or
    # revoked-sponsor (R4) rules. Missing evidence ≠ safe to approve.
    #
    # Field Manual: "NEEDS_REVIEW: the packet is incomplete, contradictory,
    # illegible, or relies on untrusted evidence." Missing critical field
    # extraction = incomplete for adjudication purposes.
    #
    # Note: this rule assumes no enumerated home list — it fires purely on
    # ABSENCE of extraction, not on the value. Works for unseen homes and
    # sponsors that eval may introduce.
    def _missing(key: str) -> bool:
        v = (fields.get(key) or "").strip().lower()
        return v in ("", "unknown")

    missing = []
    if _missing("home_world"):
        missing.append("home_world")
    if fields.get("visa_class") != "DIP-1" and _missing("sponsor_id"):
        missing.append("sponsor_id")
    if missing:
        return (
            "NEEDS_REVIEW", 0.65,
            f"missing_required:{','.join(missing)}",
        )

    return adj, conf, tag
