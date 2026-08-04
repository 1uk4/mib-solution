#!/usr/bin/env python3
"""L7 policy census: per-stage fire rates, guard co-fire matrix, per-stage
precision vs truth, upgrade outcomes. Uses the stage decomposition — each
stage evaluated independently, then compared with the dispatcher's pick.
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
from v4.policy import (
    GUARDS, PolicyContext, apply_policy, bypass_adjudicator_finding,
    upgrade_biometric_clean, upgrade_fallback_ocr, upgrade_unpaid_waiver,
)
from v4.rules import apply_rules
from v4.signals import extract_signals

PDF_DIR = Path("/tmp/mib-dev/pdfs")
LABELS = Path.home() / "Code" / "mib-doc-challenge" / "data" / "train_labels.csv"
OUT = Path(__file__).parent / "policy_census.json"

truth = {}
with LABELS.open() as f:
    for row in csv.DictReader(f):
        truth[row["case_id"]] = row["adjudication"]

GUARD_NAMES = [g.__name__ for g in GUARDS]

stats = {
    "packets": 0,
    "l6_verdicts": Counter(),
    "upgrade_fires": Counter(),          # (stage, truth)
    "bypass_fires": Counter(),           # truth
    "guard_would_fire": Counter(),       # guard name (on guarded population)
    "guard_actual_fire": Counter(),      # (guard, truth) — the dispatcher's pick
    "guard_cofire": Counter(),           # frozenset of would-fire guards
    "guard_solo": Counter(),             # (guard, truth) when it fired ALONE
    "untouched_approvals": Counter(),    # truth
    "verdict_changed_by_l7": Counter(),  # (l6_adj -> l7_adj, truth)
}

pdfs = sorted(PDF_DIR.glob("*.pdf"))
for i, p in enumerate(pdfs, 1):
    stats["packets"] += 1
    t = truth[p.stem]
    sources = acquire_sources(p)
    extract_content(sources)
    apply_filters(sources)
    bundle = extract_signals(sources)
    fields = consolidate(bundle, p.stem)
    adj6, c6, tag6 = apply_rules(fields)
    stats["l6_verdicts"][adj6] += 1

    ctx = PolicyContext(fields=fields, adj=adj6, conf=c6, tag=tag6, signals=bundle)

    # Upgrades, independently
    for stage in (upgrade_biometric_clean, upgrade_unpaid_waiver, upgrade_fallback_ocr):
        r = stage(ctx)
        if r is not None:
            stats["upgrade_fires"][f"{stage.__name__}|{t}"] += 1

    adj7, c7, tag7 = apply_policy(fields, adj6, c6, tag6, bundle)
    if adj7 != adj6:
        stats["verdict_changed_by_l7"][f"{adj6}->{adj7}|{t}"] += 1

    # Guarded population: APPROVED at L6, not upgraded away, not bypassed
    if adj6 == "APPROVED":
        if bypass_adjudicator_finding(ctx) is not None:
            stats["bypass_fires"][t] += 1
        else:
            would = [g.__name__ for g in GUARDS if g(ctx) is not None]
            for name in would:
                stats["guard_would_fire"][name] += 1
            if would:
                stats["guard_cofire"]["+".join(sorted(would))] += 1
                stats["guard_actual_fire"][f"{would[0]}|{t}"] += 1
                # NOTE: would[0] is in GUARDS order because the list
                # comprehension preserves it — equals the dispatcher pick.
                if len(would) == 1:
                    stats["guard_solo"][f"{would[0]}|{t}"] += 1
            else:
                stats["untouched_approvals"][t] += 1
    if i % 250 == 0:
        print(f"{i}/1000...", flush=True)

def cdump(c):
    return {k: v for k, v in sorted(c.items(), key=lambda kv: -kv[1])}

report = {k: cdump(v) if isinstance(v, Counter) else v for k, v in stats.items()}
OUT.write_text(json.dumps(report, indent=2))
print(json.dumps(report, indent=2))
