"""Tests for the FALLBACK_extraction_fail → OCR-signal upgrade in L7.

When L6 falls through (visa or fee unextractable) but image OCR still
contains explicit denial/review signals, promote the verdict per the same
evaluator used as L7 guard #1 for approvals. Whitelist-filter out signal
families whose precision requires positive field extraction (revoked
sponsor codes, which appear in reference lists too).
"""
import importlib

import pytest

import v3.policy


@pytest.fixture(autouse=True)
def _default_env(monkeypatch):
    monkeypatch.setenv("MIB_FALLBACK_OCR_UPGRADE", "1")
    monkeypatch.setenv("MIB_TRUST_FINDING", "1")
    monkeypatch.setenv("MIB_UPGRADE_WAIVED_ON_BIOMETRIC", "1")
    monkeypatch.setenv("MIB_DEFENSIVE_DOWNGRADE", "0")
    importlib.reload(v3.policy)
    yield
    importlib.reload(v3.policy)


def _bundle(ocr_text=""):
    return {
        "combined_text": "",
        "image_ocr": [("image_0", ocr_text)],
        "any_illegibility_excluded": False,
    }


def test_upgrade_on_finding_denied():
    ocr = "MIB Adjudicator Note\nFinding: DENIED\nReason: administrative issue"
    adj, _, tag = v3.policy.apply_policy(
        {}, "NEEDS_REVIEW", 0.41, "FALLBACK_extraction_fail",
        _bundle(ocr_text=ocr),
    )
    assert adj == "DENIED"
    assert tag == "ocr_finding:DENIED"


def test_upgrade_on_finding_review():
    ocr = "Finding: NEEDS_REVIEW — pending secondary check"
    adj, _, tag = v3.policy.apply_policy(
        {}, "NEEDS_REVIEW", 0.41, "FALLBACK_extraction_fail",
        _bundle(ocr_text=ocr),
    )
    assert adj == "NEEDS_REVIEW"
    assert tag == "ocr_finding:REVIEW"


def test_upgrade_on_disqualifying_flag():
    ocr = "Observed flags: biohazard_red present in scan"
    adj, _, tag = v3.policy.apply_policy(
        {}, "NEEDS_REVIEW", 0.41, "FALLBACK_extraction_fail",
        _bundle(ocr_text=ocr),
    )
    assert adj == "DENIED"
    assert "biohazard_red" in tag


def test_revoked_sponsor_signal_is_filtered():
    # ocr_revoked_sponsor variant must NOT upgrade the verdict in fallback
    # context — sponsor identity can't be verified without extraction.
    # Should stay at fallback REVIEW.
    ocr = "Reference: sponsor SPN-4040 has been revoked"
    adj, conf, tag = v3.policy.apply_policy(
        {}, "NEEDS_REVIEW", 0.41, "FALLBACK_extraction_fail",
        _bundle(ocr_text=ocr),
    )
    assert adj == "NEEDS_REVIEW"
    assert tag == "FALLBACK_extraction_fail"
    assert conf == 0.41


def test_no_ocr_signal_keeps_fallback():
    adj, conf, tag = v3.policy.apply_policy(
        {}, "NEEDS_REVIEW", 0.41, "FALLBACK_extraction_fail",
        _bundle(ocr_text="Ordinary form content with no risk terms"),
    )
    assert adj == "NEEDS_REVIEW"
    assert tag == "FALLBACK_extraction_fail"
    assert conf == 0.41


def test_upgrade_does_not_fire_on_other_l6_tags():
    # Regression: only FALLBACK_extraction_fail triggers this branch.
    # A REVIEW-verdict rule like R_R1_flag_present must NOT be re-evaluated.
    ocr = "Finding: DENIED\nReason: administrative"
    adj, _, tag = v3.policy.apply_policy(
        {}, "NEEDS_REVIEW", 0.96, "R_R1_flag_present",
        _bundle(ocr_text=ocr),
    )
    assert adj == "NEEDS_REVIEW"
    assert tag == "R_R1_flag_present"


def test_env_disable(monkeypatch):
    monkeypatch.setenv("MIB_FALLBACK_OCR_UPGRADE", "0")
    importlib.reload(v3.policy)
    ocr = "Finding: DENIED"
    adj, _, tag = v3.policy.apply_policy(
        {}, "NEEDS_REVIEW", 0.41, "FALLBACK_extraction_fail",
        _bundle(ocr_text=ocr),
    )
    assert adj == "NEEDS_REVIEW"
    assert tag == "FALLBACK_extraction_fail"
