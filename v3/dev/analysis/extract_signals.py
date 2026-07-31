#!/usr/bin/env python3
"""Dev tool: per-Signal correctness data → /tmp/mib-signals.jsonl.

For every FIELD_VALUE Signal emitted by L4 across all 1000 training PDFs,
records:
    case_id, field, value, truth_value, is_correct
    source_id, source_class (text|ocr), confidence
    agreement_n, has_conflict, won_consolidation

Used by dev/analysis/correctness.py to compute empirically-calibrated
confidence values — the answer to "given features F, what fraction of
extractions turn out to be correct against truth?"

Correctness match semantics:
    - risk_flags: subset match (signal's flags ⊆ truth's flags). Each
      source only sees what its own document shows; partial-flag reports
      are correct for that source.
    - All other fields: exact string match after strip().

Usage:
    python3 v3/dev/analysis/extract_signals.py
    # → writes /tmp/mib-signals.jsonl (one JSON object per Signal)

Uses OCR cache (~/.cache/mib-ocr by default) — subsequent runs are fast.
"""
from __future__ import annotations

import csv
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
os.environ.setdefault("MIB_OCR_CACHE_DIR", os.path.expanduser("~/.cache/mib-ocr"))

from v3.acquire import acquire_sources  # noqa: E402
from v3.extract import extract_content  # noqa: E402
from v3.filters import apply_filters  # noqa: E402
from v3.signals import extract_signals, FIELD_VALUE  # noqa: E402
from v3.consolidate import consolidate  # noqa: E402


CHALLENGE = Path.home() / "Code" / "mib-doc-challenge"
OUT = Path("/tmp/mib-signals.jsonl")


def _source_class(source_id: str) -> str:
    """text (combined + per-stream) vs ocr (per-image)."""
    if source_id == "combined_text" or source_id.startswith("text_stream_"):
        return "text"
    if source_id.startswith("image_"):
        return "ocr"
    return "other"


def _is_correct(field: str, extracted: str, truth: str) -> bool:
    """Compare extracted value to truth.

    Fields with pipe-delimited values (risk_flags) use subset semantics:
    the signal is correct if its reported flags are a subset of truth's.
    All other fields require exact match after stripping.
    """
    e = (extracted or "").strip()
    t = (truth or "").strip()
    if field == "risk_flags":
        # subset: every flag the signal reported is actually in truth
        e_flags = {f for f in e.split("|") if f}
        t_flags = {f for f in t.split("|") if f}
        return e_flags.issubset(t_flags) and bool(e_flags)
    return e == t


def _extract_one(pdf_path: Path, truth: dict) -> list[dict]:
    """Return one dict per emitted FIELD_VALUE Signal for this packet."""
    sources = acquire_sources(pdf_path)
    extract_content(sources)
    apply_filters(sources)
    bundle = extract_signals(sources)
    fields = consolidate(bundle, pdf_path.stem)

    agreement = fields.get("_agreement", {})
    rows: list[dict] = []
    for s in bundle["signals"]:
        if s.type != FIELD_VALUE:
            continue
        field = s.key
        truth_val = truth.get(field, "")
        agr = agreement.get(field, {}) or {}
        won = (agr.get("value") == s.value)
        rows.append({
            "case_id": pdf_path.stem,
            "field": field,
            "value": s.value,
            "truth_value": truth_val,
            "is_correct": _is_correct(field, s.value, truth_val),
            "source_id": s.source_id,
            "source_class": _source_class(s.source_id),
            "confidence": s.confidence,
            "agreement_n": agr.get("n_sources", 1),
            "n_agreeing": agr.get("n_agreeing", 1),
            "has_conflict": agr.get("has_conflict", False),
            "won_consolidation": won,
        })
    return rows


def main() -> None:
    train_dir = CHALLENGE / "data" / "train"
    labels_csv = CHALLENGE / "data" / "train_labels.csv"

    truth = {r["case_id"]: r for r in csv.DictReader(labels_csv.open())}
    pdfs = sorted(train_dir.glob("*.pdf"))
    total = len(pdfs)
    print(f"Emitting per-signal correctness data for {total} PDFs → {OUT}",
          file=sys.stderr)

    start = time.time()
    n_signals = 0
    with OUT.open("w") as f:
        for i, pdf in enumerate(pdfs, 1):
            cid = pdf.stem
            if cid not in truth:
                continue
            try:
                rows = _extract_one(pdf, truth[cid])
                for row in rows:
                    f.write(json.dumps(row, sort_keys=True) + "\n")
                    n_signals += 1
            except Exception as e:
                f.write(json.dumps({"case_id": cid, "error": repr(e)}) + "\n")
            if i % 100 == 0 or i == total:
                elapsed = time.time() - start
                rate = i / elapsed if elapsed else 0
                eta = (total - i) / rate if rate else 0
                print(f"  {i}/{total}  ({rate:.1f}/s, eta {eta:.0f}s)",
                      file=sys.stderr)

    print(f"Wrote {n_signals} signal rows to {OUT}", file=sys.stderr)


if __name__ == "__main__":
    main()
