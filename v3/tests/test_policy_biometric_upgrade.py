"""Tests for the R_A1_non_dip_waived → APPROVED upgrade on clean biometric.

When L6 defensively reviews a non-DIP waived case, presence of a Biometric
Scan Slip with cleanly-read "Observed flags: none" is treated as the
"visible hardship waiver" evidence the Field Manual contemplates, and the
decision is promoted to APPROVED.
"""
import pytest

from v3.policy import apply_policy


CLEAN_SLIP_TEXT = (
    "FORM B-13: Biometric Scan Slip\n"
    "Case ID: MIB-000109\n"
    "Species Match: SIRIUS_AVIAN\n"
    "Biometric confidence: 90%\n"
    "Observed flags: none\n"
    "SCAN IMAGE\n"
)


@pytest.fixture(autouse=True)
def _default_env(monkeypatch):
    monkeypatch.setenv("MIB_UPGRADE_WAIVED_ON_BIOMETRIC", "1")
    monkeypatch.setenv("MIB_TRUST_FINDING", "1")


def _bundle(text="", ocr=(), illeg=False):
    return {
        "combined_text": text,
        "image_ocr": list(ocr),
        "any_illegibility_excluded": illeg,
    }


def test_upgrade_fires_on_clean_slip_in_text_stream():
    adj, conf, tag = apply_policy(
        {}, "NEEDS_REVIEW", 0.70, "R_A1_non_dip_waived_TO_REVIEW",
        _bundle(text=CLEAN_SLIP_TEXT),
    )
    assert adj == "APPROVED"
    assert tag == "R_A1_non_dip_waived_biometric_clean"


def test_upgrade_fires_on_clean_slip_in_image_ocr():
    adj, _, tag = apply_policy(
        {}, "NEEDS_REVIEW", 0.70, "R_A1_non_dip_waived_TO_REVIEW",
        _bundle(ocr=[("image_0", CLEAN_SLIP_TEXT)]),
    )
    assert adj == "APPROVED"
    assert tag == "R_A1_non_dip_waived_biometric_clean"


def test_no_upgrade_when_ocr_noise_in_flags_value():
    # "none go" — MIB-000865 style. Must NOT upgrade.
    noisy = "Observed flags: none go SCAN IMAGE"
    adj, _, tag = apply_policy(
        {}, "NEEDS_REVIEW", 0.70, "R_A1_non_dip_waived_TO_REVIEW",
        _bundle(text=noisy),
    )
    assert adj == "NEEDS_REVIEW"
    assert tag == "R_A1_non_dip_waived_TO_REVIEW"


def test_no_upgrade_when_mixed_clean_and_noisy_readings():
    # OCR duplication produces multiple biometric-slip fragments. If ANY
    # reading is corrupt, we can't tell which is authoritative — don't
    # treat the slip as evidence. MIB-000865-style: one clean "none",
    # one "none GO".
    mixed = (
        "Observed flags: none\n"
        "FORM B-13: Biometric Scan Slip\n"
        "Observed flags: none GO\n"
    )
    adj, _, tag = apply_policy(
        {}, "NEEDS_REVIEW", 0.70, "R_A1_non_dip_waived_TO_REVIEW",
        _bundle(text=mixed),
    )
    assert adj == "NEEDS_REVIEW"
    assert tag == "R_A1_non_dip_waived_TO_REVIEW"


def test_upgrade_fires_when_all_multiple_reads_clean():
    # Multiple slip fragments that ALL read cleanly → upgrade.
    text = (
        "Observed flags: none\n"
        "FORM B-13: Biometric Scan Slip\n"
        "Observed flags: none\n"
    )
    adj, _, tag = apply_policy(
        {}, "NEEDS_REVIEW", 0.70, "R_A1_non_dip_waived_TO_REVIEW",
        _bundle(text=text),
    )
    assert adj == "APPROVED"
    assert tag == "R_A1_non_dip_waived_biometric_clean"


def test_no_upgrade_when_no_biometric_slip():
    adj, _, tag = apply_policy(
        {}, "NEEDS_REVIEW", 0.70, "R_A1_non_dip_waived_TO_REVIEW",
        _bundle(text="MIB Fee Receipt Fee Status waived Waiver Code DIP-WAIVER"),
    )
    assert adj == "NEEDS_REVIEW"
    assert tag == "R_A1_non_dip_waived_TO_REVIEW"


def test_no_upgrade_on_other_review_tags():
    # Regression: upgrade only fires on the specific defensive-waived tag.
    adj, _, tag = apply_policy(
        {}, "NEEDS_REVIEW", 0.65, "R_R1_flag_present",
        _bundle(text=CLEAN_SLIP_TEXT),
    )
    assert adj == "NEEDS_REVIEW"
    assert tag == "R_R1_flag_present"


def test_upgrade_disabled_by_env(monkeypatch):
    monkeypatch.setenv("MIB_UPGRADE_WAIVED_ON_BIOMETRIC", "0")
    import importlib
    import v3.policy
    importlib.reload(v3.policy)

    adj, _, tag = v3.policy.apply_policy(
        {}, "NEEDS_REVIEW", 0.70, "R_A1_non_dip_waived_TO_REVIEW",
        _bundle(text=CLEAN_SLIP_TEXT),
    )
    assert adj == "NEEDS_REVIEW"
    assert tag == "R_A1_non_dip_waived_TO_REVIEW"

    # Restore for other tests
    monkeypatch.setenv("MIB_UPGRADE_WAIVED_ON_BIOMETRIC", "1")
    importlib.reload(v3.policy)
