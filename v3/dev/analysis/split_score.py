#!/usr/bin/env python3
"""Compute per-split scores by aggregating case_scores.jsonl.

Reads:
  - /tmp/mib-splits/train.txt, val.txt (from split_train_val.py)
  - newest /tmp/mib-dev-runs/<run>/case_scores.jsonl (latest dev_score.sh run;
    do NOT use /tmp/mib-dev-output — that symlink is broken, see golden/README.md)

Reports:
  - Train score, val score, gap (val - train)
  - Per-component (extraction / classification / calibration)
  - Confusion matrix per split
  - Cat FA count per split

Purpose:
  We develop against training data — every rule change, every calibration
  refresh looks at training features. Validation is held out and only
  measured after the fact. If train >> val, we've overfit.

Usage (after dev_score.sh completes):
  python3 v3/dev/analysis/split_score.py
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

SPLITS_DIR = Path("/tmp/mib-splits")
DEV_RUNS_DIR = Path("/tmp/mib-dev-runs")


def _find_latest_case_scores() -> Path:
    """dev_score.sh writes each run to /tmp/mib-dev-runs/<timestamp>-<pid>/;
    find the newest one that contains case_scores.jsonl."""
    if not DEV_RUNS_DIR.exists():
        raise SystemExit(f"Not found: {DEV_RUNS_DIR}. Run dev_score.sh first.")
    candidates = sorted(
        (d for d in DEV_RUNS_DIR.iterdir() if (d / "case_scores.jsonl").exists()),
        key=lambda d: d.stat().st_mtime,
    )
    if not candidates:
        raise SystemExit(f"No case_scores.jsonl found under {DEV_RUNS_DIR}.")
    return candidates[-1] / "case_scores.jsonl"


CASE_SCORES = _find_latest_case_scores()

# Scoring constants from scripts/evaluate.py
EXTRACTION_SECTION_POINTS = 50.0
CLASSIFICATION_SECTION_POINTS = 80.0
CALIBRATION_SECTION_POINTS = 20.0
MISSING_PENALTY_CAP = 10.0


def _load_split(name: str) -> set[str]:
    p = SPLITS_DIR / f"{name}.txt"
    if not p.exists():
        raise SystemExit(f"Not found: {p}. Run split_train_val.py first.")
    return {line.strip() for line in p.read_text().splitlines() if line.strip()}


def _load_case_scores() -> list[dict]:
    print(f"Reading: {CASE_SCORES}", file=sys.stderr)
    return [
        json.loads(l)
        for l in CASE_SCORES.read_text().splitlines()
        if l.strip()
    ]


def _score_subset(cases: list[dict]) -> dict:
    """Aggregate per-case scores into the 4 section totals + cat FA count."""
    n = len(cases)
    if n == 0:
        return {}

    extraction_raw = sum(c["extraction_raw"] for c in cases)
    extraction_max = sum(c["extraction_max_raw"] for c in cases)
    classification_raw = sum(c["classification_raw"] for c in cases)
    classification_max = sum(c["classification_max_raw"] for c in cases)
    cat_fa = sum(1 for c in cases if c.get("catastrophic_false_approval"))
    briers = [c["confidence_brier"] for c in cases
              if c.get("confidence_brier") is not None]
    missing = sum(1 for c in cases if not c.get("present"))

    extraction_score = EXTRACTION_SECTION_POINTS * (extraction_raw / extraction_max) if extraction_max else 0
    classification_score = CLASSIFICATION_SECTION_POINTS * (classification_raw / classification_max) if classification_max else 0
    mean_brier = sum(briers) / len(briers) if briers else None
    calibration_score = (
        CALIBRATION_SECTION_POINTS * max(0.0, 1.0 - 2.0 * mean_brier)
        if mean_brier is not None else 0.0
    )
    missing_penalty = MISSING_PENALTY_CAP * (missing / n) if n else 0
    total = extraction_score + classification_score + calibration_score - missing_penalty

    confusion = Counter(
        (c.get("truth_adjudication"), c.get("pred_adjudication"))
        for c in cases if c.get("present")
    )

    return {
        "n": n,
        "extraction": extraction_score,
        "classification": classification_score,
        "calibration": calibration_score,
        "missing_penalty": missing_penalty,
        "total": total,
        "cat_fa": cat_fa,
        "mean_brier": mean_brier,
        "confusion": confusion,
    }


def _print_report(train: dict, val: dict) -> None:
    print(f"{'metric':<22s}  {'train (800)':>15s}  {'val (200)':>15s}  {'gap (val-train)'}")
    print("-" * 78)
    for key, label in [
        ("total", "Total score / 150"),
        ("extraction", "Extraction / 50"),
        ("classification", "Classification / 80"),
        ("calibration", "Calibration / 20"),
        ("cat_fa", "Cat FA count"),
    ]:
        t = train[key]
        v = val[key]
        if isinstance(t, float):
            print(f"  {label:<20s}  {t:>15.3f}  {v:>15.3f}  {v - t:+.3f}")
        else:
            print(f"  {label:<20s}  {t:>15d}  {v:>15d}  {v - t:+d}")
    print(f"  {'Mean Brier':<20s}  {train['mean_brier']:>15.4f}  {val['mean_brier']:>15.4f}  {val['mean_brier'] - train['mean_brier']:+.4f}")

    # Confusion matrices
    print("\nConfusion matrix (train):")
    for k in sorted(train["confusion"]):
        print(f"  {k[0]:<14s} → {k[1]:<14s}  {train['confusion'][k]:>4d}")
    print("\nConfusion matrix (val):")
    for k in sorted(val["confusion"]):
        print(f"  {k[0]:<14s} → {k[1]:<14s}  {val['confusion'][k]:>4d}")

    # Per-1000 normalized comparison
    print("\nNormalized to 1000 cases (compare apples-to-apples):")
    print(f"  train × 1.25 total: {train['total'] * 1000 / train['n']:.2f}")
    print(f"  val × 5     total: {val['total'] * 1000 / val['n']:.2f}")


def main() -> None:
    train_ids = _load_split("train")
    val_ids = _load_split("val")
    all_cases = _load_case_scores()

    train_cases = [c for c in all_cases if c["case_id"] in train_ids]
    val_cases = [c for c in all_cases if c["case_id"] in val_ids]

    if len(train_cases) + len(val_cases) != len(all_cases):
        print(f"WARNING: split coverage — train {len(train_cases)} + val {len(val_cases)} != total {len(all_cases)}",
              file=sys.stderr)

    train_scores = _score_subset(train_cases)
    val_scores = _score_subset(val_cases)
    _print_report(train_scores, val_scores)


if __name__ == "__main__":
    main()
