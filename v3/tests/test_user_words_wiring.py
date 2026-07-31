"""Tests for MIB_USER_WORDS env-var gate and file path resolution."""
import io
from pathlib import Path

import pytest


def test_user_words_disabled_via_env_zero(monkeypatch):
    monkeypatch.setenv("MIB_USER_WORDS", "0")
    import importlib
    from v3 import extract
    importlib.reload(extract)
    assert extract._user_words_flags() == []


def test_user_words_enabled_returns_flags(monkeypatch):
    monkeypatch.setenv("MIB_USER_WORDS", "1")
    import importlib
    from v3 import extract
    importlib.reload(extract)
    flags = extract._user_words_flags()
    assert len(flags) == 2
    assert flags[0] == "--user-words"
    assert Path(flags[1]).exists(), f"user-words file missing: {flags[1]}"


def test_user_words_file_contains_expected_tokens():
    """Sanity check on Task 1's output: closed-enum tokens must be present."""
    path = Path(__file__).parents[1] / "data" / "tesseract_user_words.txt"
    if not path.exists():
        pytest.skip("Task 1 not run yet")
    tokens = set(path.read_text().split())
    for expected in ("MED-3", "XW-1", "DIP-1", "paid", "waived", "unpaid"):
        assert expected in tokens, f"missing enum token: {expected}"


def test_ocr_calls_pass_user_words_when_enabled(monkeypatch, tmp_path):
    """When enabled, _ocr_image must pass user-words flag through to tesseract."""
    monkeypatch.setenv("MIB_USER_WORDS", "1")
    monkeypatch.setenv("MIB_OCR_CACHE_DIR", str(tmp_path))
    import importlib
    from v3 import extract
    importlib.reload(extract)

    captured = []
    original = extract._tesseract

    def spy(img_bytes, psm=6, extra_flags=None):
        captured.append(list(extra_flags) if extra_flags else [])
        return original(img_bytes, psm=psm, extra_flags=extra_flags)

    monkeypatch.setattr(extract, "_tesseract", spy)

    # Use a tiny synthetic image
    from PIL import Image
    img = Image.new("RGB", (100, 40), "white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    extract._ocr_image(buf.getvalue())

    assert captured, "no tesseract call captured"
    assert "--user-words" in captured[0]


def test_cache_config_tag_disabled(monkeypatch):
    """When MIB_USER_WORDS is off, _cache_config_tag returns empty string."""
    monkeypatch.setenv("MIB_USER_WORDS", "0")
    import importlib
    from v3 import extract
    importlib.reload(extract)
    assert extract._cache_config_tag() == ""


def test_cache_config_tag_enabled(monkeypatch):
    """When MIB_USER_WORDS is on, _cache_config_tag returns '_uw'."""
    monkeypatch.setenv("MIB_USER_WORDS", "1")
    import importlib
    from v3 import extract
    importlib.reload(extract)
    assert extract._cache_config_tag() == "_uw"
