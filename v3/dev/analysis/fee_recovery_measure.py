#!/usr/bin/env python3
"""B2-1 measurement: fee_fuzzy_recovery ON vs baseline (golden).

Generates predictions with the flag on (warm cache), scores with the
challenge scorer, reports train/val split scores, and analyzes the
per-case verdict flow against golden joined with truth.
"""
import csv
import json
import random
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, "/Users/lukaflores/Code/mib-solution")

from v4.config import Config
import v4.signals as sig
import v4.solution as sol
from v4.acquire import acquire_sources
from v4.consolidate import consolidate
from v4.extract import extract_content
from v4.filters import apply_filters
from v4.policy import apply_policy
from v4.rules import apply_rules
from v4.signals import extract_signals

REPO = Path("/Users/lukaflores/Code/mib-solution")
PDF_DIR = Path("/tmp/mib-dev/pdfs")
CHALLENGE = Path.home() / "Code" / "mib-doc-challenge"
LABELS = CHALLENGE / "data" / "train_labels.csv"
# Under /tmp/mib-dev-runs so split_score.py's newest-run discovery finds it.
OUTDIR = Path("/tmp/mib-dev-runs/feefuzzy-measure")
OUTDIR.mkdir(parents=True, exist_ok=True)
PRED = OUTDIR / "predictions.jsonl"

# replace() keeps from_env()'s ocr_cache_dir — Config(...) alone would
# silently drop the warm cache and run cold OCR (~40 min instead of ~4).
from dataclasses import replace
CFG = replace(Config.from_env(), fee_fuzzy_recovery=True)

truth = {}
with LABELS.open() as f:
    for row in csv.DictReader(f):
        truth[row["case_id"]] = row["adjudication"]

def predict(pdf_path):
    case_id = pdf_path.stem
    sources = acquire_sources(pdf_path)
    extract_content(sources, CFG)
    apply_filters(sources)
    signals = extract_signals(sources, CFG)
    fields = consolidate(signals, case_id)
    adj, c, tag = apply_rules(fields)
    adj, c, tag = apply_policy(fields, adj, c, tag, signals, CFG)
    row = dict(fields)
    for f in sol.OUTPUT_FIELDS:
        if not row.get(f):
            row[f] = sol._default_field(f)
    row["adjudication"] = adj
    row["confidence"] = c
    row.pop("_finding", None); row.pop("_source_class", None); row.pop("_agreement", None)
    return row, tag

pdfs = sorted(PDF_DIR.glob("*.pdf"))
print(f"[fee-measure] config: fee_fuzzy_recovery=ON (all other flags default)", flush=True)
print(f"[fee-measure] {len(pdfs)} PDFs from {PDF_DIR}, truth rows: {len(truth)}", flush=True)
print(f"[fee-measure] output: {OUTDIR}", flush=True)
print(f"[fee-measure] stage 1/3: generating predictions (warm OCR cache; "
      f"the fee sweep makes this slower than a plain run)...", flush=True)
tags = {}
start = time.time()
with PRED.open("w") as f:
    for i, p in enumerate(pdfs, 1):
        row, tag = predict(p)
        tags[p.stem] = tag
        f.write(json.dumps(row, sort_keys=True) + "\n")
        if i == 1 or i % 50 == 0 or i == len(pdfs):
            el = time.time() - start
            eta = el / i * (len(pdfs) - i)
            print(f"[fee-measure] {i}/{len(pdfs)}  {p.stem}  "
                  f"({el/i:.2f}s/pdf, elapsed {el:.0f}s, eta {eta:.0f}s)", flush=True)

print("[fee-measure] stage 2/3: scoring with the challenge evaluator...", flush=True)
subprocess.run([sys.executable, str(CHALLENGE / "scripts" / "evaluate.py"),
                "--truth", str(LABELS), "--submission", str(PRED),
                "--output-json", str(OUTDIR / "evaluation.json"),
                "--case-scores-jsonl", str(OUTDIR / "case_scores.jsonl")],
               check=True)

print("[fee-measure] stage 3/3: per-case flow analysis vs golden...", flush=True)

# Split scores (regenerate split lists deterministically if absent)
splits_dir = Path("/tmp/mib-splits")
if not (splits_dir / "train.txt").exists():
    subprocess.run([sys.executable, str(REPO / "v3/dev/analysis/split_train_val.py")], check=True)
train_ids = set((splits_dir / "train.txt").read_text().split())
val_ids = set((splits_dir / "val.txt").read_text().split())

def split_totals(case_scores_path):
    tr = va = 0.0
    with open(case_scores_path) as f:
        for line in f:
            r = json.loads(line)
            cid = r.get("case_id")
            s = r.get("total", r.get("score", 0.0))
            if cid in train_ids: tr += s
            else: va += s
    return tr, va

# Per-case flow vs golden
golden = {}
for line in open(REPO / "golden/native-92eb104-seed42-n1000.jsonl"):
    r = json.loads(line); golden[r["case_id"]] = r
new = {}
for line in open(PRED):
    r = json.loads(line); new[r["case_id"]] = r

flow = Counter(); fee_changes = Counter(); changed = []
for cid in golden:
    g, n = golden[cid], new[cid]
    if g["fee_status"] != n["fee_status"]:
        fee_changes[f'{g["fee_status"]}->{n["fee_status"]}'] += 1
    if g["adjudication"] != n["adjudication"]:
        t = truth[cid]
        flow[f'{g["adjudication"]}->{n["adjudication"]}|truth={t}'] += 1
        changed.append({"case": cid, "from": g["adjudication"], "to": n["adjudication"],
                        "truth": t, "fee": f'{g["fee_status"]}->{n["fee_status"]}',
                        "tag": tags[cid], "conf": n["confidence"]})

new_cat_fa = sum(1 for cid in new
                 if truth[cid] == "DENIED" and new[cid]["adjudication"] == "APPROVED")

ev = json.loads((OUTDIR / "evaluation.json").read_text())
report = {
    "evaluation": {k: ev[k] for k in ev if not isinstance(ev[k], (dict, list))},
    "split_scores": "run: python3 v3/dev/analysis/split_score.py  (baselines 118.088 / 118.041)",
    "cat_fa_new": new_cat_fa,
    "fee_value_changes": dict(fee_changes.most_common()),
    "verdict_flow_vs_golden": dict(flow.most_common()),
    "changed_cases": changed[:25],
    "n_changed_verdicts": len(changed),
}
(OUTDIR / "report.json").write_text(json.dumps(report, indent=2))
print(json.dumps(report, indent=2))
