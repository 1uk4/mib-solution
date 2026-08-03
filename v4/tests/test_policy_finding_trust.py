"""Tests for the R_ADJUDICATOR_FINDING trust bypass in v4.policy.

Ported from v3/tests/test_policy_finding_trust.py; env/reload patterns
replaced by Config injection.

When L6 approves via the adjudicator-finding rule, L7 safety guards must
not downgrade. The finding is Evidence Precedence #1 and empirically
100% accurate on all 162 training cases with a Finding line.
"""
import pytest

from v4.config import Config
from v4.policy import apply_policy


@pytest.fixture
def signals_empty():
    return {"image_ocr": [], "combined_text": "", "any_illegibility_excluded": False}


def test_finding_approved_bypasses_missing_home_world(signals_empty):
    fields = {
        "_source_class": {},
        "_agreement": {},
        # home_world absent — would normally trigger missing-required guard
    }
    adj, conf, tag = apply_policy(
        fields, "APPROVED", 0.99, "R_ADJUDICATOR_FINDING[APPROVED]",
        signals_empty,
    )
    assert adj == "APPROVED"
    assert tag == "R_ADJUDICATOR_FINDING[APPROVED]"


def test_finding_approved_bypasses_ocr_only_guard(signals_empty):
    # Enable the OCR-only guard explicitly (it is also the default) to
    # verify that even when active, R_ADJUDICATOR_FINDING bypasses it.
    fields = {
        "home_world": "Earth",
        "_source_class": {"visa_class": "ocr_only", "sponsor_id": "ocr_only"},
        "_agreement": {},
    }
    adj, _, tag = apply_policy(
        fields, "APPROVED", 0.99, "R_ADJUDICATOR_FINDING[APPROVED]",
        signals_empty,
        config=Config(ocr_only_guard=True),
    )
    assert adj == "APPROVED"
    assert tag == "R_ADJUDICATOR_FINDING[APPROVED]"


def test_finding_approved_bypasses_conflict_guard(signals_empty):
    fields = {
        "home_world": "Earth",
        "_source_class": {},
        "_agreement": {
            "sponsor_id": {"has_conflict": True, "unique_values": ["SPN-1", "SPN-2"]},
        },
    }
    adj, _, tag = apply_policy(
        fields, "APPROVED", 0.99, "R_ADJUDICATOR_FINDING[APPROVED]",
        signals_empty,
    )
    assert adj == "APPROVED"
    assert tag == "R_ADJUDICATOR_FINDING[APPROVED]"


def test_non_finding_approve_still_hits_missing_guard(signals_empty):
    # Regression: R_A1_paid_clean approvals must still be gated by guards.
    fields = {
        # home_world missing
        "_source_class": {},
        "_agreement": {},
    }
    adj, _, tag = apply_policy(
        fields, "APPROVED", 0.94, "R_A1_paid_clean",
        signals_empty,
    )
    assert adj == "NEEDS_REVIEW"
    assert tag.startswith("missing_required")


def test_bypass_disabled_by_config(signals_empty):
    # v3 intent (MIB_TRUST_FINDING=0): adjudicator-finding approvals face
    # the normal guards. Lets us A/B the change against the pre-fix
    # behavior without a rebuild.
    fields = {
        "_source_class": {},
        "_agreement": {},
    }
    adj, _, tag = apply_policy(
        fields, "APPROVED", 0.99, "R_ADJUDICATOR_FINDING[APPROVED]",
        signals_empty,
        config=Config(trust_finding=False),
    )
    assert adj == "NEEDS_REVIEW"
    assert tag.startswith("missing_required")
