#!/usr/bin/env python3
"""Dev tool: empirically-measured extraction correctness per field × source.

Reads /tmp/mib-signals.jsonl (produced by extract_signals.py). Aggregates
correctness rates so we can replace hand-picked confidence values with
data-driven ones.

Usage:
    python3 v3/dev/analysis/correctness.py
        # Overview: per-field × source_class × agreement-bucket table
    python3 v3/dev/analysis/correctness.py <field>
        # Detail for one field: per source, per agreement level
    python3 v3/dev/analysis/correctness.py --calibration
        # Suggested confidence values as a Python dict
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path


SIGNALS = Path("/tmp/mib-signals.jsonl")

FIELDS = (
    "applicant_name", "species_code", "home_world", "visa_class",
    "sponsor_id", "arrival_date", "declared_purpose", "risk_flags",
    "fee_status",
)


def _load() -> list[dict]:
    if not SIGNALS.exists():
        raise SystemExit(
            f"Not found: {SIGNALS}\n"
            f"Run: python3 v3/dev/analysis/extract_signals.py"
        )
    return [
        json.loads(l) for l in SIGNALS.read_text().splitlines()
        if l.strip() and "error" not in l  # skip error rows
    ]


def _agreement_bucket(n: int) -> str:
    """Group agreement_n into buckets that give enough samples per cell."""
    if n <= 1:
        return "1"
    if n == 2:
        return "2"
    if n <= 4:
        return "3-4"
    return "5+"


def _stats(rows: list[dict]) -> tuple[int, int, float]:
    """Return (n, correct, pct)."""
    n = len(rows)
    if n == 0:
        return 0, 0, 0.0
    correct = sum(1 for r in rows if r.get("is_correct"))
    return n, correct, correct / n


def _print_overview(all_rows: list[dict]) -> None:
    print(f"Loaded {len(all_rows)} signal rows")
    print()
    print(f"{'field':<20s} {'source':<8s} {'agreement':<10s} "
          f"{'n':>6s}  {'correct':>7s}  {'%':>7s}")
    for field in FIELDS:
        for src_class in ("text", "ocr"):
            for bucket in ("1", "2", "3-4", "5+"):
                subset = [
                    r for r in all_rows
                    if r["field"] == field
                    and r["source_class"] == src_class
                    and _agreement_bucket(r["agreement_n"]) == bucket
                ]
                if not subset:
                    continue
                n, correct, pct = _stats(subset)
                marker = " ★" if pct >= 0.95 else ""
                print(f"  {field:<18s} {src_class:<8s} {bucket:<10s} "
                      f"{n:>6d}  {correct:>7d}  {100*pct:>6.1f}%{marker}")
        print()


def _print_field_detail(all_rows: list[dict], field: str) -> None:
    rows = [r for r in all_rows if r["field"] == field]
    if not rows:
        raise SystemExit(f"No rows for field: {field}")
    print(f"\n{'='*70}\nField: {field}\n{'='*70}")
    total_n, total_correct, total_pct = _stats(rows)
    print(f"Total: n={total_n}  correct={total_correct}  ({100*total_pct:.1f}%)")
    print()

    # By source_class × agreement
    print(f"{'source':<10s} {'agreement':<10s} {'n':>6s}  {'correct':>7s}  {'%':>7s}")
    for src_class in ("text", "ocr"):
        for bucket in ("1", "2", "3-4", "5+"):
            subset = [
                r for r in rows
                if r["source_class"] == src_class
                and _agreement_bucket(r["agreement_n"]) == bucket
            ]
            if subset:
                n, correct, pct = _stats(subset)
                print(f"  {src_class:<8s} {bucket:<10s} "
                      f"{n:>6d}  {correct:>7d}  {100*pct:>6.1f}%")
    print()

    # By has_conflict
    print("With conflict vs without:")
    for conflict in (False, True):
        subset = [r for r in rows if r["has_conflict"] == conflict]
        if subset:
            n, correct, pct = _stats(subset)
            label = "conflict" if conflict else "no conflict"
            print(f"  {label:<15s} {n:>6d}  {correct:>7d}  {100*pct:>6.1f}%")
    print()


def _print_calibration(all_rows: list[dict]) -> None:
    """Suggested calibrated confidence dict — measured correctness, floored."""
    print("# Empirically calibrated confidence values")
    print("# (measured correctness rate, floored at 0.50 for stability)")
    print("# Format: (field, source_class, agreement_bucket) → confidence")
    print()
    print("CALIBRATED_CONFIDENCE = {")
    for field in FIELDS:
        for src_class in ("text", "ocr"):
            for bucket in ("1", "2", "3-4", "5+"):
                subset = [
                    r for r in all_rows
                    if r["field"] == field
                    and r["source_class"] == src_class
                    and _agreement_bucket(r["agreement_n"]) == bucket
                ]
                if len(subset) < 5:  # too few samples to trust
                    continue
                _, _, pct = _stats(subset)
                # Round to 2 decimals; floor at 0.50 to avoid extreme values
                conf = max(0.50, round(pct, 2))
                print(f'    ({field!r}, {src_class!r}, {bucket!r}): {conf:.2f},')
    print("}")


def main() -> None:
    args = sys.argv[1:]
    rows = _load()
    if not args:
        _print_overview(rows)
    elif args[0] == "--calibration":
        _print_calibration(rows)
    elif args[0] in FIELDS:
        _print_field_detail(rows, args[0])
    else:
        raise SystemExit(
            f"Unknown argument: {args[0]}\n"
            f"Usage:\n"
            f"  correctness.py               # overview\n"
            f"  correctness.py <field>       # per-field detail\n"
            f"  correctness.py --calibration # suggested conf table"
        )


if __name__ == "__main__":
    main()
