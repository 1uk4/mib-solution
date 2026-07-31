#!/usr/bin/env python3
"""Dev tool: analyze features.jsonl for signals that discriminate truth.

For every feature in the extracted features file, compute:
    - P(truth = APPROVED | feature=value)
    - P(truth = DENIED   | feature=value)
    - P(truth = REVIEW   | feature=value)
    - Deviation from base rate ("lift")

Ranks features by strongest per-class discrimination and prints the top.

Usage:
    python3 v3/dev/analysis/correlate.py            # top discriminators
    python3 v3/dev/analysis/correlate.py <feature>  # cross-tab for one feature
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

FEATURES = Path("/tmp/mib-features.jsonl")

TRUTH_CLASSES = ("APPROVED", "DENIED", "NEEDS_REVIEW")

# Features to skip — either identifiers (case_id) or the truth signal itself
_SKIP = {"case_id", "error", "truth_flags",
         "truth_fee_status", "truth_visa_class", "truth_species",
         "v1_confidence", "v3_confidence", "ocr_total_chars"}


def _load() -> list[dict]:
    if not FEATURES.exists():
        raise SystemExit(f"Not found: {FEATURES}\nRun dev_extract_features.py first.")
    return [json.loads(l) for l in FEATURES.read_text().splitlines()]


def _base_rates(rows: list[dict]) -> dict[str, float]:
    truths = Counter(r.get("truth_adjudication", "?") for r in rows)
    total = sum(truths.values())
    return {c: truths.get(c, 0) / total for c in TRUTH_CLASSES}


def _crosstab(rows: list[dict], feature: str) -> dict:
    """For each value of `feature`, count truth-class occurrences."""
    tab: dict = defaultdict(lambda: Counter())
    for r in rows:
        if feature not in r:
            continue
        val = r[feature]
        # Bucket bools/ints as-is; strings as-is
        tab[val][r.get("truth_adjudication", "?")] += 1
    return tab


def _discrimination_score(tab: dict, base_rates: dict[str, float]) -> float:
    """Score how much this feature partitions truth-class distribution."""
    score = 0.0
    for val, truths in tab.items():
        n = sum(truths.values())
        if n < 5:  # ignore rare value combos
            continue
        for cls, base in base_rates.items():
            p = truths.get(cls, 0) / n
            score = max(score, abs(p - base) * (n ** 0.5))  # sqrt weight for support
    return score


def _print_crosstab(feature: str, tab: dict, base_rates: dict[str, float]) -> None:
    print(f"\n{'='*70}\nFeature: {feature}\n{'='*70}")
    print(f"{'value':<40s} n     P(APPR)  P(DEN)   P(REV)  lift(max)")
    for val in sorted(tab.keys(), key=lambda v: -sum(tab[v].values())):
        truths = tab[val]
        n = sum(truths.values())
        if n < 3:
            continue
        pa = truths.get("APPROVED", 0) / n
        pd = truths.get("DENIED", 0) / n
        pr = truths.get("NEEDS_REVIEW", 0) / n
        lifts = [abs(pa - base_rates["APPROVED"]),
                 abs(pd - base_rates["DENIED"]),
                 abs(pr - base_rates["NEEDS_REVIEW"])]
        maxlift = max(lifts)
        marker = " ★" if maxlift > 0.30 else ""
        val_str = str(val)[:38]
        print(f"  {val_str:<38s} {n:5d}  {pa:.2f}     {pd:.2f}     {pr:.2f}    {maxlift:+.2f}{marker}")


def main() -> None:
    rows = _load()
    base_rates = _base_rates(rows)
    print(f"Loaded {len(rows)} rows")
    print(f"Base rates:  APPROVED={base_rates['APPROVED']:.2f}  "
          f"DENIED={base_rates['DENIED']:.2f}  "
          f"NEEDS_REVIEW={base_rates['NEEDS_REVIEW']:.2f}")

    if len(sys.argv) > 1:
        # Detail mode: one feature
        feature = sys.argv[1]
        tab = _crosstab(rows, feature)
        if not tab:
            raise SystemExit(f"Feature not found in data: {feature}")
        _print_crosstab(feature, tab, base_rates)
        return

    # Summary mode: rank all features by discrimination
    features = sorted(k for k in rows[0].keys() if k not in _SKIP)
    scored: list[tuple[float, str]] = []
    for feat in features:
        tab = _crosstab(rows, feat)
        score = _discrimination_score(tab, base_rates)
        scored.append((score, feat))
    scored.sort(reverse=True)

    print(f"\nTop 25 discriminating features (out of {len(scored)}):")
    print(f"{'score':>7s}  feature")
    for score, feat in scored[:25]:
        print(f"  {score:5.2f}  {feat}")

    print("\nRun `python3 v3/dev_correlate.py <feature>` to see the cross-tab.")


if __name__ == "__main__":
    main()
