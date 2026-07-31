#!/usr/bin/env python3
"""Dev tool: extract per-packet features to features.jsonl.

Runs the V3 pipeline on every training PDF and captures ~30 features per
packet — pipeline decisions, source counts, filter hits, per-field source
provenance, OCR keyword presence, and truth labels. The output is consumed
by dev_correlate.py to find features that predict truth adjudication.

Usage:
    python3 v3/dev_extract_features.py
    # → writes /tmp/mib-features.jsonl (one JSON object per packet)

Uses the OCR cache (~/.cache/mib-ocr by default) so re-runs are fast.
"""
from __future__ import annotations

import csv
import json
import os
import sys
import time
from pathlib import Path

# Ensure we can import v1/v2/v3 modules (this file is at v3/dev/analysis/,
# so mib-solution/ is parents[3])
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
os.environ.setdefault("MIB_OCR_CACHE_DIR", os.path.expanduser("~/.cache/mib-ocr"))

from v3.acquire import acquire_sources, TEXT_STREAM, IMAGE  # noqa: E402
from v3.extract import extract_content  # noqa: E402
from v3.filters import apply_filters  # noqa: E402
from v3.signals import (  # noqa: E402
    extract_signals, FIELD_VALUE, ADJUDICATOR_FINDING,
)
from v3.consolidate import consolidate  # noqa: E402
from v3.rules import apply_rules  # noqa: E402
from v3.policy import apply_policy  # noqa: E402
from v1.solution import DISQUALIFYING_FLAGS, REVIEW_ONLY_FLAGS  # noqa: E402


CHALLENGE = Path.home() / "Code" / "mib-doc-challenge"
FEATURES_OUT = Path("/tmp/mib-features.jsonl")

# OCR keyword probes — cheap boolean features indicating presence in combined OCR.
_OCR_PROBE_WORDS = (
    "biohazard", "tampering", "warrant", "embargo", "illegible",
    "identity", "sponsor mismatch", "rescinded", "denied", "review",
    "revoked", "finding", "denial", "manual", "adjudicator",
)


def _extract_one(pdf_path: Path, truth: dict) -> dict:
    """Run pipeline; return a flat feature dict for this packet."""
    sources = acquire_sources(pdf_path)
    extract_content(sources)
    apply_filters(sources)
    bundle = extract_signals(sources)
    fields = consolidate(bundle, pdf_path.stem)
    adj0, conf0, tag0 = apply_rules(fields)
    adj, conf, tag = apply_policy(fields, adj0, conf0, tag0, bundle)

    # Filter-hit counts
    n_trusted_text = sum(1 for s in sources if s.trusted and s.type == TEXT_STREAM)
    n_trusted_image = sum(1 for s in sources if s.trusted and s.type == IMAGE)
    n_untrusted = sum(1 for s in sources if not s.trusted)
    excl_reasons = [s.exclusion_reason for s in sources if not s.trusted]

    # Signal counts
    signals = bundle["signals"]
    n_field_signals = sum(1 for s in signals if s.type == FIELD_VALUE)
    n_finding_signals = sum(1 for s in signals if s.type == ADJUDICATOR_FINDING)

    # OCR content — combined + probes
    combined_ocr = " ".join(text for _sid, text in bundle["image_ocr"])
    combined_ocr_lower = combined_ocr.lower()
    ocr_probes = {
        f"ocr_has_{w.replace(' ', '_')}": (w in combined_ocr_lower)
        for w in _OCR_PROBE_WORDS
    }

    # Field source-class per field
    src_class = fields.get("_source_class", {})
    field_source = {
        f"src_{k}": src_class.get(k, "absent")
        for k in ("fee_status", "visa_class", "sponsor_id",
                  "home_world", "risk_flags", "applicant_name",
                  "arrival_date", "declared_purpose")
    }

    # Agreement metrics per field (from D-3 L5 consolidation)
    agreement = fields.get("_agreement", {})
    agreement_features: dict = {}
    any_conflict = False
    for k in ("fee_status", "visa_class", "sponsor_id",
              "home_world", "risk_flags", "applicant_name",
              "arrival_date", "declared_purpose"):
        a = agreement.get(k)
        if a is None:
            agreement_features[f"agree_{k}_n"] = 0
            agreement_features[f"agree_{k}_conflict"] = False
        else:
            agreement_features[f"agree_{k}_n"] = a["n_sources"]
            agreement_features[f"agree_{k}_conflict"] = a["has_conflict"]
            if a["has_conflict"]:
                any_conflict = True
    agreement_features["any_field_conflict"] = any_conflict

    # Truth details
    t_flags_set = set(f.strip() for f in truth["risk_flags"].split("|") if f.strip())
    truth_features = {
        "truth_adjudication": truth["adjudication"],
        "truth_flags": truth["risk_flags"],
        "truth_fee_status": truth["fee_status"],
        "truth_visa_class": truth["visa_class"],
        "truth_species": truth["species_code"],
    }
    for f in DISQUALIFYING_FLAGS | REVIEW_ONLY_FLAGS:
        truth_features[f"truth_has_{f}"] = (f in t_flags_set)

    row = {
        "case_id": pdf_path.stem,
        # Pipeline decisions
        "v1_rule_tag": tag0,
        "v1_confidence": conf0,
        "v3_adjudication": adj,
        "v3_confidence": conf,
        "prediction_correct": (adj == truth["adjudication"]),
        # Source counts
        "n_sources": len(sources),
        "n_trusted_text": n_trusted_text,
        "n_trusted_image": n_trusted_image,
        "n_untrusted": n_untrusted,
        "n_field_signals": n_field_signals,
        "n_finding_signals": n_finding_signals,
        "ocr_total_chars": len(combined_ocr),
        # Exclusion reasons — categorical bucketed
        "any_injection_sanitized": any("Injection" in r for r in excl_reasons),
        "any_redaction_sanitized": any("Redaction" in r for r in excl_reasons),
        "any_illegibility_excluded": any("Illegibility" in r for r in excl_reasons),
        **ocr_probes,
        **field_source,
        **agreement_features,
        **truth_features,
    }
    return row


def main() -> None:
    train_dir = CHALLENGE / "data" / "train"
    labels_csv = CHALLENGE / "data" / "train_labels.csv"

    truth = {r["case_id"]: r for r in csv.DictReader(labels_csv.open())}
    pdfs = sorted(train_dir.glob("*.pdf"))
    total = len(pdfs)
    print(f"Extracting features for {total} training PDFs → {FEATURES_OUT}",
          file=sys.stderr)

    start = time.time()
    with FEATURES_OUT.open("w") as f:
        for i, pdf in enumerate(pdfs, 1):
            cid = pdf.stem
            if cid not in truth:
                continue
            try:
                row = _extract_one(pdf, truth[cid])
            except Exception as e:
                row = {"case_id": cid, "error": repr(e)}
            f.write(json.dumps(row, sort_keys=True) + "\n")
            if i % 100 == 0 or i == total:
                elapsed = time.time() - start
                rate = i / elapsed if elapsed else 0
                eta = (total - i) / rate if rate else 0
                print(f"  {i}/{total}  ({rate:.1f}/s, eta {eta:.0f}s)",
                      file=sys.stderr)

    print(f"Wrote {total} rows to {FEATURES_OUT}", file=sys.stderr)


if __name__ == "__main__":
    main()
