"""Tests for the user-words dictionary wiring and cache-tag contract.

Ported from v3/tests/test_user_words_wiring.py. v3 toggled MIB_USER_WORDS
via monkeypatch.setenv + importlib.reload; v4 injects a Config instead —
same intents, no module-global mutation.

The cache-tag pins here are the brief-Rule-3 guarantee: the warm OCR cache
on disk is keyed by these exact strings.
"""
import io
from pathlib import Path

from v4.config import Config, CONFIG
from v4 import extract


def test_user_words_disabled_via_config():
    # v3 intent: MIB_USER_WORDS=0 -> no flags
    assert extract._user_words_flags(Config(user_words=False)) == []


def test_user_words_enabled_returns_flags():
    # v3 intent: MIB_USER_WORDS=1 -> ['--user-words', <existing path>]
    flags = extract._user_words_flags(Config(user_words=True))
    assert len(flags) == 2
    assert flags[0] == "--user-words"
    assert Path(flags[1]).exists(), f"user-words file missing: {flags[1]}"


def test_default_config_has_user_words_active():
    """Guard test (spec §4.1 user-words data hazard): under DEFAULT config
    the flags must be non-empty and the data file must exist. The silent []
    fallback plus warm-cache masking would otherwise hide a missing
    v4/data/tesseract_user_words.txt until the final Docker cold-cache run.
    """
    flags = extract._user_words_flags(CONFIG)
    assert flags, "user-words flags empty under default config — data file missing?"
    assert Path(flags[1]).exists()


def test_user_words_file_contains_expected_tokens():
    """Sanity check: closed-enum tokens must be present."""
    path = Path(__file__).parents[1] / "data" / "tesseract_user_words.txt"
    assert path.exists(), "v4/data/tesseract_user_words.txt missing"
    tokens = set(path.read_text().split())
    for expected in ("MED-3", "XW-1", "DIP-1", "paid", "waived", "unpaid"):
        assert expected in tokens, f"missing enum token: {expected}"


def test_ocr_calls_pass_user_words_when_enabled(monkeypatch, tmp_path):
    """When enabled, _ocr_image must pass user-words flag through to tesseract."""
    captured = []

    def spy(img_bytes, psm=6, extra_flags=None):
        captured.append(list(extra_flags) if extra_flags else [])
        return ""

    monkeypatch.setattr(extract, "_tesseract", spy)

    # Use a tiny synthetic image
    from PIL import Image
    img = Image.new("RGB", (100, 40), "white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    extract._ocr_image(
        buf.getvalue(),
        Config(user_words=True, ocr_cache_dir=str(tmp_path)),
    )

    assert captured, "no tesseract call captured"
    assert "--user-words" in captured[0]


def test_cache_config_tag_disabled():
    # v3 intent: MIB_USER_WORDS off -> "" (no suffix)
    assert extract._cache_config_tag(Config(user_words=False)) == ""


def test_cache_config_tag_enabled():
    # v3 intent: MIB_USER_WORDS on -> "_uw". This string keys the warm cache.
    assert extract._cache_config_tag(Config(user_words=True)) == "_uw"


def test_cache_config_tag_default_is_uw():
    """The production default MUST produce '_uw' — brief Rule 3."""
    assert extract._cache_config_tag(CONFIG) == "_uw"
