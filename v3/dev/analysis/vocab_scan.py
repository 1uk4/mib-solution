#!/usr/bin/env python3
"""Dev tool: scan train_labels.csv for value distributions + patterns per field.

Purpose: before deciding fuzzy-match strategy per field, understand what's
actually in the truth data:
    - Which fields are truly closed enums (list values)?
    - Which are format-strict but with large value space (sponsor_id)?
    - Which are free-form with detectable patterns (applicant_name)?

Output guides whether to:
    - Add enum-fuzzy matching (safe when enum is comprehensive)
    - Extract patterns for post-processing correction
    - Leave alone (truly free-form)

Usage:
    python3 v3/dev/analysis/vocab_scan.py
    python3 v3/dev/analysis/vocab_scan.py <field>
"""
from __future__ import annotations

import csv
import re
import sys
from collections import Counter
from pathlib import Path


CHALLENGE = Path.home() / "Code" / "mib-doc-challenge"
LABELS = CHALLENGE / "data" / "train_labels.csv"

FIELDS = ("applicant_name", "species_code", "home_world", "visa_class",
          "sponsor_id", "arrival_date", "declared_purpose",
          "risk_flags", "fee_status")


def _load_field(field: str) -> list[str]:
    """All truth values for a field, empty strings included."""
    values = []
    with LABELS.open() as f:
        for row in csv.DictReader(f):
            values.append(row.get(field, ""))
    return values


def _describe_lengths(values: list[str]) -> str:
    if not values:
        return "(empty)"
    ls = [len(v) for v in values if v]
    if not ls:
        return "(all empty)"
    c = Counter(ls)
    top = c.most_common(3)
    return f"min={min(ls)}, max={max(ls)}, mean={sum(ls)/len(ls):.1f}, common={top}"


def _substring_patterns(values: list[str], n: int = 3, top: int = 15) -> tuple[list, list]:
    """Return (top starting n-grams, top ending n-grams)."""
    starts = Counter(v[:n] for v in values if len(v) >= n)
    ends = Counter(v[-n:] for v in values if len(v) >= n)
    return starts.most_common(top), ends.most_common(top)


def _scan_enum_field(field: str, values: list[str]) -> None:
    """For fields expected to be closed enums: list all + distribution."""
    non_empty = [v for v in values if v]
    counter = Counter(non_empty)
    unique = len(counter)
    print(f"total non-empty: {len(non_empty)}  unique: {unique}")
    print()
    print(f"{'count':>6s}  {'value':<40s}")
    for v, c in counter.most_common():
        print(f"  {c:5d}  {v!r}")


def _scan_multivalue_field(field: str, values: list[str]) -> None:
    """For fields with pipe-delimited multi-value (risk_flags)."""
    all_flags: list[str] = []
    for v in values:
        for flag in v.split("|"):
            if flag.strip():
                all_flags.append(flag.strip())
    counter = Counter(all_flags)
    print(f"total flag-occurrences: {len(all_flags)}  unique: {len(counter)}")
    print()
    print("individual flag counts:")
    for f, c in counter.most_common():
        print(f"  {c:5d}  {f!r}")
    print()
    print("compound values (top 15):")
    compound = Counter(v if v else "(empty)" for v in values)
    for v, c in compound.most_common(15):
        print(f"  {c:5d}  {v!r}")


def _scan_format_strict_field(field: str, values: list[str],
                              pattern: re.Pattern) -> None:
    """For fields with a strict format (sponsor_id: SPN-####)."""
    non_empty = [v for v in values if v]
    unique = len(set(non_empty))
    print(f"total non-empty: {len(non_empty)}  unique: {unique}")
    matching = [v for v in non_empty if pattern.fullmatch(v)]
    non_matching = [v for v in non_empty if not pattern.fullmatch(v)]
    print(f"matching pattern {pattern.pattern!r}: {len(matching)}")
    print(f"NOT matching (would need pattern extension):")
    for v in Counter(non_matching).most_common():
        print(f"  {v[1]:5d}  {v[0]!r}")
    # For sponsor_id: show unique count relative to possible space
    if field == "sponsor_id" and matching:
        digits = [v[-4:] for v in matching]
        print(f"digit range: min={min(digits)}, max={max(digits)}")


def _scan_freeform_field(field: str, values: list[str]) -> None:
    """For truly free-form fields: patterns, prefixes, suffixes."""
    non_empty = [v for v in values if v]
    counter = Counter(non_empty)
    unique = len(counter)
    print(f"total non-empty: {len(non_empty)}  unique: {unique}")
    print(f"length: {_describe_lengths(non_empty)}")
    print()

    # Check if uniqueness is low (small vocab)
    if unique <= 30:
        print("All values (small vocab — might be enum after all):")
        for v, c in counter.most_common():
            print(f"  {c:5d}  {v!r}")
        return

    # Character composition
    all_chars = ''.join(non_empty)
    char_types = Counter()
    for c in all_chars:
        if c.isalpha():
            char_types['alpha'] += 1
        elif c.isdigit():
            char_types['digit'] += 1
        elif c.isspace():
            char_types['space'] += 1
        else:
            char_types['other'] += 1
    print(f"character types: {dict(char_types)}")
    if 'other' in char_types:
        other_chars = Counter(c for c in all_chars if not c.isalnum() and not c.isspace())
        print(f"  non-alnum chars: {dict(other_chars.most_common(10))}")

    # Word count distribution
    word_counts = Counter(len(v.split()) for v in non_empty)
    print(f"word counts: {dict(word_counts.most_common(5))}")

    # Prefix/suffix patterns (3-char)
    starts, ends = _substring_patterns(non_empty, n=3, top=10)
    print("top starting 3-grams:")
    for s, c in starts:
        print(f"  {c:5d}  {s!r}")
    print("top ending 3-grams:")
    for e, c in ends:
        print(f"  {c:5d}  {e!r}")

    # Show 15 random samples so we can eyeball
    import random
    random.seed(42)
    sample = random.sample(non_empty, min(15, len(non_empty)))
    print()
    print("15 random samples:")
    for s in sample:
        print(f"  {s!r}")


def scan_field(field: str) -> None:
    print(f"\n{'='*70}\nFIELD: {field}\n{'='*70}")
    values = _load_field(field)

    if field in ("visa_class", "fee_status", "species_code", "home_world"):
        _scan_enum_field(field, values)
    elif field == "risk_flags":
        _scan_multivalue_field(field, values)
    elif field == "sponsor_id":
        _scan_format_strict_field(field, values, re.compile(r"SPN-\d{4}"))
    elif field == "arrival_date":
        _scan_format_strict_field(field, values, re.compile(r"\d{4}-\d{2}-\d{2}"))
    else:
        _scan_freeform_field(field, values)


def main() -> None:
    args = sys.argv[1:]
    if args:
        for f in args:
            if f in FIELDS:
                scan_field(f)
            else:
                print(f"Unknown field: {f}", file=sys.stderr)
    else:
        for f in FIELDS:
            scan_field(f)


if __name__ == "__main__":
    main()
