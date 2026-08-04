#!/usr/bin/env python3
"""V4 solution — layered-trust pipeline, standalone.

Data flows through 7 layers, each with a single responsibility:

    L1 acquire.py       PDF → list[Source]
    L2 extract.py       Sources ← .content populated (text decode / OCR)
    L3 filters/         Sources ← .trusted populated (adversarial filter)
    L4 signals.py       Trusted sources → signal bundle
    L5 consolidate.py   Signals → consolidated field dict
    L6 rules.py         Fields → (adj, conf, tag)
    L7 policy.py        (adj, conf, tag) ← safety-adjusted final

Foundation modules read by every layer: config.py (feature flags),
confidence.py (calibrated confidence registry), vocab.py (closed enums),
patterns.py (compiled regexes / word lists).

DOES: wire the layers together, emit schema-valid predictions.jsonl rows
(sorted case_id order, sort_keys JSON), catch per-PDF crashes into a safe
NEEDS_REVIEW row.
DOES NOT: contain any extraction or decision logic of its own.

v4 imports nothing from v1/v2/v3 — those directories are frozen reference.
Behavior is byte-identical to v3 at commit 92eb104 (verified against
golden/ — see parity.sh).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from v4.acquire import acquire_sources
from v4.confidence import conf
from v4.consolidate import consolidate
from v4.extract import extract_content
from v4.filters import apply_filters
from v4.policy import apply_policy
from v4.rules import apply_rules
from v4.signals import extract_signals

VERSION = "v4_standalone"

OUTPUT_FIELDS = (
    "applicant_name", "species_code", "home_world", "visa_class",
    "sponsor_id", "arrival_date", "declared_purpose", "risk_flags",
    "fee_status",
)


def _default_field(name: str) -> str:
    """Schema-valid placeholders for fields we couldn't extract."""
    return {
        "applicant_name": "unknown",
        "species_code": "unknown",
        "home_world": "unknown",
        "visa_class": "unknown",
        "sponsor_id": "SPN-0000",
        "arrival_date": "1900-01-01",
        "declared_purpose": "unknown",
        "risk_flags": "none",
        "fee_status": "unknown",
    }[name]


def predict_case(pdf_path: Path) -> dict:
    case_id = pdf_path.stem

    # L1-L7
    sources = acquire_sources(pdf_path)
    extract_content(sources)
    apply_filters(sources)
    signals = extract_signals(sources)
    fields = consolidate(signals, case_id)
    adj, c, tag = apply_rules(fields)
    adj, c, tag = apply_policy(fields, adj, c, tag, signals)

    # Assemble schema-valid prediction row. (risk_flags needs no special
    # case: consolidate guarantees it truthy, and the defaults loop would
    # cover it regardless.)
    row = dict(fields)
    for f in OUTPUT_FIELDS:
        if not row.get(f):
            row[f] = _default_field(f)
    row["adjudication"] = adj
    row["confidence"] = c
    row.pop("_finding", None)
    row.pop("_source_class", None)
    row.pop("_agreement", None)
    return row


def main(input_dir: str, output_path: str) -> None:
    pdfs = sorted(Path(input_dir).glob("*.pdf"))
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    total = len(pdfs)
    # Progress cadence: log every N packets, but also force a log every 15s
    # of wall time so long-OCR packets don't leave the user in silence.
    step = 1 if total <= 20 else 5 if total <= 100 else 10 if total <= 500 else 20
    print(f"[v4] processing {total} pdfs...", file=sys.stderr, flush=True)
    start = time.time()
    last_log = start
    with out.open("w") as f:
        for i, pdf in enumerate(pdfs, 1):
            try:
                pred = predict_case(pdf)
            except Exception:
                # Never let one bad PDF break the batch. Emit safe REVIEW.
                # NOTE (B2-2): reusing FALLBACK_extraction_fail's confidence
                # for the crash path is a known calibration smell — preserved
                # verbatim this phase; logged in v4/OBSERVATIONS.md.
                pred = {
                    "case_id": pdf.stem,
                    **{k: _default_field(k) for k in OUTPUT_FIELDS},
                    "adjudication": "NEEDS_REVIEW",
                    "confidence": conf("FALLBACK_extraction_fail"),
                }
            f.write(json.dumps(pred, sort_keys=True) + "\n")
            now = time.time()
            if i == 1 or i % step == 0 or i == total or (now - last_log) >= 15:
                elapsed = now - start
                sec_per_pdf = elapsed / i if i > 0 else 0
                eta = (total - i) * sec_per_pdf
                print(
                    f"[v4] {i}/{total}  {pdf.stem}  "
                    f"({sec_per_pdf:.2f}s/pdf, elapsed {_fmt_duration(elapsed)}, "
                    f"eta {_fmt_duration(eta)})",
                    file=sys.stderr, flush=True,
                )
                last_log = now
    print(f"[v4] done in {_fmt_duration(time.time() - start)}",
          file=sys.stderr, flush=True)


def _fmt_duration(seconds: float) -> str:
    """Human-readable duration: '3s', '1m 12s', '1h 3m'."""
    total = int(round(seconds))
    if total < 60:
        return f"{total}s"
    if total < 3600:
        m, s = divmod(total, 60)
        return f"{m}m {s:02d}s"
    h, rem = divmod(total, 3600)
    m = rem // 60
    return f"{h}h {m:02d}m"


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: python3 -m v4.solution <input_pdf_dir> <output_path>")
    main(sys.argv[1], sys.argv[2])
