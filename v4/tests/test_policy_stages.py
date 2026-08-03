"""Per-stage isolation tests for the 9 L7 policy stages (spec §8).

Each stage is exercised directly through make_ctx — one firing case and
one non-firing case — including the two default-off stages under an
injected Config. The dispatcher's sequencing is covered separately by the
ported end-to-end policy tests; these tests pin each stage's own contract.
"""
from v4.config import Config
from v4.policy import (
    BYPASSES,
    GUARDS,
    UPGRADES,
    Verdict,
    bypass_adjudicator_finding,
    guard_defensive_downgrade,
    guard_field_conflict,
    guard_missing_required,
    guard_ocr_only,
    guard_ocr_risk_override,
    make_ctx,
    upgrade_biometric_clean,
    upgrade_fallback_ocr,
    upgrade_unpaid_waiver,
)


CLEAN_SLIP = "FORM B-13: Biometric Scan Slip\nObserved flags: none\nSCAN IMAGE\n"


def _signals(text="", ocr=(), illeg=False):
    return {"combined_text": text, "image_ocr": list(ocr),
            "any_illegibility_excluded": illeg}


class TestRegistries:
    def test_stage_counts(self):
        assert len(UPGRADES) == 3
        assert len(BYPASSES) == 1
        assert len(GUARDS) == 5

    def test_guard_order_is_pinned(self):
        assert GUARDS == [guard_ocr_risk_override, guard_ocr_only,
                          guard_field_conflict, guard_missing_required,
                          guard_defensive_downgrade]

    def test_upgrade_order_is_pinned(self):
        assert UPGRADES == [upgrade_biometric_clean, upgrade_unpaid_waiver,
                            upgrade_fallback_ocr]


class TestUpgradeBiometricClean:
    def test_fires(self):
        r = upgrade_biometric_clean(make_ctx(
            adj="NEEDS_REVIEW", tag="R_A1_non_dip_waived_TO_REVIEW",
            signals=_signals(text=CLEAN_SLIP)))
        assert r == Verdict("APPROVED", 0.80, "R_A1_non_dip_waived_biometric_clean")

    def test_passes_on_wrong_tag(self):
        assert upgrade_biometric_clean(make_ctx(
            adj="NEEDS_REVIEW", tag="R_R1_flag_present",
            signals=_signals(text=CLEAN_SLIP))) is None


class TestUpgradeUnpaidWaiver:
    def test_fires_when_enabled(self):
        r = upgrade_unpaid_waiver(make_ctx(
            adj="DENIED", tag="R3_unpaid",
            signals=_signals(text=CLEAN_SLIP),
            config=Config(upgrade_unpaid_on_waiver=True)))
        assert r == Verdict("APPROVED", 0.80, "R3_unpaid_biometric_waiver")

    def test_passes_under_default_config(self):
        assert upgrade_unpaid_waiver(make_ctx(
            adj="DENIED", tag="R3_unpaid",
            signals=_signals(text=CLEAN_SLIP))) is None


class TestUpgradeFallbackOcr:
    def test_fires_on_finding(self):
        r = upgrade_fallback_ocr(make_ctx(
            adj="NEEDS_REVIEW", tag="FALLBACK_extraction_fail",
            signals=_signals(ocr=[("image_0", "Finding: DENIED")])))
        assert r == Verdict("DENIED", 0.95, "ocr_finding:DENIED")

    def test_falls_through_on_no_signal(self):
        assert upgrade_fallback_ocr(make_ctx(
            adj="NEEDS_REVIEW", tag="FALLBACK_extraction_fail",
            signals=_signals(ocr=[("image_0", "plain content")]))) is None

    def test_falls_through_on_excluded_family(self):
        # ocr_revoked_sponsor is prefix-excluded on the fallback path only.
        assert upgrade_fallback_ocr(make_ctx(
            adj="NEEDS_REVIEW", tag="FALLBACK_extraction_fail",
            signals=_signals(ocr=[("image_0", "sponsor SPN-4040 noted")]))) is None


class TestBypassAdjudicatorFinding:
    def test_fires_and_returns_verdict_unchanged(self):
        ctx = make_ctx(adj="APPROVED", conf=0.99,
                       tag="R_ADJUDICATOR_FINDING[APPROVED]")
        assert bypass_adjudicator_finding(ctx) == ctx.verdict

    def test_passes_when_disabled(self):
        assert bypass_adjudicator_finding(make_ctx(
            adj="APPROVED", conf=0.99, tag="R_ADJUDICATOR_FINDING[APPROVED]",
            config=Config(trust_finding=False))) is None


class TestGuardOcrRiskOverride:
    def test_fires_on_risk_text(self):
        r = guard_ocr_risk_override(make_ctx(
            signals=_signals(ocr=[("image_0", "biohazard_red observed")])))
        assert r is not None and r.adj == "DENIED"

    def test_passes_on_clean_text(self):
        assert guard_ocr_risk_override(make_ctx(
            signals=_signals(ocr=[("image_0", "ordinary content")]))) is None


class TestGuardOcrOnly:
    def test_fires_on_ocr_only_field(self):
        r = guard_ocr_only(make_ctx(
            fields={"_source_class": {"fee_status": "ocr_only"}}))
        assert r == Verdict("NEEDS_REVIEW", 0.65, "ocr_only_downgrade:fee_status")

    def test_passes_when_disabled(self):
        assert guard_ocr_only(make_ctx(
            fields={"_source_class": {"fee_status": "ocr_only"}},
            config=Config(ocr_only_guard=False))) is None


class TestGuardFieldConflict:
    def test_fires_on_conflict(self):
        r = guard_field_conflict(make_ctx(fields={"_agreement": {
            "sponsor_id": {"has_conflict": True, "unique_values": ["SPN-1", "SPN-2"]}}}))
        assert r == Verdict("NEEDS_REVIEW", 0.65, "field_conflict:sponsor_id(SPN-1/SPN-2)")

    def test_passes_without_conflict(self):
        assert guard_field_conflict(make_ctx(fields={"_agreement": {}})) is None


class TestGuardMissingRequired:
    def test_fires_on_missing_home_world(self):
        r = guard_missing_required(make_ctx(
            fields={"sponsor_id": "SPN-1234", "visa_class": "XW-1"}))
        assert r == Verdict("NEEDS_REVIEW", 0.65, "missing_required:home_world")

    def test_dip1_exempts_sponsor(self):
        assert guard_missing_required(make_ctx(
            fields={"home_world": "Proxima-b", "visa_class": "DIP-1"})) is None


class TestGuardDefensiveDowngrade:
    _FIELDS = {"_source_class": {"risk_flags": "absent"}}

    def test_fires_when_enabled(self):
        r = guard_defensive_downgrade(make_ctx(
            fields=dict(self._FIELDS), tag="R_A1_paid_clean",
            config=Config(defensive_downgrade=True)))
        assert r == Verdict("NEEDS_REVIEW", 0.65,
                            "defensive_downgrade_thin_evidence:R_A1_paid_clean")

    def test_passes_under_default_config(self):
        assert guard_defensive_downgrade(make_ctx(
            fields=dict(self._FIELDS), tag="R_A1_paid_clean")) is None
