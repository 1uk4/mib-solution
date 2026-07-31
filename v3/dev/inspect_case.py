#!/usr/bin/env python3
"""Packet inspector — dev tool for pattern discovery.

Runs the V3 layered pipeline on one training packet, dumps everything to
a scratch directory, prints a human-readable summary, and lists image
paths for further inspection (Claude Code can Read them; humans can
`open` them).

Usage:
    python3 v3/dev/inspect_case.py <case_id>
    python3 v3/dev/inspect_case.py <case_id> [<challenge_dir>]

(Named `inspect_case.py` rather than `inspect.py` to avoid shadowing the
Python stdlib `inspect` module, which dataclasses etc. import.)

Output goes to /tmp/mib-inspect/<case_id>/ — cleared on each run.
Requires pdftoppm (poppler) for page rendering; skipped if not installed.
"""
from __future__ import annotations

import csv
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Repo import path — this file is at v3/dev/inspect.py so parents[2] is repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ.setdefault("MIB_OCR_CACHE_DIR", os.path.expanduser("~/.cache/mib-ocr"))

from v3.acquire import acquire_sources, TEXT_STREAM, IMAGE  # noqa: E402
from v3.extract import extract_content  # noqa: E402
from v3.filters import apply_filters  # noqa: E402
from v3.signals import extract_signals, FIELD_VALUE, ADJUDICATOR_FINDING  # noqa: E402
from v3.consolidate import consolidate  # noqa: E402
from v3.rules import apply_rules  # noqa: E402
from v3.policy import apply_policy  # noqa: E402


DEFAULT_CHALLENGE = Path.home() / "Code" / "mib-doc-challenge"
INSPECT_ROOT = Path("/tmp/mib-inspect")


def _load_truth(challenge_dir: Path, case_id: str) -> dict | None:
    labels = challenge_dir / "data" / "train_labels.csv"
    if not labels.exists():
        return None
    with labels.open() as f:
        for row in csv.DictReader(f):
            if row["case_id"] == case_id:
                return row
    return None


def _render_pages(pdf_path: Path, out_dir: Path) -> list[Path]:
    """Render each PDF page to a PNG at 200 DPI."""
    if not shutil.which("pdftoppm"):
        return []
    prefix = out_dir / "rendered-page"
    subprocess.run(
        ["pdftoppm", "-r", "200", str(pdf_path), str(prefix), "-png"],
        capture_output=True, timeout=30,
    )
    return sorted(out_dir.glob("rendered-page-*.png"))


def _save_images(sources, out_dir: Path) -> list[Path]:
    """Save each IMAGE source's raw bytes to /tmp for viewing."""
    paths: list[Path] = []
    for src in sources:
        if src.type != IMAGE:
            continue
        p = out_dir / f"{src.id}.png"
        p.write_bytes(src.raw)
        paths.append(p)
    return paths


def _print_truth(truth: dict | None) -> None:
    if truth:
        print(f"TRUTH  adjudication={truth['adjudication']}  "
              f"risk_flags={truth['risk_flags']!r}")
        print(f"  applicant={truth['applicant_name']!r}  "
              f"visa={truth['visa_class']}  fee={truth['fee_status']}  "
              f"home={truth['home_world']}  sponsor={truth['sponsor_id']}")
    else:
        print("TRUTH  (unavailable — case not in train_labels.csv)")
    print()


def _print_sources(sources) -> None:
    text_sources = [s for s in sources if s.type == TEXT_STREAM]
    image_sources = [s for s in sources if s.type == IMAGE]
    print(f"SOURCES  {len(text_sources)} text_stream, {len(image_sources)} image")
    for s in sources:
        trust = "✓" if s.trusted else "✗"
        reason = f"  ← {s.exclusion_reason}" if s.exclusion_reason else ""
        if s.type == IMAGE:
            m = s.metadata
            info = f"{m.get('width', '?')}x{m.get('height', '?')} " \
                   f"{m.get('mode', '?')} " \
                   f"bright={m.get('mean_brightness', '?')} " \
                   f"std={m.get('brightness_std', '?')}"
        else:
            info = f"filters={[f.decode() for f in s.metadata.get('filters', [])]}"
        preview = " ".join((s.content or "").split())[:60]
        print(f"  [{trust}] {s.id:20s} {info}{reason}")
        if preview:
            print(f"      content: {preview!r}")
    print()


def _print_pipeline(bundle: dict, fields: dict,
                    l6: tuple[str, float, str], l7: tuple[str, float, str]) -> None:
    print(f"L6 RULE     {l6[0]:14s} conf={l6[1]:.2f}  tag={l6[2]}")
    print(f"L7 FINAL    {l7[0]:14s} conf={l7[1]:.2f}  tag={l7[2]}")
    print()

    print("CONSOLIDATED FIELDS")
    for k in ("applicant_name", "species_code", "home_world", "visa_class",
              "sponsor_id", "arrival_date", "declared_purpose",
              "risk_flags", "fee_status"):
        v = fields.get(k, "")
        src = fields.get("_source_class", {}).get(k, "?")
        agr = fields.get("_agreement", {}).get(k)
        agr_str = ""
        if agr:
            conflict = " ⚠ CONFLICT" if agr["has_conflict"] else ""
            agr_str = f"  n={agr['n_sources']} agree={agr['n_agreeing']}{conflict}"
        print(f"  {k:20s} {v!r:35s} src={src}{agr_str}")
    finding = fields.get("_finding", "")
    if finding:
        print(f"  _finding             {finding!r}")
    print()

    signals = bundle["signals"]
    print(f"SIGNALS  ({len(signals)} emitted)")
    from collections import defaultdict
    by_key = defaultdict(list)
    for s in signals:
        by_key[(s.type, s.key)].append(s)
    for (typ, key), ss in sorted(by_key.items()):
        if len(ss) > 1:
            print(f"  {typ}/{key or '(-)'}:")
            for s in ss:
                print(f"      {s.source_id:20s} conf={s.confidence}  value={s.value!r}")
        else:
            s = ss[0]
            print(f"  {typ}/{key or '(-)'}: {s.value!r}  ({s.source_id}, conf={s.confidence})")
    print()


def _print_paths(case_id: str, page_paths: list[Path],
                 image_paths: list[Path], text_dump: Path) -> None:
    print(f"ARTIFACTS in {INSPECT_ROOT / case_id}/")
    if page_paths:
        print(f"  Rendered pages ({len(page_paths)}):")
        for p in page_paths:
            print(f"    {p}")
    else:
        print("  Rendered pages: (pdftoppm not installed — skipping)")
    if image_paths:
        print(f"  Embedded images ({len(image_paths)}):")
        for p in image_paths:
            print(f"    {p}")
    print(f"  Text dump: {text_dump}")
    print()


def inspect(case_id: str, challenge_dir: Path) -> None:
    pdf_path = challenge_dir / "data" / "train" / f"{case_id}.pdf"
    if not pdf_path.exists():
        raise SystemExit(f"PDF not found: {pdf_path}")

    out_dir = INSPECT_ROOT / case_id
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    truth = _load_truth(challenge_dir, case_id)

    # Run the layered pipeline, keeping intermediate state
    sources = acquire_sources(pdf_path)
    extract_content(sources)
    apply_filters(sources)
    bundle = extract_signals(sources)
    fields = consolidate(bundle, case_id)
    l6 = apply_rules(fields)
    l7 = apply_policy(fields, *l6, bundle)

    # Dump text stream + save images + render pages for eyeballing
    text_dump = out_dir / "text_streams.txt"
    text_dump.write_text("\n\n---SOURCE BOUNDARY---\n\n".join(
        f"[{s.id}] trusted={s.trusted}\n{s.content}"
        for s in sources
    ))
    image_paths = _save_images(sources, out_dir)
    page_paths = _render_pages(pdf_path, out_dir)

    # Print the summary
    print(f"\n{'='*70}\n  {case_id}\n{'='*70}\n")
    _print_truth(truth)
    _print_sources(sources)
    _print_pipeline(bundle, fields, l6, l7)
    _print_paths(case_id, page_paths, image_paths, text_dump)

    # Open the PDF for the human's eyes
    opener = shutil.which("open") or shutil.which("xdg-open")
    if opener:
        subprocess.Popen([opener, str(pdf_path)],
                         stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("usage: inspect.py <case_id> [<challenge_dir>]")
    case_id = sys.argv[1]
    challenge_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_CHALLENGE
    inspect(case_id, challenge_dir)
