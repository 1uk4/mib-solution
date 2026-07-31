"""Tests for v3.extract sharpen (third) OCR pass and its cache tagging.

Real Tesseract is invoked; tests use tiny known-good PNGs so they run in
under 1 second. If tesseract is not on PATH, tests are skipped.
"""
import io
import os
import shutil
import subprocess
import tempfile

import pytest
from PIL import Image, ImageDraw, ImageFont, ImageFilter

from v3 import extract

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
    def test_uses_triple_suffix(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MIB_OCR_CACHE_DIR", str(tmp_path))
        # Reset cache state
        import importlib
        importlib.reload(extract)
        img_bytes = _make_text_png("HELLO")
        extract._ocr_image_triple(img_bytes)
        # Verify a file with _triple suffix was created (Amendment B: glob tolerates future suffix)
        files = list(tmp_path.glob("*_triple*.txt"))
        assert len(files) == 1


class TestDispatchGating:
    def test_env_off_uses_dual(self, monkeypatch):
        # Amendment A: use setenv("0") instead of delenv
        monkeypatch.setenv("MIB_OCR_SHARPEN", "0")
        import importlib
        importlib.reload(extract)
        assert extract._SHARPEN_ENABLED is False

    def test_env_on_uses_triple(self, monkeypatch):
        monkeypatch.setenv("MIB_OCR_SHARPEN", "1")
        import importlib
        importlib.reload(extract)
        assert extract._SHARPEN_ENABLED is True
