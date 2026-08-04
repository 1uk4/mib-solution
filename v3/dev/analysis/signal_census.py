#!/usr/bin/env python3
"""L4 signal census over the 1000-packet corpus (warm OCR cache).

Measures: signal emission by tier/tag family; authority-level distribution;
WHICH signal wins each field at L5 (win rates per recovery pass — the
"does each fuzzy pass matter" number); validation rejections; re-OCR
repair fires; the fee_status unknown@0.4 sentinel outcome.
"""
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, "/Users/lukaflores/Code/mib-solution")

import v4.signals as sig
from v4.acquire import acquire_sources, IMAGE, TEXT_STREAM
from v4.consolidate import consolidate, _SCHEMA_FIELDS
from v4.extract import extract_content
from v4.filters import apply_filters
from v4.signals import FIELD_VALUE, extract_signals
from v4.source_type import classify

PDF_DIR = Path("/tmp/mib-dev/pdfs")
OUT = Path(__file__).parent / "signal_census.json"

def tag_family(tag):
    if tag.startswith("v1_extract_fields"): return "combined_text"
    if tag.startswith("text_stream_L"): return "per_stream"
    if tag.startswith("image_ocr_fuzzy_flag"): return "fuzzy_flag"
    if tag.startswith("image_ocr_fuzzy_sponsor"): return "fuzzy_sponsor"
    if tag.startswith("image_ocr_fuzzy_"): return "fuzzy_label"
    if tag.startswith("image_ocr_L"): return "per_image_strict"
    return tag

# Instrument validation rejections and re-OCR repairs
orig_valid = sig._valid_field_value
orig_repair = sig._reocr_repair
valid_counts = Counter()
repair_counts = Counter()

def valid_spy(key, value):
    ok = orig_valid(key, value)
    valid_counts[(key, "ok" if ok else "rejected")] += 1
    return ok

def repair_spy(source, field, current):
    out = orig_repair(source, field, current)
    repair_counts[(field, "repaired" if out else "no-op")] += 1
    return out

sig._valid_field_value = valid_spy
sig._reocr_repair = repair_spy

stats = {
    "packets": 0,
    "signals_total": 0,
    "emitted_by_family": Counter(),
    "winner_by_family": Counter(),        # (field, family) -> count
    "winner_family_total": Counter(),
    "level_hist_text": Counter(),
    "level_hist_image": Counter(),
    "findings_emitted": 0,
    "fee_unknown_sentinel": {"emitted": 0, "outvoted": 0},
    "fields_filled": Counter(),
    "fields_empty": Counter(),
}

pdfs = sorted(PDF_DIR.glob("*.pdf"))
for i, p in enumerate(pdfs, 1):
    stats["packets"] += 1
    sources = acquire_sources(p)
    extract_content(sources)
    apply_filters(sources)
    for s in sources:
        if not s.trusted or not s.content.strip():
            continue
        lvl = classify(s)
        if s.type == TEXT_STREAM:
            stats["level_hist_text"][lvl] += 1
        else:
            stats["level_hist_image"][lvl] += 1
    bundle = extract_signals(sources)
    signals = bundle["signals"]
    stats["signals_total"] += len(signals)
    for s in signals:
        if s.type == FIELD_VALUE:
            stats["emitted_by_family"][tag_family(s.tag)] += 1
        else:
            stats["findings_emitted"] += 1

    # Winner analysis — replicate L5's pick per field
    by_key = {}
    for s in signals:
        if s.type == FIELD_VALUE:
            by_key.setdefault(s.key, []).append(s)
    fee_unknown = [s for s in by_key.get("fee_status", [])
                   if s.source_id == "combined_text" and s.value == "unknown"]
    if fee_unknown:
        stats["fee_unknown_sentinel"]["emitted"] += 1
    for key in _SCHEMA_FIELDS:
        cands = by_key.get(key, [])
        if not cands:
            stats["fields_empty"][key] += 1
            continue
        stats["fields_filled"][key] += 1
        best = max(cands, key=lambda s: (s.confidence, s.source_id))
        fam = tag_family(best.tag)
        stats["winner_by_family"][f"{key}:{fam}"] += 1
        stats["winner_family_total"][fam] += 1
        if key == "fee_status" and fee_unknown and best.source_id != "combined_text":
            stats["fee_unknown_sentinel"]["outvoted"] += 1
    if i % 250 == 0:
        print(f"{i}/1000...", flush=True)

def cdump(c):
    return {str(k): v for k, v in sorted(c.items(), key=lambda kv: -kv[1])}

report = {
    "packets": stats["packets"],
    "signals_total": stats["signals_total"],
    "signals_per_packet": round(stats["signals_total"] / stats["packets"], 1),
    "findings_emitted": stats["findings_emitted"],
    "emitted_by_family": cdump(stats["emitted_by_family"]),
    "winner_family_total": cdump(stats["winner_family_total"]),
    "winner_by_field_family": cdump(stats["winner_by_family"]),
    "level_hist_text": {f"L{k}": v for k, v in sorted(stats["level_hist_text"].items())},
    "level_hist_image": {f"L{k}": v for k, v in sorted(stats["level_hist_image"].items())},
    "validation": cdump(valid_counts),
    "reocr_repairs": cdump(repair_counts),
    "fee_unknown_sentinel": stats["fee_unknown_sentinel"],
    "fields_empty": cdump(stats["fields_empty"]),
}
OUT.write_text(json.dumps(report, indent=2))
print(json.dumps(report, indent=2))
