"""OCR pool-breadth variants (B2-12): identity when off, correct pass
wiring when on, gate behavior, and the deskew estimator on synthetic skew.
"""
import io

import pytest
from PIL import Image, ImageDraw

from v4.config import Config
from v4 import extract
from v4.extract import (
    _apply_pool_passes, _DESKEW_GATE_TOKENS, _ROTATION_GATE_TOKENS,
    _estimate_skew_angle, _pool_token_count,
)


def _png(draw_fn, size=(400, 300)) -> bytes:
    img = Image.new("L", size, 255)
    draw_fn(ImageDraw.Draw(img))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


HIGH_YIELD = "word " * 60          # 60 tokens — above both gates
LOW_YIELD = "one two"              # 2 tokens — below both gates


class TestIdentityWhenOff:
    def test_default_config_is_identity(self, monkeypatch):
        calls = []
        monkeypatch.setattr(extract, "_tesseract",
                            lambda *a, **k: calls.append(1) or "")
        raw = _png(lambda d: None)
        out = _apply_pool_passes(raw, HIGH_YIELD, Config())
        assert out == HIGH_YIELD
        assert calls == []


class TestPsm11:
    def test_psm11_appended_ungated(self, monkeypatch):
        seen = []
        monkeypatch.setattr(extract, "_tesseract",
                            lambda b, psm=6, extra_flags=None:
                            seen.append(psm) or "SPARSE HIT")
        raw = _png(lambda d: None)
        out = _apply_pool_passes(raw, HIGH_YIELD, Config(ocr_pool_psm11=True))
        assert seen == [11]
        assert out.endswith("SPARSE HIT") and out.startswith(HIGH_YIELD)


class TestRotationTrigger:
    def test_no_rotation_on_readable_page(self, monkeypatch):
        calls = []
        monkeypatch.setattr(extract, "_tesseract",
                            lambda *a, **k: calls.append(1) or "x")
        raw = _png(lambda d: None)
        out = _apply_pool_passes(raw, HIGH_YIELD, Config(ocr_pool_rotations=True))
        assert calls == [] and out == HIGH_YIELD

    def test_no_rotation_on_horizontal_text_page(self, monkeypatch):
        # Row-dominant profile = text is already horizontal; rotating
        # cannot help, so the trigger must refuse even at low yield.
        calls = []
        monkeypatch.setattr(extract, "_tesseract",
                            lambda *a, **k: calls.append(1) or "x")
        raw = _png(lambda d: [d.line((20, y, 380, y), fill=0, width=3)
                              for y in range(40, 280, 30)])
        out = _apply_pool_passes(raw, LOW_YIELD, Config(ocr_pool_rotations=True))
        assert calls == [] and out == LOW_YIELD

    def test_rotations_fire_on_column_dominant_page(self, monkeypatch):
        # Sparse vertical stripes = text rows running vertically = the
        # rotation signature. Density matters: rotated real text measures
        # ink 0.01-0.07, so the synthetic must stay under the photo veto.
        seen = []
        monkeypatch.setattr(extract, "_tesseract",
                            lambda b, psm=6, extra_flags=None:
                            seen.append(psm) or "ROT")
        raw = _png(lambda d: [d.line((x, 20, x, 280), fill=0, width=2)
                              for x in range(60, 380, 60)])
        out = _apply_pool_passes(raw, LOW_YIELD, Config(ocr_pool_rotations=True))
        assert seen == [6, 6, 6]          # three rotations
        assert "ROT" in out

    def test_no_rotation_on_photo(self, monkeypatch):
        # Ink-heavy page (photo/portrait) → geometry passes vetoed.
        calls = []
        monkeypatch.setattr(extract, "_tesseract",
                            lambda *a, **k: calls.append(1) or "x")
        raw = _png(lambda d: d.rectangle((0, 0, 400, 300), fill=90))
        out = _apply_pool_passes(raw, LOW_YIELD, Config(ocr_pool_rotations=True))
        assert calls == [] and out == LOW_YIELD

    def test_no_rotation_on_blank_page(self, monkeypatch):
        # Near-zero variance on both axes → absolute floor blocks the fire.
        calls = []
        monkeypatch.setattr(extract, "_tesseract",
                            lambda *a, **k: calls.append(1) or "x")
        raw = _png(lambda d: None)
        out = _apply_pool_passes(raw, LOW_YIELD, Config(ocr_pool_rotations=True))
        assert calls == [] and out == LOW_YIELD

    def test_gate_constants_ordering(self):
        assert _ROTATION_GATE_TOKENS < _DESKEW_GATE_TOKENS
        assert _pool_token_count(HIGH_YIELD) > _DESKEW_GATE_TOKENS
        assert _pool_token_count(LOW_YIELD) < _ROTATION_GATE_TOKENS


class TestDeskewTrigger:
    def test_deskew_fires_on_tilted_lines(self, monkeypatch):
        seen = []
        monkeypatch.setattr(extract, "_tesseract",
                            lambda b, psm=6, extra_flags=None:
                            seen.append(psm) or "DESKEWED")
        img = Image.new("L", (400, 300), 255)
        d = ImageDraw.Draw(img)
        for y in range(40, 280, 30):
            d.line((20, y, 380, y), fill=0, width=3)
        tilted = img.rotate(3, fillcolor=255)
        buf = io.BytesIO(); tilted.save(buf, format="PNG")
        out = _apply_pool_passes(buf.getvalue(), LOW_YIELD,
                                 Config(ocr_pool_deskew=True))
        assert seen == [6] and "DESKEWED" in out

    def test_no_deskew_on_aligned_page(self, monkeypatch):
        calls = []
        monkeypatch.setattr(extract, "_tesseract",
                            lambda *a, **k: calls.append(1) or "x")
        raw = _png(lambda d: [d.line((20, y, 380, y), fill=0, width=3)
                              for y in range(40, 280, 30)])
        out = _apply_pool_passes(raw, LOW_YIELD, Config(ocr_pool_deskew=True))
        assert calls == [] and out == LOW_YIELD


class TestDeskewEstimator:
    def test_aligned_lines_need_no_correction(self):
        raw = _png(lambda d: [d.line((20, y, 380, y), fill=0, width=3)
                              for y in range(40, 280, 30)])
        assert _estimate_skew_angle(raw) == 0

    def test_synthetic_skew_detected_and_sign_correct(self):
        # Draw horizontal lines, then tilt the image by +3 degrees; the
        # corrective angle re-aligning the rows is -3 (tolerance +/-1).
        img = Image.new("L", (400, 300), 255)
        d = ImageDraw.Draw(img)
        for y in range(40, 280, 30):
            d.line((20, y, 380, y), fill=0, width=3)
        tilted = img.rotate(3, fillcolor=255)
        buf = io.BytesIO()
        tilted.save(buf, format="PNG")
        angle = _estimate_skew_angle(buf.getvalue())
        assert angle in (-2, -3, -4), f"expected ~-3, got {angle}"

    def test_blank_image_returns_zero(self):
        assert _estimate_skew_angle(_png(lambda d: None)) == 0
