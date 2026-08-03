#!/usr/bin/env python3
"""Closed vocabularies and rule tables — every enum the pipeline trusts.

DOES: define the closed-world value sets (species, homes, visas, fees,
flags, sponsors) and the two stale-arrival constants, each with provenance.

DOES NOT: extract, match, or validate anything — consumers import these
sets and apply their own matching (word-boundary, fuzzy, exact) per site.

Vocab discipline (brief Pitfall 7): every list below derives from
train_labels.csv and/or the Field Manual, transcribed from
v1/solution.py:48-88 where each was originally validated. Never extend
from memory — "refunded" was once hallucinated into FEE_VALUES; actual
values are only paid/waived/unpaid/unknown. To re-derive a column:
    awk -F',' 'NR>1 {print $N}' train_labels.csv | sort -u
Provenance tags: [DOC] = stated in challenge docs / Field Manual;
[INFERRED-STRONG] = derived from training data with 100% consistency.
"""
from __future__ import annotations

from datetime import date as Date

# train_labels.csv `species_code` column, complete value set.
SPECIES = [
    "TRIANGULAN", "JOVIAN_GASFORM", "CENTAURI_SYNTH", "LUNA_SECURID",
    "KAIJU_MICRO", "ORION_GRAYS", "ALPHA_DRACONIAN", "SIRIUS_AVIAN",
    "VENUSIAN_MYCELIAL", "AQUARIAN_MANTIS", "ARCTURIAN", "ANDROMEDAN",
]

# train_labels.csv `home_world` column, complete value set.
HOME_WORLDS = [
    "Luyten-b", "Europa Station", "Titan Freeport", "Barnard-c",
    "Gliese-581g", "Mars Dome-7", "Kepler-186f", "Sirius Outpost",
    "Wolf-1061c", "Proxima-b", "Zeta Reticuli", "TRAPPIST-1e", "Eris Relay",
]

# Field Manual §Visa Classes + train_labels.csv `visa_class` column.
VISA_CLASSES = ["XW-1", "XW-2", "DIP-1", "MED-3", "TRANSIT-7"]
# Field Manual §Fee Rules + train_labels.csv `fee_status` column.
FEE_VALUES = ["paid", "waived", "unpaid", "unknown"]

# Field Manual §Risk Flags: disqualifying (auto-DENY) vs review-only.
DISQUALIFYING_FLAGS = {
    "memory_tampering", "planetary_embargo", "active_warrant", "biohazard_red",
}
REVIEW_ONLY_FLAGS = {
    "identity_conflict", "sponsor_mismatch", "illegible_biometrics",
    "rescinded_denial",
}
ALL_FLAGS = DISQUALIFYING_FLAGS | REVIEW_ONLY_FLAGS

# Rule tables (v1 EDGE_CASES.md §7-8)
REVOKED_SPONSORS = {
    "SPN-0007", "SPN-0139", "SPN-4040",  # [DOC]
    "SPN-7331", "SPN-2718", "SPN-9090",  # [INFERRED-STRONG]
}
EMBARGOED_HOMES_SOFT = {"Wolf-1061c"}      # [INFERRED-STRONG] — DIP-1 exempt
EMBARGOED_HOMES_HARD = {"TRAPPIST-1e", "Eris Relay"}  # [INFERRED-STRONG] — DENY regardless of visa
# Rationale: training data shows these worlds are 100% DENIED across all
# visa classes including DIP-1. We originally relied on R2 catching them
# via the planetary_embargo flag, but the flag often lives only in an
# image-based sponsor letter / adjudicator note we can't read from text.
# Encoding the world directly closes the gap.

# Stale-arrival reference date (proxy: max arrival in training set)
# Real "packet receipt date" is unknown [PENDING] — will need OCR to confirm.
RECEIPT_DATE_PROXY = Date(2026, 7, 12)
STALE_DAYS = 180
