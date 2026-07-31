#!/usr/bin/env python3
"""L5 — Cross-reference & consolidation.

Consumes L4's typed Signal records, produces the field dict L6 (rules)
consumes.

Phase D-1 (this file): pass-through consolidation. Each FIELD_VALUE Signal
becomes a field entry directly. If multiple signals exist for the same
key, highest-confidence wins (with source_id as tiebreaker for stability).
ADJUDICATOR_FINDING is written to `_finding` for V1's rule engine.

Behavior is byte-identical to the previous L5 because Phase D-1's L4 emits
at most one signal per (type, key) pair from combined text.

Phase D-2/D-3 (later):
    - Multiple sources per key → group and detect conflicts
    - Conflict resolution: agreement → high confidence
                            disagreement → downgrade / route to REVIEW
    - Source trust weighting: text-stream > OCR
"""
from __future__ import annotations

from collections import defaultdict

from v3.signals import Signal, FIELD_VALUE, ADJUDICATOR_FINDING, FLAG_DECLARATION


# Schema fields L6 expects. Missing fields default to empty string; the
# adjudicate() function distinguishes empty from populated.
_SCHEMA_FIELDS = (
    "applicant_name", "species_code", "home_world", "visa_class",
    "sponsor_id", "arrival_date", "declared_purpose", "risk_flags",
    "fee_status",
)


def consolidate(bundle: dict, case_id: str) -> dict:
    """Turn a Signal bundle into the field dict L6 consumes.

    `bundle` is the dict returned by extract_signals — contains 'signals'
    (list[Signal]) and 'image_ocr' (for L7 policy).

    Also stores per-field provenance in `_source_class`:
        'text'      → at least one text-stream signal contributed
        'ocr_only'  → only image-OCR signals; no text-stream backing
        'absent'    → no signal at all (field is default/empty)
    L7 uses this to prevent approval on OCR-only inputs (safety principle).
    """
    signals: list[Signal] = bundle["signals"]
    fields: dict = {"case_id": case_id}
    source_class: dict[str, str] = {}
    agreement: dict[str, dict] = {}

    # Group FIELD_VALUE signals by key
    by_key: dict[str, list[Signal]] = defaultdict(list)
    for s in signals:
        if s.type == FIELD_VALUE:
            by_key[s.key].append(s)

    for key in _SCHEMA_FIELDS:
        candidates = by_key.get(key, [])
        if candidates:
            # Pick the highest-confidence Signal as the field value.
            # combined_text at 1.0 always wins ties over per-stream (0.9)
            # and per-image (0.7), preserving V1's cross-source logic.
            best = max(candidates, key=lambda s: (s.confidence, s.source_id))
            fields[key] = best.value

            # Provenance for L7 safety net
            has_text = any(s.source_id == "combined_text" or
                           s.source_id.startswith("text_stream_")
                           for s in candidates)
            source_class[key] = "text" if has_text else "ocr_only"

            # Agreement scoring — how corroborated is the winning value?
            # Distinct sources emitting the same value = corroboration.
            # Distinct sources emitting DIFFERENT values = conflict.
            distinct_values = {s.value for s in candidates}
            n_sources = len(candidates)
            n_agreeing = sum(1 for s in candidates if s.value == best.value)
            agreement[key] = {
                "value": best.value,
                "n_sources": n_sources,
                "n_agreeing": n_agreeing,
                "unique_values": sorted(distinct_values),
                "has_conflict": len(distinct_values) > 1,
            }
        else:
            fields[key] = ""
            source_class[key] = "absent"
            agreement[key] = None

    # Adjudicator finding — passed through as internal-only marker for V1 rules
    for s in signals:
        if s.type == ADJUDICATOR_FINDING:
            fields["_finding"] = s.value
            break

    # V1's flag extractor already defaults to "none" when no flag found.
    # Preserve that here for schema compatibility.
    if not fields.get("risk_flags"):
        fields["risk_flags"] = "none"

    fields["_source_class"] = source_class
    fields["_agreement"] = agreement
    return fields
