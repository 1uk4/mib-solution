"""Tests for v4.extract sharpen (third) OCR pass and its cache tagging.

Ported from v3/tests/test_ocr_triple_pass.py. v3's TestDispatchGating
asserted the module-global _SHARPEN_ENABLED after env + reload; that global
no longer exists — the v4 tests assert the BEHAVIOR (which OCR routine
extract_content dispatches to) under an injected Config, which is the
intent the attribute check stood in for.

Real Tesseract is invoked; tests use tiny known-good PNGs so they run in
under 1 second. If tesseract is not on PATH, tests are skipped.
"""
import io
import shutil

import pytest
from PIL import Image, ImageDraw

from v4.config import Config
from v4 import extract
from v4.acquire import Source, IMAGE

pytestmark = pytest.mark.skipif(
    shutil.which("tesseract") is None,
    reason="tesseract CLI not installed",
)


def _make_text_png(text: str, size=(400, 100)) -> bytes:
    img = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(img)
    # Default font works cross-platform; we don't need font quality, just
    # legible text tesseract can read.
    d.text((10, 30), text, fill="black")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _doc_source(raw: bytes) -> Source:
    """A Source whose metadata passes _should_ocr AND _looks_like_document."""
    return Source(
        type=IMAGE, id="image_0", raw=raw,
        metadata={"width": 1224, "height": 1584,
                  "mean_brightness": 250.0, "brightness_std": 50.0},
    )


class TestTriplePass:
    def test_returns_union_of_three_passes(self):
        img_bytes = _make_text_png("HELLO WORLD")
        out = extract._ocr_image_triple(img_bytes)
        # Union means each pass's output is concatenated; length should be
        # roughly 3× a single pass. Just verify HELLO WORLD is present.
        assert "HELLO" in out.upper()

    def test_sharpen_pass_actually_runs(self, monkeypatch):
        """Third pass must call tesseract on a sharpened variant."""
        calls = []
        original = extract._tesseract

        def spy(img_bytes, psm=6, extra_flags=None):
            calls.append({"psm": psm, "bytes_len": len(img_bytes)})
            return original(img_bytes, psm=psm, extra_flags=extra_flags)

        monkeypatch.setattr(extract, "_tesseract", spy)
        img_bytes = _make_text_png("HELLO")
        extract._ocr_image_triple(img_bytes)
        # Three invocations: baseline (psm=6), upscaled (psm=3), sharpened (psm=6)
        assert len(calls) == 3
        psms = [c["psm"] for c in calls]
        assert psms.count(6) == 2  # baseline + sharpened
        assert 3 in psms            # upscaled


class TestCacheKey:
    def test_uses_triple_suffix(self, tmp_path):
        cfg = Config(ocr_cache_dir=str(tmp_path))
        img_bytes = _make_text_png("HELLO")
        extract._ocr_image_triple(img_bytes, cfg)
        # A file with the _triple suffix (plus config tag) was created.
        files = list(tmp_path.glob("*_triple*.txt"))
        assert len(files) == 1
        # Full tag pin: default user_words=True -> "_triple_uw" (brief Rule 3)
        assert files[0].name.endswith("_triple_uw.txt")


class TestDispatchGating:
    def test_config_off_uses_dual(self, monkeypatch):
        # v3 intent: MIB_OCR_SHARPEN=0 -> dual-pass path
        called = {}
        monkeypatch.setattr(extract, "_ocr_image_dual",
                            lambda raw, config: called.setdefault("dual", True) and "")
        monkeypatch.setattr(extract, "_ocr_image_triple",
                            lambda raw, config: called.setdefault("triple", True) and "")
        src = _doc_source(_make_text_png("HELLO"))
        extract.extract_content([src], Config(ocr_sharpen=False))
        assert called == {"dual": True}

    def test_config_on_uses_triple(self, monkeypatch):
        # v3 intent: MIB_OCR_SHARPEN=1 -> triple-pass path
        called = {}
        monkeypatch.setattr(extract, "_ocr_image_dual",
                            lambda raw, config: called.setdefault("dual", True) and "")
        monkeypatch.setattr(extract, "_ocr_image_triple",
                            lambda raw, config: called.setdefault("triple", True) and "")
        src = _doc_source(_make_text_png("HELLO"))
        extract.extract_content([src], Config(ocr_sharpen=True))
        assert called == {"triple": True}
