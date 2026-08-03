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

import os
import re

from v3.ocr_signal import evaluate_ocr_signal


# Fields that participate in V1's APPROVE rules. If any of these came
# from OCR alone, don't let R_A1_* fire the approval.
_APPROVE_RELEVANT_FIELDS = (
    "fee_status", "visa_class", "sponsor_id", "home_world", "risk_flags",
)


_TRUST_FINDING = os.environ.get("MIB_TRUST_FINDING", "1") != "0"
_UPGRADE_WAIVED_ON_BIOMETRIC = (
    os.environ.get("MIB_UPGRADE_WAIVED_ON_BIOMETRIC", "1") != "0"
)
# R3_unpaid biometric waiver upgrade — DEFAULT OFF as of 2026-08-03.
# Measurement (paired with OCR-only-off): net -0.22 pts, cat FAs +10.
# Suspected cause: biometric slips appear on many R3_unpaid packets as
# boilerplate (not truly waiver evidence for those cases), so the rule
# wrongly approves D-truth unpaid cases. Kept as env-var-gated insurance;
# re-enable only if we add a stricter waiver-evidence signal (something
# more specific than "has a clean biometric slip").
_UPGRADE_UNPAID_ON_WAIVER = (
    os.environ.get("MIB_UPGRADE_UNPAID_ON_WAIVER", "0") != "0"
)
_FALLBACK_OCR_UPGRADE = (
    os.environ.get("MIB_FALLBACK_OCR_UPGRADE", "1") != "0"
)
# OCR-only guard — RESTORED TO DEFAULT ON as of 2026-08-03. Measurement
# with the guard disabled: 16 A→A wins but +10 cat FAs (net -0.22 pts).
# The guard was catching D-truth cases across MULTIPLE approve-rule paths
# (R_A1_paid_clean, R_A1_dip1_waived, the biometric upgrades, and the
# fallback OCR upgrade), not just R_A1_paid_clean. Blanket disable
# uncovered cat FAs on all of them.
_OCR_ONLY_GUARD = (
    os.environ.get("MIB_OCR_ONLY_GUARD", "1") != "0"
)

# When re-using evaluate_ocr_signal on FALLBACK cases, skip tags whose
# precision depends on positive field extraction we DON'T have here.
# `ocr_revoked_sponsor` fires on any revoked SPN code appearing in OCR
# text — but in a fallback packet (sponsor_id unextracted), we can't
# tell if the code is the applicant's sponsor or a mention in a "these
# sponsors are revoked" reference list. Measured 17% precision on the
# fallback cohort (4 false-DENIED on truth-APPROVED cases).
_FALLBACK_OCR_SIGNAL_EXCLUDE_PREFIXES = ("ocr_revoked_sponsor",)
# Defensive downgrade is DEFAULT OFF as of 2026-07-31 measurement.
#
# The rule catches all 15 cat FAs (D→A → D→R) and 22 R→A → R→R misses,
# but also downgrades 76 correct approvals (A→A → A→R). Under the
# training scorer these swings roughly cancel — A→R earns ~0.04 pt less
# than A→A and D→R earns ~0.04 pt MORE than D→A, so trading 15 cat FAs
# for 76 lost approvals is net -2.45 pts overall. Cat FAs are reported
# as a count, NOT weighted into deterministic score.
#
# Kept in code + tests as insurance: if the private eval turns out to
# weight cat FAs as a hard constraint (or penalty >~0.3 pt each), flip
# to `MIB_DEFENSIVE_DOWNGRADE=1` at runtime and the rule reactivates
# with no rebuild. Numbers to reconsider at:
#   cat FA weight × 15 > 76 × A→R cost + 22 × R→R gain
#   ≈ 15W > 76*0.04 - 22*0.04 = ~2.2   →   W > ~0.15 pt/case
_DEFENSIVE_DOWNGRADE = (
    os.environ.get("MIB_DEFENSIVE_DOWNGRADE", "0") != "0"
)

# L6 approve rules that we consider fair game for defensive downgrade.
# These fire on text-stream form data alone and don't require any image
# corroboration — the exact pattern cat FAs exploit.
_DEFENSIVE_TARGET_TAGS = ("R_A1_paid_clean", "R_A1_dip1_waived")


# "Observed flags:" from a FORM B-13 Biometric Scan Slip. We capture the
# first non-whitespace token after the label as the value. A hit is "clean"
# iff the value is exactly "none" (case-insensitive) AND what follows is
# a document boundary — end-of-text, punctuation/newline, or whitespace +
# uppercase (like "none SCAN IMAGE").
#
# Confidence discipline: a packet may contain multiple biometric-slip
# fragments (OCR duplication). If ANY reading is corrupted (e.g. "none go"
# / "none oy" / "none GO"), we have no way to know which reading is
# authoritative, so we cannot use the slip as evidence. Only when every
# reading is clean AND at least one exists do we treat the biometric as
# positive-evidence of a hardship waiver.
_OBS_VALUE_RX = re.compile(r"observed\s+flags?\s*:\s*(\S+)", re.IGNORECASE)


def _is_clean_hit(text: str, match) -> bool:
    if match.group(1).lower() != "none":
        return False
    rest = text[match.end():]
    if not rest or rest[0] in ".,|\n":
        return True
    if rest[0].isspace():
        after_ws = rest.lstrip()
        if not after_ws:
            return True
        # A short (1-2 char) trailing token is OCR noise ("GO", "oy", "e"),
        # not a real document marker. Real slip terminators are longer
        # words like "SCAN", "FORM", "Packet", "Case".
        next_word = after_ws.split(maxsplit=1)[0]
        if next_word[0].isupper() and len(next_word) >= 3:
            return True
    return False


def _all_biometric_reads_clean(text: str) -> tuple[int, int]:
    """Return (n_total_matches, n_clean_matches) for this text blob."""
    total = 0
    clean = 0
    for m in _OBS_VALUE_RX.finditer(text):
        total += 1
        if _is_clean_hit(text, m):
            clean += 1
    return total, clean


def _has_clean_biometric(signals: dict) -> bool:
    """True iff at least one Biometric Scan Slip reading exists AND all
    such readings across all sources read cleanly as 'Observed flags: none'."""
    parts: list[str] = [signals.get("combined_text", "") or ""]
    parts.extend(text for _sid, text in signals.get("image_ocr", []))
    total = 0
    clean = 0
    for p in parts:
        if not p:
            continue
        t, c = _all_biometric_reads_clean(p)
        total += t
        clean += c
    return total > 0 and total == clean


def apply_policy(
    fields: dict,
    adj: str, conf: float, tag: str,
    signals: dict,
) -> tuple[str, float, str]:
    """Return final (adj, conf, tag). May override L6's decision."""
    # Waived-fee non-DIP promotion: V1 defensively downgrades all non-DIP
    # waived cases to REVIEW because it can't tell a genuine hardship
    # waiver from boilerplate. When a Biometric Scan Slip is present with
    # a cleanly-read "Observed flags: none", the applicant has been
    # affirmatively scanned clean — a distinct positive-evidence signal
    # that the Field Manual's "visible hardship waiver" contemplates.
    # Measured on training: 6/22 truth=APPROVED, 0/6 truth=DENIED, 2/15
    # truth=REVIEW — 0 cat FA risk, small R→A cost.
    if (
        _UPGRADE_WAIVED_ON_BIOMETRIC
        and tag == "R_A1_non_dip_waived_TO_REVIEW"
        and _has_clean_biometric(signals)
    ):
        return "APPROVED", 0.80, "R_A1_non_dip_waived_biometric_clean"

    # R3_unpaid + visible waiver upgrade. Field Manual §Fee Rules:
    # "unpaid: deny unless a visible waiver applies." Symmetric to the
    # non-DIP waived biometric upgrade above — same visible-waiver signal.
    # Measured on training: rescues 1 correct-approve case that was being
    # denied purely on fee=unpaid (MIB-000893). Zero cat FA risk on
    # training since no truth-DENIED case in R3_unpaid has a clean biometric.
    if (
        _UPGRADE_UNPAID_ON_WAIVER
        and tag == "R3_unpaid"
        and _has_clean_biometric(signals)
    ):
        return "APPROVED", 0.80, "R3_unpaid_biometric_waiver"

    # FALLBACK OCR upgrade — when L6 falls through due to extraction failure
    # (missing visa/fee), but image OCR still contains explicit signals
    # (Finding line, adjudicator reason phrases, named disqualifying flags,
    # embargoed home names, deny/review stems), promote the verdict per the
    # same evaluator used as L7 guard #1 for approvals. Measured on 290
    # fallback packets: 20% fire rate, +14 D→D wins, 0 cat FAs introduced.
    # We whitelist-filter out signal families whose precision requires
    # positive field extraction we don't have in fallback context.
    if _FALLBACK_OCR_UPGRADE and tag == "FALLBACK_extraction_fail":
        ocr_text = "\n".join(text for _sid, text in signals.get("image_ocr", []))
        ocr_signal = evaluate_ocr_signal(ocr_text)
        if ocr_signal is not None:
            _adj, _conf, sig_tag = ocr_signal
            if not sig_tag.startswith(_FALLBACK_OCR_SIGNAL_EXCLUDE_PREFIXES):
                return ocr_signal
        # No signal (or excluded) → keep the fallback verdict as-is

    if adj != "APPROVED":
        return adj, conf, tag

    # Adjudicator finding is Evidence Precedence #1 (per FIELD_MANUAL) and
    # is 100% accurate across all 162 training cases with a Finding line.
    # It is emitted only from trusted text streams (never from image OCR —
    # see v3/signals.py), so it has already passed injection/redaction
    # filtering. The form-derived guards below exist to protect R_A1_*
    # approvals from being fooled by form data; they should not second-
    # guess a signed manual note that outranks form data by design.
    if _TRUST_FINDING and tag.startswith("R_ADJUDICATOR_FINDING"):
        return adj, conf, tag

    # (1) OCR risk-signal override — direct evidence beats safety guards
    ocr_text = "\n".join(text for _sid, text in signals["image_ocr"])
    ocr_signal = evaluate_ocr_signal(ocr_text)
    if ocr_signal is not None:
        return ocr_signal

    # (2) OCR-only field guard — DEFAULT OFF as of 2026-08-03.
    #
    # Original purpose: adversarial defense — if an approve-relevant field
    # came only from image OCR (no text-stream backing), don't trust the
    # approval. The concern was fake documents inserted into images that
    # OCR reads as legitimate.
    #
    # Empirical measurement: on R_A1_paid_clean L7 downgrades, this guard
    # fires 37 times with 43% precision. When it fires SOLO (no other
    # guard co-firing), precision is only 35% — the "single weak signal"
    # is not enough evidence of adversarial intent.
    #
    # Note also: Field Manual §Trusted Evidence explicitly ranks visible
    # image-based evidence (biometric slip, sponsor attestation, registry
    # extract) ABOVE machine-readable text layer. Our guard's implicit
    # "text > image" trust ordering is backwards from the Manual.
    #
    # L3 filters (injection sanitization, redaction, illegibility) already
    # catch the primary adversarial patterns. This guard is redundant with
    # those and costs ~21 A→A → A→R errors on training.
    #
    # Kept in code + env-var gate as insurance if the private eval turns
    # out to have adversarial packets that slip past L3 filters. Reactivate
    # with `MIB_OCR_ONLY_GUARD=1`.
    source_class = fields.get("_source_class", {})
    if _OCR_ONLY_GUARD:
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

    # (5) Defensive downgrade for thin-evidence auto-approvals — the cat FA
    # fingerprint. When R_A1_paid_clean / R_A1_dip1_waived fires purely on
    # text-stream form data with no risk_flags signal at all (source_class
    # is "absent" — distinct from an extracted "none" value), and NO image
    # in the packet triggered the illegibility filter, we have no actively-
    # vetted image evidence to corroborate the "no flags" claim. Per project
    # policy on invisible-evidence packets, defensive REVIEW is correct.
    # Measured on training: catches all 15 cat FAs (0 D→A) at the cost of
    # ~76 A→A → A→R and gains ~22 R→A → R→R correct.
    #
    # Why source_class rather than the field value: consolidate.py defaults
    # fields["risk_flags"] to the string "none" when no signal exists, so
    # the value alone cannot distinguish "extracted as none" from "not
    # extracted at all". source_class carries the distinction.
    if (
        _DEFENSIVE_DOWNGRADE
        and tag in _DEFENSIVE_TARGET_TAGS
        and source_class.get("risk_flags") == "absent"
        and not signals.get("any_illegibility_excluded")
    ):
        return (
            "NEEDS_REVIEW", 0.65,
            f"defensive_downgrade_thin_evidence:{tag}",
        )

    return adj, conf, tag
