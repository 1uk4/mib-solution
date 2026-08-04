#!/usr/bin/env python3
"""B2-12 measurement: one OCR pool variant ON vs baseline (golden).

Usage:
    python3 v3/dev/analysis/ocr_pool_measure.py ocr_pool_psm11 [N]
    python3 v3/dev/analysis/ocr_pool_measure.py ocr_pool_rotations [N]
    python3 v3/dev/analysis/ocr_pool_measure.py ocr_pool_deskew [N]

Optional N = smoke mode: run only the first N PDFs to verify the variant
works end-to-end (scored against a filtered truth manifest so the numbers
are internally valid for those N — but NOT a real measurement; only the
full run feeds an accept/reject decision).

Generates predictions with exactly that flag on (warm cache for existing
passes; the new pass cold-OCRs once and is cached for reruns), scores with
the challenge evaluator, and reports the per-case flow vs golden joined
with truth. Follow the FULL run with split_score.py (baselines
118.088 / 118.041).
"""
import csv
import dataclasses
import json
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, "/Users/lukaflores/Code/mib-solution")

from v4.config import Config
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

FLAG = sys.argv[1] if len(sys.argv) > 1 else ""
valid_flags = {f.name for f in dataclasses.fields(Config) if f.name.startswith("ocr_pool_")}
if FLAG not in valid_flags:
    raise SystemExit(f"usage: ocr_pool_measure.py <{'|'.join(sorted(valid_flags))}> [N]")
LIMIT = int(sys.argv[2]) if len(sys.argv) > 2 else None

OUTDIR = Path(f"/tmp/mib-dev-runs/pool-{FLAG}" + (f"-smoke{LIMIT}" if LIMIT else ""))
OUTDIR.mkdir(parents=True, exist_ok=True)
PRED = OUTDIR / "predictions.jsonl"

# replace() keeps from_env()'s ocr_cache_dir — Config(...) alone would
# silently drop the warm cache and run cold OCR.
CFG = dataclasses.replace(Config.from_env(), **{FLAG: True})

truth = {}
with LABELS.open() as f:
    for row in csv.DictReader(f):
        truth[row["case_id"]] = row

FIELDS = sol.OUTPUT_FIELDS


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
    for f in FIELDS:
        if not row.get(f):
            row[f] = sol._default_field(f)
    row["adjudication"] = adj
    row["confidence"] = c
    row.pop("_finding", None); row.pop("_source_class", None); row.pop("_agreement", None)
    return row, tag


pdfs = sorted(PDF_DIR.glob("*.pdf"))
if LIMIT:
    pdfs = pdfs[:LIMIT]
print(f"[pool-measure] variant: {FLAG}=ON (all other flags default)", flush=True)
if LIMIT:
    print(f"[pool-measure] *** SMOKE MODE: first {LIMIT} PDFs only — "
          f"verifies mechanics, NOT a measurement ***", flush=True)
print(f"[pool-measure] {len(pdfs)} PDFs from {PDF_DIR}; output: {OUTDIR}", flush=True)
print(f"[pool-measure] stage 1/3: predictions (new pass cold-OCRs once; "
      f"first run is the slow one)...", flush=True)
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
            print(f"[pool-measure] {i}/{len(pdfs)}  {p.stem}  "
                  f"({el/i:.2f}s/pdf, elapsed {el:.0f}s, eta {eta:.0f}s)", flush=True)

print("[pool-measure] stage 2/3: scoring with the challenge evaluator...", flush=True)
truth_csv = LABELS
if LIMIT:
    # Filter the manifest to the smoke sample so the evaluator doesn't
    # count the other 1000-N cases as missing.
    ids = {p.stem for p in pdfs}
    truth_csv = OUTDIR / "labels_smoke.csv"
    with LABELS.open() as fin, truth_csv.open("w", newline="") as fout:
        r = csv.DictReader(fin)
        w = csv.DictWriter(fout, fieldnames=r.fieldnames)
        w.writeheader()
        for row in r:
            if row["case_id"] in ids:
                w.writerow(row)
subprocess.run([sys.executable, str(CHALLENGE / "scripts" / "evaluate.py"),
                "--truth", str(truth_csv), "--submission", str(PRED),
                "--output-json", str(OUTDIR / "evaluation.json"),
                "--case-scores-jsonl", str(OUTDIR / "case_scores.jsonl")],
               check=True)

print("[pool-measure] stage 3/3: per-case flow analysis vs golden...", flush=True)
golden = {}
for line in open(REPO / "golden/native-92eb104-seed42-n1000.jsonl"):
    r = json.loads(line); golden[r["case_id"]] = r
new = {}
for line in open(PRED):
    r = json.loads(line); new[r["case_id"]] = r

verdict_flow = Counter(); field_changes = Counter(); changed = []
for cid in new:              # new ⊆ golden; in smoke mode only N cases exist
    g, n = golden[cid], new[cid]
    for fld in FIELDS:
        if g[fld] != n[fld]:
            outcome = "RIGHT" if n[fld] == truth[cid][fld] else (
                "WAS_RIGHT_NOW_WRONG" if g[fld] == truth[cid][fld] else "wrong_to_wrong")
            field_changes[f"{fld}:{outcome}"] += 1
    if g["adjudication"] != n["adjudication"]:
        t = truth[cid]["adjudication"]
        verdict_flow[f'{g["adjudication"]}->{n["adjudication"]}|truth={t}'] += 1
        changed.append({"case": cid, "from": g["adjudication"], "to": n["adjudication"],
                        "truth": t, "tag": tags[cid], "conf": n["confidence"]})

new_cat_fa = sum(1 for cid in new
                 if truth[cid]["adjudication"] == "DENIED"
                 and new[cid]["adjudication"] == "APPROVED")

report = {
    "variant": FLAG,
    "mode": f"SMOKE ({LIMIT} PDFs) — mechanics check only" if LIMIT else "FULL",
    "split_scores": ("n/a in smoke mode" if LIMIT else
                     "run: python3 v3/dev/analysis/split_score.py  (baselines 118.088 / 118.041)"),
    "cat_fa_new": new_cat_fa,
    "field_changes_vs_golden": dict(field_changes.most_common()),
    "verdict_flow_vs_golden": dict(verdict_flow.most_common()),
    "n_changed_verdicts": len(changed),
    "changed_cases": changed[:25],
}
(OUTDIR / "report.json").write_text(json.dumps(report, indent=2))
print(json.dumps(report, indent=2))
