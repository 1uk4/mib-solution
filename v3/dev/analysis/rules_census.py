#!/usr/bin/env python3
"""L6 rule-chain census: fire counts, shadow matrix (order-fragility),
unreachable-line check, and the FALLBACK bucket decomposition (B2-1).
"""
import csv
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, "/Users/lukaflores/Code/mib-solution")

from v4.acquire import acquire_sources
from v4.consolidate import consolidate
from v4.extract import extract_content
from v4.filters import apply_filters
from v4.rules import _flag_set, _is_stale, apply_rules
from v4.signals import extract_signals
from v4.vocab import (
    DISQUALIFYING_FLAGS, EMBARGOED_HOMES_HARD, EMBARGOED_HOMES_SOFT,
    REVOKED_SPONSORS,
)

PDF_DIR = Path("/tmp/mib-dev/pdfs")
LABELS = Path.home() / "Code" / "mib-doc-challenge" / "data" / "train_labels.csv"
OUT = Path(__file__).parent / "rules_census.json"

truth = {}
with LABELS.open() as f:
    for row in csv.DictReader(f):
        truth[row["case_id"]] = row["adjudication"]

def conditions(fields):
    """Which rule conditions are TRUE, independent of chain order."""
    visa = fields.get("visa_class", "")
    fee = fields.get("fee_status", "")
    sponsor = fields.get("sponsor_id", "")
    home = fields.get("home_world", "")
    arrival = fields.get("arrival_date", "")
    flags = _flag_set(fields.get("risk_flags", ""))
    finding = fields.get("_finding", "")
    c = {}
    c["FINDING"] = finding in ("APPROVED", "DENIED", "NEEDS_REVIEW")
    c["R0"] = home in EMBARGOED_HOMES_HARD
    c["R1"] = visa == "TRANSIT-7"
    c["R2"] = bool(flags & DISQUALIFYING_FLAGS)
    c["R4"] = sponsor in REVOKED_SPONSORS and bool(visa) and visa != "DIP-1"
    c["R4b"] = home in EMBARGOED_HOMES_SOFT and bool(visa) and visa != "DIP-1"
    c["R3"] = fee == "unpaid"
    c["R5"] = bool(arrival) and bool(visa) and _is_stale(arrival) and visa != "DIP-1"
    c["R_R1"] = bool(flags)
    c["FALLBACK"] = not visa or not fee
    c["MISS_ARR"] = not arrival
    c["R_R2"] = fee == "unknown"
    return c

def base_tag(tag):
    return tag.split("[")[0]

stats = {
    "fires": Counter(),
    "truth_by_tag": Counter(),          # (base_tag, truth) pairs
    "shadow": Counter(),                # fired_tag -> also-true condition
    "multi_deny_true": 0,               # >1 hard-deny condition true at once
    "unreachable_final": 0,
    "fallback_partition": Counter(),    # visa_only / fee_only / both
    "fallback_truth": Counter(),        # (partition, truth)
}

pdfs = sorted(PDF_DIR.glob("*.pdf"))
for i, p in enumerate(pdfs, 1):
    sources = acquire_sources(p)
    extract_content(sources)
    apply_filters(sources)
    bundle = extract_signals(sources)
    fields = consolidate(bundle, p.stem)
    adj, c_, tag = apply_rules(fields)
    bt = base_tag(tag)
    t = truth[p.stem]
    stats["fires"][bt] += 1
    stats["truth_by_tag"][f"{bt}|{t}"] += 1

    cond = conditions(fields)
    deny_conds = [k for k in ("R0", "R1", "R2", "R4", "R4b", "R3", "R5") if cond[k]]
    if len(deny_conds) > 1:
        stats["multi_deny_true"] += 1
    for name, is_true in cond.items():
        if is_true and name != bt.replace("R_ADJUDICATOR_FINDING", "FINDING") \
                and not bt.startswith(name):
            stats["shadow"][f"{bt}->also:{name}"] += 1

    if bt == "FALLBACK_extraction_fail":
        visa = fields.get("visa_class", "")
        fee = fields.get("fee_status", "")
        if not visa and not fee:
            part = "both_missing"
        elif not visa:
            part = "visa_only_missing"
        elif not fee:
            part = "fee_only_missing"
        else:
            part = "UNREACHABLE_FINAL_LINE"
            stats["unreachable_final"] += 1
        stats["fallback_partition"][part] += 1
        stats["fallback_truth"][f"{part}|{t}"] += 1
    if i % 250 == 0:
        print(f"{i}/1000...", flush=True)

def cdump(c):
    return {k: v for k, v in sorted(c.items(), key=lambda kv: -kv[1])}

report = {
    "fires": cdump(stats["fires"]),
    "truth_by_tag": cdump(stats["truth_by_tag"]),
    "shadow_top": dict(Counter(stats["shadow"]).most_common(20)),
    "multi_deny_true_packets": stats["multi_deny_true"],
    "unreachable_final_line": stats["unreachable_final"],
    "fallback_partition": cdump(stats["fallback_partition"]),
    "fallback_truth": cdump(stats["fallback_truth"]),
}
OUT.write_text(json.dumps(report, indent=2))
print(json.dumps(report, indent=2))
