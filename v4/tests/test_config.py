"""Config module tests (spec §8): documented defaults, immutability, and
the single surviving env read."""
import dataclasses

import pytest

from v4.config import Config, CONFIG


# The exact documented split: 8 booleans ON, 2 OFF, plus the cache path.
EXPECTED_TRUE = {
    "ocr_sharpen", "user_words", "normalize_values", "reocr_char_whitelist",
    "trust_finding", "upgrade_waived_on_biometric", "fallback_ocr_upgrade",
    "ocr_only_guard",
}
EXPECTED_FALSE = {"upgrade_unpaid_on_waiver", "defensive_downgrade",
                  "fee_fuzzy_recovery"}


class TestDefaults:
    def test_field_inventory_is_exactly_documented(self):
        names = {f.name for f in dataclasses.fields(Config)}
        assert names == EXPECTED_TRUE | EXPECTED_FALSE | {"ocr_cache_dir"}

    def test_eight_booleans_default_true(self):
        c = Config()
        for name in EXPECTED_TRUE:
            assert getattr(c, name) is True, name

    def test_two_booleans_default_false(self):
        c = Config()
        for name in EXPECTED_FALSE:
            assert getattr(c, name) is False, name

    def test_cache_dir_defaults_none(self):
        assert Config().ocr_cache_dir is None


class TestImmutability:
    def test_frozen(self):
        c = Config()
        with pytest.raises(dataclasses.FrozenInstanceError):
            c.defensive_downgrade = True  # type: ignore[misc]


class TestFromEnv:
    def test_reads_cache_dir(self, monkeypatch):
        monkeypatch.setenv("MIB_OCR_CACHE_DIR", "/tmp/some-cache")
        assert Config.from_env().ocr_cache_dir == "/tmp/some-cache"

    def test_unset_gives_none(self, monkeypatch):
        monkeypatch.delenv("MIB_OCR_CACHE_DIR", raising=False)
        assert Config.from_env().ocr_cache_dir is None

    def test_whitespace_gives_none(self, monkeypatch):
        monkeypatch.setenv("MIB_OCR_CACHE_DIR", "   ")
        assert Config.from_env().ocr_cache_dir is None

    def test_env_does_not_touch_behavior_flags(self, monkeypatch):
        monkeypatch.setenv("MIB_DEFENSIVE_DOWNGRADE", "1")  # retired var
        assert Config.from_env().defensive_downgrade is False

    def test_module_singleton_exists(self):
        assert isinstance(CONFIG, Config)
