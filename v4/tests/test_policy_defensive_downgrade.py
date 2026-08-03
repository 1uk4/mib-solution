"""Tests for L7 defensive downgrade on thin-evidence auto-approvals.

Ported from v3/tests/test_policy_defensive_downgrade.py; env/reload
patterns replaced by Config injection.

Rule: when R_A1_paid_clean or R_A1_dip1_waived approves purely on text-
stream form data (no risk_flags extracted) and no image in the packet
triggered the illegibility filter (i.e., we have no actively-vetted image
evidence to corroborate the "no flags" claim), downgrade to REVIEW.

NOTE: defensive downgrade is DEFAULT OFF in production (see v4/config.py).
These tests inject Config(defensive_downgrade=True) to exercise the rule.
"""
from v4.config import Config
from v4.policy import apply_policy


_ENABLED = Config(defensive_downgrade=True)


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
    adj, _, tag = apply_policy(
        _fields(),
        "APPROVED", 0.94, "R_A1_paid_clean",
        _bundle(illeg=False),
        config=_ENABLED,
    )
    assert adj == "NEEDS_REVIEW"
    assert tag == "defensive_downgrade_thin_evidence:R_A1_paid_clean"


def test_downgrade_fires_on_thin_dip1_waived_approve():
    adj, _, tag = apply_policy(
        _fields(visa_class="DIP-1", fee_status="waived"),
        "APPROVED", 0.92, "R_A1_dip1_waived",
        _bundle(illeg=False),
        config=_ENABLED,
    )
    assert adj == "NEEDS_REVIEW"
    assert tag == "defensive_downgrade_thin_evidence:R_A1_dip1_waived"


def test_no_downgrade_when_risk_flags_extracted():
    # Even a "none" value with a real signal source (source_class="text")
    # means flag extraction succeeded — the packet was vetted for flags.
    adj, _, tag = apply_policy(
        _fields(risk_flags_absent=False),
        "APPROVED", 0.94, "R_A1_paid_clean",
        _bundle(illeg=False),
        config=_ENABLED,
    )
    assert adj == "APPROVED"
    assert tag == "R_A1_paid_clean"


def test_no_downgrade_when_illegibility_fired():
    # At least one image triggered the L3 illegibility filter — the packet
    # was actively vetted for image evidence quality.
    adj, _, tag = apply_policy(
        _fields(),
        "APPROVED", 0.94, "R_A1_paid_clean",
        _bundle(illeg=True),
        config=_ENABLED,
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
        adj, _, tag = apply_policy(
            _fields(),
            "APPROVED", 0.80, other_tag,
            _bundle(illeg=False),
            config=_ENABLED,
        )
        assert adj == "APPROVED", f"tag={other_tag} was downgraded"
        assert tag == other_tag


def test_default_is_off():
    # Explicit regression: with the production default (defensive_downgrade
    # False), the rule does NOT fire — a packet that would otherwise be
    # downgraded stays APPROVED.
    adj, _, tag = apply_policy(
        _fields(),
        "APPROVED", 0.94, "R_A1_paid_clean",
        _bundle(illeg=False),
    )
    assert adj == "APPROVED"
    assert tag == "R_A1_paid_clean"
