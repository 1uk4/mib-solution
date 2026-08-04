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

_MIN_DIMENSION = 100          # below this → icon/divider/watermark


def _should_ocr(source: Source) -> bool:
    """Return False for images obviously without text. Safe skips only.

    Blank-canvas rule removed 2026-08-03: 0 fires in 4,079 training
    images. The <100px rule is free and fails safe, so it stays.
    """
    if source.type != IMAGE:
        return True
    m = source.metadata
    w = m.get("width")
    h = m.get("height")
    if not (w and h):
        return True  # unknown metadata → be safe
    if max(w, h) < _MIN_DIMENSION:
        return False
    return True


# ---------------------------------------------------------------------------
# Document-shape gate: which images get dual-pass OCR?
# ---------------------------------------------------------------------------

# Real rendered documents in this dataset are letter-size (~1224×1584);
# stamps / photos / decorative elements are smaller. The multi-pass config
# (psm=3 + 2x upscale, sharpen) is worth the extra ~1s only for actual
# documents — it doesn't help on non-text imagery and doubles OCR compute
# across the whole packet if fired indiscriminately.
_DOC_MIN_DIMENSION = 800


def _looks_like_document(source: Source) -> bool:
    """True if the image is likely a rendered document worth multi-pass OCR.

    Dimension is the whole gate — empirically identical to "is a JPEG" on
    training. Brightness veto removed 2026-08-03: 0 fires in 4,079 images.
    Needs PIL for the upscale pass, hence the Image guard.
    """
    if Image is None:
        return False
    m = source.metadata
    w = m.get("width", 0) or 0
    h = m.get("height", 0) or 0
    if max(w, h) < _DOC_MIN_DIMENSION:
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
            src.content = _apply_pool_passes(src.raw, src.content, config)
    return sources


# ---------------------------------------------------------------------------
# OCR pool-breadth variants (B2-12) — compositional, individually cached
# ---------------------------------------------------------------------------

# Gates read the token yield of the strategy output BEFORE pool additions,
# so they are deterministic and independent of pool order. Chosen to target
# the population each variant exists for; the variant itself is accepted or
# rejected by full-score measurement, not by these constants.
_POOL_TOKEN_RE = re.compile(r"[A-Za-z]{2,}")
_ROTATION_GATE_TOKENS = 12   # near-nothing readable → try whole rotations
_DESKEW_GATE_TOKENS = 40     # degraded-but-not-empty → try tilt correction

# Geometry triggers (spot-derived 2026-08-03 from thumbnail statistics):
# document pages are BRIGHT (coarse-grid ink 0.000-0.07); photos/portraits
# are ink-heavy (>=0.15) and isotropic — no rotation ever yields a field
# from them. Text pages are strongly row-dominant (row_var/col_var 1.5-32),
# so a rotated text page is unmistakably column-dominant. Thresholds sit in
# the wide gaps between those observed clusters.
_POOL_INK_PHOTO_MIN = 0.15    # >= this: photo/noise → skip geometry passes
_ROT_COL_DOMINANCE = 2.0      # col_var must beat row_var by this factor
_ROT_VAR_FLOOR = 30.0         # and clear an absolute floor (blank pages
                              # have near-zero variance on both axes)
_DESKEW_MIN_ANGLE = 2         # degrees; 1-degree tilt barely hurts OCR
# Deskew acceptance (spot-derived from the would-fire population): specks
# on near-blank pages produce huge variance RATIOS on tiny baselines
# (v0~5 -> best~28), while genuine tilted structure shows best_v 138-238.
# Require absolute structure AND real improvement.
_DESKEW_VAR_FLOOR = 100.0
_DESKEW_MIN_RATIO = 1.5


def _pool_token_count(content: str) -> int:
    return len(_POOL_TOKEN_RE.findall(content)) if content else 0


def _page_geometry(image_bytes: bytes) -> tuple[float, float, float]:
    """(ink_fraction, row_var, col_var) from a small grayscale thumbnail.

    Row/col ink profiles come from C-speed 1xH / Wx1 BOX resizes; ink
    fraction from a 64x64 grid. Milliseconds per image; the whole point is
    to buy a *reason* before paying for a Tesseract call.
    """
    if Image is None:
        return (0.0, 0.0, 0.0)
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img.load()
        g = img.convert("L")
        if g.width > 400:
            g = g.resize((400, max(1, g.height * 400 // g.width)))
        w, h = g.size
        rows = list(g.resize((1, h), Image.BOX).getdata())
        cols = list(g.resize((w, 1), Image.BOX).getdata())

        def var(xs):
            m = sum(xs) / len(xs)
            return sum((x - m) ** 2 for x in xs) / len(xs)

        px = list(g.resize((64, 64), Image.BOX).getdata())
        ink = sum(1 for p in px if p < 200) / len(px)
        return (ink, var(rows), var(cols))
    except Exception:
        return (0.0, 0.0, 0.0)


def _apply_pool_passes(image_bytes: bytes, content: str,
                       config: Config = CONFIG) -> str:
    """Append enabled pool passes to the strategy output.

    Compositional by design: each pass has its own cache entry, so adding
    a pool member never invalidates the warm triple/dual/single caches.
    Appended last — the union-order experiment (B2-7) measured order as
    net-neutral, and append-last is the conservative end.
    With all pool flags off (default) this is the identity.

    Rotation/deskew fire only with a geometric REASON (thumbnail evidence,
    milliseconds): a photo-heavy page gets neither; rotation needs a
    column-dominant ink profile (text rows running vertically); deskew
    needs a clear >=2-degree tilt. Token gates alone would pay full-price
    Tesseract on ~1.4 evidence-free images per packet."""
    parts = [content]
    if config.ocr_pool_psm11:
        t = _ocr_pool_psm11(image_bytes, config)
        if t.strip():
            parts.append(t)
    if config.ocr_pool_rotations or config.ocr_pool_deskew:
        yield_tokens = _pool_token_count(content)
        want_rot = config.ocr_pool_rotations and yield_tokens < _ROTATION_GATE_TOKENS
        want_dsk = config.ocr_pool_deskew and yield_tokens < _DESKEW_GATE_TOKENS
        if want_rot or want_dsk:
            ink, row_var, col_var = _page_geometry(image_bytes)
            if ink < _POOL_INK_PHOTO_MIN:  # photo/noise pages: no geometry pass
                if (want_rot and col_var > _ROT_COL_DOMINANCE * row_var
                        and col_var > _ROT_VAR_FLOOR):
                    t = _ocr_pool_rotations(image_bytes, config)
                    if t.strip():
                        parts.append(t)
                if want_dsk:
                    a, best_v, v0 = _skew_profile(image_bytes)
                    if (abs(a) >= _DESKEW_MIN_ANGLE
                            and best_v >= _DESKEW_VAR_FLOOR
                            and best_v >= _DESKEW_MIN_RATIO * v0):
                        t = _ocr_pool_deskew(image_bytes, config)
                        if t.strip():
                            parts.append(t)
    return "\n".join(parts) if len(parts) > 1 else content


def _ocr_pool_psm11(image_bytes: bytes, config: Config = CONFIG) -> str:
    """Sparse-text pass (psm 11): finds isolated words block-segmentation
    merges or drops — stamps, annotations, note fragments. Output order is
    detection order, not reading order; safe in a pool, appended last."""
    tag = "_p11" + _cache_config_tag(config)
    cached = _cache_get(image_bytes, tag=tag, config=config)
    if cached is not None:
        return cached
    result = _tesseract(image_bytes, psm=11, extra_flags=_user_words_flags(config))
    _cache_put(image_bytes, result, tag=tag, config=config)
    return result


def _ocr_pool_rotations(image_bytes: bytes, config: Config = CONFIG) -> str:
    """90/180/270 lossless rotations, psm 6 each, non-empty results joined.
    Only called on near-empty pages (gate above) — a readable page never
    pays for this."""
    tag = "_rot" + _cache_config_tag(config)
    cached = _cache_get(image_bytes, tag=tag, config=config)
    if cached is not None:
        return cached
    uw = _user_words_flags(config)
    parts = []
    if Image is not None:
        for tr in (Image.ROTATE_90, Image.ROTATE_180, Image.ROTATE_270):
            png = _pil_png(image_bytes, lambda im, tr=tr: im.transpose(tr))
            if png:
                t = _tesseract(png, psm=6, extra_flags=uw)
                if t.strip():
                    parts.append(t)
    result = "\n".join(parts)
    _cache_put(image_bytes, result, tag=tag, config=config)
    return result


# Deskew estimation: on a correctly-aligned page, ink concentrates into
# sharp horizontal rows, so the variance of row-ink-sums peaks at the true
# correction angle. Row sums come from a C-speed 1xH BOX resize of a small
# grayscale thumbnail — the whole 11-angle search costs milliseconds.
_DESKEW_ANGLES = range(-5, 6)          # 1-degree steps
_DESKEW_MARGIN = 1.05                  # best must beat 0-degrees by 5%


def _skew_profile(image_bytes: bytes) -> tuple[int, float, float]:
    """(best_angle, best_variance, zero_variance) from the row-ink profile."""
    if Image is None:
        return (0, 0.0, 0.0)
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img.load()
        g = img.convert("L")
        if g.width > 400:
            g = g.resize((400, max(1, g.height * 400 // g.width)))
        h = g.height
        best_a, best_v, v0 = 0, -1.0, 0.0
        for a in _DESKEW_ANGLES:
            r = g.rotate(a, fillcolor=255) if a else g
            rows = list(r.resize((1, h), Image.BOX).getdata())
            mean = sum(rows) / h
            v = sum((x - mean) ** 2 for x in rows) / h
            if a == 0:
                v0 = v
            if v > best_v:
                best_v, best_a = v, a
        return (best_a, best_v, v0)
    except Exception:
        return (0, 0.0, 0.0)


def _estimate_skew_angle(image_bytes: bytes) -> int:
    """Best small correction angle in degrees, or 0 if none clearly wins."""
    best_a, best_v, v0 = _skew_profile(image_bytes)
    if best_a != 0 and best_v > v0 * _DESKEW_MARGIN:
        return best_a
    return 0


def _ocr_pool_deskew(image_bytes: bytes, config: Config = CONFIG) -> str:
    """One corrective rotate + OCR when the profile clearly names a tilt.
    Grayscale full-res rotate (white fill, expanded canvas) so no content
    is cropped. Cached including the no-tilt empty result — the estimator
    is deterministic, so the decision is too."""
    tag = "_dsk" + _cache_config_tag(config)
    cached = _cache_get(image_bytes, tag=tag, config=config)
    if cached is not None:
        return cached
    result = ""
    angle = _estimate_skew_angle(image_bytes)
    if angle:
        png = _pil_png(image_bytes, lambda im: im.convert("L").rotate(
            angle, resample=Image.BICUBIC, fillcolor=255, expand=True))
        if png:
            result = _tesseract(png, psm=6, extra_flags=_user_words_flags(config))
    _cache_put(image_bytes, result, tag=tag, config=config)
    return result


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


# Hang guard, not a budget: the slowest observed pass (~2s, 2x-upscaled
# letter page) is well under this. A hung Tesseract costs one image, never
# a packet.
_TESSERACT_TIMEOUT_S = 15


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
            timeout=_TESSERACT_TIMEOUT_S,
        )
        return proc.stdout.decode("utf-8", errors="replace")
    except Exception:
        return ""


def _pil_png(image_bytes: bytes, transform) -> bytes | None:
    """Decode via PIL, apply transform(img) -> img, re-encode PNG.
    None on any failure (missing PIL included)."""
    if Image is None:
        return None
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img.load()
        img = transform(img)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return None


def _upscale_png(image_bytes: bytes, scale: int) -> bytes | None:
    """LANCZOS upscale for the higher-DPI OCR pass."""
    return _pil_png(image_bytes, lambda im: im.resize(
        (im.width * scale, im.height * scale), Image.LANCZOS))


def _sharpen_png(image_bytes: bytes) -> bytes | None:
    """Unsharp mask (radius=2, percent=150) — recovers character-level
    misreads by boosting edge contrast (Annax→Asinax class)."""
    return _pil_png(image_bytes, lambda im: im.filter(
        ImageFilter.UnsharpMask(radius=2, percent=150)))


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
    uw = _user_words_flags(config)
    text_base = _tesseract(image_bytes, psm=6, extra_flags=uw)
    upscaled = _upscale_png(image_bytes, scale=2)
    text_hires = _tesseract(upscaled, psm=3, extra_flags=uw) if upscaled else ""
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
    uw = _user_words_flags(config)
    text_base = _tesseract(image_bytes, psm=6, extra_flags=uw)
    upscaled = _upscale_png(image_bytes, scale=2)
    text_hires = _tesseract(upscaled, psm=3, extra_flags=uw) if upscaled else ""
    sharpened = _sharpen_png(image_bytes)
    text_sharp = _tesseract(sharpened, psm=6, extra_flags=uw) if sharpened else ""
    parts = [t for t in (text_base, text_hires, text_sharp) if t]
    result = "\n".join(parts)
    _cache_put(image_bytes, result, tag=cache_tag, config=config)
    return result
