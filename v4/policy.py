#!/usr/bin/env python3
"""L7 — Decision policy.

Safety adjustments applied on top of L6's rule output: three upgrades
(which may promote any verdict), one trust bypass, and five guards
(which only ever demote an APPROVED).

DOES: apply the ordered upgrade → gate → bypass → guard chain below.
An upgrade that fires returns immediately — its result deliberately
skips every guard. The trust bypass exempts adjudicator-finding
approvals from the guards (a signed manual note outranks form-derived
suspicion by Field Manual precedence).
DOES NOT: extract anything, re-litigate source trust (L3 owns trust),
or emit any tag without a confidence-registry entry.

NOTE: this single-file verbatim port (from v3/policy.py) exists for the
P4 parity gate; P5 replaces it with the staged v4/policy/ package behind
the same public signature.
"""
from __future__ import annotations

from v4.config import Config, CONFIG
from v4.confidence import conf as _conf
from v4.evidence import evaluate_ocr_signal, has_clean_biometric


# Fields that participate in the APPROVE rules. If any of these came
# from OCR alone, don't let R_A1_* fire the approval.
_APPROVE_RELEVANT_FIELDS = (
    "fee_status", "visa_class", "sponsor_id", "home_world", "risk_flags",
)

# When re-using evaluate_ocr_signal on FALLBACK cases, skip tags whose
# precision depends on positive field extraction we DON'T have here.
# `ocr_revoked_sponsor` fires on any revoked SPN code appearing in OCR
# text — but in a fallback packet (sponsor_id unextracted), we can't
# tell if the code is the applicant's sponsor or a mention in a "these
# sponsors are revoked" reference list. Measured 17% precision on the
# fallback cohort (4 false-DENIED on truth-APPROVED cases).
_FALLBACK_OCR_SIGNAL_EXCLUDE_PREFIXES = ("ocr_revoked_sponsor",)

# L6 approve rules that we consider fair game for defensive downgrade.
# These fire on text-stream form data alone and don't require any image
# corroboration — the exact pattern cat FAs exploit.
_DEFENSIVE_TARGET_TAGS = ("R_A1_paid_clean", "R_A1_dip1_waived")


def apply_policy(
    fields: dict,
    adj: str, conf: float, tag: str,
    signals: dict,
    config: Config = CONFIG,
) -> tuple[str, float, str]:
    """Return final (adj, conf, tag). May override L6's decision."""
    # Waived-fee non-DIP promotion: L6 defensively downgrades all non-DIP
    # waived cases to REVIEW because it can't tell a genuine hardship
    # waiver from boilerplate. When a Biometric Scan Slip is present with
    # a cleanly-read "Observed flags: none", the applicant has been
    # affirmatively scanned clean — a distinct positive-evidence signal
    # that the Field Manual's "visible hardship waiver" contemplates.
    # Measured on training: 6/22 truth=APPROVED, 0/6 truth=DENIED, 2/15
    # truth=REVIEW — 0 cat FA risk, small R→A cost.
    if (
        config.upgrade_waived_on_biometric
        and tag == "R_A1_non_dip_waived_TO_REVIEW"
        and has_clean_biometric(signals)
    ):
        return ("APPROVED", _conf("R_A1_non_dip_waived_biometric_clean"),
                "R_A1_non_dip_waived_biometric_clean")

    # R3_unpaid + visible waiver upgrade. Field Manual §Fee Rules:
    # "unpaid: deny unless a visible waiver applies." Symmetric to the
    # non-DIP waived biometric upgrade above — same visible-waiver signal.
    # DEFAULT OFF (config) — measured -0.22 pts, +10 cat FAs: biometric
    # slips appear on many R3_unpaid packets as boilerplate, so the rule
    # wrongly approves D-truth unpaid cases. Kept as insurance; re-enable
    # only with a stricter waiver-evidence signal.
    if (
        config.upgrade_unpaid_on_waiver
        and tag == "R3_unpaid"
        and has_clean_biometric(signals)
    ):
        return ("APPROVED", _conf("R3_unpaid_biometric_waiver"),
                "R3_unpaid_biometric_waiver")

    # FALLBACK OCR upgrade — when L6 falls through due to extraction failure
    # (missing visa/fee), but image OCR still contains explicit signals
    # (Finding line, adjudicator reason phrases, named disqualifying flags,
    # embargoed home names, deny/review stems), promote the verdict per the
    # same evaluator used as L7 guard #1 for approvals. Measured on 290
    # fallback packets: 20% fire rate, +14 D→D wins, 0 cat FAs introduced.
    # We whitelist-filter out signal families whose precision requires
    # positive field extraction we don't have in fallback context.
    if config.fallback_ocr_upgrade and tag == "FALLBACK_extraction_fail":
        ocr_text = "\n".join(text for _sid, text in signals.get("image_ocr", []))
        ocr_signal = evaluate_ocr_signal(ocr_text)
        if ocr_signal is not None:
            _adj, _c, sig_tag = ocr_signal
            if not sig_tag.startswith(_FALLBACK_OCR_SIGNAL_EXCLUDE_PREFIXES):
                return ocr_signal
        # No signal (or excluded) → keep the fallback verdict as-is

    if adj != "APPROVED":
        return adj, conf, tag

    # Adjudicator finding is Evidence Precedence #1 (per FIELD_MANUAL) and
    # is 100% accurate across all 162 training cases with a Finding line.
    # It is emitted only from trusted text streams (never from image OCR —
    # see v4/signals.py), so it has already passed injection/redaction
    # filtering. The form-derived guards below exist to protect R_A1_*
    # approvals from being fooled by form data; they should not second-
    # guess a signed manual note that outranks form data by design.
    if config.trust_finding and tag.startswith("R_ADJUDICATOR_FINDING"):
        return adj, conf, tag

    # (1) OCR risk-signal override — direct evidence beats safety guards
    ocr_text = "\n".join(text for _sid, text in signals["image_ocr"])
    ocr_signal = evaluate_ocr_signal(ocr_text)
    if ocr_signal is not None:
        return ocr_signal

    # (2) OCR-only field guard — DEFAULT ON (config.ocr_only_guard).
    #
    # [Comment corrected 2026-08-03 during the v4 rewrite: the v3 comment
    # block said "DEFAULT OFF as of 2026-08-03" while the code default was
    # ON — the comment described a briefly-tested state, not the shipped
    # one. The measured basis for ON:]
    #
    # Purpose: adversarial defense — if an approve-relevant field came only
    # from image OCR (no text-stream backing), don't trust the approval.
    # The concern is fake documents inserted into images that OCR reads as
    # legitimate.
    #
    # Measurement (2026-08-03): disabling the guard won 16 A→A but
    # uncovered +10 cat FAs across MULTIPLE approve-rule paths
    # (R_A1_paid_clean, R_A1_dip1_waived, the biometric upgrades, and the
    # fallback OCR upgrade) — net -0.22 pts. When it fires SOLO its
    # precision is only ~35%, but the cat-FA protection spans paths that
    # blanket disabling exposes. Kept ON.
    #
    # Note: Field Manual §Trusted Evidence ranks visible image-based
    # evidence ABOVE the machine-readable text layer, so this guard's
    # implicit "text > image" ordering is backwards from the Manual —
    # a known tension, resolved empirically in favor of keeping the guard
    # (see spec Pitfall 5).
    source_class = fields.get("_source_class", {})
    if config.ocr_only_guard:
        ocr_only = [
            f for f in _APPROVE_RELEVANT_FIELDS
            if source_class.get(f) == "ocr_only"
        ]
        if ocr_only:
            return (
                "NEEDS_REVIEW", _conf("ocr_only_downgrade"),
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
            "NEEDS_REVIEW", _conf("field_conflict"),
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
            "NEEDS_REVIEW", _conf("missing_required"),
            f"missing_required:{','.join(missing)}",
        )

    # (5) Defensive downgrade for thin-evidence auto-approvals — the cat FA
    # fingerprint. When R_A1_paid_clean / R_A1_dip1_waived fires purely on
    # text-stream form data with no risk_flags signal at all (source_class
    # is "absent" — distinct from an extracted "none" value), and NO image
    # in the packet triggered the illegibility filter, we have no actively-
    # vetted image evidence to corroborate the "no flags" claim. Per project
    # policy on invisible-evidence packets, defensive REVIEW is correct.
    # DEFAULT OFF (config) — catches all 15 training cat FAs (0 D→A) at the
    # cost of ~76 A→A → A→R: net -2.45 pts. Insurance in case the private
    # eval weights cat FAs as a hard constraint.
    #
    # Why source_class rather than the field value: consolidate.py defaults
    # fields["risk_flags"] to the string "none" when no signal exists, so
    # the value alone cannot distinguish "extracted as none" from "not
    # extracted at all". source_class carries the distinction.
    if (
        config.defensive_downgrade
        and tag in _DEFENSIVE_TARGET_TAGS
        and source_class.get("risk_flags") == "absent"
        and not signals.get("any_illegibility_excluded")
    ):
        return (
            "NEEDS_REVIEW", _conf("defensive_downgrade_thin_evidence"),
            f"defensive_downgrade_thin_evidence:{tag}",
        )

    return adj, conf, tag
