#!/usr/bin/env python3
"""L5 consolidation census: conflict rates, source_class distribution,
and tiebreak decisiveness (equal-confidence disagreeing candidates where
lexicographic source_id ordering picks the value)."""
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, "/Users/lukaflores/Code/mib-solution")

from v4.acquire import acquire_sources
from v4.consolidate import consolidate, _SCHEMA_FIELDS
from v4.extract import extract_content
from v4.filters import apply_filters
from v4.signals import FIELD_VALUE, extract_signals

PDF_DIR = Path("/tmp/mib-dev/pdfs")
OUT = Path(__file__).parent / "consolidate_census.json"

APPROVE_RELEVANT = ("fee_status", "visa_class", "sponsor_id", "home_world", "risk_flags")

stats = {
    "packets": 0,
    "conflict_by_field": Counter(),
    "packets_with_any_conflict": 0,
    "packets_with_approve_relevant_conflict": 0,
    "source_class": Counter(),          # (field, class) -> count
    "tiebreak_decided_value": Counter(),  # equal max-conf, differing values
    "tiebreak_examples": [],
    "n_sources_hist": Counter(),
    "findings": 0,
}

pdfs = sorted(PDF_DIR.glob("*.pdf"))
for i, p in enumerate(pdfs, 1):
    stats["packets"] += 1
    sources = acquire_sources(p)
    extract_content(sources)
    apply_filters(sources)
    bundle = extract_signals(sources)
    fields = consolidate(bundle, p.stem)
    if fields.get("_finding"):
        stats["findings"] += 1

    agreement = fields["_agreement"]
    any_conflict = False
    any_ar_conflict = False
    for k in _SCHEMA_FIELDS:
        a = agreement.get(k)
        sc = fields["_source_class"].get(k, "?")
        stats["source_class"][f"{k}:{sc}"] += 1
        if a:
            stats["n_sources_hist"][a["n_sources"]] += 1
            if a["has_conflict"]:
                stats["conflict_by_field"][k] += 1
                any_conflict = True
                if k in APPROVE_RELEVANT:
                    any_ar_conflict = True
    if any_conflict:
        stats["packets_with_any_conflict"] += 1
    if any_ar_conflict:
        stats["packets_with_approve_relevant_conflict"] += 1

    # Tiebreak decisiveness: candidates at max confidence with >1 distinct value
    by_key = {}
    for s in bundle["signals"]:
        if s.type == FIELD_VALUE:
            by_key.setdefault(s.key, []).append(s)
    for k, cands in by_key.items():
        top = max(s.confidence for s in cands)
        top_cands = [s for s in cands if s.confidence == top]
        if len({s.value for s in top_cands}) > 1:
            stats["tiebreak_decided_value"][k] += 1
            if len(stats["tiebreak_examples"]) < 10:
                stats["tiebreak_examples"].append({
                    "case": p.stem, "field": k, "conf": top,
                    "values": sorted({f"{s.source_id}={s.value[:25]}" for s in top_cands}),
                })
    if i % 250 == 0:
        print(f"{i}/1000...", flush=True)

report = {
    "packets": stats["packets"],
    "findings_passed_through": stats["findings"],
    "packets_with_any_conflict": stats["packets_with_any_conflict"],
    "packets_with_approve_relevant_conflict": stats["packets_with_approve_relevant_conflict"],
    "conflict_by_field": dict(stats["conflict_by_field"].most_common()),
    "tiebreak_decided_value_by_field": dict(stats["tiebreak_decided_value"].most_common()),
    "tiebreak_examples": stats["tiebreak_examples"],
    "source_class": dict(sorted(stats["source_class"].items())),
    "n_sources_hist": {str(k): v for k, v in sorted(stats["n_sources_hist"].items())},
}
OUT.write_text(json.dumps(report, indent=2))
print(json.dumps(report, indent=2))
