"""Tests for v3.normalize — per-field value normalization for OCR output.

Rules under test:
  sponsor_id: strip whitespace + punctuation, validate SPN-\\d{4}
  home_world: collapse spurious internal space in letter-digit tokens
  arrival_date: validate YYYY-MM-DD, digit-repair year if outside 2020-2030
  visa_class:  snap to closed enum
  fee_status:  snap to closed enum
  free-form:   strip surrounding whitespace only, no snap
"""
from v3.normalize import value


class TestSponsorId:
    def test_clean_value_unchanged(self):
        assert value("sponsor_id", "SPN-1234") == "SPN-1234"

    def test_strips_spurious_internal_space(self):
        assert value("sponsor_id", "SPN- 6099") == "SPN-6099"

    def test_strips_trailing_period(self):
        assert value("sponsor_id", "SPN-1234.") == "SPN-1234"

    def test_invalid_format_kept_raw(self):
        # 3 digits — not valid but we don't repair
        assert value("sponsor_id", "SPN-999") == "SPN-999"

    def test_empty(self):
        assert value("sponsor_id", "") == ""


class TestHomeWorld:
    def test_clean_value_unchanged(self):
        assert value("home_world", "Wolf-1061c") == "Wolf-1061c"

    def test_collapse_spurious_space_in_letter_digit_token(self):
        assert value("home_world", "Wolf-106 1c.") == "Wolf-1061c"

    def test_multiword_planet_name_untouched(self):
        # Legitimate multi-word: no pattern match, no collapse
        assert value("home_world", "Alpha Centauri") == "Alpha Centauri"

    def test_strips_trailing_punctuation(self):
        assert value("home_world", "Kepler-186f;") == "Kepler-186f"

    def test_empty(self):
        assert value("home_world", "") == ""


class TestArrivalDate:
    def test_clean_value_unchanged(self):
        assert value("arrival_date", "2026-02-13") == "2026-02-13"

    def test_strips_surrounding_punctuation(self):
        assert value("arrival_date", ".2026-02-13,") == "2026-02-13"

    def test_year_repair_from_2526_to_2026(self):
        # OCR read '0' as '5' in year — substitute back
        assert value("arrival_date", "2526-02-13") == "2026-02-13"

    def test_year_repair_from_2026_leading_wrong_digit(self):
        # 2626 → try candidates that give year in range → 2026
        assert value("arrival_date", "2626-02-13") == "2026-02-13"

    def test_year_repair_declines_when_no_valid_substitution(self):
        # 1926 → not repairable to anything in 2020-2030
        assert value("arrival_date", "1926-02-13") == "1926-02-13"

    def test_invalid_format_kept_raw(self):
        assert value("arrival_date", "not a date") == "not a date"

    def test_empty(self):
        assert value("arrival_date", "") == ""


class TestVisaClass:
    def test_clean_value_unchanged(self):
        assert value("visa_class", "MED-3") == "MED-3"

    def test_snap_ocr_variant(self):
        # OCR read 'MED-3' as 'MED_3' or 'MED 3' — snap to enum
        assert value("visa_class", "MED_3") == "MED-3"

    def test_snap_case_variant(self):
        assert value("visa_class", "med-3") == "MED-3"

    def test_unknown_value_kept_raw(self):
        assert value("visa_class", "TOTALLY-BOGUS") == "TOTALLY-BOGUS"


class TestFeeStatus:
    def test_clean_value_unchanged(self):
        assert value("fee_status", "paid") == "paid"

    def test_case_normalized(self):
        assert value("fee_status", "PAID") == "paid"

    def test_snap_ocr_variant(self):
        # Common OCR error: 'waived' read as 'walved' or 'waived.'
        assert value("fee_status", "waived.") == "waived"

    def test_unknown_value_kept_raw(self):
        assert value("fee_status", "reimbursed") == "reimbursed"


class TestFreeForm:
    def test_applicant_name_no_snap(self):
        # Should NOT snap to a training-set name enum
        assert value("applicant_name", "Xanax Ozorquell") == "Xanax Ozorquell"

    def test_strips_surrounding_whitespace(self):
        assert value("applicant_name", "  Xanax Ozorquell  ") == "Xanax Ozorquell"

    def test_species_code_no_snap(self):
        # species_code is open-ended per fuzzy discipline; only Tesseract
        # user-words biases, we do NOT snap to memorized set
        assert value("species_code", "NEWSPECIESX") == "NEWSPECIESX"

    def test_declared_purpose_no_snap(self):
        assert value("declared_purpose", "xenobotany") == "xenobotany"
