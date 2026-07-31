#!/usr/bin/env python3
"""Dev tool: OCR-signal precision + co-occurrence vs truth.

Foundation for RULE_AUDIT decisions. Empirical evidence for whether each
pattern in v3/ocr_signal.py is trustworthy at its current confidence,
and how patterns interact when multiple fire on the same packet.

Rule-writing methodology (this tool + RULE_AUDIT.md together):
    A rule needs BOTH empirical evidence AND a semantic reason to
    generalize to unseen packets:

      1. Evidence (this tool)  — the pattern's truth distribution
      2. Assumption  — WHY the pattern predicts the outcome
                       (e.g. "an adjudicator's stated Finding, if valid,
                        is the highest form of truth")

    A pattern with 100% precision on n=5 with no semantic reason to
    generalize is NOT a rule — it's a coincidence. A pattern with 80%
    precision on n=200 backed by a strong semantic reason IS a rule.

Runs the full pipeline through cached OCR. First run: ~2.5 min.
Subsequent runs: instant (per-packet fired-signals cached to /tmp).

Usage:
    python3 v3/dev/analysis/ocr_signal_audit.py
        # Marginal precision: when signal fires, what does truth say?
    python3 v3/dev/analysis/ocr_signal_audit.py --solo
        # Solo-fire precision: when ONLY this signal fires (cleanest measure)
    python3 v3/dev/analysis/ocr_signal_audit.py --pairs [--min N]
        # Co-occurrence: for each signal pair, truth breakdown when both fire.
        # Answers "when A and B disagree in their implication, which is right?"
    python3 v3/dev/analysis/ocr_signal_audit.py --interaction <A> <B>
        # List packets where A and B both fire, grouped by truth
    python3 v3/dev/analysis/ocr_signal_audit.py --rebuild
        # Force re-extraction (use after changing OCR pipeline)
"""
from __future__ import annotations

import csv
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
os.environ.setdefault("MIB_OCR_CACHE_DIR", os.path.expanduser("~/.cache/mib-ocr"))

from v1.solution import (  # noqa: E402
    DISQUALIFYING_FLAGS,
    REVIEW_ONLY_FLAGS,
    REVOKED_SPONSORS,
    EMBARGOED_HOMES_HARD,
)
from v3.acquire import acquire_sources, IMAGE  # noqa: E402
from v3.extract import extract_content  # noqa: E402
from v3.filters import apply_filters  # noqa: E402
from v3.signals import fuzzy_flag_pattern  # noqa: E402
from v3.ocr_signal import (  # noqa: E402
    OCR_FINDING_DENIED_RE,
    OCR_FINDING_REVIEW_RE,
    STAMP_DENY_STEMS,
    STAMP_REVIEW_STEMS,
)


CHALLENGE = Path.home() / "Code" / "mib-doc-challenge"
CACHE = Path("/tmp/mib-ocr-audit.jsonl")
TRUTH_BUCKETS = ("APPROVED", "NEEDS_REVIEW", "DENIED")


# ---------------------------------------------------------------------------
# Signal registry
# ---------------------------------------------------------------------------
# Names mirror ocr_signal.py's tag strings so audit rows line up with what
# gets emitted at production time. To add a new signal to the audit:
#   1. Add the matcher here.
#   2. Rebuild cache with --rebuild.
#   3. Read the marginal / solo / pairs tables.

def _flag_matcher(flag: str):
    pat = fuzzy_flag_pattern(flag)
    return lambda text: bool(pat.search(text))


def _substr_matcher(needle: str):
    up = needle.upper()
    return lambda text: up in text.upper()


def _re_matcher(pattern: re.Pattern):
    return lambda text: bool(pattern.search(text))


def _revoked_matcher(sponsor: str):
    return lambda text: sponsor in text


def _home_matcher(home: str):
    lo = home.lower()
    return lambda text: lo in text.lower()


def _build_signal_registry() -> list[tuple[str, callable]]:
    sigs: list[tuple[str, callable]] = [
        ("finding:DENIED", _re_matcher(OCR_FINDING_DENIED_RE)),
        ("finding:REVIEW", _re_matcher(OCR_FINDING_REVIEW_RE)),
    ]
    for flag in sorted(DISQUALIFYING_FLAGS):
        sigs.append((f"disq_flag:{flag}", _flag_matcher(flag)))
    for flag in sorted(REVIEW_ONLY_FLAGS):
        sigs.append((f"review_flag:{flag}", _flag_matcher(flag)))
    for sp in sorted(REVOKED_SPONSORS):
        sigs.append((f"revoked_sponsor:{sp}", _revoked_matcher(sp)))
    for home in sorted(EMBARGOED_HOMES_HARD):
        sigs.append((f"embargo_home:{home}", _home_matcher(home)))
    for stem in STAMP_DENY_STEMS:
        sigs.append((f"deny_stem:{stem}", _substr_matcher(stem)))
    for stem in STAMP_REVIEW_STEMS:
        sigs.append((f"review_stem:{stem}", _substr_matcher(stem)))
    return sigs


SIGNALS = _build_signal_registry()


# ---------------------------------------------------------------------------
# Extraction: which signals fire per packet
# ---------------------------------------------------------------------------


def _build_records(force_rebuild: bool = False) -> list[dict]:
    """Return list of {case_id, truth_adj, fired: [signal_names]}.

    Uses /tmp/mib-ocr-audit.jsonl cache. Re-extracts if missing or if
    --rebuild is passed. OCR itself is cached separately (MIB_OCR_CACHE_DIR),
    so re-extraction is fast when only signal patterns changed.
    """
    if CACHE.exists() and CACHE.stat().st_size > 0 and not force_rebuild:
        print(f"Loading cached audit from {CACHE}", file=sys.stderr)
        return [
            json.loads(l) for l in CACHE.read_text().splitlines()
            if l.strip() and '"fired"' in l
        ]

    truth = {r["case_id"]: r for r in
             csv.DictReader((CHALLENGE / "data" / "train_labels.csv").open())}
    pdfs = sorted((CHALLENGE / "data" / "train").glob("*.pdf"))
    print(f"Extracting OCR signals from {len(pdfs)} PDFs "
          f"(uses OCR cache — first run ~2.5min, later runs fast)",
          file=sys.stderr)

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
                # Concatenate trusted image OCR — matches production L7 input
                text = "\n".join(
                    s.content for s in srcs
                    if s.type == IMAGE and s.trusted
                )
                fired = [name for name, matcher in SIGNALS if matcher(text)]
                rec = {
                    "case_id": cid,
                    "truth_adj": truth[cid]["adjudication"],
                    "fired": fired,
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


# ---------------------------------------------------------------------------
# Analyses
# ---------------------------------------------------------------------------


def _truth_dist(rows: list[dict]) -> dict[str, int]:
    return Counter(r["truth_adj"] for r in rows)


def _fmt_pct(n: int, total: int) -> str:
    return f"{100 * n / total:>5.1f}%" if total else "  0.0%"


def _marginal(records: list[dict]) -> None:
    """Truth distribution when a signal fires — regardless of what else fires."""
    print("Marginal precision: when signal fires (any co-signals allowed)\n")
    print(f"  {'signal':<38s} {'n':>5s}  "
          f"{'APPROVED':>8s} {'REVIEW':>8s} {'DENIED':>8s}  {'majority':>18s}")
    for name, _ in SIGNALS:
        fires = [r for r in records if name in r["fired"]]
        n = len(fires)
        if n == 0:
            continue
        t = _truth_dist(fires)
        maj_bucket, maj_n = Counter(t).most_common(1)[0]
        print(f"  {name:<38s} {n:>5d}  "
              f"{_fmt_pct(t.get('APPROVED', 0), n):>8s} "
              f"{_fmt_pct(t.get('NEEDS_REVIEW', 0), n):>8s} "
              f"{_fmt_pct(t.get('DENIED', 0), n):>8s}  "
              f"{maj_bucket + ' ' + _fmt_pct(maj_n, n):>18s}")


def _solo(records: list[dict]) -> None:
    """Cleanest measure: only this signal fires — no other pattern hit."""
    print("Solo precision: only this signal fires (no other audit pattern hit)\n")
    print(f"  {'signal':<38s} {'n_solo':>7s}  "
          f"{'APPROVED':>8s} {'REVIEW':>8s} {'DENIED':>8s}  {'majority':>18s}")
    for name, _ in SIGNALS:
        solo = [r for r in records if r["fired"] == [name]]
        n = len(solo)
        if n == 0:
            continue
        t = _truth_dist(solo)
        maj_bucket, maj_n = Counter(t).most_common(1)[0]
        print(f"  {name:<38s} {n:>7d}  "
              f"{_fmt_pct(t.get('APPROVED', 0), n):>8s} "
              f"{_fmt_pct(t.get('NEEDS_REVIEW', 0), n):>8s} "
              f"{_fmt_pct(t.get('DENIED', 0), n):>8s}  "
              f"{maj_bucket + ' ' + _fmt_pct(maj_n, n):>18s}")


def _pairs(records: list[dict], min_n: int = 5) -> None:
    """For each pair that co-fires on >= min_n packets, show truth breakdown.

    Use this to answer "when signal A and signal B both fire (and imply
    different verdicts), which one does truth agree with?"
    """
    print(f"Pairs co-occurring on >= {min_n} packets\n")
    print(f"  {'signal_A':<32s} {'signal_B':<32s} {'n':>4s}  "
          f"{'A':>4s} {'R':>4s} {'D':>4s}  {'winner':>12s}")
    names = [n for n, _ in SIGNALS]
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            both = [r for r in records
                    if a in r["fired"] and b in r["fired"]]
            if len(both) < min_n:
                continue
            t = _truth_dist(both)
            maj_bucket, maj_n = Counter(t).most_common(1)[0]
            print(f"  {a:<32s} {b:<32s} {len(both):>4d}  "
                  f"{t.get('APPROVED', 0):>4d} "
                  f"{t.get('NEEDS_REVIEW', 0):>4d} "
                  f"{t.get('DENIED', 0):>4d}  "
                  f"{maj_bucket + ' ' + _fmt_pct(maj_n, len(both)):>12s}")


def _interaction(records: list[dict], a: str, b: str) -> None:
    """Enumerate packets where A and B both fire, grouped by truth."""
    both = [r for r in records if a in r["fired"] and b in r["fired"]]
    print(f"Signals {a!r} + {b!r} both fire on n={len(both)} packets")
    if not both:
        return
    by_truth = defaultdict(list)
    for r in both:
        by_truth[r["truth_adj"]].append(r["case_id"])
    for bucket in TRUTH_BUCKETS:
        cases = by_truth.get(bucket, [])
        print(f"\n{bucket}: {len(cases)}")
        for cid in cases[:20]:
            print(f"  {cid}")
        if len(cases) > 20:
            print(f"  ... {len(cases) - 20} more")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _usage() -> None:
    print(__doc__, file=sys.stderr)


def main() -> None:
    args = sys.argv[1:]
    force = "--rebuild" in args
    if force:
        args.remove("--rebuild")

    records = _build_records(force_rebuild=force)
    records = [r for r in records if "fired" in r]

    if not args:
        _marginal(records)
    elif args[0] == "--solo":
        _solo(records)
    elif args[0] == "--pairs":
        min_n = 5
        if "--min" in args:
            min_n = int(args[args.index("--min") + 1])
        _pairs(records, min_n=min_n)
    elif args[0] == "--interaction" and len(args) >= 3:
        _interaction(records, args[1], args[2])
    else:
        _usage()
        sys.exit(1)


if __name__ == "__main__":
    main()
