#!/usr/bin/env python3
"""Dev tool: per-image OCR quality baseline.

For each image source in training, measures whether OCR successfully
extracted the fields we KNOW that document type should contain (per
Field Manual). Ground truth per PACKET is used as the reference; each
document type has an "expected fields" set derived from what a real
instance of that type contains:

    L1 adjudicator note   → adjudicator finding (_finding), manual corrections
    L2 intake form         → all 9 case fields (canonical intake source)
    L3 biometric slip      → applicant_name (identity shown on slip)
    L4 sponsor attestation → applicant_name, sponsor_id (letter FROM sponsor TO applicant)
    L5 registry extract    → applicant_name, species_code, home_world, arrival_date

Also correlates OCR-extraction success with image properties (size,
brightness, contrast) so we can identify what makes OCR fail:
    * Low-DPI thumbnails (need upscaling before OCR)
    * Low-contrast stamps (need binarization)
    * Very-bright / very-dark images (need normalization)

Cache: /tmp/mib-ocr-quality.jsonl — per-source records. Uses the OCR cache
in MIB_OCR_CACHE_DIR so re-runs after preprocessing changes just need
--rebuild.

Usage:
    python3 v3/dev/analysis/ocr_quality_audit.py
        # Coverage table: source-type × field → % extracted, % correct
    python3 v3/dev/analysis/ocr_quality_audit.py --props
        # Image-property correlations with OCR success
    python3 v3/dev/analysis/ocr_quality_audit.py --worst <level> [N]
        # List N (default 20) worst-OCR images for level; inspect_case for detail
    python3 v3/dev/analysis/ocr_quality_audit.py --rebuild
        # Regenerate cache (after preprocessing / config changes)
"""
from __future__ import annotations

import csv
import json
import os
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
os.environ.setdefault("MIB_OCR_CACHE_DIR", os.path.expanduser("~/.cache/mib-ocr"))

from v1.solution import extract_fields  # noqa: E402
from v3.acquire import acquire_sources, IMAGE  # noqa: E402
from v3.extract import extract_content  # noqa: E402
from v3.filters import apply_filters  # noqa: E402
from v3.source_type import classify  # noqa: E402


CHALLENGE = Path.home() / "Code" / "mib-doc-challenge"
CACHE = Path("/tmp/mib-ocr-quality.jsonl")

ALL_FIELDS = (
    "applicant_name", "species_code", "home_world", "visa_class",
    "sponsor_id", "arrival_date", "declared_purpose", "risk_flags",
    "fee_status",
)

# Per source-type: which fields the document type SHOULD contain. OCR
# failing to extract a field NOT in this set doesn't count as a defect
# (some fields aren't printed on every document type).
EXPECTED_FIELDS = {
    1: set(),  # adjudicator note — variable; not scored for field extraction
    2: set(ALL_FIELDS),  # intake form: canonical, has all 9
    3: {"applicant_name"},  # biometric slip: identity only
    4: {"applicant_name", "sponsor_id"},  # sponsor letter
    5: {"applicant_name", "species_code", "home_world", "arrival_date"},
    6: set(),  # L6 — no reference expectation
}


def _is_correct(field: str, extracted: str, truth: str) -> bool:
    e = (extracted or "").strip()
    t = (truth or "").strip()
    if field == "risk_flags":
        e_flags = {f for f in e.split("|") if f}
        t_flags = {f for f in t.split("|") if f}
        return e_flags.issubset(t_flags) and bool(e_flags)
    return e == t


def _build_records(force_rebuild: bool = False) -> list[dict]:
    """Per-image record with OCR extraction outcome vs truth."""
    if CACHE.exists() and CACHE.stat().st_size > 0 and not force_rebuild:
        print(f"Loading cached audit from {CACHE}", file=sys.stderr)
        return [
            json.loads(l) for l in CACHE.read_text().splitlines()
            if l.strip() and '"level"' in l
        ]

    truth = {r["case_id"]: r for r in
             csv.DictReader((CHALLENGE / "data" / "train_labels.csv").open())}
    pdfs = sorted((CHALLENGE / "data" / "train").glob("*.pdf"))
    print(f"Auditing OCR quality across {len(pdfs)} PDFs (uses OCR cache)",
          file=sys.stderr)

    records: list[dict] = []
    start = time.time()
    with CACHE.open("w") as f:
        for i, pdf in enumerate(pdfs, 1):
            cid = pdf.stem
            if cid not in truth:
                continue
            try:
                srcs = acquire_sources(pdf)
                extract_content(srcs)
                apply_filters(srcs)
                for s in srcs:
                    if s.type != IMAGE or not s.trusted:
                        continue
                    level = classify(s)
                    ocr_text = s.content or ""
                    extracted = extract_fields(ocr_text, "PENDING")
                    per_field = {}
                    for field in ALL_FIELDS:
                        val = extracted.get(field, "")
                        per_field[field] = {
                            "extracted": val,
                            "truth": truth[cid].get(field, ""),
                            "present": bool(val),
                            "correct": _is_correct(field, val, truth[cid].get(field, "")),
                        }
                    rec = {
                        "case_id": cid,
                        "source_id": s.id,
                        "level": level,
                        "ocr_len": len(ocr_text),
                        "props": {
                            k: s.metadata.get(k)
                            for k in ("width", "height", "mode",
                                      "mean_brightness", "brightness_std")
                        },
                        "fields": per_field,
                    }
                    f.write(json.dumps(rec) + "\n")
                    records.append(rec)
            except Exception as e:
                f.write(json.dumps({"case_id": cid, "error": repr(e)}) + "\n")
            if i % 100 == 0 or i == len(pdfs):
                elapsed = time.time() - start
                rate = i / elapsed if elapsed else 0
                eta = (len(pdfs) - i) / rate if rate else 0
                print(f"  {i}/{len(pdfs)} ({rate:.1f}/s, eta {eta:.0f}s)",
                      file=sys.stderr)
    return records


def _coverage(records: list[dict]) -> None:
    """Per-level × field: how often is OCR extracting the expected field
    AND getting it right?"""
    print("OCR field-extraction quality per source type\n")
    print("Each row: for images classified at that level, what fraction of "
          "sources extracted this field, and of those, what fraction match truth.\n")
    print(f"  {'level':<6s} {'n_images':>9s}  {'field':<20s} {'expected':>8s} "
          f"{'extract%':>9s} {'correct%':>9s}")
    for lvl in (1, 2, 3, 4, 5, 6):
        level_recs = [r for r in records if r["level"] == lvl]
        if not level_recs:
            continue
        n = len(level_recs)
        for field in ALL_FIELDS:
            expected = field in EXPECTED_FIELDS[lvl]
            n_extracted = sum(1 for r in level_recs if r["fields"][field]["present"])
            n_correct = sum(1 for r in level_recs if r["fields"][field]["correct"])
            ext_pct = 100 * n_extracted / n
            corr_pct = 100 * n_correct / n_extracted if n_extracted else 0
            marker = " *" if expected else "  "
            print(f"  L{lvl:<5d} {n:>9d}  {field:<20s} {'yes' if expected else 'no':>7s}{marker} "
                  f"{ext_pct:>8.1f}% {corr_pct:>8.1f}%")
        print()


def _props(records: list[dict]) -> None:
    """How image size / brightness / contrast correlate with OCR success."""
    print("OCR success rate by image property (any-field extraction)\n")

    def _bucket(recs, key, edges):
        buckets = defaultdict(list)
        for r in recs:
            v = r["props"].get(key)
            if v is None:
                continue
            for lo, hi in edges:
                if lo <= v < hi:
                    label = f"[{lo}, {hi})"
                    buckets[label].append(r)
                    break
        return buckets

    def _any_extract(r):
        return any(r["fields"][f]["present"] for f in ALL_FIELDS)

    # For L2 images (canonical intake — should always have all fields)
    l2 = [r for r in records if r["level"] == 2]
    l6 = [r for r in records if r["level"] == 6]

    for label, recs in [("L2 (intake form)", l2), ("L6 (unclassified)", l6)]:
        print(f"--- {label} — n={len(recs)} ---")
        for key, edges in [
            ("width", [(0, 400), (400, 800), (800, 1600), (1600, 3200)]),
            ("mean_brightness", [(0, 50), (50, 128), (128, 200), (200, 256)]),
            ("brightness_std", [(0, 15), (15, 40), (40, 80), (80, 128)]),
        ]:
            buckets = _bucket(recs, key, edges)
            print(f"  {key}:")
            for bkt_label in sorted(buckets):
                bkt = buckets[bkt_label]
                any_ex = sum(1 for r in bkt if _any_extract(r))
                pct = 100 * any_ex / len(bkt) if bkt else 0
                print(f"    {bkt_label:<15s} n={len(bkt):>4d}  any-extract={pct:>5.1f}%")
        print()


def _worst(records: list[dict], level: int, n: int = 20) -> None:
    """List images at a given level where OCR extracted least."""
    lvl_recs = [r for r in records if r["level"] == level]
    print(f"Worst-OCR images at L{level} (n={len(lvl_recs)})\n")

    def _extract_count(r):
        expected = EXPECTED_FIELDS.get(r["level"], set())
        if not expected:
            expected = set(ALL_FIELDS)
        return sum(1 for f in expected if r["fields"][f]["present"])

    # Sort ascending by extract count — worst OCR first
    lvl_recs.sort(key=lambda r: _extract_count(r))
    expected_count = len(EXPECTED_FIELDS.get(level, set())) or len(ALL_FIELDS)
    print(f"  Expected extractable fields per L{level} image: {expected_count}\n")
    print(f"  {'case_id':<14s} {'source':<12s} {'extracted':>9s}  {'wxh':<12s} "
          f"{'brightness':>10s} {'std':>6s}  {'ocr_len':>8s}")
    for r in lvl_recs[:n]:
        ec = _extract_count(r)
        w = r["props"].get("width") or 0
        h = r["props"].get("height") or 0
        b = r["props"].get("mean_brightness") or 0
        std = r["props"].get("brightness_std") or 0
        print(f"  {r['case_id']:<14s} {r['source_id']:<12s} "
              f"{ec:>4d}/{expected_count:<4d} {f'{w}x{h}':<12s} "
              f"{b:>10.1f} {std:>6.1f}  {r['ocr_len']:>8d}")


def main() -> None:
    args = sys.argv[1:]
    force = "--rebuild" in args
    if force:
        args.remove("--rebuild")
    records = _build_records(force_rebuild=force)
    records = [r for r in records if "level" in r]

    if not args:
        _coverage(records)
    elif args[0] == "--props":
        _props(records)
    elif args[0] == "--worst" and len(args) >= 2:
        level = int(args[1])
        n = int(args[2]) if len(args) >= 3 else 20
        _worst(records, level, n)
    else:
        print(__doc__, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
