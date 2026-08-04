#!/usr/bin/env python3
"""Output-invariants audit (B2-11):
(a) per-field extraction accuracy x evaluator weights (never measured)
(b) emission-consistency: APPROVED rows carrying self-denying values
(c) OCR-signal presence on REVIEW tags evaluate_ocr_signal never covers
(d) fee sentinel-outvote cases vs truth (collision check)
"""
import csv
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, "/Users/lukaflores/Code/mib-solution")

from v4.acquire import acquire_sources
from v4.consolidate import consolidate
from v4.evidence import evaluate_ocr_signal
from v4.extract import extract_content
from v4.filters import apply_filters
from v4.policy import apply_policy
from v4.rules import apply_rules
from v4.signals import FIELD_VALUE, extract_signals
from v4.vocab import DISQUALIFYING_FLAGS, EMBARGOED_HOMES_HARD, REVOKED_SPONSORS

REPO = Path("/Users/lukaflores/Code/mib-solution")
LABELS = Path.home() / "Code" / "mib-doc-challenge" / "data" / "train_labels.csv"
FIELDS = ("applicant_name", "species_code", "home_world", "visa_class",
          "sponsor_id", "arrival_date", "declared_purpose", "risk_flags", "fee_status")
WEIGHTS = {"applicant_name": 5, "species_code": 6, "home_world": 5, "visa_class": 5,
           "sponsor_id": 5, "arrival_date": 4, "declared_purpose": 3,
           "risk_flags": 8, "fee_status": 4}

truth = {}
with LABELS.open() as f:
    for row in csv.DictReader(f):
        truth[row["case_id"]] = row

golden = {}
for line in open(REPO / "golden/native-92eb104-seed42-n1000.jsonl"):
    r = json.loads(line); golden[r["case_id"]] = r

# ---- (a) per-field extraction accuracy + weighted loss ----
wrong = Counter(); wrong_absent = Counter()
for cid, g in golden.items():
    t = truth[cid]
    for f in FIELDS:
        if g[f] != t[f]:
            wrong[f] += 1
            # was our emission a default placeholder (nothing extracted)?
            defaults = {"applicant_name": "unknown", "species_code": "unknown",
                        "home_world": "unknown", "visa_class": "unknown",
                        "sponsor_id": "SPN-0000", "arrival_date": "1900-01-01",
                        "declared_purpose": "unknown", "risk_flags": "none",
                        "fee_status": "unknown"}
            if g[f] == defaults[f]:
                wrong_absent[f] += 1
total_w = sum(WEIGHTS.values())
print("=== (a) per-field extraction: wrong / of-which-default-emitted / weighted pts lost (of 50) ===")
for f in sorted(FIELDS, key=lambda x: -wrong[x] * WEIGHTS[x]):
    pts = wrong[f] / 1000 * WEIGHTS[f] / total_w * 50
    print(f"  {f:18s} wrong={wrong[f]:4d}  absent={wrong_absent[f]:4d}  w={WEIGHTS[f]}  lost={pts:.2f} pts")
print(f"  TOTAL lost: {sum(wrong[f]/1000*WEIGHTS[f]/total_w*50 for f in FIELDS):.2f} (scoreboard says {50-40.16:.2f})")

# ---- (b) emission consistency on APPROVED rows ----
viol = Counter(); examples = []
for cid, g in golden.items():
    if g["adjudication"] != "APPROVED":
        continue
    flags = set(g["risk_flags"].split("|")) if g["risk_flags"] not in ("", "none") else set()
    checks = {
        "disq_flag": bool(flags & DISQUALIFYING_FLAGS),
        "unpaid_fee": g["fee_status"] == "unpaid",
        "transit7": g["visa_class"] == "TRANSIT-7",
        "hard_embargo": g["home_world"] in EMBARGOED_HOMES_HARD,
        "revoked_sponsor_nondip": g["sponsor_id"] in REVOKED_SPONSORS and g["visa_class"] != "DIP-1",
    }
    for k, v in checks.items():
        if v:
            viol[k] += 1
            if len(examples) < 8:
                examples.append((cid, k, truth[cid]["adjudication"]))
n_approved = sum(1 for g in golden.values() if g["adjudication"] == "APPROVED")
print(f"\n=== (b) emission consistency: {n_approved} APPROVED rows, violations: {dict(viol) or 'NONE'} ===")
for e in examples: print("   ", e)

# ---- (c) + (d) need pipeline internals ----
UNCOVERED = {"R_R1_flag_present", "R_R2_unknown_fee", "FALLBACK_missing_arrival",
             "R_A1_non_dip_waived_TO_REVIEW"}
c_stats = Counter(); c_examples = []
d_stats = Counter(); d_examples = []
pdfs = sorted(Path("/tmp/mib-dev/pdfs").glob("*.pdf"))
for i, p in enumerate(pdfs, 1):
    cid = p.stem
    sources = acquire_sources(p); extract_content(sources); apply_filters(sources)
    bundle = extract_signals(sources)
    fields = consolidate(bundle, cid)
    adj6, c6, tag6 = apply_rules(fields)
    adj7, c7, tag7 = apply_policy(fields, adj6, c6, tag6, bundle)

    # (c) uncovered REVIEW tags with a live OCR signal
    if tag7 in UNCOVERED:
        ocr_text = "\n".join(t for _sid, t in bundle.get("image_ocr", []))
        sig = evaluate_ocr_signal(ocr_text)
        t = truth[cid]["adjudication"]
        if sig is not None:
            sadj, sconf, stag = sig
            fam = stag.split(":")[0]
            agree = "SIG==TRUTH" if sadj == t else "sig!=truth"
            c_stats[f"{tag7}|{fam}->{sadj}|{agree}"] += 1
            if len(c_examples) < 12:
                c_examples.append((cid, tag7, stag, sadj, t))
        else:
            c_stats[f"{tag7}|no_signal"] += 1

    # (d) fee sentinel outvote: combined unknown@0.4 present, winner elsewhere
    fee_cands = [s for s in bundle["signals"] if s.type == FIELD_VALUE and s.key == "fee_status"]
    sentinel = [s for s in fee_cands if s.source_id == "combined_text" and s.value == "unknown"]
    if sentinel and fee_cands:
        best = max(fee_cands, key=lambda s: (s.confidence, s.source_id))
        if best.source_id != "combined_text":
            tf = truth[cid]["fee_status"]
            outcome = ("outvote_RIGHT" if best.value == tf
                       else ("outvote_WRONG_truth_unknown" if tf == "unknown"
                             else "outvote_WRONG_other"))
            d_stats[outcome] += 1
            if len(d_examples) < 10:
                d_examples.append((cid, f"sentinel->{best.value}", f"truth={tf}", best.tag[:30]))
    if i % 250 == 0:
        print(f"  ...{i}/1000", flush=True)

print(f"\n=== (c) OCR signals on uncovered REVIEW tags ===")
for k, v in c_stats.most_common(): print(f"  {k}: {v}")
for e in c_examples: print("   ", e)
print(f"\n=== (d) fee sentinel outvotes vs truth ===")
for k, v in d_stats.most_common(): print(f"  {k}: {v}")
for e in d_examples: print("   ", e)
