#!/usr/bin/env python3
"""L6 — Rule engine.

DOES: turn a consolidated field dict into (adjudication, confidence,
rule_tag) via the ordered rule chain below (absorbed verbatim from
v1/solution.py:571-662 — the proven ruleset).
DOES NOT: know where a value came from or whether the source was trusted
(L3 already guaranteed that), and never overrides itself — L7 (policy)
owns every post-rule adjustment.

Rule order matters: hard-DENY signals that only need a single field fire
BEFORE the extraction-failure fallback so partial extractions can still
catch certain-DENY cases (e.g. we can read home_world but not fee).

Rule tags let downstream layers audit which decisions used inferred rules;
every tag resolves in the confidence registry (v4/confidence.py).
"""
from __future__ import annotations

from datetime import date as Date

from v4.confidence import conf
from v4.vocab import (
    DISQUALIFYING_FLAGS,
    EMBARGOED_HOMES_HARD,
    EMBARGOED_HOMES_SOFT,
    RECEIPT_DATE_PROXY,
    REVOKED_SPONSORS,
    STALE_DAYS,
)


def _flag_set(risk_flags: str) -> set[str]:
    if not risk_flags or risk_flags == "none":
        return set()
    return {f.strip() for f in risk_flags.split("|") if f.strip()}


def _is_stale(arrival: str) -> bool:
    try:
        d = Date.fromisoformat(arrival)
    except (ValueError, TypeError):
        return False
    return (RECEIPT_DATE_PROXY - d).days > STALE_DAYS


def adjudicate(fields: dict) -> tuple[str, float, str]:
    """Return (adjudication, confidence, rule_tag) for a case.

    Order matters: hard-DENY signals that only need a single field fire
    BEFORE the extraction-failure fallback so partial extractions can still
    catch certain-DENY cases (e.g. we can read home_world but not fee).
    """
    visa = fields.get("visa_class", "")
    fee = fields.get("fee_status", "")
    sponsor = fields.get("sponsor_id", "")
    home = fields.get("home_world", "")
    arrival = fields.get("arrival_date", "")
    flags = _flag_set(fields.get("risk_flags", ""))
    finding = fields.get("_finding", "")

    # ---- Adjudicator finding: evidence precedence #1 per FIELD_MANUAL ----
    # When the packet contains a signed manual finding, it overrides all
    # form-derived rules. Training shows this signal is 100% accurate (162
    # cases, 79 DENIED / 50 REVIEW / 33 APPROVED, no mismatches).
    if finding in ("APPROVED", "DENIED", "NEEDS_REVIEW"):
        return finding, conf("R_ADJUDICATOR_FINDING"), f"R_ADJUDICATOR_FINDING[{finding}]"

    # ---- Hard-DENY signals that stand alone (fire regardless of missing fields) ----
    # R0: hard-embargoed home worlds — always DENY, no visa exemption
    if home in EMBARGOED_HOMES_HARD:
        return "DENIED", conf("R0_hard_embargo"), f"R0_hard_embargo[{home}]"

    # R1: TRANSIT-7 visa always DENY (only needs visa field)
    if visa == "TRANSIT-7":
        return "DENIED", conf("R1_transit7"), "R1_transit7"

    # R2: any disqualifying flag always DENY (only needs flags — even single flag word)
    disq = flags & DISQUALIFYING_FLAGS
    if disq:
        tag = f"R2_disqualifier[{'|'.join(sorted(disq))}]"
        return "DENIED", conf("R2_disqualifier"), tag

    # R4: revoked sponsor + non-DIP-1 → DENY (needs both sponsor and visa)
    if sponsor in REVOKED_SPONSORS and visa and visa != "DIP-1":
        return "DENIED", conf("R4_revoked_sponsor"), "R4_revoked_sponsor"

    # R4b: soft-embargoed home (Wolf-1061c) + non-DIP-1 → DENY (needs home and visa)
    if home in EMBARGOED_HOMES_SOFT and visa and visa != "DIP-1":
        return "DENIED", conf("R4b_embargoed_home"), "R4b_embargoed_home"

    # R3: unpaid fee → DENY (only needs fee)
    if fee == "unpaid":
        return "DENIED", conf("R3_unpaid"), "R3_unpaid"

    # R5: stale arrival + non-DIP-1 → DENY (needs arrival and visa)
    if arrival and visa and _is_stale(arrival) and visa != "DIP-1":
        return "DENIED", conf("R5_stale"), "R5_stale"

    # R_R1: any review-only flag (disqualifying handled above) → REVIEW.
    # Placed BEFORE the extraction-failure fallback: if we extracted a
    # review flag, that's high-precision evidence (96% correct empirically),
    # more reliable than falling through to FALLBACK's 41%. Same REVIEW
    # verdict either way — only calibration improves.
    if flags:
        return "NEEDS_REVIEW", conf("R_R1_flag_present"), "R_R1_flag_present"

    # ---- Extraction-failure fallback ----
    # If we can't positively identify visa or fee, we can't safely apply
    # remaining rules and definitely can't approve. Route to REVIEW.
    if not visa or not fee:
        return "NEEDS_REVIEW", conf("FALLBACK_extraction_fail"), "FALLBACK_extraction_fail"

    if not arrival:
        # Missing arrival date → REVIEW per [DOC]
        return "NEEDS_REVIEW", conf("FALLBACK_missing_arrival"), "FALLBACK_missing_arrival"

    # ---- REVIEW pipeline (§10) ----
    if fee == "unknown":
        return "NEEDS_REVIEW", conf("R_R2_unknown_fee"), "R_R2_unknown_fee"

    # ---- APPROVE (§10) ----
    if fee == "paid":
        return "APPROVED", conf("R_A1_paid_clean"), "R_A1_paid_clean"
    if fee == "waived":
        if visa == "DIP-1":
            return "APPROVED", conf("R_A1_dip1_waived"), "R_A1_dip1_waived"
        # non-DIP-1 waived: measured on training set, REVIEW beats APPROVE
        # by 38 raw and eliminates 10 catastrophic false approvals. The
        # "hardship waiver" assumption was too aggressive without OCR to
        # verify the visible waiver document actually exists.
        return "NEEDS_REVIEW", conf("R_A1_non_dip_waived"), "R_A1_non_dip_waived_TO_REVIEW"

    # Should be unreachable given fee_status enum
    return "NEEDS_REVIEW", conf("FALLBACK_extraction_fail"), "FALLBACK_extraction_fail"


def apply_rules(fields: dict) -> tuple[str, float, str]:
    """Return (adjudication, confidence, rule_tag) for a consolidated field dict."""
    return adjudicate(fields)
