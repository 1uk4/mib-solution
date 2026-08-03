#!/usr/bin/env python3
"""Deterministic 800/200 train/validation split of the challenge's 1000
training packets.

Writes case-id lists to /tmp/mib-splits/{train.txt,val.txt}. Uses a fixed
random seed so the split is reproducible across runs and across machines.

Usage:
    python3 v3/dev/analysis/split_train_val.py

Downstream:
    v3/dev/analysis/split_score.py reads the split lists + case_scores.jsonl
    and reports per-split scores. Any rule change should be evaluated on
    BOTH splits; a change that improves train but hurts val is overfitting.

Why fixed at import time:
    The split must never move once development starts using it, or we lose
    the "held-out" guarantee. If someone edits the seed or the split_size,
    they invalidate all prior validation measurements. Keep this file frozen.
"""
from __future__ import annotations

import csv
import random
import sys
from pathlib import Path

SEED = 20260803         # date-based; never change
TRAIN_SIZE = 800        # 80/20 split
CHALLENGE_DIR = Path.home() / "Code" / "mib-doc-challenge"
LABELS_CSV = CHALLENGE_DIR / "data" / "train_labels.csv"
OUT_DIR = Path("/tmp/mib-splits")


def main() -> None:
    if not LABELS_CSV.exists():
        raise SystemExit(f"Not found: {LABELS_CSV}")

    with LABELS_CSV.open() as f:
        case_ids = sorted(r["case_id"] for r in csv.DictReader(f))

    if len(case_ids) != 1000:
        print(f"WARNING: expected 1000 cases, found {len(case_ids)}", file=sys.stderr)

    rng = random.Random(SEED)
    shuffled = case_ids.copy()
    rng.shuffle(shuffled)

    train_ids = sorted(shuffled[:TRAIN_SIZE])
    val_ids = sorted(shuffled[TRAIN_SIZE:])

    OUT_DIR.mkdir(exist_ok=True)
    (OUT_DIR / "train.txt").write_text("\n".join(train_ids) + "\n")
    (OUT_DIR / "val.txt").write_text("\n".join(val_ids) + "\n")

    print(f"Split (seed={SEED}, train_size={TRAIN_SIZE}):")
    print(f"  train: {len(train_ids)} case IDs → {OUT_DIR / 'train.txt'}")
    print(f"  val:   {len(val_ids)} case IDs → {OUT_DIR / 'val.txt'}")
    print(f"  first 3 train: {train_ids[:3]}")
    print(f"  first 3 val:   {val_ids[:3]}")


if __name__ == "__main__":
    main()
