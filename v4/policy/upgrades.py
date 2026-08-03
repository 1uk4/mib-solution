#!/usr/bin/env python3
"""L7 upgrade stages — may PROMOTE any verdict; run before the APPROVED gate.

DOES: hold the three upgrade stages, in their required order:
biometric-clean → unpaid-waiver → fallback-OCR. A stage returns a Verdict
to fire (the dispatcher returns it immediately, SKIPPING every guard) or
None to pass.
DOES NOT: demote anything (that is guards.py), or bypass guards for
already-approved cases (bypasses.py).

Note the third stage's fall-through: when the fallback packet has no
usable OCR signal (or only an excluded family), it returns None and the
case continues to the gate — unlike the first two stages, firing is
conditional on the signal's family, not just on the tag.
"""
from __future__ import annotations

from v4.confidence import conf as _conf
from v4.evidence import evaluate_ocr_signal, has_clean_biometric
from v4.policy.context import PolicyContext, Verdict

# When re-using evaluate_ocr_signal on FALLBACK cases, skip tags whose
# precision depends on positive field extraction we DON'T have here.
# `ocr_revoked_sponsor` fires on any revoked SPN code appearing in OCR
# text — but in a fallback packet (sponsor_id unextracted), we can't
# tell if the code is the applicant's sponsor or a mention in a "these
# sponsors are revoked" reference list. Measured 17% precision on the
# fallback cohort (4 false-DENIED on truth-APPROVED cases).
_FALLBACK_OCR_SIGNAL_EXCLUDE_PREFIXES = ("ocr_revoked_sponsor",)


def upgrade_biometric_clean(ctx: PolicyContext) -> Verdict | None:
    """Waived-fee non-DIP promotion: L6 defensively downgrades all non-DIP
    waived cases to REVIEW because it can't tell a genuine hardship
    waiver from boilerplate. When a Biometric Scan Slip is present with
    a cleanly-read "Observed flags: none", the applicant has been
    affirmatively scanned clean — a distinct positive-evidence signal
    that the Field Manual's "visible hardship waiver" contemplates.
    Measured on training: 6/22 truth=APPROVED, 0/6 truth=DENIED, 2/15
    truth=REVIEW — 0 cat FA risk, small R→A cost."""
    if (
        ctx.config.upgrade_waived_on_biometric
        and ctx.tag == "R_A1_non_dip_waived_TO_REVIEW"
        and has_clean_biometric(ctx.signals)
    ):
        return Verdict("APPROVED", _conf("R_A1_non_dip_waived_biometric_clean"),
                       "R_A1_non_dip_waived_biometric_clean")
    return None


def upgrade_unpaid_waiver(ctx: PolicyContext) -> Verdict | None:
    """R3_unpaid + visible waiver upgrade. Field Manual §Fee Rules:
    "unpaid: deny unless a visible waiver applies." Symmetric to the
    biometric-clean upgrade above — same visible-waiver signal.
    DEFAULT OFF (config) — measured -0.22 pts, +10 cat FAs: biometric
    slips appear on many R3_unpaid packets as boilerplate, so the rule
    wrongly approves D-truth unpaid cases. Kept as insurance; re-enable
    only with a stricter waiver-evidence signal."""
    if (
        ctx.config.upgrade_unpaid_on_waiver
        and ctx.tag == "R3_unpaid"
        and has_clean_biometric(ctx.signals)
    ):
        return Verdict("APPROVED", _conf("R3_unpaid_biometric_waiver"),
                       "R3_unpaid_biometric_waiver")
    return None


def upgrade_fallback_ocr(ctx: PolicyContext) -> Verdict | None:
    """FALLBACK OCR upgrade — when L6 falls through due to extraction
    failure (missing visa/fee), but image OCR still contains explicit
    signals (Finding line, adjudicator reason phrases, named disqualifying
    flags, embargoed home names, deny/review stems), promote the verdict
    per the same evaluator used as guard #1 for approvals. Measured on 290
    fallback packets: 20% fire rate, +14 D→D wins, 0 cat FAs introduced.
    We whitelist-filter out signal families whose precision requires
    positive field extraction we don't have in fallback context.

    Fall-through: no signal (or an excluded family) returns None — the
    fallback verdict continues unchanged to the gate."""
    if ctx.config.fallback_ocr_upgrade and ctx.tag == "FALLBACK_extraction_fail":
        ocr_text = "\n".join(text for _sid, text in ctx.signals.get("image_ocr", []))
        ocr_signal = evaluate_ocr_signal(ocr_text)
        if ocr_signal is not None:
            _adj, _c, sig_tag = ocr_signal
            if not sig_tag.startswith(_FALLBACK_OCR_SIGNAL_EXCLUDE_PREFIXES):
                return Verdict(*ocr_signal)
    return None


UPGRADES = [upgrade_biometric_clean, upgrade_unpaid_waiver, upgrade_fallback_ocr]
