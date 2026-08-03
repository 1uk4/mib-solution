#!/usr/bin/env python3
"""Confidence registry — every confidence value the pipeline can emit.

DOES: hold all 31 confidence values in one table with their empirical
basis (fire count + measured accuracy on the 1000-packet training set),
and define how emitted rule tags map to registry keys (exact / prefix /
alias) for the completeness test.

DOES NOT: decide anything. Rules and policy stages look values up by key;
no caller may hardcode a confidence literal (the registry completeness
test enforces the reverse direction: every emittable tag resolves here).

Calibration principle (brief Pitfall 3): Brier score is minimized when
confidence equals P(correct | rule fired), so every value below is the
rule's measured accuracy, capped at 0.99 for eval-set tail headroom.
Values were calibrated 2026-08-03 from features.jsonl via
v3/dev/analysis/calibrate_confidence.py; preserved exactly per brief
Rule 1. Sources consolidated: v1/solution.py:552-568 (15 L6 entries),
v3/policy.py literals (6 L7 entries), v3/ocr_signal.py literals
(10 evidence entries).
"""
from __future__ import annotations

from typing import NamedTuple


class RuleConf(NamedTuple):
    value: float          # emitted confidence == measured accuracy (≤0.99)
    fires: int | None     # fire count on the 1000-packet training set
    accuracy: float | None  # measured accuracy for those fires
    note: str             # provenance / measurement context


CONFIDENCE: dict[str, RuleConf] = {
    # ---- L6 rule tags (v1/solution.py:552-568, empirically calibrated) ----
    "R_ADJUDICATOR_FINDING": RuleConf(0.99, 162, 1.00,
        "Finding: line, evidence precedence #1; 100% correct post-bypass. "
        "Raw features.jsonl showed 88.9% but that reflects the "
        "pre-finding-trust-bypass pipeline."),
    "R0_hard_embargo":       RuleConf(0.99, 34, 1.00, "TRAPPIST-1e / Eris Relay always DENY (was 0.98)"),
    "R1_transit7":           RuleConf(0.97, 37, 0.97, "TRANSIT-7 always DENY (was 0.98)"),
    "R2_disqualifier":       RuleConf(0.99, 56, 1.00, "disqualifying flag always DENY (was 0.98)"),
    "R3_unpaid":             RuleConf(0.96, 27, 0.96, "unpaid fee DENY (was 0.98)"),
    "R4_revoked_sponsor":    RuleConf(0.98, 62, 0.98, "revoked sponsor + non-DIP-1 DENY (was 0.95)"),
    "R4b_embargoed_home":    RuleConf(0.99, 11, 1.00, "soft-embargo home + non-DIP-1 DENY (was 0.90)"),
    "R5_stale":              RuleConf(0.96, 24, 0.96, "stale arrival + non-DIP-1 DENY (was 0.95)"),
    "R_R1_flag_present":     RuleConf(0.94, 70, 0.94, "review-only flag -> REVIEW; post-reorder count (was 0.90)"),
    "R_R2_unknown_fee":      RuleConf(0.99, 24, 1.00, "unknown fee -> REVIEW (was 0.98)"),
    "R_A1_paid_clean":       RuleConf(0.69, 168, 0.69, "paid + clean auto-approve; L7-kept accuracy (was 0.94)"),
    "R_A1_dip1_waived":      RuleConf(0.79, 22, 0.79, "DIP-1 waived auto-approve; L7-kept accuracy (was 0.92)"),
    "R_A1_non_dip_waived":   RuleConf(0.37, 43, 0.37,
        "non-DIP waived -> REVIEW; accuracy after biometric-upgrade splits (was 0.70). "
        "Emitted tag is R_A1_non_dip_waived_TO_REVIEW (see ALIASES)."),
    "FALLBACK_extraction_fail": RuleConf(0.34, 251, 0.34,
        "extraction-failure catch-all; accuracy after rule-reorder pulled "
        "R_R1 cases out (was 0.50). Split candidacy tracked as B2-1."),
    "FALLBACK_missing_arrival": RuleConf(0.46, 13, 0.46, "missing arrival -> REVIEW (was 0.60)"),

    # ---- L7 policy override tags (v3/policy.py literals) ----
    "R_A1_non_dip_waived_biometric_clean": RuleConf(0.80, 8, None,
        "biometric-clean waived upgrade; 6/22 A-truth, 0/6 D-truth, 2/15 R-truth"),
    "R3_unpaid_biometric_waiver": RuleConf(0.80, None, None,
        "unpaid biometric waiver upgrade — DEFAULT OFF (config), insurance path"),
    "ocr_only_downgrade":     RuleConf(0.65, 37, 0.43,
        "OCR-only field guard; 43% precision overall, 35% when firing solo"),
    "field_conflict":         RuleConf(0.65, None, None, "multi-source conflict guard -> REVIEW"),
    "missing_required":       RuleConf(0.65, None, None, "missing home_world / sponsor_id guard -> REVIEW"),
    "defensive_downgrade_thin_evidence": RuleConf(0.65, 91, None,
        "defensive downgrade — DEFAULT OFF (config); catches all 15 cat FAs, "
        "costs 76 correct approvals (net -2.45 pts)"),

    # ---- Evidence tags (v3/ocr_signal.py literals) ----
    "ocr_finding:DENIED":     RuleConf(0.95, 43, 1.00, "explicit OCR Finding: DENIED; 100% marginal"),
    "ocr_finding:REVIEW":     RuleConf(0.85, 34, 1.00, "explicit OCR Finding: NEEDS_REVIEW; 100% marginal"),
    "ocr_reason:damaged_registry":     RuleConf(0.92, 27, 1.00, "adjudicator reason phrase; 27/27 DENIED"),
    "ocr_reason:visible_policy_notes": RuleConf(0.92, 29, 1.00, "adjudicator reason phrase; 29/29 DENIED"),
    "ocr_disq_flag":          RuleConf(0.90, None, None, "named disqualifying flag in OCR -> DENIED"),
    "ocr_revoked_sponsor":    RuleConf(0.90, None, None,
        "revoked SPN code in OCR -> DENIED; prefix-excluded on the fallback "
        "path (17% precision there — reference-list mentions)"),
    "ocr_embargo_home":       RuleConf(0.90, None, None, "hard-embargo home name in OCR -> DENIED"),
    "ocr_deny_stem":          RuleConf(0.85, None, None, "BIOHAZ/EMBARG/DENIE stem -> DENIED; ocr_has_biohazard 18/18 DENIED"),
    "ocr_review_flag":        RuleConf(0.85, None, None, "review-only flag in OCR -> REVIEW"),
    "ocr_review_stem":        RuleConf(0.65, None, None, "REVOK/RESCIN stem -> REVIEW; may be prose about a revocation"),
}


def conf(key: str) -> float:
    """Confidence for a registry key. KeyError = registry bug, on purpose."""
    return CONFIDENCE[key].value


# ---------------------------------------------------------------------------
# Emitted-tag -> registry-key mapping (for the completeness test)
# ---------------------------------------------------------------------------
# Emitted tags come in three shapes (spec §4.2):
#   EXACT   — the tag string is (or aliases to) a registry key. Includes four
#             colon-carrying CONSTANTS (ocr_finding:* / ocr_reason:*) that
#             need exact match despite looking parameterized.
#   PREFIX  — parameterized families; emitted tag starts with the family
#             prefix, registry key is the family base. field_conflict: is the
#             only open-ended one (embeds arbitrary extracted values).
#   ALIASES — emitted tag differs from its registry key.

EXACT: frozenset[str] = frozenset({
    # L6 plain tags
    "R1_transit7", "R4_revoked_sponsor", "R4b_embargoed_home", "R3_unpaid",
    "R5_stale", "R_R1_flag_present", "FALLBACK_extraction_fail",
    "FALLBACK_missing_arrival", "R_R2_unknown_fee", "R_A1_paid_clean",
    "R_A1_dip1_waived", "R_A1_non_dip_waived_TO_REVIEW",
    # L7 policy exact tags
    "R_A1_non_dip_waived_biometric_clean", "R3_unpaid_biometric_waiver",
    # Evidence colon-constants (exact match despite the colon)
    "ocr_finding:DENIED", "ocr_finding:REVIEW",
    "ocr_reason:damaged_registry", "ocr_reason:visible_policy_notes",
})

PREFIX: dict[str, str] = {
    # emitted-tag prefix                      -> registry key
    "R_ADJUDICATOR_FINDING[":                    "R_ADJUDICATOR_FINDING",
    "R0_hard_embargo[":                          "R0_hard_embargo",
    "R2_disqualifier[":                          "R2_disqualifier",
    "ocr_only_downgrade:":                       "ocr_only_downgrade",
    "field_conflict:":                           "field_conflict",
    "missing_required:":                         "missing_required",
    "defensive_downgrade_thin_evidence:":        "defensive_downgrade_thin_evidence",
    "ocr_disq_flag:":                            "ocr_disq_flag",
    "ocr_revoked_sponsor:":                      "ocr_revoked_sponsor",
    "ocr_embargo_home:":                         "ocr_embargo_home",
    "ocr_deny_stem:":                            "ocr_deny_stem",
    "ocr_review_flag:":                          "ocr_review_flag",
    "ocr_review_stem:":                          "ocr_review_stem",
}

ALIASES: dict[str, str] = {
    "R_A1_non_dip_waived_TO_REVIEW": "R_A1_non_dip_waived",
}


def registry_key_for(tag: str) -> str | None:
    """Resolve an emitted tag to its registry key, or None if unknown.

    Exact tags win over prefix families so the ocr_finding:*/ocr_reason:*
    constants never fall through to a prefix rule.
    """
    if tag in EXACT:
        return ALIASES.get(tag, tag)
    for prefix, key in PREFIX.items():
        if tag.startswith(prefix):
            return key
    return None
