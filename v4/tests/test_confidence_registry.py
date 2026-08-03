"""Confidence registry tests (spec §4.2): every emittable tag resolves to a
registry key; transcribed values match their v1/v3 sources exactly.

The v1/v3 imports below are the ORACLE for transcription fidelity — this is
the one sanctioned place v4 tests may read frozen-version code. Production
v4 modules never import v1/v2/v3.
"""
from v4.confidence import (
    ALIASES, CONFIDENCE, EXACT, PREFIX, RuleConf, conf, registry_key_for,
)


class TestCompleteness:
    def test_every_exact_tag_resolves(self):
        for tag in EXACT:
            key = registry_key_for(tag)
            assert key in CONFIDENCE, tag

    def test_every_prefix_family_resolves(self):
        for prefix, key in PREFIX.items():
            assert key in CONFIDENCE, prefix
            # A representative emitted tag from the family resolves too.
            assert registry_key_for(prefix + "x") == key

    def test_alias_target_exists_and_alias_is_exact(self):
        for tag, key in ALIASES.items():
            assert tag in EXACT
            assert key in CONFIDENCE

    def test_colon_constants_are_exact_not_prefix(self):
        # These four look parameterized but are constant strings; they must
        # resolve to their own entries, not fall through to a prefix family.
        for tag in ("ocr_finding:DENIED", "ocr_finding:REVIEW",
                    "ocr_reason:damaged_registry", "ocr_reason:visible_policy_notes"):
            assert registry_key_for(tag) == tag

    def test_unknown_tag_returns_none(self):
        assert registry_key_for("R_MADE_UP_RULE") is None

    def test_registry_has_no_orphan_keys(self):
        # Every registry key is reachable from some emitted-tag shape.
        reachable = {registry_key_for(t) for t in EXACT}
        reachable |= set(PREFIX.values())
        assert reachable == set(CONFIDENCE)

    def test_entry_shape(self):
        for key, entry in CONFIDENCE.items():
            assert isinstance(entry, RuleConf), key
            assert 0.0 < entry.value <= 0.99, key


class TestTranscriptionFidelity:
    """Values must equal the frozen sources byte-for-byte (brief Rule 1)."""

    def test_l6_values_match_v1(self):
        from v1.solution import CONFIDENCE as V1
        assert len(V1) == 15
        for key, value in V1.items():
            assert conf(key) == value, key

    def test_policy_values_match_v3_literals(self):
        # v3/policy.py:178,191 (upgrades) and :261,277,305,330 (guards)
        assert conf("R_A1_non_dip_waived_biometric_clean") == 0.80
        assert conf("R3_unpaid_biometric_waiver") == 0.80
        assert conf("ocr_only_downgrade") == 0.65
        assert conf("field_conflict") == 0.65
        assert conf("missing_required") == 0.65
        assert conf("defensive_downgrade_thin_evidence") == 0.65

    def test_evidence_values_match_v3_ocr_signal_literals(self):
        # v3/ocr_signal.py:121-161
        assert conf("ocr_finding:DENIED") == 0.95
        assert conf("ocr_finding:REVIEW") == 0.85
        assert conf("ocr_reason:damaged_registry") == 0.92
        assert conf("ocr_reason:visible_policy_notes") == 0.92
        assert conf("ocr_disq_flag") == 0.90
        assert conf("ocr_revoked_sponsor") == 0.90
        assert conf("ocr_embargo_home") == 0.90
        assert conf("ocr_deny_stem") == 0.85
        assert conf("ocr_review_flag") == 0.85
        assert conf("ocr_review_stem") == 0.65

    def test_total_entry_count(self):
        assert len(CONFIDENCE) == 31
