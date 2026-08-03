#!/usr/bin/env python3
"""Empirically calibrate per-rule confidence values from training data.

For each L6 rule tag, compute the confidence value that minimizes Brier
loss on the training set — which for a proper scoring rule equals the
measured accuracy: P(correct | this rule fired).

Also ranks rules by "improvement opportunity" (wrong-prediction count) so
we can see which rules are producing the most errors and are the highest
ROI targets for structural fixes vs pure calibration.

Usage
-----
Overview + current vs suggested confidence:
    python3 v3/dev/analysis/calibrate_confidence.py

Machine-readable Python snippet ready to paste into v1/solution.py:
    python3 v3/dev/analysis/calibrate_confidence.py --emit

Rank rules by improvement opportunity (highest wrong-prediction count):
    python3 v3/dev/analysis/calibrate_confidence.py --gap

Estimate the calibration score delta from applying the empirical values:
    python3 v3/dev/analysis/calibrate_confidence.py --brier

Regenerate the features file first (necessary after rule changes):
    python3 v3/dev/analysis/extract_features.py
    python3 v3/dev/analysis/calibrate_confidence.py

Workflow with rule improvement
------------------------------
1. Run this script to see per-rule accuracy + improvement opportunities.
2. Pick a low-accuracy, high-fire-count rule (e.g. R_A1_paid_clean).
3. Investigate WHY that rule mispredicts (per-case features, cohort analysis).
4. Change the rule / add a sub-classifier / relax an over-fire guard.
5. Regenerate `/tmp/mib-features.jsonl` via `extract_features.py`.
6. Re-run this script — new accuracies drive new confidence values.
7. Paste the emitted CONFIDENCE block into v1/solution.py.

The point: calibration and rule improvement compose. Fixing rules RAISES
empirical accuracy; empirical accuracy sets the honest confidence.
Together they compound.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

# Import current CONFIDENCE to diff against
REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
from v1.solution import CONFIDENCE as CURRENT  # noqa: E402

FEATURES = Path("/tmp/mib-features.jsonl")

# Rule-family → adjudication verdict the rule outputs. Used to identify
# "L7 kept the L6 verdict" cases (where L6 confidence flows to output).
# Dynamic-tag rules (R2_disqualifier[flag], R_ADJUDICATOR_FINDING[verdict])
# are matched by base prefix.
_FAMILY_TO_ADJ = {
    "R_ADJUDICATOR_FINDING": None,     # dynamic: verdict-specific
    "R0_hard_embargo": "DENIED",
    "R1_transit7": "DENIED",
    "R2_disqualifier": "DENIED",
    "R3_unpaid": "DENIED",
    "R4_revoked_sponsor": "DENIED",
    "R4b_embargoed_home": "DENIED",
    "R5_stale": "DENIED",
    "R_R1_flag_present": "NEEDS_REVIEW",
    "R_R2_unknown_fee": "NEEDS_REVIEW",
    "R_A1_paid_clean": "APPROVED",
    "R_A1_dip1_waived": "APPROVED",
    # Note: L6 tag suffix "_TO_REVIEW" — this is the "R_A1_non_dip_waived" family
    "R_A1_non_dip_waived": "NEEDS_REVIEW",
    "FALLBACK_extraction_fail": "NEEDS_REVIEW",
    "FALLBACK_missing_arrival": "NEEDS_REVIEW",
}

_TAG_STRIP = re.compile(r"^([^[]+)")

# Cap at slightly-below-1.0 to leave headroom for unseen eval-set tail cases
# that could push measured 100% training accuracy down to 98-99% on eval.
_CONFIDENCE_CAP = 0.99


def _base_family(tag: str) -> str:
    """Strip dynamic suffix from a v1 rule tag: R2_disqualifier[flag] → R2_disqualifier."""
    m = _TAG_STRIP.match(tag)
    base = m.group(1) if m else tag
    # R_A1_non_dip_waived_TO_REVIEW → R_A1_non_dip_waived
    if base.endswith("_TO_REVIEW"):
        base = base[: -len("_TO_REVIEW")]
    return base


def _load_features() -> list[dict]:
    if not FEATURES.exists():
        raise SystemExit(
            f"Not found: {FEATURES}\n"
            f"Run: python3 v3/dev/analysis/extract_features.py"
        )
    return [
        json.loads(l) for l in FEATURES.read_text().splitlines()
        if l.strip() and "error" not in l
    ]


def _measure(rows: list[dict]) -> dict[str, dict]:
    """Return {family: {fires, kept, correct, accuracy, wrong_count}}."""
    grouped = defaultdict(list)
    for r in rows:
        grouped[_base_family(r["v1_rule_tag"])].append(r)

    out: dict[str, dict] = {}
    for family, cases in grouped.items():
        expected = _FAMILY_TO_ADJ.get(family)
        # R_ADJUDICATOR_FINDING outputs whatever verdict was found — for
        # calibration purposes, "kept" means v3_adjudication matches the
        # bracketed verdict in the tag. Simplification: treat all as kept
        # since L7 finding-trust bypass (post-2026-07-31) keeps them all.
        if family == "R_ADJUDICATOR_FINDING":
            kept = cases
        elif expected is None:
            kept = []
        else:
            kept = [r for r in cases if r["v3_adjudication"] == expected]
        correct = sum(1 for r in kept if r["v3_adjudication"] == r["truth_adjudication"])
        acc = (correct / len(kept)) if kept else 0.0
        wrong = len(kept) - correct
        out[family] = {
            "fires": len(cases),
            "kept": len(kept),
            "correct": correct,
            "wrong_count": wrong,
            "accuracy": acc,
        }
    return out


def _empirical_confidence(acc: float) -> float:
    """Round to 2 decimals, cap at _CONFIDENCE_CAP."""
    return min(_CONFIDENCE_CAP, round(acc, 2))


def _print_overview(stats: dict[str, dict]) -> None:
    print(f"{'family':<38s}  {'fires':>6s}  {'kept':>6s}  {'accuracy':>10s}  "
          f"{'now':>5s}  {'emp':>5s}  {'Δconf'}")
    print("-" * 100)
    for family in sorted(CURRENT):
        s = stats.get(family)
        if not s:
            print(f"  {family:<36s}  {'-':>6s}  {'-':>6s}  {'-':>10s}  "
                  f"{CURRENT[family]:.2f}  {'-':>5s}  (no training data)")
            continue
        emp = _empirical_confidence(s["accuracy"])
        delta = emp - CURRENT[family]
        marker = " ★" if abs(delta) > 0.10 else ""
        print(f"  {family:<36s}  {s['fires']:>6d}  {s['kept']:>6d}  "
              f"{100*s['accuracy']:>8.1f}%  {CURRENT[family]:.2f}  "
              f"{emp:.2f}  {delta:+.2f}{marker}")


def _print_gap(stats: dict[str, dict]) -> None:
    """Rank rules by wrong-prediction count — biggest structural opportunities."""
    rows = [
        (family, s["fires"], s["accuracy"], s["wrong_count"])
        for family, s in stats.items()
        if s["kept"] > 0
    ]
    rows.sort(key=lambda t: -t[3])
    print(f"{'rank':>4s}  {'family':<38s}  {'fires':>6s}  {'accuracy':>10s}  "
          f"{'wrong':>6s}  {'note'}")
    print("-" * 90)
    for i, (family, fires, acc, wrong) in enumerate(rows, 1):
        note = ""
        if acc < 0.50 and wrong >= 20:
            note = "← rule needs work OR data is Cohort B / invisible"
        elif acc < 0.75 and wrong >= 20:
            note = "← candidate for sub-classification"
        print(f"  {i:>2d}.  {family:<36s}  {fires:>6d}  {100*acc:>8.1f}%  "
              f"{wrong:>6d}  {note}")


def _emit_snippet(stats: dict[str, dict]) -> None:
    print("# EMPIRICALLY CALIBRATED — generated by calibrate_confidence.py")
    print("# Paste into v1/solution.py to update CONFIDENCE table.")
    print("CONFIDENCE = {")
    for family in sorted(CURRENT):
        s = stats.get(family)
        if not s or s["kept"] == 0:
            print(f'    {family!r:<38s}: {CURRENT[family]},  # unchanged (no training data)')
            continue
        emp = _empirical_confidence(s["accuracy"])
        old = CURRENT[family]
        old_new = f"{old:.2f} → {emp:.2f}" if abs(emp - old) > 0.01 else f"{old:.2f}"
        print(f'    {family!r:<38s}: {emp},'
              f'  # {s["kept"]} fires, {100*s["accuracy"]:.0f}% correct ({old_new})')
    print("}")


def _brier_estimate(rows: list[dict], stats: dict[str, dict]) -> None:
    """Estimate calibration score change if we apply the empirical values."""
    from collections import Counter
    tag_counts = Counter(_base_family(r["v1_rule_tag"]) for r in rows)
    tag_correct = defaultdict(int)
    for r in rows:
        if r["v3_adjudication"] == r["truth_adjudication"]:
            tag_correct[_base_family(r["v1_rule_tag"])] += 1

    total_old = 0.0
    total_new = 0.0
    for family, n in tag_counts.items():
        if family not in CURRENT:
            continue
        c_old = CURRENT[family]
        s = stats.get(family, {"accuracy": 0.0})
        c_new = _empirical_confidence(s["accuracy"])
        correct = tag_correct[family]
        wrong = n - correct
        # Sum of Brier over these cases
        total_old += correct * (c_old - 1) ** 2 + wrong * (c_old - 0) ** 2
        total_new += correct * (c_new - 1) ** 2 + wrong * (c_new - 0) ** 2

    n_total = len(rows)
    mean_brier_old = total_old / n_total
    mean_brier_new = total_new / n_total
    print(f"Cases scored:              {n_total}")
    print(f"Current mean Brier:        {mean_brier_old:.4f}")
    print(f"Empirical mean Brier:      {mean_brier_new:.4f}")
    print(f"Δmean Brier:               {mean_brier_new - mean_brier_old:+.4f}")
    old_cal = 20 * max(0, 1 - 2 * mean_brier_old)
    new_cal = 20 * max(0, 1 - 2 * mean_brier_new)
    print(f"Current calibration (est): {old_cal:.2f} / 20")
    print(f"Empirical calibration:     {new_cal:.2f} / 20")
    print(f"ΔCalibration score:        {new_cal - old_cal:+.2f}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--emit", action="store_true",
                        help="Print the CONFIDENCE dict as a Python snippet")
    parser.add_argument("--gap", action="store_true",
                        help="Rank rules by wrong-prediction count (structural opportunities)")
    parser.add_argument("--brier", action="store_true",
                        help="Estimate the calibration score delta")
    args = parser.parse_args()

    rows = _load_features()
    stats = _measure(rows)

    if args.emit:
        _emit_snippet(stats)
    elif args.gap:
        _print_gap(stats)
    elif args.brier:
        _brier_estimate(rows, stats)
    else:
        _print_overview(stats)


if __name__ == "__main__":
    main()
