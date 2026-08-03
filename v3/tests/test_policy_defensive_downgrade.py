"""Tests for L7 defensive downgrade on thin-evidence auto-approvals.

Rule: when R_A1_paid_clean or R_A1_dip1_waived approves purely on text-
stream form data (no risk_flags extracted) and no image in the packet
triggered the illegibility filter (i.e., we have no actively-vetted image
evidence to corroborate the "no flags" claim), downgrade to REVIEW.

NOTE: defensive downgrade is DEFAULT OFF in production (see comment in
v3/policy.py explaining why). These tests explicitly enable it via
`MIB_DEFENSIVE_DOWNGRADE=1` and reload the module to exercise the rule.
Callers must use `v3.policy.apply_policy` (attribute lookup) rather than
importing `apply_policy` at module top — the reload replaces the module's
function object.
"""
import importlib

import pytest

import v3.policy


@pytest.fixture(autouse=True)
def _default_env(monkeypatch):
    # Force-enable defensive downgrade for these tests. Module-level env
    # read means we must reload the module after setting env vars.
    monkeypatch.setenv("MIB_DEFENSIVE_DOWNGRADE", "1")
    monkeypatch.setenv("MIB_TRUST_FINDING", "1")
    monkeypatch.setenv("MIB_UPGRADE_WAIVED_ON_BIOMETRIC", "1")
    importlib.reload(v3.policy)
    yield
    # Restore default (off) for downstream tests
    monkeypatch.setenv("MIB_DEFENSIVE_DOWNGRADE", "0")
    importlib.reload(v3.policy)


def _fields(risk_flags_absent=True, **kwargs):
    """Field dict with defaults sufficient to skip earlier L7 guards.

    risk_flags_absent=True mirrors production: no signal extracted →
    consolidate defaults value to "none" AND source_class to "absent".
    risk_flags_absent=False means a real "none" (or other) signal was
    extracted → source_class = "text".
    """
    base = {
        "home_world": "Earth",
        "sponsor_id": "SPN-1234",
        "visa_class": "MED-3",
        "fee_status": "paid",
        "risk_flags": "none",  # consolidate.py defaults to this either way
        "_source_class": {
            "fee_status": "text", "visa_class": "text",
            "sponsor_id": "text", "home_world": "text",
            "risk_flags": "absent" if risk_flags_absent else "text",
        },
        "_agreement": {},
    }
    base.update(kwargs)
    return base


def _bundle(illeg=False, ocr=(), text=""):
    return {
        "combined_text": text,
        "image_ocr": list(ocr),
        "any_illegibility_excluded": illeg,
    }


def test_downgrade_fires_on_thin_paid_clean_approve():
    adj, _, tag = v3.policy.apply_policy(
        _fields(),
        "APPROVED", 0.94, "R_A1_paid_clean",
        _bundle(illeg=False),
    )
    assert adj == "NEEDS_REVIEW"
    assert tag == "defensive_downgrade_thin_evidence:R_A1_paid_clean"


def test_downgrade_fires_on_thin_dip1_waived_approve():
    adj, _, tag = v3.policy.apply_policy(
        _fields(visa_class="DIP-1", fee_status="waived"),
        "APPROVED", 0.92, "R_A1_dip1_waived",
        _bundle(illeg=False),
    )
    assert adj == "NEEDS_REVIEW"
    assert tag == "defensive_downgrade_thin_evidence:R_A1_dip1_waived"


def test_no_downgrade_when_risk_flags_extracted():
    # Even a "none" value with a real signal source (source_class="text")
    # means flag extraction succeeded — the packet was vetted for flags.
    adj, _, tag = v3.policy.apply_policy(
        _fields(risk_flags_absent=False),
        "APPROVED", 0.94, "R_A1_paid_clean",
        _bundle(illeg=False),
    )
    assert adj == "APPROVED"
    assert tag == "R_A1_paid_clean"


def test_no_downgrade_when_illegibility_fired():
    # At least one image triggered the L3 illegibility filter — the packet
    # was actively vetted for image evidence quality.
    adj, _, tag = v3.policy.apply_policy(
        _fields(),
        "APPROVED", 0.94, "R_A1_paid_clean",
        _bundle(illeg=True),
    )
    assert adj == "APPROVED"
    assert tag == "R_A1_paid_clean"


def test_no_downgrade_on_untargeted_l6_tags():
    # Regression: the defensive downgrade is scoped to R_A1_paid_clean and
    # R_A1_dip1_waived only. R_A1_non_dip_waived_biometric_clean (our
    # biometric upgrade tag) and R_ADJUDICATOR_FINDING must stay approved.
    for other_tag in (
        "R_A1_non_dip_waived_biometric_clean",
        "R_ADJUDICATOR_FINDING[APPROVED]",
    ):
        adj, _, tag = v3.policy.apply_policy(
            _fields(),
            "APPROVED", 0.80, other_tag,
            _bundle(illeg=False),
        )
        assert adj == "APPROVED", f"tag={other_tag} was downgraded"
        assert tag == other_tag


def test_default_is_off(monkeypatch):
    # Explicit regression: with the production default (MIB_DEFENSIVE_DOWNGRADE
    # unset or "0"), the rule does NOT fire — a packet that would otherwise
    # be downgraded stays APPROVED.
    monkeypatch.setenv("MIB_DEFENSIVE_DOWNGRADE", "0")
    importlib.reload(v3.policy)
    adj, _, tag = v3.policy.apply_policy(
        _fields(),
        "APPROVED", 0.94, "R_A1_paid_clean",
        _bundle(illeg=False),
    )
    assert adj == "APPROVED"
    assert tag == "R_A1_paid_clean"
