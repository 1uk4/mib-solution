#!/usr/bin/env python3
"""L2 investigation (#4 upscale value/scale, #5 union-order sensitivity).

Stage A: for a deterministic 120-packet sample, run each doc-image's OCR
passes SEPARATELY (base psm6, upscale2.0 psm3, upscale1.5 psm3, sharpen
psm6), threaded, texts stored by sha256.

Stage B: replay the FULL pipeline per composition config with
_ocr_image_triple patched to compose stored pass texts (no re-OCR).
Non-doc images hit the warm single-pass cache. Per config: compare the 9
extracted fields per case against truth and against production (C0).

Configs:
  C0          [base, up2.0, sharp]   production
  ORDER_REV   [sharp, up2.0, base]   reversed union order
  ORDER_HFIRST[up2.0, base, sharp]   upscale first
  SCALE_15    [base, up1.5, sharp]   cheaper upscale
  NO_UPSCALE  [base, sharp]          drop the upscale pass
  DUAL_EQUIV  [base, up2.0]          drop the sharpen pass
  BASE_ONLY   [base]                 single pass
"""
import csv
import hashlib
import io
import json
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, "/Users/lukaflores/Code/mib-solution")

from PIL import Image, ImageFilter

import v4.extract as extract
from v4.acquire import acquire_sources, IMAGE
from v4.consolidate import consolidate
from v4.extract import _tesseract, _user_words_flags, _looks_like_document, extract_content
from v4.filters import apply_filters
from v4.signals import extract_signals
from v4.config import CONFIG

REPO = Path("/Users/lukaflores/Code/mib-solution")
PDF_DIR = Path("/tmp/mib-dev/pdfs")
LABELS = Path.home() / "Code" / "mib-doc-challenge" / "data" / "train_labels.csv"
OUT = Path(__file__).parent / "ocr_pass_results.json"

FIELDS = ("applicant_name", "species_code", "home_world", "visa_class",
          "sponsor_id", "arrival_date", "declared_purpose", "risk_flags",
          "fee_status")

# --- Sample selection (deterministic) ---
rng = random.Random(20260803)
all_pdfs = sorted(PDF_DIR.glob("*.pdf"))
sample = sorted(rng.sample(all_pdfs, 120), key=lambda p: p.stem)

truth = {}
with LABELS.open() as f:
    for row in csv.DictReader(f):
        truth[row["case_id"]] = {k: row[k] for k in FIELDS}

# --- Stage A: per-pass OCR of every doc image in the sample ---
def png_transform(raw, fn):
    img = Image.open(io.BytesIO(raw)); img.load()
    img = fn(img)
    buf = io.BytesIO(); img.save(buf, format="PNG")
    return buf.getvalue()

doc_images = {}  # sha -> raw
per_packet_docs = {}
for p in sample:
    shas = []
    for s in acquire_sources(p):
        if s.type == IMAGE and _looks_like_document(s):
            sha = hashlib.sha256(s.raw).hexdigest()
            doc_images[sha] = s.raw
            shas.append(sha)
    per_packet_docs[p.stem] = shas

print(f"sample: {len(sample)} packets, {len(doc_images)} unique doc images", flush=True)

UW = _user_words_flags(CONFIG)

def run_passes(item):
    sha, raw = item
    out = {"base": _tesseract(raw, psm=6, extra_flags=UW)}
    up2 = png_transform(raw, lambda im: im.resize((im.width * 2, im.height * 2), Image.LANCZOS))
    out["up20"] = _tesseract(up2, psm=3, extra_flags=UW)
    up15 = png_transform(raw, lambda im: im.resize((int(im.width * 1.5), int(im.height * 1.5)), Image.LANCZOS))
    out["up15"] = _tesseract(up15, psm=3, extra_flags=UW)
    sharp = png_transform(raw, lambda im: im.filter(ImageFilter.UnsharpMask(radius=2, percent=150)))
    out["sharp"] = _tesseract(sharp, psm=6, extra_flags=UW)
    return sha, out

t0 = time.time()
pass_texts = {}
with ThreadPoolExecutor(max_workers=8) as ex:
    for i, (sha, texts) in enumerate(ex.map(run_passes, doc_images.items()), 1):
        pass_texts[sha] = texts
        if i % 40 == 0:
            print(f"  stage A: {i}/{len(doc_images)} images ({time.time()-t0:.0f}s)", flush=True)
print(f"stage A done in {time.time()-t0:.0f}s", flush=True)

# --- Stage B: pipeline replay per composition config ---
CONFIGS = {
    "C0":           ["base", "up20", "sharp"],
    "ORDER_REV":    ["sharp", "up20", "base"],
    "ORDER_HFIRST": ["up20", "base", "sharp"],
    "SCALE_15":     ["base", "up15", "sharp"],
    "NO_UPSCALE":   ["base", "sharp"],
    "DUAL_EQUIV":   ["base", "up20"],
    "BASE_ONLY":    ["base"],
}

orig_triple = extract._ocr_image_triple
current = ["C0"]

def patched_triple(image_bytes, config=CONFIG):
    sha = hashlib.sha256(image_bytes).hexdigest()
    if sha in pass_texts:
        keys = CONFIGS[current[0]]
        parts = [pass_texts[sha][k] for k in keys if pass_texts[sha][k]]
        return "\n".join(parts)
    return orig_triple(image_bytes, config)

extract._ocr_image_triple = patched_triple

def extract_fields_for(pdf_path):
    sources = acquire_sources(pdf_path)
    extract_content(sources)
    apply_filters(sources)
    bundle = extract_signals(sources)
    f = consolidate(bundle, pdf_path.stem)
    return {k: f.get(k, "") for k in FIELDS}

results = {}
t0 = time.time()
for cfg in CONFIGS:
    current[0] = cfg
    per_case = {}
    for p in sample:
        per_case[p.stem] = extract_fields_for(p)
    results[cfg] = per_case
    print(f"  stage B: {cfg} done ({time.time()-t0:.0f}s)", flush=True)

# --- Analysis ---
def truth_matches(per_case):
    n = 0
    for cid, fields in per_case.items():
        for k in FIELDS:
            got = fields[k] or ("none" if k == "risk_flags" else "")
            if got == truth[cid][k]:
                n += 1
    return n

report = {"sample_packets": len(sample), "doc_images": len(doc_images),
          "total_fields": len(sample) * len(FIELDS), "configs": {}}
base_matches = truth_matches(results["C0"])
for cfg, per_case in results.items():
    diffs = []
    for cid in per_case:
        for k in FIELDS:
            if per_case[cid][k] != results["C0"][cid][k]:
                diffs.append((cid, k, results["C0"][cid][k], per_case[cid][k]))
    m = truth_matches(per_case)
    report["configs"][cfg] = {
        "truth_matched_fields": m,
        "delta_vs_C0": m - base_matches,
        "fields_differing_from_C0": len(diffs),
        "cases_differing_from_C0": len({d[0] for d in diffs}),
        "example_diffs": diffs[:10],
    }

OUT.write_text(json.dumps(report, indent=2))
print(json.dumps({k: {kk: vv for kk, vv in v.items() if kk != "example_diffs"}
                  for k, v in report["configs"].items()}, indent=2))
print(f"full report: {OUT}")
