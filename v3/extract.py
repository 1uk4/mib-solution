#!/usr/bin/env python3
"""L2 — Content extraction.

For each Source produced by L1 (acquire.py), populate `content` with the
decoded string form:
    TEXT_STREAM → PDF text-operator decode (cheap)
    IMAGE       → Tesseract OCR (expensive, cached, gated)

Two optimizations over the naive path:

1. Pre-OCR skip gate (`_should_ocr`) — skip images that cannot plausibly
   contain useful text (blank canvases, sub-100px icons). Only skips we're
   VERY confident about — misses on real text would be worse than the
   runtime cost of processing every image. Portrait-vs-stamp classification
   is deferred; portraits still get OCR'd until we have a reliable
   classifier.

2. OCR result cache — SHA-256 of image bytes → cached OCR text. When the
   env var `MIB_OCR_CACHE_DIR` is set, results are read/written there.
   Same image bytes → same OCR text, so caching is architecturally safe
   (never changes what would be produced). Zero-op when the env var is
   unset (production submission).

L2 does not judge content — a source that decodes to injection text or to
garbled OCR still has its .content populated. L3 (filters) is responsible
for deciding what to trust.
"""
from __future__ import annotations

import hashlib
import io
import os
import subprocess
from pathlib import Path

from v1.solution import _decode_stream, TEXT_OP_RE, _pdf_unescape
from v3.acquire import Source, TEXT_STREAM, IMAGE

try:
    from PIL import Image
except ImportError:
    Image = None


# ---------------------------------------------------------------------------
# OCR result cache
# ---------------------------------------------------------------------------

# Env var set by dev tooling. Unset in production → cache is a no-op.
_CACHE_DIR = os.environ.get("MIB_OCR_CACHE_DIR", "").strip() or None
_CACHE_CREATED = False


def _cache_path(image_bytes: bytes, tag: str = "") -> Path | None:
    """Cache path for an OCR result. `tag` distinguishes OCR configs
    (e.g. "_dual" for two-pass) so a change in OCR config doesn't collide
    with existing single-pass cache entries.
    """
    if _CACHE_DIR is None:
        return None
    h = hashlib.sha256(image_bytes).hexdigest()
    return Path(_CACHE_DIR) / f"{h}{tag}.txt"


def _cache_get(image_bytes: bytes, tag: str = "") -> str | None:
    p = _cache_path(image_bytes, tag)
    if p is None or not p.exists():
        return None
    try:
        return p.read_text(encoding="utf-8")
    except Exception:
        return None


def _cache_put(image_bytes: bytes, text: str, tag: str = "") -> None:
    global _CACHE_CREATED
    p = _cache_path(image_bytes, tag)
    if p is None:
        return
    try:
        if not _CACHE_CREATED:
            p.parent.mkdir(parents=True, exist_ok=True)
            _CACHE_CREATED = True
        p.write_text(text, encoding="utf-8")
    except Exception:
        pass  # best-effort — a cache miss on next run is fine


# ---------------------------------------------------------------------------
# Pre-OCR skip gate (Phase 2)
# ---------------------------------------------------------------------------

# Thresholds chosen to be safe: only skip when confidence of "no text" is
# very high. A truly blank canvas is BOTH uniform-mean AND uniform-variance.
# Text documents have high mean (white bg) but ALSO high variance (dark text
# pixels), so requiring BOTH conditions is necessary to avoid skipping real
# text like FORM B-13 biometric slips or adjudicator notes.
_MIN_DIMENSION = 100          # below this → icon/divider/watermark
_BLANK_MEAN_HIGH = 240        # above this AND low std → nearly-white canvas
_BLANK_MEAN_LOW = 20          # below this AND low std → nearly-black canvas
_BLANK_STD_MAX = 3            # low std → nearly-uniform pixels; sparse
                              # adjudicator notes have std ~5-15, so any
                              # threshold above ~3 risks skipping real content


def _should_ocr(source: Source) -> bool:
    """Return False for images obviously without text. Safe skips only."""
    if source.type != IMAGE:
        return True
    m = source.metadata
    w = m.get("width")
    h = m.get("height")
    if not (w and h):
        return True  # unknown metadata → be safe
    if max(w, h) < _MIN_DIMENSION:
        return False
    # Both mean AND std must be blank-like to skip.
    # Text documents have mean ~250 but std ~50+ (from text pixels).
    brightness = m.get("mean_brightness", 128)
    std_dev = m.get("brightness_std", 100)
    if std_dev < _BLANK_STD_MAX:
        if brightness > _BLANK_MEAN_HIGH or brightness < _BLANK_MEAN_LOW:
            return False
    return True


# ---------------------------------------------------------------------------
# Document-shape gate: which images get dual-pass OCR?
# ---------------------------------------------------------------------------

# Real rendered documents in this dataset are letter-size (~1224×1584) with
# bright backgrounds. Stamps / photos / decorative elements are smaller and
# darker. The dual-pass config (psm=3 + 2x upscale) is worth the extra ~1s
# only for actual documents — it doesn't help on non-text imagery and
# doubles OCR compute across the whole packet if fired indiscriminately.
_DOC_MIN_DIMENSION = 800
_DOC_MIN_BRIGHTNESS = 80    # below this → photo, not a bright document page


def _looks_like_document(source: Source) -> bool:
    """Return True if this image is likely a rendered document worth dual-pass OCR.

    Heuristic based on empirical measurement of the training set: L1-L5
    classified sources are uniformly large (>=800px in the long dimension)
    with bright backgrounds. Non-doc content (photos, stamps) is smaller
    or darker.

    Approved gate: needs both PIL available (for upscale) and letter-shaped
    bright dimensions.
    """
    if Image is None:
        return False
    m = source.metadata
    w = m.get("width", 0) or 0
    h = m.get("height", 0) or 0
    if max(w, h) < _DOC_MIN_DIMENSION:
        return False
    brightness = m.get("mean_brightness", 128)
    if brightness < _DOC_MIN_BRIGHTNESS:
        return False
    return True


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def extract_content(sources: list[Source]) -> list[Source]:
    """Populate .content on every source (in place). Returns the same list."""
    for src in sources:
        if src.type == TEXT_STREAM:
            src.content = _extract_text_stream(src)
        elif src.type == IMAGE and _should_ocr(src):
            if _looks_like_document(src):
                src.content = _ocr_image_dual(src.raw)
            else:
                src.content = _ocr_image(src.raw)
    return sources


def _extract_text_stream(src: Source) -> str:
    """Decode a PDF text stream through its filter chain and pull `(...) Tj`."""
    filters = src.metadata.get("filters", [])
    data = _decode_stream(list(filters), src.raw)
    if data is None:
        return ""
    pieces: list[str] = []
    for m in TEXT_OP_RE.finditer(data):
        try:
            pieces.append(_pdf_unescape(m.group(1)).decode("latin-1", "replace"))
        except Exception:
            continue
    return "\n".join(pieces)


def _tesseract(image_bytes: bytes, psm: int = 6) -> str:
    """Single Tesseract invocation. Returns text or empty on failure."""
    try:
        proc = subprocess.run(
            ["tesseract", "-", "-", "--psm", str(psm)],
            input=image_bytes,
            capture_output=True,
            timeout=15,
        )
        return proc.stdout.decode("utf-8", errors="replace")
    except Exception:
        return ""


def _upscale_png(image_bytes: bytes, scale: int) -> bytes | None:
    """Decode with PIL, upscale, re-encode as PNG. None on failure."""
    if Image is None:
        return None
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img.load()
        img = img.resize((img.width * scale, img.height * scale), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return None


def _ocr_image(image_bytes: bytes) -> str:
    """Baseline single-pass OCR (psm=6). Content-hash cached."""
    cached = _cache_get(image_bytes)
    if cached is not None:
        return cached
    result = _tesseract(image_bytes, psm=6)
    _cache_put(image_bytes, result)
    return result


def _ocr_image_dual(image_bytes: bytes) -> str:
    """Dual-pass OCR for images that look like rendered documents.

    Runs Tesseract twice with different configs and returns the union:
      1. psm=6 on the raw image (baseline — assumes uniform block of text;
         works well on forms already rendered at target DPI)
      2. psm=3 on a 2× upscaled copy (auto page segmentation; works well
         on multi-line labeled forms and small-font documents that
         benefit from higher effective DPI)

    Union rationale: neither config strictly dominates on a per-image
    basis, but each catches text the other misses. V1's field-extraction
    regexes see both variants and match whichever OCR read the labeled
    field correctly. Measured on 30 L2 intake forms: baseline extracted
    87 correct fields, union extracted 106 (+22%).

    Cached under a "_dual" suffix so single-pass cache entries for the
    same image stay valid for callers that use `_ocr_image` directly.
    """
    cached = _cache_get(image_bytes, tag="_dual")
    if cached is not None:
        return cached
    text_base = _tesseract(image_bytes, psm=6)
    upscaled = _upscale_png(image_bytes, scale=2)
    text_hires = _tesseract(upscaled, psm=3) if upscaled else ""
    result = text_base + "\n" + text_hires if text_hires else text_base
    _cache_put(image_bytes, result, tag="_dual")
    return result
