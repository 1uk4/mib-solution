#!/usr/bin/env python3
"""MIB Doc Challenge — Version 2: V1 + selective OCR safety guard.

Architecture (deliberately minimal — V3 can grow):
1. Run V1 pipeline as-is (text extraction + field extraction + rules)
2. If V1 says DENIED or NEEDS_REVIEW → return V1 (already safe)
3. If V1 says APPROVED → OCR the packet's embedded JPEGs before confirming
     - Extract JPEG streams via V1's PDF parser
     - Run Tesseract on each (offline, ~50 MB dep)
     - Scan OCR text for any risk signal from V1's own vocabulary
     - If any hit → downgrade to NEEDS_REVIEW (safer than DENY; OCR can misread)
     - Else → keep V1's APPROVE

Non-goals for V2 (deferred to V3+):
- Page rendering (uses only embedded JPEGs — no poppler/pypdfium2)
- Image preprocessing (no OpenCV, no deskewing, no contrast enhancement)
- OCR-driven extraction score wins on missing fields
- New adjudication rules — V2 reuses V1's rules entirely
- Cross-referencing OCR against text-layer for the adversarial-label pattern

The safety principle inherited from V1 holds: never approve without positive
evidence. OCR is an *additional* source of negative evidence for the APPROVE
path only.
"""
from __future__ import annotations

import base64
import io
import json
import re
import subprocess
import sys
import zlib
from pathlib import Path

from v1.solution import (
    STREAM_RE,
    DISQUALIFYING_FLAGS,
    REVIEW_ONLY_FLAGS,
    REVOKED_SPONSORS,
    EMBARGOED_HOMES_HARD,
    CONFIDENCE,
    _default_field,
    predict_case as v1_predict_case,
)

VERSION = "v2_ocr_selective"

# --- OCR signal taxonomy (see v2/EDGE_CASES.md for rationale) ---
# Signals are ranked strong → weak. Stronger signals earn a route to DENIED
# with high confidence; weaker signals route to NEEDS_REVIEW.
#
# Adversarial safety: none of the DENY-strength signals can be planted by an
# adversary trying to get approval — an attacker wants the packet APPROVED,
# so they wouldn't plant "Finding: DENIED" or an exact flag label.

# STRONG signals (→ DENIED)
# Adjudicator's own denial finding in an OCR-readable image. V1 proved this
# pattern 100% accurate on 162 text-stream cases; OCR extends it to image-
# rendered adjudicator notes.
OCR_FINDING_DENIED_RE = re.compile(
    r"Finding\s*[:.]?\s*(DENIED|DENIE|REJECT)",
    re.IGNORECASE,
)
# Underscored disqualifying flag labels. These are the internal enum values —
# "biohazard_red" is not a phrase a human writes in narrative; it's the exact
# machine label. If OCR reads it, some form/stamp literally rendered it.
EXACT_DISQ_FLAGS = tuple(f.lower() for f in DISQUALIFYING_FLAGS)

# MEDIUM signals (→ NEEDS_REVIEW with high confidence)
OCR_FINDING_REVIEW_RE = re.compile(
    r"Finding\s*[:.]?\s*NEEDS[_\s]?REVIEW",
    re.IGNORECASE,
)
EXACT_REVIEW_FLAGS = tuple(f.lower() for f in REVIEW_ONLY_FLAGS)

# WEAK signals (→ NEEDS_REVIEW with lower confidence) — stem matches
# tolerate Tesseract truncation on stylized text. All stems below are
# essentially never seen outside the risk context; benign generic words
# like BIOMETRIC / MEMORY / WARRAN / TAMPER were pruned in iter 0.
STAMP_STEMS = ("DENIE", "REVOK", "RESCIN", "BIOHAZ", "EMBARG")

# JPEG magic — used to validate a DCT stream really contains JPEG data
JPEG_MAGIC = b"\xff\xd8\xff"

# PIL is only needed to reconstruct raw-pixel images into PNGs for Tesseract
try:
    from PIL import Image
except ImportError:  # pragma: no cover - defensive; Dockerfile installs pillow
    Image = None  # type: ignore


def _dict_int(dict_bytes: bytes, key: bytes) -> int | None:
    m = re.search(rb"/" + key + rb"\s+(\d+)", dict_bytes)
    return int(m.group(1)) if m else None


def _dict_name(dict_bytes: bytes, key: bytes) -> bytes | None:
    m = re.search(rb"/" + key + rb"\s+/(\w+)", dict_bytes)
    return m.group(1) if m else None


def extract_images(pdf_path: Path) -> list[bytes]:
    """Return each embedded image in the PDF as PNG/JPEG bytes for Tesseract.

    Two flavors of image stream appear in this dataset:
      1. DCTDecode → data is a JPEG (after ASCII85 unwrap)
      2. FlateDecode (no DCT) → data is raw pixels compressed with Flate

    Both are handled. Streams we can't recognize are silently skipped —
    an OCR-inaccessible image should not break the packet's V1 decision.
    """
    try:
        raw = pdf_path.read_bytes()
    except Exception:
        return []

    images: list[bytes] = []
    for m in STREAM_RE.finditer(raw):
        d = m.group("dict")
        if b"/Subtype /Image" not in d:
            continue

        data = m.group("data")
        if b"ASCII85Decode" in d:
            end = data.find(b"~>")
            if end >= 0:
                data = data[:end]
            try:
                data = base64.a85decode(data.strip(), adobe=False)
            except Exception:
                continue

        if b"DCTDecode" in d:
            if data.startswith(JPEG_MAGIC):
                images.append(data)
            continue

        if b"FlateDecode" in d and Image is not None:
            try:
                pixels = zlib.decompress(data)
            except Exception:
                continue
            width = _dict_int(d, b"Width")
            height = _dict_int(d, b"Height")
            if not (width and height):
                continue
            colorspace = _dict_name(d, b"ColorSpace") or b"DeviceRGB"
            if colorspace == b"DeviceRGB":
                mode, expected = "RGB", width * height * 3
            elif colorspace == b"DeviceGray":
                mode, expected = "L", width * height
            else:
                continue  # skip CMYK / indexed / etc. — rare here
            if len(pixels) != expected:
                continue
            try:
                img = Image.frombytes(mode, (width, height), pixels)
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                images.append(buf.getvalue())
            except Exception:
                continue
    return images


def ocr_image(image_bytes: bytes) -> str:
    """Run Tesseract on any raster format it recognizes, return text.

    Uses tesseract's stdin/stdout mode so we avoid temp files. Wrapped in
    a broad exception guard because OCR is a "nice to have" — any failure
    should not break the pipeline; V1's decision stands.
    """
    try:
        proc = subprocess.run(
            ["tesseract", "-", "-", "--psm", "6"],
            input=image_bytes,
            capture_output=True,
            timeout=15,
        )
        return proc.stdout.decode("utf-8", errors="replace")
    except Exception:
        return ""


def evaluate_ocr_signal(ocr_text: str) -> tuple[str, float, str] | None:
    """Grade OCR content into (adjudication, confidence, audit_tag) or None.

    Returns None when OCR reveals nothing risk-adjacent — caller keeps V1's
    APPROVE. Otherwise returns the strongest signal found, ordered from
    highest-severity to lowest.

    Routing rationale:
    - Adjudicator OCR "Finding: DENIED" → DENIED (V1 proved this pattern
      100% accurate on text streams; the OCR variant inherits that trust)
    - Exact underscored disqualifying flag → DENIED (label is machine-only
      text; if visible it means a stamp/form literally rendered it)
    - Revoked sponsor SPN → DENIED via V1's rule set (same as R4)
    - Hard-embargoed home → DENIED via V1's rule set (same as R0)
    - Adjudicator "Finding: NEEDS_REVIEW" → REVIEW (0.85)
    - Exact review-only flag → REVIEW (0.85)
    - Stem match (BIOHAZ etc.) → REVIEW (0.65) — tolerates OCR truncation
      but stems are broad enough to occasionally false-fire
    """
    if not ocr_text:
        return None

    # ---- Strong signals → DENIED ----
    if OCR_FINDING_DENIED_RE.search(ocr_text):
        return ("DENIED", 0.95, "ocr_finding:DENIED")

    lower = ocr_text.lower()
    for flag in EXACT_DISQ_FLAGS:
        if flag in lower:
            return ("DENIED", 0.90, f"ocr_exact_disq_flag:{flag}")

    for sp in REVOKED_SPONSORS:
        if sp in ocr_text:
            return ("DENIED", 0.90, f"ocr_revoked_sponsor:{sp}")

    for home in EMBARGOED_HOMES_HARD:
        if home.lower() in lower:
            return ("DENIED", 0.90, f"ocr_embargo_home:{home}")

    # ---- Medium signals → REVIEW ----
    if OCR_FINDING_REVIEW_RE.search(ocr_text):
        return ("NEEDS_REVIEW", 0.85, "ocr_finding:REVIEW")

    for flag in EXACT_REVIEW_FLAGS:
        if flag in lower:
            return ("NEEDS_REVIEW", 0.85, f"ocr_exact_review_flag:{flag}")

    # ---- Weak signals → REVIEW at lower confidence ----
    upper = ocr_text.upper()
    for stem in STAMP_STEMS:
        if stem in upper:
            return ("NEEDS_REVIEW", 0.65, f"ocr_stamp_stem:{stem}")

    return None


def predict_case(pdf_path: Path) -> dict:
    """V1 + OCR safety check on APPROVEs."""
    v1_pred = v1_predict_case(pdf_path)

    if v1_pred["adjudication"] != "APPROVED":
        return v1_pred  # already safe path — no OCR

    images = extract_images(pdf_path)
    if not images:
        return v1_pred  # nothing to inspect; trust V1

    ocr_text = "\n".join(ocr_image(img) for img in images)
    signal = evaluate_ocr_signal(ocr_text)
    if signal is None:
        return v1_pred  # OCR confirmed nothing risky

    adjudication, confidence, _tag = signal
    v1_pred["adjudication"] = adjudication
    v1_pred["confidence"] = confidence
    return v1_pred


def main(input_dir: str, output_path: str) -> None:
    pdfs = sorted(Path(input_dir).glob("*.pdf"))
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as f:
        for pdf in pdfs:
            try:
                pred = predict_case(pdf)
            except Exception:
                pred = {
                    "case_id": pdf.stem,
                    **{k: _default_field(k) for k in (
                        "applicant_name", "species_code", "home_world",
                        "visa_class", "sponsor_id", "arrival_date",
                        "declared_purpose", "fee_status",
                    )},
                    "risk_flags": "none",
                    "adjudication": "NEEDS_REVIEW",
                    "confidence": CONFIDENCE["FALLBACK_extraction_fail"],
                }
            f.write(json.dumps(pred, sort_keys=True) + "\n")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: v2/solution.py <input_pdf_dir> <output_path>")
    main(sys.argv[1], sys.argv[2])
