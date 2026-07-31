#!/usr/bin/env python3
"""Vocab audit — enumerate closed-enum field values from train_labels.csv
and cross-check against FIELD_MANUAL.md before writing them into any
hardcoded list (user-words, enum snap tables, whitelists).

Rule (per memory `vocab_audit_discipline.md`): never trust
recalled/generated lists. If a value doesn't appear in either training
labels or the Manual, reject it.

Usage:
    python3 v3/dev/analysis/vocab_audit.py \
        --challenge-dir ../mib-doc-challenge \
        --out v3/data/tesseract_user_words.txt \
        --report v3/data/vocab_audit_report.md
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter
from pathlib import Path

CLOSED_ENUM_FIELDS = ("visa_class", "fee_status", "species_code")


def enum_values_from_labels(labels_path: Path) -> dict[str, Counter]:
    """Return {field_name: Counter(value → count)} for the closed-enum fields."""
    result = {f: Counter() for f in CLOSED_ENUM_FIELDS}
    with labels_path.open() as f:
        for row in csv.DictReader(f):
            for field in CLOSED_ENUM_FIELDS:
                v = (row.get(field) or "").strip()
                if v:
                    result[field][v] += 1
    return result


def manual_tokens(manual_path: Path) -> set[str]:
    """Return every token that looks like an enum value in the Field Manual.

    Heuristic: any ALLCAPS_WORD or `TOKEN-N` or `token` inside backticks.
    Also captures visa classes like MED-3, XW-1 and species like TRIANGULAN.
    """
    text = manual_path.read_text()
    backticked = set(re.findall(r"`([^`]+)`", text))
    allcaps = set(re.findall(r"\b[A-Z]{4,}\b", text))
    return {t.strip() for t in (backticked | allcaps) if t.strip()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--challenge-dir", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--report", required=True, type=Path)
    args = ap.parse_args()

    labels = args.challenge_dir / "data" / "train_labels.csv"
    manual = args.challenge_dir / "FIELD_MANUAL.md"
    if not labels.exists() or not manual.exists():
        print(f"missing: {labels} or {manual}", file=sys.stderr)
        sys.exit(1)

    training_values = enum_values_from_labels(labels)
    manual_set = manual_tokens(manual)

    # Build the user-words list. A token is included if it appears in
    # training AND is a closed-enum value. Manual-only tokens are included
    # too (eval may introduce them).
    tokens: set[str] = set()
    for field, counter in training_values.items():
        for v in counter:
            tokens.add(v)
    manual_only_enum = {t for t in manual_set if "-" in t and any(c.isdigit() for c in t)}
    tokens |= manual_only_enum

    # Filter out obvious non-tokens (long phrases, punctuation-heavy)
    tokens = {t for t in tokens if len(t) <= 40 and " " not in t}

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w") as f:
        for t in sorted(tokens):
            f.write(t + "\n")

    # Build report
    lines = ["# Vocab audit report\n\n"]
    lines.append(f"Generated from `{labels}` and `{manual}`.\n\n")
    for field, counter in training_values.items():
        lines.append(f"## {field}\n\n")
        lines.append("| value | count | in_manual |\n|---|---|---|\n")
        for v, n in counter.most_common():
            in_manual = "yes" if v in manual_set else "no"
            lines.append(f"| `{v}` | {n} | {in_manual} |\n")
        lines.append("\n")
    lines.append(f"## Manual-only enum tokens (kept in user-words)\n\n")
    for t in sorted(manual_only_enum):
        lines.append(f"- `{t}`\n")
    lines.append(f"\n**Total user-words tokens written:** {len(tokens)}\n")

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text("".join(lines))
    print(f"wrote {len(tokens)} tokens to {args.out}")
    print(f"report: {args.report}")


if __name__ == "__main__":
    main()
