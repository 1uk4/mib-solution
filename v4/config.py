#!/usr/bin/env python3
"""Feature configuration — the ONLY place v4 behavior flags live.

DOES: define every feature flag with its measured justification; read the
single surviving env var (MIB_OCR_CACHE_DIR) in from_env(); expose one
import-time singleton (CONFIG).

DOES NOT: gate anything itself — consumers receive a Config (parameter
defaulting to CONFIG) and decide. Nothing else in v4 reads os.environ.

History: v3 had 11 env vars scattered across 3 files. Ten were retired to
literal defaults because no production path ever set them — the Dockerfile
has no ENV lines, run.sh exports nothing, and the eval harness passes no
-e flags — so every flag always took its compiled-in default in every
submission run. The two default-off features keep their code paths in
v4/policy/ as insurance (flip the field and rebuild to reactivate). See
docs/superpowers/specs/2026-08-03-v4-standalone-rewrite-design.md §4.1.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    # --- L2 OCR pipeline ---
    # Triple-pass OCR (baseline + upscaled + sharpened). Flipped default-on
    # 2026-07-31 after a sweep confirmed +10.27 pts on 1000 training PDFs.
    ocr_sharpen: bool = True
    # Tesseract --user-words dictionary (v4/data/tesseract_user_words.txt).
    # Measured OCR quality gain. Participates in the OCR cache key: when on,
    # cache tags carry the "uw" suffix ("_uw" / "_dual_uw" / "_triple_uw").
    user_words: bool = True

    # --- L4 signal emission ---
    # Post-extraction value normalization (sponsor digit-lookalike repair,
    # date snapping, enum snapping). Part of the default-on OCR value
    # quality bundle (commit 6ce73eb).
    normalize_values: bool = True
    # Char-whitelist re-OCR for structured field format failures.
    reocr_char_whitelist: bool = True
    # Fuzzy-label recovery for fee_status — OFF pending measurement (B2-1).
    # fee_status was the one labeled field WITHOUT fuzzy recovery (the
    # docstring's "dedicated block" never existed); 163 fallback cases are
    # fee-only-missing. Enum-snapped to paid/waived/unpaid like visa_class.
    fee_fuzzy_recovery: bool = False
    # --- OCR pool-breadth variants (B2-12) — each OFF pending its own
    # measurement. Compositional pool members: appended after the
    # triple/dual/single strategy output, each cached under its own tag so
    # enabling one never invalidates the existing warm caches.
    # psm 11 = sparse-text mode: hunts isolated words (stamps, annotations,
    # note fragments) that block-segmentation merges or drops. All images.
    # ACCEPTED 2026-08-03: +0.371 train / +0.898 val (val outgained train),
    # cat FAs flat, 107 fields fixed vs 14 broken, ~+0.3 s/pdf.
    ocr_pool_psm11: bool = True
    # 90/180/270 lossless rotations, gated to pages whose pooled text is
    # near-empty — targets fully-rotated embedded content.
    ocr_pool_rotations: bool = False
    # Projection-profile deskew (PIL-only): estimate small tilt from the
    # row-ink profile, one corrective rotate + OCR when |angle| >= 1 deg.
    # Gated to degraded (low-yield) pages.
    ocr_pool_deskew: bool = False

    # --- L7 policy ---
    # Adjudicator-finding trust bypass. Evidence precedence #1 per the Field
    # Manual; 162 training cases, 100% correct. Guards must not second-guess
    # a signed manual note.
    trust_finding: bool = True
    # Waived-fee non-DIP promotion on a cleanly-read biometric slip.
    # Measured: 6/22 truth=APPROVED, 0/6 truth=DENIED, 2/15 truth=REVIEW —
    # zero cat-FA risk, small R->A cost.
    upgrade_waived_on_biometric: bool = True
    # R3_unpaid biometric waiver upgrade — DEFAULT OFF (2026-08-03).
    # Measured net -0.22 pts, cat FAs +10: biometric slips appear on many
    # R3_unpaid packets as boilerplate, so the rule wrongly approves
    # D-truth unpaid cases. Insurance path; re-enable only with a stricter
    # waiver-evidence signal.
    upgrade_unpaid_on_waiver: bool = False
    # FALLBACK_extraction_fail OCR upgrade. Measured on 290 fallback
    # packets: 20% fire rate, +14 D->D wins, 0 cat FAs introduced.
    fallback_ocr_upgrade: bool = True
    # OCR-only field guard — ON (restored 2026-08-03). Disabling it won
    # 16 A->A but cost +10 cat FAs across MULTIPLE approve paths
    # (net -0.22 pts).
    ocr_only_guard: bool = True
    # Defensive downgrade of thin-evidence auto-approvals — DEFAULT OFF
    # (2026-07-31). Catches all 15 training cat FAs but downgrades 76
    # correct approvals: net -2.45 pts. Insurance in case the private eval
    # weights cat FAs as a hard constraint (binding at >~0.15 pt/case).
    defensive_downgrade: bool = False

    # --- Dev infrastructure (not a behavior flag) ---
    # OCR result cache directory. Set by dev_score.sh on the native path;
    # unset (None) in production, where caching is a no-op. Caching never
    # changes output: same image bytes -> same OCR text.
    ocr_cache_dir: str | None = None

    @classmethod
    def from_env(cls) -> "Config":
        """Read the one surviving env var. Behavior flags are literals."""
        cache = os.environ.get("MIB_OCR_CACHE_DIR", "").strip() or None
        return cls(ocr_cache_dir=cache)


CONFIG = Config.from_env()
