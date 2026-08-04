#!/usr/bin/env python3
"""L1 — Source enumeration.

Walks a PDF and enumerates every extractable content source: text streams
and embedded images. Each source is a Source dataclass with a stable
identifier, raw bytes, and metadata. No interpretation happens here — L2
(extract.py) is responsible for turning raw bytes into readable content.

DOES: enumerate sources, decode image bytes to OCR-ready JPEG/PNG, attach
cheap metadata (dims, brightness stats).
DOES NOT: decode text, judge content, or assign trust — that is L2/L3.

The Source object flows through subsequent layers, accumulating fields:
    L1 (acquire) populates: type, id, raw, metadata
    L2 (extract) populates: content
    L3 (filters) populates: trusted, exclusion_reason

Keeping Source mutable-but-append-only is the pragmatic middle ground:
each layer's contribution is visible, but we avoid re-allocating a new
list per layer.
"""
from __future__ import annotations

import base64
import io
import re
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from v4.patterns import STREAM_RE, FILTER_RE

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None  # type: ignore


# ---------------------------------------------------------------------------
# Source model
# ---------------------------------------------------------------------------

TEXT_STREAM = "TEXT_STREAM"
IMAGE = "IMAGE"


@dataclass
class Source:
    """One addressable piece of content extracted from a PDF.

    Fields populated by L1 (acquire):
        type: TEXT_STREAM | IMAGE
        id:   stable identifier ('text_stream_0', 'image_2')
        raw:  raw bytes ready for L2 to interpret
        metadata: layer-agnostic metadata (dims, filters, byte size)

    Fields populated by L2 (extract):
        content: decoded text (empty string on failure — never None)

    Fields populated by L3 (filters):
        trusted: default True; a detector sets False + reason
        exclusion_reason: which detector excluded, and why
    """
    type: str
    id: str
    raw: bytes
    metadata: dict = field(default_factory=dict)
    content: str = ""
    trusted: bool = True
    exclusion_reason: str = ""


# ---------------------------------------------------------------------------
# Image extraction helpers (support both JPEG and Flate-raw pixel streams)
# ---------------------------------------------------------------------------

JPEG_MAGIC = b"\xff\xd8\xff"

# Stream-dict entry patterns, precompiled once (called per image stream).
_DICT_INT_RES = {
    key: re.compile(rb"/" + key + rb"\s+(\d+)")
    for key in (b"Width", b"Height")
}
_DICT_NAME_RES = {
    key: re.compile(rb"/" + key + rb"\s+/(\w+)")
    for key in (b"ColorSpace",)
}


def _dict_int(dict_bytes: bytes, key: bytes) -> int | None:
    m = _DICT_INT_RES[key].search(dict_bytes)
    return int(m.group(1)) if m else None


def _dict_name(dict_bytes: bytes, key: bytes) -> bytes | None:
    m = _DICT_NAME_RES[key].search(dict_bytes)
    return m.group(1) if m else None


def _image_meta(image_bytes: bytes) -> dict:
    """Header-only metadata. width/height feed L2's gates; bytes/mode are
    audit-only. Absent keys must fail toward "do OCR".

    Brightness stats removed 2026-08-03: 0 gate decisions in 4,079
    training images, at a full pixel decode per image.
    """
    meta = {"bytes": len(image_bytes)}
    if Image is None:
        return meta
    try:
        img = Image.open(io.BytesIO(image_bytes))
        w, h = img.size
        meta.update({
            "width": w,
            "height": h,
            "mode": img.mode,
        })
    except Exception:
        pass
    return meta


def _extract_image_bytes(dict_bytes: bytes, stream_data: bytes) -> bytes | None:
    """Return image content as JPEG or PNG bytes ready for OCR.

    Two flavors observed in this dataset:
      - DCTDecode → data is a JPEG after ASCII85 unwrap.
      - FlateDecode (no DCT) → data is raw pixels after ASCII85 + Flate.
        Reconstructed to PNG via PIL for downstream compatibility.

    Anything else returns None (silently skipped).
    """
    data = stream_data

    if b"ASCII85Decode" in dict_bytes:
        end = data.find(b"~>")
        if end >= 0:
            data = data[:end]
        try:
            data = base64.a85decode(data.strip(), adobe=False)
        except Exception:
            return None

    if b"DCTDecode" in dict_bytes:
        return data if data.startswith(JPEG_MAGIC) else None

    if b"FlateDecode" in dict_bytes and Image is not None:
        try:
            pixels = zlib.decompress(data)
        except Exception:
            return None
        w = _dict_int(dict_bytes, b"Width")
        h = _dict_int(dict_bytes, b"Height")
        if not (w and h):
            return None
        cs = _dict_name(dict_bytes, b"ColorSpace") or b"DeviceRGB"
        if cs == b"DeviceRGB":
            mode, expected = "RGB", w * h * 3
        elif cs == b"DeviceGray":
            mode, expected = "L", w * h
        else:
            return None
        if len(pixels) != expected:
            return None
        try:
            img = Image.frombytes(mode, (w, h), pixels)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()
        except Exception:
            return None

    return None


# ---------------------------------------------------------------------------
# Public API — acquire_sources
# ---------------------------------------------------------------------------


def _is_image_stream(dict_bytes: bytes) -> bool:
    """A stream is treated as an image if it's tagged /Subtype /Image or
    uses an image codec filter (DCT / CCITTFax). Text streams may also use
    FlateDecode, so we can't use that alone. CCITT: 0 occurrences in
    training; would fail safe (dropped as undecodable image)."""
    return (
        b"/Subtype /Image" in dict_bytes
        or b"DCTDecode" in dict_bytes
        or b"CCITTFaxDecode" in dict_bytes
    )


def acquire_sources(pdf_path: Path) -> list[Source]:
    """Enumerate every source in a PDF.

    Returns a list of Source objects in stream-appearance order.
    Sources whose image data can't be decoded are dropped silently —
    the downstream pipeline receives only sources we could actually read
    the raw bytes of. (L2 handles OCR failure on raw bytes it does receive.)
    """
    try:
        raw = pdf_path.read_bytes()
    except Exception:
        return []

    sources: list[Source] = []
    text_idx = 0
    image_idx = 0

    for m in STREAM_RE.finditer(raw):
        dict_bytes = m.group("dict")
        stream_data = m.group("data")

        if _is_image_stream(dict_bytes):
            img_bytes = _extract_image_bytes(dict_bytes, stream_data)
            if img_bytes is None:
                continue
            sources.append(Source(
                type=IMAGE,
                id=f"image_{image_idx}",
                raw=img_bytes,
                metadata=_image_meta(img_bytes),
            ))
            image_idx += 1
        else:
            # Text stream — store raw stream bytes; L2 decodes via filter chain.
            # We keep filter names as bytes to match `_decode_stream`'s API.
            filters = FILTER_RE.findall(dict_bytes)
            sources.append(Source(
                type=TEXT_STREAM,
                id=f"text_stream_{text_idx}",
                raw=stream_data,
                metadata={"filters": filters},
            ))
            text_idx += 1

    return sources
