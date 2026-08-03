"""Tests for the R_ADJUDICATOR_FINDING trust bypass in v3.policy.

When L6 approves via the adjudicator-finding rule, L7 safety guards must
not downgrade. The finding is Evidence Precedence #1 and empirically
100% accurate on all 162 training cases with a Finding line.
"""
import os

import pytest

from v3.policy import apply_policy


@pytest.fixture
def signals_empty():
    return {"image_ocr": [], "combined_text": "", "any_illegibility_excluded": False}


@pytest.fixture(autouse=True)
def _default_env(monkeypatch):
    monkeypatch.setenv("MIB_TRUST_FINDING", "1")


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


def test_finding_approved_bypasses_ocr_only_guard(signals_empty, monkeypatch):
    # OCR-only guard is default off; enable it explicitly to verify that
    # even when active, R_ADJUDICATOR_FINDING bypasses it. Preserves
    # regression protection if the guard is ever re-enabled.
    monkeypatch.setenv("MIB_OCR_ONLY_GUARD", "1")
    import importlib
    import v3.policy
    importlib.reload(v3.policy)
    try:
        fields = {
            "home_world": "Earth",
            "_source_class": {"visa_class": "ocr_only", "sponsor_id": "ocr_only"},
            "_agreement": {},
        }
        adj, _, tag = v3.policy.apply_policy(
            fields, "APPROVED", 0.99, "R_ADJUDICATOR_FINDING[APPROVED]",
            signals_empty,
        )
        assert adj == "APPROVED"
        assert tag == "R_ADJUDICATOR_FINDING[APPROVED]"
    finally:
        monkeypatch.setenv("MIB_OCR_ONLY_GUARD", "0")
        importlib.reload(v3.policy)


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


def test_bypass_disabled_by_env(signals_empty, monkeypatch):
    # With MIB_TRUST_FINDING=0, adjudicator-finding approvals face the
    # normal guards. This lets us A/B the change against the pre-fix
    # behavior without a rebuild.
    monkeypatch.setenv("MIB_TRUST_FINDING", "0")
    # Reload the module so the env var is re-read
    import importlib
    import v3.policy
    importlib.reload(v3.policy)

    fields = {
        "_source_class": {},
        "_agreement": {},
    }
    adj, _, tag = v3.policy.apply_policy(
        fields, "APPROVED", 0.99, "R_ADJUDICATOR_FINDING[APPROVED]",
        signals_empty,
    )
    assert adj == "NEEDS_REVIEW"
    assert tag.startswith("missing_required")

    # Restore module state so later tests see the default-on behavior
    monkeypatch.setenv("MIB_TRUST_FINDING", "1")
    importlib.reload(v3.policy)
