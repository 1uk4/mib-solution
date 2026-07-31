#!/usr/bin/env python3
"""Dev tool: audit source_type.classify against training data.

Validates that the marker set for each Field Manual authority level (L1-L6)
actually identifies the right document type — and that the level is
predictive of truth.

Extended level-by-level: after adding L2 markers to source_type.py, rerun
this to verify the new marker set is empirically sound before wiring
level-based confidence into signals.py.

Measures per level:
    1. Coverage — how many sources across training data match each level
    2. Truth correlation — when a source classified at level L is the ONLY
       source for a field, is its extracted value more likely to be correct
       than the same field extracted from an L6 source?
    3. Marker attribution — which specific marker phrase triggered each
       classification (for debugging false positives)

Usage:
    python3 v3/dev/analysis/source_type_audit.py
        # Coverage + first-line samples per level
    python3 v3/dev/analysis/source_type_audit.py --samples <level> [N]
        # Show N (default 20) first-line samples for a given level
    python3 v3/dev/analysis/source_type_audit.py --l1-cases
        # List all packets containing at least one L1 source
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

from v3.acquire import acquire_sources, TEXT_STREAM, IMAGE  # noqa: E402
from v3.extract import extract_content  # noqa: E402
from v3.filters import apply_filters  # noqa: E402
from v3.source_type import classify  # noqa: E402


CHALLENGE = Path.home() / "Code" / "mib-doc-challenge"
CACHE = Path("/tmp/mib-source-types.jsonl")


def _first_line(text: str, maxlen: int = 80) -> str:
    """First non-empty line of a source, truncated for display."""
    for line in (text or "").splitlines():
        s = line.strip()
        if s:
            return s[:maxlen]
    return ""


def _build_records(force_rebuild: bool = False) -> list[dict]:
    """Per-source: {case_id, source_id, kind, level, first_line, trusted}."""
    if CACHE.exists() and CACHE.stat().st_size > 0 and not force_rebuild:
        print(f"Loading cached audit from {CACHE}", file=sys.stderr)
        return [
            json.loads(l) for l in CACHE.read_text().splitlines()
            if l.strip() and '"level"' in l
        ]

    truth = {r["case_id"]: r for r in
             csv.DictReader((CHALLENGE / "data" / "train_labels.csv").open())}
    pdfs = sorted((CHALLENGE / "data" / "train").glob("*.pdf"))
    print(f"Classifying sources across {len(pdfs)} PDFs "
          f"(uses OCR cache — fast after first pass)", file=sys.stderr)

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
                    if not s.trusted:
                        continue
                    rec = {
                        "case_id": cid,
                        "truth_adj": truth[cid]["adjudication"],
                        "source_id": s.id,
                        "kind": s.type,
                        "level": classify(s),
                        "first_line": _first_line(s.content),
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
    print("Source-level coverage across training set\n")
    total = len(records)
    print(f"Total trusted sources classified: {total}")
    by_level = Counter(r["level"] for r in records)
    print(f"{'level':>5s}  {'n':>7s}  {'%':>7s}  {'text':>7s}  {'image':>7s}")
    for lvl in sorted(by_level):
        n = by_level[lvl]
        pct = 100 * n / total
        n_text = sum(1 for r in records if r["level"] == lvl and r["kind"] == TEXT_STREAM)
        n_image = sum(1 for r in records if r["level"] == lvl and r["kind"] == IMAGE)
        print(f"  L{lvl}   {n:>7d}  {pct:>6.1f}%  {n_text:>7d}  {n_image:>7d}")

    # For L1 specifically, show packet-level coverage (how many packets have ≥1 L1 source)
    l1_packets = {r["case_id"] for r in records if r["level"] == 1}
    total_packets = len({r["case_id"] for r in records})
    print()
    print(f"Packets with ≥1 L1 source: {len(l1_packets)} / {total_packets} "
          f"({100 * len(l1_packets) / total_packets:.1f}%)")


def _samples(records: list[dict], level: int, n: int = 20) -> None:
    matches = [r for r in records if r["level"] == level]
    print(f"L{level}: {len(matches)} sources classified\n")
    if not matches:
        return
    # Show a spread across cases
    seen_lines: Counter = Counter()
    print(f"First-line distribution (top 30):")
    for r in matches:
        seen_lines[r["first_line"]] += 1
    for line, count in seen_lines.most_common(30):
        print(f"  {count:>4d}  {line!r}")

    print(f"\nFirst {n} sample sources:")
    for r in matches[:n]:
        print(f"  {r['case_id']}  {r['source_id']:<18s} "
              f"[{r['kind']}] first_line={r['first_line']!r}")


def _l1_cases(records: list[dict]) -> None:
    """For each packet containing an L1 source, show truth + L1 source ids."""
    by_case: dict = defaultdict(list)
    for r in records:
        if r["level"] == 1:
            by_case[r["case_id"]].append(r)
    print(f"Packets with ≥1 L1 source: {len(by_case)}\n")
    # Truth distribution across these packets
    truths = Counter()
    for cid, sigs in by_case.items():
        truths[sigs[0]["truth_adj"]] += 1
    print(f"Truth distribution: {dict(truths)}")
    print()
    for cid, sigs in list(by_case.items())[:40]:
        truth = sigs[0]["truth_adj"]
        src_ids = [s["source_id"] for s in sigs]
        print(f"  {cid}  truth={truth}  sources={src_ids}")


def main() -> None:
    args = sys.argv[1:]
    force = "--rebuild" in args
    if force:
        args.remove("--rebuild")

    records = _build_records(force_rebuild=force)
    records = [r for r in records if "level" in r]

    if not args:
        _coverage(records)
    elif args[0] == "--samples" and len(args) >= 2:
        level = int(args[1])
        n = int(args[2]) if len(args) >= 3 else 20
        _samples(records, level, n)
    elif args[0] == "--l1-cases":
        _l1_cases(records)
    else:
        print(__doc__, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
