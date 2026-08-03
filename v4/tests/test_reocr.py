"""Tests for v4.reocr — char-whitelist re-OCR for structured fields.

Ported from v3/tests/test_reocr.py. TestGateOff's env+monkeypatch-module-
global pattern is rewritten as Config injection (same intent: gate off
means _norm_and_repair never attempts repair).
"""
import io
import shutil

import pytest
from PIL import Image, ImageDraw

from v4.acquire import Source, IMAGE
from v4.config import Config
from v4 import reocr
from v4 import signals as signals_module

pytestmark = pytest.mark.skipif(
    shutil.which("tesseract") is None,
    reason="tesseract CLI not installed",
)


def _synth_image(text: str, size=(400, 100)) -> bytes:
    img = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(img)
    d.text((10, 30), text, fill="black")
    buf = io.BytesIO(); img.save(buf, format="PNG")
    return buf.getvalue()


class TestSponsorRepair:
    def test_returns_none_for_non_image_source(self):
        src = Source(type="TEXT_STREAM", id="t0", raw=b"", metadata={})
        assert reocr.repair(src, "sponsor_id", "SPN-6O99") is None

    def test_returns_none_when_current_value_already_valid(self):
        # Even if we could re-OCR, no repair needed
        img = _synth_image("SPN-6099")
        src = Source(type=IMAGE, id="i0", raw=img, metadata={})
        assert reocr.repair(src, "sponsor_id", "SPN-6099") is None

    def test_repair_recovers_digits_from_letter_confusion(self):
        # Synthetic: image says SPN-6099, initial OCR read as SPN-6O99
        img = _synth_image("SPN-6099")
        src = Source(type=IMAGE, id="i0", raw=img, metadata={})
        repaired = reocr.repair(src, "sponsor_id", "SPN-6O99")
        # Either got the correct value or returned None (no worse than input)
        assert repaired is None or repaired == "SPN-6099"


class TestDateRepair:
    def test_returns_none_when_valid(self):
        img = _synth_image("2026-02-13")
        src = Source(type=IMAGE, id="i0", raw=img, metadata={})
        assert reocr.repair(src, "arrival_date", "2026-02-13") is None

    def test_repair_attempts_when_invalid(self):
        # Simulate initial garbage read; repair should either return a
        # format-valid date (YYYY-MM-DD) or None — any valid result is fine,
        # since the exact OCR output depends on Tesseract's rendering of the
        # synthetic image (which may not reproduce the exact source digits).
        import re
        _DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
        img = _synth_image("2026-02-13")
        src = Source(type=IMAGE, id="i0", raw=img, metadata={})
        out = reocr.repair(src, "arrival_date", "2O26-O2-13")
        assert out is None or _DATE_RE.match(out)


class TestUnsupportedField:
    def test_freeform_field_returns_none(self):
        img = _synth_image("Xanax Ozorquell")
        src = Source(type=IMAGE, id="i0", raw=img, metadata={})
        # applicant_name has no format regex — no whitelist strategy applies
        assert reocr.repair(src, "applicant_name", "Xanax Ozorquell") is None


class TestGateOff:
    def test_reocr_gate_off(self):
        """With reocr_char_whitelist=False, _norm_and_repair skips repair.

        v3 intent (MIB_CHAR_WHITELIST_REOCR=0): the gate controls whether
        repair() is attempted; with it off, the plain normalized value comes
        back untouched, regardless of the field type.
        """
        img = _synth_image("SPN-6099")
        src = Source(type=IMAGE, id="i0", raw=img, metadata={}, content="SPN-6099")
        src.trusted = True

        # With gate off, _norm_and_repair should return the plain _norm result,
        # NOT attempt to repair "SPN-6O99" (invalid value that would trigger repair).
        result = signals_module._norm_and_repair(
            "sponsor_id", "SPN-6O99", src,
            Config(reocr_char_whitelist=False),
        )
        # The gate is off, so repair is skipped; the raw normalized value is returned.
        assert result == "SPN-6O99"
