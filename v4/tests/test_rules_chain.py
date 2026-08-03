"""L6 rule-chain coverage: one synthetic case per rule tag.

New in v4 (P4). Each test constructs the minimal field dict that reaches
its rule and asserts the (verdict, tag) pair plus the registry-sourced
confidence. Field values are chosen from v4.vocab so vocab drift breaks
these tests loudly.
"""
from v4.confidence import conf
from v4.rules import apply_rules


def _fields(**kwargs):
    """A baseline clean-approve field dict; override per test."""
    base = {
        "visa_class": "XW-1",
        "fee_status": "paid",
        "sponsor_id": "SPN-1234",
        "home_world": "Proxima-b",
        "arrival_date": "2026-05-01",
        "risk_flags": "none",
        "_finding": "",
    }
    base.update(kwargs)
    return base


def test_adjudicator_finding():
    adj, c, tag = apply_rules(_fields(_finding="DENIED"))
    assert (adj, tag) == ("DENIED", "R_ADJUDICATOR_FINDING[DENIED]")
    assert c == conf("R_ADJUDICATOR_FINDING")


def test_r0_hard_embargo():
    adj, c, tag = apply_rules(_fields(home_world="TRAPPIST-1e"))
    assert (adj, tag) == ("DENIED", "R0_hard_embargo[TRAPPIST-1e]")
    assert c == conf("R0_hard_embargo")


def test_r1_transit7():
    adj, c, tag = apply_rules(_fields(visa_class="TRANSIT-7"))
    assert (adj, tag) == ("DENIED", "R1_transit7")
    assert c == conf("R1_transit7")


def test_r2_disqualifier():
    adj, c, tag = apply_rules(_fields(risk_flags="biohazard_red"))
    assert (adj, tag) == ("DENIED", "R2_disqualifier[biohazard_red]")
    assert c == conf("R2_disqualifier")


def test_r2_disqualifier_multi_flag_sorted():
    adj, _, tag = apply_rules(
        _fields(risk_flags="memory_tampering|active_warrant"))
    assert adj == "DENIED"
    assert tag == "R2_disqualifier[active_warrant|memory_tampering]"


def test_r4_revoked_sponsor():
    adj, c, tag = apply_rules(_fields(sponsor_id="SPN-0007"))
    assert (adj, tag) == ("DENIED", "R4_revoked_sponsor")
    assert c == conf("R4_revoked_sponsor")


def test_r4_revoked_sponsor_dip1_exempt():
    adj, _, tag = apply_rules(_fields(sponsor_id="SPN-0007", visa_class="DIP-1"))
    assert adj == "APPROVED"  # DIP-1 exempt from R4


def test_r4b_embargoed_home():
    adj, c, tag = apply_rules(_fields(home_world="Wolf-1061c"))
    assert (adj, tag) == ("DENIED", "R4b_embargoed_home")
    assert c == conf("R4b_embargoed_home")


def test_r3_unpaid():
    adj, c, tag = apply_rules(_fields(fee_status="unpaid"))
    assert (adj, tag) == ("DENIED", "R3_unpaid")
    assert c == conf("R3_unpaid")


def test_r5_stale():
    # RECEIPT_DATE_PROXY is 2026-07-12; STALE_DAYS 180 — a 2025 arrival is stale.
    adj, c, tag = apply_rules(_fields(arrival_date="2025-01-01"))
    assert (adj, tag) == ("DENIED", "R5_stale")
    assert c == conf("R5_stale")


def test_r_r1_flag_present():
    adj, c, tag = apply_rules(_fields(risk_flags="identity_conflict"))
    assert (adj, tag) == ("NEEDS_REVIEW", "R_R1_flag_present")
    assert c == conf("R_R1_flag_present")


def test_fallback_extraction_fail():
    adj, c, tag = apply_rules(_fields(visa_class="", fee_status=""))
    assert (adj, tag) == ("NEEDS_REVIEW", "FALLBACK_extraction_fail")
    assert c == conf("FALLBACK_extraction_fail")


def test_fallback_missing_arrival():
    adj, c, tag = apply_rules(_fields(arrival_date=""))
    assert (adj, tag) == ("NEEDS_REVIEW", "FALLBACK_missing_arrival")
    assert c == conf("FALLBACK_missing_arrival")


def test_r_r2_unknown_fee():
    adj, c, tag = apply_rules(_fields(fee_status="unknown"))
    assert (adj, tag) == ("NEEDS_REVIEW", "R_R2_unknown_fee")
    assert c == conf("R_R2_unknown_fee")


def test_a1_paid_clean():
    adj, c, tag = apply_rules(_fields())
    assert (adj, tag) == ("APPROVED", "R_A1_paid_clean")
    assert c == conf("R_A1_paid_clean")


def test_a1_dip1_waived():
    adj, c, tag = apply_rules(_fields(visa_class="DIP-1", fee_status="waived"))
    assert (adj, tag) == ("APPROVED", "R_A1_dip1_waived")
    assert c == conf("R_A1_dip1_waived")


def test_a1_non_dip_waived_to_review():
    adj, c, tag = apply_rules(_fields(fee_status="waived"))
    assert (adj, tag) == ("NEEDS_REVIEW", "R_A1_non_dip_waived_TO_REVIEW")
    assert c == conf("R_A1_non_dip_waived")
