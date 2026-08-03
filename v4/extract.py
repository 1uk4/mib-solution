#!/usr/bin/env python3
"""L2 — Content extraction.

For each Source produced by L1 (acquire.py), populate `content` with the
decoded string form:
    TEXT_STREAM → PDF text-operator decode (cheap)
    IMAGE       → Tesseract OCR (expensive, cached, gated)

DOES: decode text streams; run (multi-pass) OCR with skip gates and a
content-hash cache; absorb the stdlib PDF stream decoders
(_decode_stream/_pdf_unescape, from v1:113-149).
DOES NOT: judge content — a source that decodes to injection text or to
garbled OCR still has its .content populated. L3 (filters) decides trust.

Two optimizations over the naive path:

1. Pre-OCR skip gate (`_should_ocr`) — skip images that cannot plausibly
   contain useful text (blank canvases, sub-100px icons). Only skips we're
   VERY confident about — misses on real text would be worse than the
   runtime cost of processing every image. Portrait-vs-stamp classification
   is deferred; portraits still get OCR'd until we have a reliable
   classifier.

2. OCR result cache — SHA-256 of image bytes → cached OCR text. When
   config.ocr_cache_dir is set (dev only; production leaves it None),
   results are read/written there. Same image bytes → same OCR text, so
   caching is architecturally safe (never changes what would be produced).

Cache-key contract (brief Rule 3): tags must remain byte-identical to
v3's — "_uw" (single-pass), "_dual_uw" (dual), "_triple_uw" (triple)
under default config — or the warm cache on disk is silently invalidated.
Pinned by v4/tests/test_user_words_wiring.py.
"""
from __future__ import annotations

import base64
import hashlib
import io
import re
import subprocess
import zlib
from pathlib import Path

from v4.acquire import Source, TEXT_STREAM, IMAGE
from v4.config import Config, CONFIG
from v4.patterns import TEXT_OP_RE

try:
    from PIL import Image, ImageFilter
except ImportError:
    Image = None
    ImageFilter = None


# ---------------------------------------------------------------------------
# PDF stream decoding (absorbed verbatim from v1/solution.py:113-149)
# ---------------------------------------------------------------------------


def _decode_stream(filters: list[bytes], data: bytes) -> bytes | None:
    """Apply a chain of PDF filters to a stream's data."""
    for f in filters:
        if f == b"ASCII85":
            end = data.find(b"~>")
            if end >= 0:
                data = data[:end]
            try:
                data = base64.a85decode(data.strip(), adobe=False)
            except Exception:
                return None
        elif f == b"Flate":
            try:
                data = zlib.decompress(data)
            except Exception:
                return None
        else:
            # Unknown filter (DCTDecode for JPEGs, CCITTFaxDecode, etc.) —
            # can't recover text from these with stdlib.
            return None
    return data


def _pdf_unescape(raw: bytes) -> bytes:
    """Undo PDF string escapes: \\n \\r \\t \\( \\) \\\\ and octal \\NNN."""
    def sub(m: re.Match) -> bytes:
        c = m.group(1)
        if c in (b"n",): return b"\n"
        if c in (b"r",): return b"\r"
        if c in (b"t",): return b"\t"
        if c in (b"b",): return b"\b"
        if c in (b"f",): return b"\f"
        if c in (b"(", b")", b"\\"): return c
        if c.isdigit():
            return bytes([int(c, 8) & 0xFF])
        return c
    return re.sub(rb"\\(\d{1,3}|.)", sub, raw)


# ---------------------------------------------------------------------------
# OCR result cache
# ---------------------------------------------------------------------------

# Per-directory mkdir memo. v3 used a single module-level _CACHE_CREATED
# bool (one cache dir per process); keyed by dir here so Config injection
# in tests can point different calls at different directories. Production
# behavior is identical — one dir, one mkdir.
_CACHE_DIRS_CREATED: set[str] = set()

_USER_WORDS_PATH = Path(__file__).parent / "data" / "tesseract_user_words.txt"


def _user_words_flags(config: Config = CONFIG) -> list[str]:
    """Return tesseract flags for the user-words dictionary, or [] if
    disabled or the file is missing."""
    if not config.user_words:
        return []
    if not _USER_WORDS_PATH.exists():
        return []
    return ["--user-words", str(_USER_WORDS_PATH)]


def _cache_config_tag(config: Config = CONFIG) -> str:
    """Suffix appended to cache tags so runs with different OCR config
    (user-words on/off, other future flags) do not collide.
    Order: alphabetical by flag short-name. Extend when new flags land."""
    parts = []
    if config.user_words:
        parts.append("uw")
    return ("_" + "_".join(parts)) if parts else ""


def _cache_path(image_bytes: bytes, tag: str = "",
                config: Config = CONFIG) -> Path | None:
    """Cache path for an OCR result. `tag` distinguishes OCR configs
    (e.g. "_dual" for two-pass) so a change in OCR config doesn't collide
    with existing single-pass cache entries.
    """
    if config.ocr_cache_dir is None:
        return None
    h = hashlib.sha256(image_bytes).hexdigest()
    return Path(config.ocr_cache_dir) / f"{h}{tag}.txt"


def _cache_get(image_bytes: bytes, tag: str = "",
               config: Config = CONFIG) -> str | None:
    p = _cache_path(image_bytes, tag, config)
    if p is None or not p.exists():
        return None
    try:
        return p.read_text(encoding="utf-8")
    except Exception:
        return None


def _cache_put(image_bytes: bytes, text: str, tag: str = "",
               config: Config = CONFIG) -> None:
    p = _cache_path(image_bytes, tag, config)
    if p is None:
        return
    try:
        parent = str(p.parent)
        if parent not in _CACHE_DIRS_CREATED:
            p.parent.mkdir(parents=True, exist_ok=True)
            _CACHE_DIRS_CREATED.add(parent)
        p.write_text(text, encoding="utf-8")
    except Exception:
        pass  # best-effort — a cache miss on next run is fine


# ---------------------------------------------------------------------------
# Pre-OCR skip gate
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


def extract_content(sources: list[Source], config: Config = CONFIG) -> list[Source]:
    """Populate .content on every source (in place). Returns the same list."""
    for src in sources:
        if src.type == TEXT_STREAM:
            src.content = _extract_text_stream(src)
        elif src.type == IMAGE and _should_ocr(src):
            if _looks_like_document(src):
                if config.ocr_sharpen:
                    src.content = _ocr_image_triple(src.raw, config)
                else:
                    src.content = _ocr_image_dual(src.raw, config)
            else:
                src.content = _ocr_image(src.raw, config)
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


def _tesseract(image_bytes: bytes, psm: int = 6,
               extra_flags: list[str] | None = None) -> str:
    """Single Tesseract invocation. Returns text or empty on failure.

    `extra_flags` — additional CLI flags to pass after the psm arg. Used by:
      - user-words dictionary: ['--user-words', '/path/to/words.txt']
      - char-whitelist re-OCR: ['-c', 'tessedit_char_whitelist=SPN-0123456789']
    """
    cmd = ["tesseract", "-", "-", "--psm", str(psm)]
    if extra_flags:
        cmd.extend(extra_flags)
    try:
        proc = subprocess.run(
            cmd,
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


def _sharpen_png(image_bytes: bytes) -> bytes | None:
    """Apply unsharp mask sharpen, re-encode as PNG. None on failure.

    Empirical basis: on MIB-000032, sharpen recovered 'Asinax Qommora' where
    baseline read 'Annax Qormora' — 1 character closer to truth 'Arinax'.
    Radius=2, percent=150 matches the parameters tested during design.
    """
    if Image is None:
        return None
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img.load()
        img = img.filter(ImageFilter.UnsharpMask(radius=2, percent=150))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return None


def _ocr_image(image_bytes: bytes, config: Config = CONFIG) -> str:
    """Baseline single-pass OCR (psm=6). Content-hash cached."""
    tag = _cache_config_tag(config)
    cached = _cache_get(image_bytes, tag=tag, config=config)
    if cached is not None:
        return cached
    result = _tesseract(image_bytes, psm=6, extra_flags=_user_words_flags(config))
    _cache_put(image_bytes, result, tag=tag, config=config)
    return result


def _ocr_image_dual(image_bytes: bytes, config: Config = CONFIG) -> str:
    """Dual-pass OCR for images that look like rendered documents.

    Runs Tesseract twice with different configs and returns the union:
      1. psm=6 on the raw image (baseline — assumes uniform block of text;
         works well on forms already rendered at target DPI)
      2. psm=3 on a 2× upscaled copy (auto page segmentation; works well
         on multi-line labeled forms and small-font documents that
         benefit from higher effective DPI)

    Union rationale: neither config strictly dominates on a per-image
    basis, but each catches text the other misses. The field-extraction
    regexes see both variants and match whichever OCR read the labeled
    field correctly. Measured on 30 L2 intake forms: baseline extracted
    87 correct fields, union extracted 106 (+22%).

    Cached under a "_dual" suffix so single-pass cache entries for the
    same image stay valid for callers that use `_ocr_image` directly.
    """
    tag = "_dual" + _cache_config_tag(config)
    cached = _cache_get(image_bytes, tag=tag, config=config)
    if cached is not None:
        return cached
    text_base = _tesseract(image_bytes, psm=6, extra_flags=_user_words_flags(config))
    upscaled = _upscale_png(image_bytes, scale=2)
    text_hires = _tesseract(upscaled, psm=3, extra_flags=_user_words_flags(config)) if upscaled else ""
    result = text_base + "\n" + text_hires if text_hires else text_base
    _cache_put(image_bytes, result, tag=tag, config=config)
    return result


def _ocr_image_triple(image_bytes: bytes, config: Config = CONFIG) -> str:
    """Triple-pass OCR: baseline + upscaled + sharpened.

    Extends _ocr_image_dual with a third pass on a sharpened variant.
    Sharpen recovers character-level misreads (Annax→Asinax class) by
    boosting edge contrast before Tesseract sees the image.

    Cached under a "_triple" suffix (plus config tag) so single-pass and
    dual-pass cache entries stay valid for their respective callers.
    """
    cache_tag = "_triple" + _cache_config_tag(config)
    cached = _cache_get(image_bytes, tag=cache_tag, config=config)
    if cached is not None:
        return cached
    text_base = _tesseract(image_bytes, psm=6, extra_flags=_user_words_flags(config))
    upscaled = _upscale_png(image_bytes, scale=2)
    text_hires = _tesseract(upscaled, psm=3, extra_flags=_user_words_flags(config)) if upscaled else ""
    sharpened = _sharpen_png(image_bytes)
    text_sharp = _tesseract(sharpened, psm=6, extra_flags=_user_words_flags(config)) if sharpened else ""
    parts = [t for t in (text_base, text_hires, text_sharp) if t]
    result = "\n".join(parts)
    _cache_put(image_bytes, result, tag=cache_tag, config=config)
    return result
