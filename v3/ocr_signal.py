#!/usr/bin/env python3
"""V3's OCR signal evaluator — replaces V2's evaluate_ocr_signal at L7.

Design change from V2: the STAMP_STEMS tuple is split by semantic weight.
Stems that name a specific disqualifying risk category (BIOHAZ, EMBARG)
route to DENIED with high confidence — presence in OCR is direct evidence
of that risk. Stems that could appear in adjudicator prose (DENIE, REVOK,
RESCIN) stay at REVIEW because they might be talking ABOUT a denial
without one being present.

Empirical basis (correlator run on 1000 training PDFs):
  ocr_has_biohazard = True → 100% truth-DENIED (n=18)
  ocr_has_embargo   = True → strongly correlates with DENIED
  ocr_has_illegible = True → 54% REVIEW, 42% DENIED (kept at REVIEW)

Universal-rule principle: rules must have a semantic reason to work, not
just an empirical correlation. BIOHAZ → DENY works because the flag name
IS the risk category — the document literally says "there's a biohazard
concern." That generalizes to unseen packets. Adding a rule like
"3-image packets → DENY" would be overfitting even if the correlation is
strong on training.
"""
from __future__ import annotations

import re

from v1.solution import (
    DISQUALIFYING_FLAGS,
    REVIEW_ONLY_FLAGS,
    REVOKED_SPONSORS,
    EMBARGOED_HOMES_HARD,
)
from v3.signals import fuzzy_flag_pattern


# --- Signal patterns (severity descending) ---
# Finding-line regexes tolerate the four common OCR separator variants
# between "Finding" and the verdict: colon, period, dash, em-dash, or
# just whitespace. Real Tesseract outputs include "Finding: DENIED",
# "Finding:DENIED" (no space), and occasionally "Finding-DENIED".
OCR_FINDING_DENIED_RE = re.compile(
    r"Finding\s*[:.\-—]?\s*(DENIED|DENIE|REJECT)", re.IGNORECASE
)
OCR_FINDING_REVIEW_RE = re.compile(
    r"Finding\s*[:.\-—]?\s*NEEDS[_\s\-—]?REVIEW", re.IGNORECASE
)

# Adjudicator "Reason:" phrase detectors — high-precision text that adjudicator
# notes use to explain denials. Discovered by scanning all Manual Adjudicator
# Notes in the training set for "Reason: ..." completions.
#
# Empirical evidence (adjudicator-reason audit on 1000 packets):
#   "damaged registry evidence"  → 27/27 = 100% DENIED (5 solo, no Finding line)
#   "visible policy notes"       → 29/29 = 100% DENIED (4 solo, no Finding line)
#
# Semantic reason: these phrases ARE the adjudicator's stated justification
# for denial. They co-appear in "denial supported by damaged registry evidence
# and visible policy notes" — the boilerplate the adjudicator uses when a
# packet is denied due to registry/policy evidence, not a specific flag.
OCR_REASON_DAMAGED_REGISTRY_RE = re.compile(
    r"damaged\s+registry\s+evidence", re.IGNORECASE
)
OCR_REASON_VISIBLE_POLICY_NOTES_RE = re.compile(
    r"visible\s+policy\s+notes", re.IGNORECASE
)

# Precompiled fuzzy patterns for disqualifying / review flags. Replaces
# plain substring match on lowercased text — fuzzy is tolerant to the
# same separator variants (`_`, space, `-`, `—`) as the flag detector at
# L4 (v3/signals.py). Word-boundary anchors mean partial-word matches
# like "biohazardredflag" don't fire; they were unreliable evidence anyway.
DISQ_FLAG_PATTERNS = [(f, fuzzy_flag_pattern(f)) for f in DISQUALIFYING_FLAGS]
REVIEW_FLAG_PATTERNS = [(f, fuzzy_flag_pattern(f)) for f in REVIEW_ONLY_FLAGS]

# STAMP stems — split by semantic weight (see module docstring).
#
# DENIE promoted from REVIEW to DENY based on correlator finding:
# ocr_has_denied = True → 100% truth-DENIED on 45 training cases.
# "Denied" appearing in OCR is a strong DENY signal, even outside the
# specific "Finding: DENIED" pattern (which is caught by OCR_FINDING_DENIED_RE
# and remains at conf 0.95).
STAMP_DENY_STEMS = ("BIOHAZ", "EMBARG", "DENIE")
STAMP_REVIEW_STEMS = ("REVOK", "RESCIN")


def evaluate_ocr_signal(ocr_text: str) -> tuple[str, float, str] | None:
    """Grade OCR content into (adjudication, confidence, tag) or None.

    Check order is authority-tiered (not severity-tiered):

        Tier 1  Explicit adjudicator Finding (DENIED or REVIEW)
        Tier 2  Named risk categories (disq flags, revoked sponsors,
                embargoed homes, deny-side stems)
        Tier 3  Review-side flags and stems

    First hit wins. Rationale for tier-1-before-tier-2 in RULE_OCR_FINDING_AUTH
    below — an explicit Finding overrides ambient risk-word mentions on the
    same packet. Returns None if nothing risk-adjacent is present — caller
    keeps V1's APPROVE.
    """
    if not ocr_text:
        return None

    # --- Tier 1: explicit adjudicator Finding — highest authority ---
    # R_OCR_FINDING_AUTH: when an adjudicator's stated Finding is present in
    # image OCR, it's the authoritative signal — checked BEFORE any flag /
    # stem / embargo / revoked-sponsor pattern.
    #
    # Evidence (ocr_signal_audit.py on 1000 training PDFs):
    #   finding:DENIED marginal: 100% DENIED (n=43)
    #   finding:REVIEW marginal: 100% NEEDS_REVIEW (n=34)
    #   finding:REVIEW + deny_stem:EMBARG: 100% NEEDS_REVIEW (n=3) — direct
    #     proof that Finding overrides an ambient risk-stem match on the
    #     same packet (previously we returned DENIED spuriously — MIB-000656).
    #
    # Assumption: an adjudicator, if valid (not injected/tampered), is the
    # highest form of truth in a packet. Their Finding is a holistic verdict
    # integrating all evidence, so it overrides any single risk-word mention
    # that appears alongside it (e.g. inside their own "Reason: ..." line).
    if OCR_FINDING_DENIED_RE.search(ocr_text):
        return ("DENIED", 0.95, "ocr_finding:DENIED")
    if OCR_FINDING_REVIEW_RE.search(ocr_text):
        return ("NEEDS_REVIEW", 0.85, "ocr_finding:REVIEW")

    # Adjudicator reason phrases — the note's stated justification for
    # denial when a Finding line isn't present or wasn't OCR'd cleanly.
    # 100% DENIED precision on training (n=27/n=29).
    if OCR_REASON_DAMAGED_REGISTRY_RE.search(ocr_text):
        return ("DENIED", 0.92, "ocr_reason:damaged_registry")
    if OCR_REASON_VISIBLE_POLICY_NOTES_RE.search(ocr_text):
        return ("DENIED", 0.92, "ocr_reason:visible_policy_notes")

    # --- Tier 2: named risk categories → DENIED ---
    for flag, pattern in DISQ_FLAG_PATTERNS:
        if pattern.search(ocr_text):
            return ("DENIED", 0.90, f"ocr_disq_flag:{flag}")

    for sp in REVOKED_SPONSORS:
        if sp in ocr_text:
            return ("DENIED", 0.90, f"ocr_revoked_sponsor:{sp}")

    lower = ocr_text.lower()
    for home in EMBARGOED_HOMES_HARD:
        if home.lower() in lower:
            return ("DENIED", 0.90, f"ocr_embargo_home:{home}")

    # Disqualifying stems → DENIED (BIOHAZ, EMBARG empirically 100%;
    # DENIE promoted based on ocr_has_denied=True → 100% DENIED on 45 cases)
    upper = ocr_text.upper()
    for stem in STAMP_DENY_STEMS:
        if stem in upper:
            return ("DENIED", 0.85, f"ocr_deny_stem:{stem}")

    # --- Tier 3: review-side flags and stems ---
    for flag, pattern in REVIEW_FLAG_PATTERNS:
        if pattern.search(ocr_text):
            return ("NEEDS_REVIEW", 0.85, f"ocr_review_flag:{flag}")

    for stem in STAMP_REVIEW_STEMS:
        if stem in upper:
            return ("NEEDS_REVIEW", 0.65, f"ocr_review_stem:{stem}")

    return None
