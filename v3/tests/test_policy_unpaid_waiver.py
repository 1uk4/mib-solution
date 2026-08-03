"""Tests for the R3_unpaid → visible-waiver upgrade in L7.

Field Manual §Fee Rules: "unpaid: deny unless a visible waiver applies."
Symmetric to the non-DIP waived biometric upgrade — a clean Biometric
Scan Slip is treated as evidence of the visible waiver.
"""
import importlib

import pytest

import v3.policy


CLEAN_SLIP = (
    "FORM B-13: Biometric Scan Slip\n"
    "Case ID: MIB-000893\n"
    "Applicant: Zonia Miraquell\n"
    "Species Match: SIRIUS_AVIAN\n"
    "Observed flags: none\n"
    "SCAN IMAGE\n"
)


@pytest.fixture(autouse=True)
def _default_env(monkeypatch):
    monkeypatch.setenv("MIB_UPGRADE_UNPAID_ON_WAIVER", "1")
    monkeypatch.setenv("MIB_UPGRADE_WAIVED_ON_BIOMETRIC", "1")
    monkeypatch.setenv("MIB_TRUST_FINDING", "1")
    importlib.reload(v3.policy)
    yield
    importlib.reload(v3.policy)


def _bundle(text=""):
    return {
        "combined_text": text,
        "image_ocr": [],
        "any_illegibility_excluded": False,
    }


def test_upgrade_fires_on_r3_unpaid_with_clean_biometric():
    adj, _, tag = v3.policy.apply_policy(
        {}, "DENIED", 0.96, "R3_unpaid",
        _bundle(text=CLEAN_SLIP),
    )
    assert adj == "APPROVED"
    assert tag == "R3_unpaid_biometric_waiver"


def test_no_upgrade_without_biometric():
    # No biometric slip present — R3_unpaid stays DENIED.
    adj, _, tag = v3.policy.apply_policy(
        {}, "DENIED", 0.96, "R3_unpaid",
        _bundle(text="MIB Fee Receipt. Fee Status unpaid."),
    )
    assert adj == "DENIED"
    assert tag == "R3_unpaid"


def test_no_upgrade_on_other_deny_tags():
    # Regression: only R3_unpaid triggers this — R2_disqualifier and other
    # DENY rules should stay denied even if biometric slip present.
    adj, _, tag = v3.policy.apply_policy(
        {}, "DENIED", 0.99, "R2_disqualifier[biohazard_red]",
        _bundle(text=CLEAN_SLIP),
    )
    assert adj == "DENIED"
    assert tag == "R2_disqualifier[biohazard_red]"


def test_env_disable(monkeypatch):
    monkeypatch.setenv("MIB_UPGRADE_UNPAID_ON_WAIVER", "0")
    importlib.reload(v3.policy)
    adj, _, tag = v3.policy.apply_policy(
        {}, "DENIED", 0.96, "R3_unpaid",
        _bundle(text=CLEAN_SLIP),
    )
    assert adj == "DENIED"
    assert tag == "R3_unpaid"
