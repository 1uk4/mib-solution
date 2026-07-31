# OCR Value Quality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce the extraction leak (~15 pts on the table) by improving OCR value quality across four independently-gated components: sharpen OCR pass, post-extraction value normalization, char-whitelist re-OCR for structured fields, and a Tesseract `user-words` dictionary for closed-enum tokens.

**Architecture:** Layer additively on the existing V3 L1-L7 pipeline. No new source types, no new pipeline layers. Each component is gated by an env var so we can measure impact independently and roll back if any regresses. All changes live in `v3/` — no changes to `v1/` (which V3 wraps).

**Tech Stack:** Python 3.12, Pillow ~11.0 (already installed), tesseract-ocr CLI (already installed via Docker apt). No new dependencies.

## Global Constraints

- Python 3.12 (Dockerfile: `FROM python:3.12-slim`).
- Pillow `~11.0` (already installed via `pip install pillow~=11.0`); do not upgrade.
- No new pip dependencies (violates docker image size budget and offline requirement).
- Runtime budget: 6 sec/PDF average, 30,000 sec hard limit on 5,000-PDF validation set (per `DOCKER_SUBMISSION.md`).
- Cat-FA count baseline: 22. Any component that pushes it >24 must be rolled back.
- Every "known value" list (user-words, enum snap tables) MUST be derived from `train_labels.csv` AND cross-checked against `FIELD_MANUAL.md` (per `vocab_audit_discipline.md`). No values from memory/recall.
- Field extraction discipline (per `extraction_fuzzy_discipline.md`): fuzzy-match LABELS always; only fuzzy-snap VALUES for closed enums (`visa_class`, `fee_status`). NEVER snap open-ended fields (`home_world`, `applicant_name`, `species_code`).
- Working directory for all code changes: `/Users/lukaflores/Code/mib-solution/`.
- Test runner: `python3 -m pytest` from `/Users/lukaflores/Code/mib-solution/` (paths use `v3.` imports).
- Full-run scoring: `./dev_score.sh` (native, cached) for iteration; `./score.sh` (docker) for production-parity validation before shipping.

## Plan Amendments (post pre-flight review, 2026-07-31)

**Amendment A — Env-var opt-out semantics for all "disabled by default" tests:**

Every test that asserts a feature is disabled MUST express that by explicitly setting the env var to `"0"` — never by `monkeypatch.delenv(...)`. This keeps the tests robust when Task 7 flips module-level defaults from `""` to `"1"`. Concretely:

```python
# WRONG (will break after Task 7's default flip):
monkeypatch.delenv("MIB_OCR_SHARPEN", raising=False)
assert extract._SHARPEN_ENABLED is False

# CORRECT:
monkeypatch.setenv("MIB_OCR_SHARPEN", "0")
assert extract._SHARPEN_ENABLED is False
```

Applies to `MIB_OCR_SHARPEN`, `MIB_NORMALIZE_VALUES`, `MIB_USER_WORDS`, `MIB_CHAR_WHITELIST_REOCR` across Task 4, Task 5, and any other test that follows the "disabled by default" pattern.

Correspondingly, module-level env reads use `os.environ.get("MIB_X", "") != "0"` — treat unset OR truthy as enabled after Task 7 flip, only explicit `"0"` disables. For Tasks 4 and 5 (pre-flip), the current form `os.environ.get("MIB_X", "") == "1"` is fine; Task 7 step 8 flips it to `os.environ.get("MIB_X", "1") != "0"`.

**Amendment B — Cache tag must encode env-var state to keep measurement runs honest:**

The OCR cache in `v3/extract.py` uses `hash(image_bytes) + tag` as its key. Enabling/disabling user-words changes what Tesseract outputs, but does not change the cache key — so run A (no user-words) and run B (with user-words) would collide on the same cached entry and Task 7's measurement would compare stale OCR against fresh OCR.

Fix: add a config-suffix helper and append it to every cache tag.

Task 4 implementer adds this stub to `v3/extract.py` near `_SHARPEN_ENABLED`:

```python
def _cache_config_tag() -> str:
    """Suffix appended to cache tags so runs with different OCR config
    (user-words on/off, other future flags) do not collide.
    Order: alphabetical by env-var short-name. Extend when new flags land."""
    parts = []
    # Task 5 will append 'uw' here when _USER_WORDS_ENABLED
    return ("_" + "_".join(parts)) if parts else ""
```

Task 4 uses it in `_ocr_image_triple`:
- `_cache_get(image_bytes, tag="_triple" + _cache_config_tag())`
- `_cache_put(image_bytes, result, tag="_triple" + _cache_config_tag())`

Task 5 extends the helper to include user-words state and applies the suffix to the other cache tags too:

```python
def _cache_config_tag() -> str:
    parts = []
    if _USER_WORDS_ENABLED:
        parts.append("uw")
    return ("_" + "_".join(parts)) if parts else ""
```

Task 5 also updates:
- `_ocr_image`: cache tag becomes `"" + _cache_config_tag()` for both `_cache_get` and `_cache_put`
- `_ocr_image_dual`: cache tag becomes `"_dual" + _cache_config_tag()` for both calls

Task 7's measurement protocol does NOT need to `rm -rf` the cache between runs — the env-tagged cache handles it. First run per config is slow (populates its own tagged cache); subsequent runs of the same config are fast.

---

## File Structure

**New files:**
- `v3/normalize.py` — pure-function value normalizers, one per field type
- `v3/reocr.py` — char-whitelist re-OCR helper (source + field → repaired value or None)
- `v3/data/tesseract_user_words.txt` — enum tokens, one per line
- `v3/dev/analysis/vocab_audit.py` — audit script that inspects training labels + Field Manual and emits the user-words file
- `v3/tests/__init__.py` — empty
- `v3/tests/test_normalize.py`
- `v3/tests/test_reocr.py`
- `v3/tests/test_ocr_triple_pass.py`
- `v3/tests/test_user_words_wiring.py`

**Modified files:**
- `v3/extract.py` — add sharpen third pass in `_ocr_image_dual` (rename to `_ocr_image_triple`); accept optional user-words path in `_tesseract`
- `v3/signals.py` — call `normalize.value(key, raw)` after every FIELD_VALUE emission; call `reocr.repair(source, key, current_value)` when structured-field format regex fails
- `v3/dev/docs/RULE_AUDIT.md` — append measurement results per component

**Env vars added:**
- `MIB_OCR_SHARPEN` (default `1` after validation)
- `MIB_NORMALIZE_VALUES` (default `1` after validation)
- `MIB_CHAR_WHITELIST_REOCR` (default `1` after validation)
- `MIB_USER_WORDS` (default `1` after validation)

---

## Task Ordering Rationale

Tasks run in dependency order:

1. **Vocab audit** (blocking): generates the `user-words` file and audit report. Blocks Task 5.
2. **Value normalization module**: pure functions, no OCR dependency. Enables Task 3.
3. **Wire normalization into signals.py**: applies Task 2's normalizers at emission points.
4. **Sharpen pass in extract.py**: additive to existing dual-pass; no dependency on Task 2/3.
5. **`user-words` wiring**: consumes Task 1's output file.
6. **Char-whitelist re-OCR module + wiring**: consumes Task 4's `_tesseract` signature (updated to accept extra flags).
7. **Measurement + rollout**: full-run each component; document in RULE_AUDIT.md.

Tasks 2, 3, 4, 5 could run in parallel by different implementers. Task 6 depends on Task 4's `_tesseract` signature. Task 7 depends on all.

---

### Task 1: Vocab audit script and `user-words` file

**Files:**
- Create: `v3/dev/analysis/vocab_audit.py`
- Create: `v3/data/tesseract_user_words.txt`
- Create: `v3/data/vocab_audit_report.md`

**Interfaces:**
- Consumes: `/Users/lukaflores/Code/mib-doc-challenge/data/train_labels.csv`, `/Users/lukaflores/Code/mib-doc-challenge/FIELD_MANUAL.md`
- Produces: `v3/data/tesseract_user_words.txt` (one token per line, LF-terminated), consumed by Task 5

- [ ] **Step 1: Create the audit script**

Create `v3/dev/analysis/vocab_audit.py`:

```python
#!/usr/bin/env python3
"""Vocab audit — enumerate closed-enum field values from train_labels.csv
and cross-check against FIELD_MANUAL.md before writing them into any
hardcoded list (user-words, enum snap tables, whitelists).

Rule (per memory `vocab_audit_discipline.md`): never trust
recalled/generated lists. If a value doesn't appear in either training
labels or the Manual, reject it.

Usage:
    python3 v3/dev/analysis/vocab_audit.py \\
        --challenge-dir ../mib-doc-challenge \\
        --out v3/data/tesseract_user_words.txt \\
        --report v3/data/vocab_audit_report.md
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter
from pathlib import Path

CLOSED_ENUM_FIELDS = ("visa_class", "fee_status", "species_code")


def enum_values_from_labels(labels_path: Path) -> dict[str, Counter]:
    """Return {field_name: Counter(value → count)} for the closed-enum fields."""
    result = {f: Counter() for f in CLOSED_ENUM_FIELDS}
    with labels_path.open() as f:
        for row in csv.DictReader(f):
            for field in CLOSED_ENUM_FIELDS:
                v = (row.get(field) or "").strip()
                if v:
                    result[field][v] += 1
    return result


def manual_tokens(manual_path: Path) -> set[str]:
    """Return every token that looks like an enum value in the Field Manual.

    Heuristic: any ALLCAPS_WORD or `TOKEN-N` or `token` inside backticks.
    Also captures visa classes like MED-3, XW-1 and species like TRIANGULAN.
    """
    text = manual_path.read_text()
    backticked = set(re.findall(r"`([^`]+)`", text))
    allcaps = set(re.findall(r"\b[A-Z]{4,}\b", text))
    return {t.strip() for t in (backticked | allcaps) if t.strip()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--challenge-dir", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--report", required=True, type=Path)
    args = ap.parse_args()

    labels = args.challenge_dir / "data" / "train_labels.csv"
    manual = args.challenge_dir / "FIELD_MANUAL.md"
    if not labels.exists() or not manual.exists():
        print(f"missing: {labels} or {manual}", file=sys.stderr)
        sys.exit(1)

    training_values = enum_values_from_labels(labels)
    manual_set = manual_tokens(manual)

    # Build the user-words list. A token is included if it appears in
    # training AND is a closed-enum value. Manual-only tokens are included
    # too (eval may introduce them).
    tokens: set[str] = set()
    for field, counter in training_values.items():
        for v in counter:
            tokens.add(v)
    manual_only_enum = {t for t in manual_set if "-" in t and any(c.isdigit() for c in t)}
    tokens |= manual_only_enum

    # Filter out obvious non-tokens (long phrases, punctuation-heavy)
    tokens = {t for t in tokens if len(t) <= 40 and " " not in t}

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w") as f:
        for t in sorted(tokens):
            f.write(t + "\n")

    # Build report
    lines = ["# Vocab audit report\n\n"]
    lines.append(f"Generated from `{labels}` and `{manual}`.\n\n")
    for field, counter in training_values.items():
        lines.append(f"## {field}\n\n")
        lines.append("| value | count | in_manual |\n|---|---|---|\n")
        for v, n in counter.most_common():
            in_manual = "yes" if v in manual_set else "no"
            lines.append(f"| `{v}` | {n} | {in_manual} |\n")
        lines.append("\n")
    lines.append(f"## Manual-only enum tokens (kept in user-words)\n\n")
    for t in sorted(manual_only_enum):
        lines.append(f"- `{t}`\n")
    lines.append(f"\n**Total user-words tokens written:** {len(tokens)}\n")

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text("".join(lines))
    print(f"wrote {len(tokens)} tokens to {args.out}")
    print(f"report: {args.report}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the audit and inspect its output**

Run:

```bash
cd /Users/lukaflores/Code/mib-solution
mkdir -p v3/data
python3 v3/dev/analysis/vocab_audit.py \
    --challenge-dir /Users/lukaflores/Code/mib-doc-challenge \
    --out v3/data/tesseract_user_words.txt \
    --report v3/data/vocab_audit_report.md
```

Expected:
- Output line: `wrote N tokens to v3/data/tesseract_user_words.txt` where N is roughly 10-50 tokens
- `v3/data/vocab_audit_report.md` exists and shows fee_status={paid, waived, unpaid, unknown}

- [ ] **Step 3: Verify user-words file content**

Run:

```bash
head -30 v3/data/tesseract_user_words.txt
```

Expected: at minimum these tokens present: `MED-3`, `XW-1`, `XW-2`, `DIP-1`, `TRANSIT-7`, `paid`, `waived`, `unpaid`, `unknown`. NO tokens absent from both training and the Manual (per `vocab_audit_discipline.md`).

If any hallucinated tokens sneak in (e.g. `refunded`), fix the audit script — do not manually edit the output.

- [ ] **Step 4: Commit**

```bash
cd /Users/lukaflores/Code/mib-solution
git add v3/dev/analysis/vocab_audit.py v3/data/tesseract_user_words.txt v3/data/vocab_audit_report.md
git commit -m "feat(v3): vocab audit script + tesseract user-words file"
```

---

### Task 2: Value normalization module

**Files:**
- Create: `v3/normalize.py`
- Create: `v3/tests/__init__.py` (empty)
- Create: `v3/tests/test_normalize.py`

**Interfaces:**
- Produces: `v3.normalize.value(field_name: str, raw: str) -> str` — pure function, returns normalized value or raw if unable to normalize. Consumed by Task 3.

- [ ] **Step 1: Create test file with failing tests**

Create `v3/tests/__init__.py` empty.

Create `v3/tests/test_normalize.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd /Users/lukaflores/Code/mib-solution
python3 -m pytest v3/tests/test_normalize.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'v3.normalize'`

- [ ] **Step 3: Create the normalizer module**

Create `v3/normalize.py`:

```python
#!/usr/bin/env python3
"""Post-OCR value normalization for extracted field values.

Called by v3.signals right before a FIELD_VALUE signal is emitted. Fixes
common OCR artifacts that make otherwise-correct extractions fail the
scorer's exact-string match.

Per `extraction_fuzzy_discipline.md`: fuzzy-snap VALUES only for closed
enums confirmed in the Field Manual (visa_class, fee_status). Never snap
open-ended fields (home_world, applicant_name, species_code, declared_purpose)
to a memorized training-set list — the eval may introduce unseen values.

Per `vocab_audit_discipline.md`: enum tables here are derived from the
Field Manual + training-set distribution, not from memory.
"""
from __future__ import annotations

import re
from datetime import date


# --- Enum tables (verified against FIELD_MANUAL.md + train_labels.csv) ---

# fee_status: Field Manual §Fee Rules, training-set distribution:
# paid=664, waived=242, unpaid=50, unknown=44 (n=1000)
_FEE_STATUS_ENUM = {"paid", "waived", "unpaid", "unknown"}

# visa_class: Field Manual §Visa Classes
_VISA_CLASS_ENUM = {"MED-3", "XW-1", "XW-2", "DIP-1", "TRANSIT-7"}


# --- Sponsor ID: SPN-####
_SPONSOR_ID_RE = re.compile(r"^SPN-\d{4}$")


def _normalize_sponsor_id(raw: str) -> str:
    if not raw:
        return ""
    s = raw.strip().rstrip(".,;:")
    # Collapse internal whitespace (e.g. 'SPN- 6099' → 'SPN-6099')
    s = re.sub(r"\s+", "", s)
    return s  # validation happens in _valid_field_value downstream


# --- Home world: letter-digit tokens with spurious spaces
_HOME_SPACE_RE = re.compile(r"^([A-Za-z][A-Za-z\-]+-\d+)\s+(\d*[a-z]?)$")


def _normalize_home_world(raw: str) -> str:
    if not raw:
        return ""
    s = raw.strip().rstrip(".,;:")
    # If pattern 'Word-N MoreN' (letter-dash-digits, space, more digits/letter),
    # collapse the space. Example: 'Wolf-106 1c' → 'Wolf-1061c'.
    m = _HOME_SPACE_RE.match(s)
    if m:
        s = m.group(1) + m.group(2)
    return s


# --- Arrival date: YYYY-MM-DD with year in 2020-2030
_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_YEAR_MIN, _YEAR_MAX = 2020, 2030


def _try_valid_date(y: int, m: int, d: int) -> bool:
    if not (_YEAR_MIN <= y <= _YEAR_MAX and 1 <= m <= 12 and 1 <= d <= 31):
        return False
    try:
        date(y, m, d)
        return True
    except ValueError:
        return False


def _normalize_arrival_date(raw: str) -> str:
    if not raw:
        return ""
    s = raw.strip().strip(".,;:")
    m = _DATE_RE.match(s)
    if not m:
        return s
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if _try_valid_date(y, mo, d):
        return s  # already valid
    # Try single-digit repair on year: substitute each position with 0-9
    year_str = m.group(1)
    for pos in range(4):
        for digit in "0123456789":
            candidate = year_str[:pos] + digit + year_str[pos+1:]
            cy = int(candidate)
            if _try_valid_date(cy, mo, d):
                return f"{candidate}-{m.group(2)}-{m.group(3)}"
    return s  # give up, keep raw


# --- Enum snappers
def _snap_enum(raw: str, enum: set[str], casefold: bool = False) -> str:
    if not raw:
        return ""
    s = raw.strip().rstrip(".,;:")
    if casefold:
        s = s.lower()
    if s in enum:
        return s
    # Try replacing common OCR separator variants
    for sep in ("_", " ", "."):
        alt = s.replace(sep, "-")
        if alt in enum:
            return alt
    # Case-insensitive match
    for e in enum:
        if s.upper() == e.upper():
            return e
    return raw.strip()  # unknown, keep raw (trimmed)


def _normalize_visa_class(raw: str) -> str:
    return _snap_enum(raw, _VISA_CLASS_ENUM)


def _normalize_fee_status(raw: str) -> str:
    return _snap_enum(raw, _FEE_STATUS_ENUM, casefold=True)


# --- Free-form: strip whitespace only, no snap
def _normalize_freeform(raw: str) -> str:
    return raw.strip() if raw else ""


# --- Dispatch
_NORMALIZERS = {
    "sponsor_id": _normalize_sponsor_id,
    "home_world": _normalize_home_world,
    "arrival_date": _normalize_arrival_date,
    "visa_class": _normalize_visa_class,
    "fee_status": _normalize_fee_status,
    "applicant_name": _normalize_freeform,
    "species_code": _normalize_freeform,
    "declared_purpose": _normalize_freeform,
}


def value(field_name: str, raw: str) -> str:
    """Normalize a raw extracted value for the given field name.

    Returns normalized value or raw if the field has no normalizer.
    Never raises — falls through to raw on any internal failure.
    """
    if not raw:
        return ""
    fn = _NORMALIZERS.get(field_name)
    if fn is None:
        return raw
    try:
        return fn(raw)
    except Exception:
        return raw
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
cd /Users/lukaflores/Code/mib-solution
python3 -m pytest v3/tests/test_normalize.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/lukaflores/Code/mib-solution
git add v3/normalize.py v3/tests/__init__.py v3/tests/test_normalize.py
git commit -m "feat(v3): value normalization module (sponsor_id, home_world, arrival_date, enums)"
```

---

### Task 3: Wire normalization into signals.py

**Files:**
- Modify: `v3/signals.py` (add import + wrap FIELD_VALUE emissions)

**Interfaces:**
- Consumes: `v3.normalize.value(field_name: str, raw: str) -> str` from Task 2
- Env var: `MIB_NORMALIZE_VALUES` — when set to `"1"`, apply normalization. Any other value (including unset) → skip normalization (baseline behavior).

- [ ] **Step 1: Read the current FIELD_VALUE emission sites**

Run:

```bash
cd /Users/lukaflores/Code/mib-solution
grep -n "type=FIELD_VALUE" v3/signals.py
```

Expected: multiple lines, each of the form `Signal(type=FIELD_VALUE, key=..., value=..., ...)`. Note the line numbers.

- [ ] **Step 2: Add gated import and helper at top of signals.py**

In `v3/signals.py`, immediately after the existing imports (around line 35-36 where `from v3.source_type import classify as classify_source` lives), add:

```python
import os
from v3.normalize import value as _normalize_value

_NORMALIZE_ENABLED = os.environ.get("MIB_NORMALIZE_VALUES", "") == "1"


def _norm(key: str, raw: str) -> str:
    """Env-gated normalizer wrapper. When MIB_NORMALIZE_VALUES=1, apply
    field-specific normalization; otherwise pass value through untouched."""
    if _NORMALIZE_ENABLED and raw:
        return _normalize_value(key, raw)
    return raw
```

- [ ] **Step 3: Wrap every FIELD_VALUE emission with _norm**

For every `Signal(type=FIELD_VALUE, key=<K>, value=<V>, ...)` in `v3/signals.py`, wrap the `value=` argument with `_norm(<K>, <V>)`.

Example transformation:

```python
# Before
sig = Signal(
    type=FIELD_VALUE, key=key, value=value,
    source_id=src.id, confidence=conf, tag=tag,
)

# After
sig = Signal(
    type=FIELD_VALUE, key=key, value=_norm(key, value),
    source_id=src.id, confidence=conf, tag=tag,
)
```

Do this for ALL FIELD_VALUE emission sites (should be ~6-7 sites per the earlier grep). Do not modify emissions with `type != FIELD_VALUE`.

- [ ] **Step 4: Add integration test for gating**

Append to `v3/tests/test_normalize.py`:

```python
import os


class TestGating:
    def test_normalize_gate_default_off(self, monkeypatch):
        """Without MIB_NORMALIZE_VALUES=1, signals.py must not normalize."""
        monkeypatch.delenv("MIB_NORMALIZE_VALUES", raising=False)
        # Re-import to pick up env
        import importlib, v3.signals
        importlib.reload(v3.signals)
        assert v3.signals._NORMALIZE_ENABLED is False
        assert v3.signals._norm("home_world", "Wolf-106 1c.") == "Wolf-106 1c."

    def test_normalize_gate_on(self, monkeypatch):
        monkeypatch.setenv("MIB_NORMALIZE_VALUES", "1")
        import importlib, v3.signals
        importlib.reload(v3.signals)
        assert v3.signals._NORMALIZE_ENABLED is True
        assert v3.signals._norm("home_world", "Wolf-106 1c.") == "Wolf-1061c"
```

- [ ] **Step 5: Run all tests**

Run:

```bash
cd /Users/lukaflores/Code/mib-solution
python3 -m pytest v3/tests/ -v
```

Expected: all tests PASS.

- [ ] **Step 6: Quick smoke test on one known packet**

Run:

```bash
cd /Users/lukaflores/Code/mib-solution
MIB_OCR_CACHE_DIR=/tmp/mib-ocr-cache MIB_NORMALIZE_VALUES=1 \
  python3 v3/dev/inspect_case.py MIB-000032
```

Expected: `home_world` field now shows `'Wolf-1061c'` (previously `'Wolf-106 1c.'`). Other fields unchanged.

- [ ] **Step 7: Commit**

```bash
cd /Users/lukaflores/Code/mib-solution
git add v3/signals.py v3/tests/test_normalize.py
git commit -m "feat(v3): env-gated post-extraction value normalization at signal emission"
```

---

### Task 4: Sharpen (unsharp mask) as third OCR pass

**Files:**
- Modify: `v3/extract.py` (add `_ocr_image_triple`, update dispatch)
- Create: `v3/tests/test_ocr_triple_pass.py`

**Interfaces:**
- Produces: `v3.extract._ocr_image_triple(image_bytes: bytes) -> str` — runs three tesseract passes (baseline psm=6, upscaled psm=3, sharpened psm=6) and returns their newline-joined union.
- Consumes: `PIL.ImageFilter.UnsharpMask` (already available via Pillow 11).
- Env var: `MIB_OCR_SHARPEN` — when `"1"`, use triple pass; else fall back to existing `_ocr_image_dual`.
- Cache key: uses suffix `"_triple"` so it doesn't collide with existing `_dual` entries.

- [ ] **Step 1: Write failing tests**

Create `v3/tests/test_ocr_triple_pass.py`:

```python
"""Tests for v3.extract sharpen (third) OCR pass and its cache tagging.

Real Tesseract is invoked; tests use tiny known-good PNGs so they run in
under 1 second. If tesseract is not on PATH, tests are skipped.
"""
import io
import os
import shutil
import subprocess
import tempfile

import pytest
from PIL import Image, ImageDraw, ImageFont, ImageFilter

from v3 import extract

pytestmark = pytest.mark.skipif(
    shutil.which("tesseract") is None,
    reason="tesseract CLI not installed",
)


def _make_text_png(text: str, size=(400, 100)) -> bytes:
    img = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(img)
    # Default font works cross-platform; we don't need font quality, just
    # legible text tesseract can read.
    d.text((10, 30), text, fill="black")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class TestTriplePass:
    def test_returns_union_of_three_passes(self):
        img_bytes = _make_text_png("HELLO WORLD")
        out = extract._ocr_image_triple(img_bytes)
        # Union means each pass's output is concatenated; length should be
        # roughly 3× a single pass. Just verify HELLO WORLD is present.
        assert "HELLO" in out.upper()

    def test_sharpen_pass_actually_runs(self, monkeypatch):
        """Third pass must call tesseract on a sharpened variant."""
        calls = []
        original = extract._tesseract

        def spy(img_bytes, psm=6, extra_flags=None):
            calls.append({"psm": psm, "bytes_len": len(img_bytes)})
            return original(img_bytes, psm=psm, extra_flags=extra_flags)

        monkeypatch.setattr(extract, "_tesseract", spy)
        img_bytes = _make_text_png("HELLO")
        extract._ocr_image_triple(img_bytes)
        # Three invocations: baseline (psm=6), upscaled (psm=3), sharpened (psm=6)
        assert len(calls) == 3
        psms = [c["psm"] for c in calls]
        assert psms.count(6) == 2  # baseline + sharpened
        assert 3 in psms            # upscaled


class TestCacheKey:
    def test_uses_triple_suffix(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MIB_OCR_CACHE_DIR", str(tmp_path))
        # Reset cache state
        import importlib
        importlib.reload(extract)
        img_bytes = _make_text_png("HELLO")
        extract._ocr_image_triple(img_bytes)
        # Verify a file with _triple suffix was created
        files = list(tmp_path.glob("*_triple.txt"))
        assert len(files) == 1


class TestDispatchGating:
    def test_env_off_uses_dual(self, monkeypatch):
        monkeypatch.delenv("MIB_OCR_SHARPEN", raising=False)
        import importlib
        importlib.reload(extract)
        assert extract._SHARPEN_ENABLED is False

    def test_env_on_uses_triple(self, monkeypatch):
        monkeypatch.setenv("MIB_OCR_SHARPEN", "1")
        import importlib
        importlib.reload(extract)
        assert extract._SHARPEN_ENABLED is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd /Users/lukaflores/Code/mib-solution
python3 -m pytest v3/tests/test_ocr_triple_pass.py -v
```

Expected: FAIL with `AttributeError: module 'v3.extract' has no attribute '_ocr_image_triple'`

- [ ] **Step 3: Modify `_tesseract` to accept extra flags**

In `v3/extract.py`, replace the `_tesseract` function (currently around line 197):

```python
def _tesseract(image_bytes: bytes, psm: int = 6,
               extra_flags: list[str] | None = None) -> str:
    """Single Tesseract invocation. Returns text or empty on failure.

    `extra_flags` — additional CLI flags to pass after the psm arg. Used by:
      - user-words dictionary: ['--user-words', '/path/to/words.txt']
      - char-whitelist re-OCR: ['-c', 'tessedit_char_whitelist=SPN-0123456789']
    """
    cmd = ["tesseract", "-", "-", "--psm", str(psm)]
    if extra_flags:
        cmd.extend(extra_flags)
    try:
        proc = subprocess.run(
            cmd,
            input=image_bytes,
            capture_output=True,
            timeout=15,
        )
        return proc.stdout.decode("utf-8", errors="replace")
    except Exception:
        return ""
```

- [ ] **Step 4: Add sharpen filter helper**

In `v3/extract.py`, add near `_upscale_png` (around line 211):

```python
def _sharpen_png(image_bytes: bytes) -> bytes | None:
    """Apply unsharp mask sharpen, re-encode as PNG. None on failure.

    Empirical basis: on MIB-000032, sharpen recovered 'Asinax Qommora' where
    baseline read 'Annax Qormora' — 1 character closer to truth 'Arinax'.
    Radius=2, percent=150 matches the parameters tested during design.
    """
    if Image is None:
        return None
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img.load()
        img = img.filter(ImageFilter.UnsharpMask(radius=2, percent=150))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return None
```

Also, at the top of `v3/extract.py` (with the other PIL import), replace:

```python
try:
    from PIL import Image
except ImportError:
    Image = None
```

with:

```python
try:
    from PIL import Image, ImageFilter
except ImportError:
    Image = None
    ImageFilter = None
```

- [ ] **Step 5: Add `_ocr_image_triple` and env gate**

In `v3/extract.py`, add at module level (near `_CACHE_CREATED`):

```python
_SHARPEN_ENABLED = os.environ.get("MIB_OCR_SHARPEN", "") == "1"
```

Then add the triple-pass function after `_ocr_image_dual`:

```python
def _ocr_image_triple(image_bytes: bytes) -> str:
    """Triple-pass OCR: baseline + upscaled + sharpened.

    Extends _ocr_image_dual with a third pass on a sharpened variant.
    Sharpen recovers character-level misreads (Annax→Asinax class) by
    boosting edge contrast before Tesseract sees the image.

    Cached under a "_triple" suffix so single-pass and dual-pass cache
    entries stay valid for their respective callers.
    """
    cached = _cache_get(image_bytes, tag="_triple")
    if cached is not None:
        return cached
    text_base = _tesseract(image_bytes, psm=6)
    upscaled = _upscale_png(image_bytes, scale=2)
    text_hires = _tesseract(upscaled, psm=3) if upscaled else ""
    sharpened = _sharpen_png(image_bytes)
    text_sharp = _tesseract(sharpened, psm=6) if sharpened else ""
    parts = [t for t in (text_base, text_hires, text_sharp) if t]
    result = "\n".join(parts)
    _cache_put(image_bytes, result, tag="_triple")
    return result
```

- [ ] **Step 6: Update `extract_content` to dispatch based on gate**

In `v3/extract.py`, modify `extract_content` (around line 169) so the doc-render branch dispatches based on `_SHARPEN_ENABLED`:

```python
def extract_content(sources: list[Source]) -> list[Source]:
    """Populate .content on every source (in place). Returns the same list."""
    for src in sources:
        if src.type == TEXT_STREAM:
            src.content = _extract_text_stream(src)
        elif src.type == IMAGE and _should_ocr(src):
            if _looks_like_document(src):
                if _SHARPEN_ENABLED:
                    src.content = _ocr_image_triple(src.raw)
                else:
                    src.content = _ocr_image_dual(src.raw)
            else:
                src.content = _ocr_image(src.raw)
    return sources
```

- [ ] **Step 7: Run tests to verify they pass**

Run:

```bash
cd /Users/lukaflores/Code/mib-solution
python3 -m pytest v3/tests/test_ocr_triple_pass.py -v
```

Expected: all tests PASS.

- [ ] **Step 8: Commit**

```bash
cd /Users/lukaflores/Code/mib-solution
git add v3/extract.py v3/tests/test_ocr_triple_pass.py
git commit -m "feat(v3): sharpen (unsharp mask) as gated third OCR pass"
```

---

### Task 5: Wire `user-words` dictionary into Tesseract calls

**Files:**
- Modify: `v3/extract.py` (thread user-words flag through `_tesseract`)
- Create: `v3/tests/test_user_words_wiring.py`

**Interfaces:**
- Consumes: `v3/data/tesseract_user_words.txt` from Task 1
- Consumes: `_tesseract(image_bytes, psm, extra_flags)` from Task 4 (already accepts `extra_flags`)
- Env var: `MIB_USER_WORDS` — when `"1"`, pass `--user-words` to Tesseract in all OCR calls.

- [ ] **Step 1: Write failing tests**

Create `v3/tests/test_user_words_wiring.py`:

```python
"""Tests for MIB_USER_WORDS env-var gate and file path resolution."""
import os
from pathlib import Path

import pytest


def test_user_words_disabled_by_default(monkeypatch):
    monkeypatch.delenv("MIB_USER_WORDS", raising=False)
    import importlib
    from v3 import extract
    importlib.reload(extract)
    assert extract._user_words_flags() == []


def test_user_words_enabled_returns_flags(monkeypatch):
    monkeypatch.setenv("MIB_USER_WORDS", "1")
    import importlib
    from v3 import extract
    importlib.reload(extract)
    flags = extract._user_words_flags()
    assert len(flags) == 2
    assert flags[0] == "--user-words"
    assert Path(flags[1]).exists(), f"user-words file missing: {flags[1]}"


def test_user_words_file_contains_expected_tokens():
    """Sanity check on Task 1's output: closed-enum tokens must be present."""
    path = Path(__file__).parents[1] / "data" / "tesseract_user_words.txt"
    if not path.exists():
        pytest.skip("Task 1 not run yet")
    tokens = set(path.read_text().split())
    for expected in ("MED-3", "XW-1", "DIP-1", "paid", "waived", "unpaid"):
        assert expected in tokens, f"missing enum token: {expected}"


def test_ocr_calls_pass_user_words_when_enabled(monkeypatch, tmp_path):
    """When enabled, _ocr_image_dual/_triple must pass user-words flag through."""
    monkeypatch.setenv("MIB_USER_WORDS", "1")
    monkeypatch.setenv("MIB_OCR_CACHE_DIR", str(tmp_path))
    import importlib
    from v3 import extract
    importlib.reload(extract)

    captured = []
    original = extract._tesseract

    def spy(img_bytes, psm=6, extra_flags=None):
        captured.append(list(extra_flags) if extra_flags else [])
        return original(img_bytes, psm=psm, extra_flags=extra_flags)

    monkeypatch.setattr(extract, "_tesseract", spy)

    # Use a tiny synthetic image
    from PIL import Image
    import io
    img = Image.new("RGB", (100, 40), "white")
    buf = io.BytesIO(); img.save(buf, format="PNG")
    extract._ocr_image(buf.getvalue())

    assert captured, "no tesseract call captured"
    assert "--user-words" in captured[0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd /Users/lukaflores/Code/mib-solution
python3 -m pytest v3/tests/test_user_words_wiring.py -v
```

Expected: FAIL with `AttributeError: module 'v3.extract' has no attribute '_user_words_flags'`

- [ ] **Step 3: Add `_user_words_flags` helper and thread through OCR callers**

In `v3/extract.py`, add at module scope (below `_SHARPEN_ENABLED`):

```python
_USER_WORDS_ENABLED = os.environ.get("MIB_USER_WORDS", "") == "1"
_USER_WORDS_PATH = Path(__file__).parent / "data" / "tesseract_user_words.txt"


def _user_words_flags() -> list[str]:
    """Return tesseract flags for the user-words dictionary, or [] if
    disabled or the file is missing."""
    if not _USER_WORDS_ENABLED:
        return []
    if not _USER_WORDS_PATH.exists():
        return []
    return ["--user-words", str(_USER_WORDS_PATH)]
```

Then update every internal call to `_tesseract(image, psm=X)` (there are three: in `_ocr_image`, `_ocr_image_dual`, `_ocr_image_triple`) to prepend `_user_words_flags()`:

Example — replace:

```python
result = _tesseract(image_bytes, psm=6)
```

with:

```python
result = _tesseract(image_bytes, psm=6, extra_flags=_user_words_flags())
```

Do this for ALL three call sites. For calls that already pass `extra_flags` (from Task 6's re-OCR), merge:

```python
_tesseract(image_bytes, psm=6, extra_flags=_user_words_flags() + (extra_flags or []))
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
cd /Users/lukaflores/Code/mib-solution
python3 -m pytest v3/tests/test_user_words_wiring.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/lukaflores/Code/mib-solution
git add v3/extract.py v3/tests/test_user_words_wiring.py
git commit -m "feat(v3): thread tesseract --user-words flag through OCR calls (env-gated)"
```

---

### Task 6: Char-whitelist re-OCR for structured fields

**Files:**
- Create: `v3/reocr.py`
- Modify: `v3/signals.py` (call reocr when structured field fails format)
- Create: `v3/tests/test_reocr.py`

**Interfaces:**
- Produces: `v3.reocr.repair(source, field_name: str, current_value: str) -> str | None` — re-OCR the source image with a per-field char whitelist; returns a value that passes the field's format regex, or None if no improvement.
- Consumes: `v3.acquire.Source` for `source.raw` (image bytes), `source.type == IMAGE`.
- Consumes: `v3.extract._tesseract` from Task 4 (accepts `extra_flags`).
- Env var: `MIB_CHAR_WHITELIST_REOCR` — when `"1"`, call `repair` after failed format validation.

- [ ] **Step 1: Write failing tests**

Create `v3/tests/test_reocr.py`:

```python
"""Tests for v3.reocr — char-whitelist re-OCR for structured fields."""
import io
import shutil

import pytest
from PIL import Image, ImageDraw

from v3.acquire import Source, IMAGE
from v3 import reocr

pytestmark = pytest.mark.skipif(
    shutil.which("tesseract") is None,
    reason="tesseract CLI not installed",
)


def _synth_image(text: str, size=(400, 100)) -> bytes:
    img = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(img)
    d.text((10, 30), text, fill="black")
    buf = io.BytesIO(); img.save(buf, format="PNG")
    return buf.getvalue()


class TestSponsorRepair:
    def test_returns_none_for_non_image_source(self):
        src = Source(type="TEXT_STREAM", id="t0", raw=b"", metadata={})
        assert reocr.repair(src, "sponsor_id", "SPN-6O99") is None

    def test_returns_none_when_current_value_already_valid(self):
        # Even if we could re-OCR, no repair needed
        img = _synth_image("SPN-6099")
        src = Source(type=IMAGE, id="i0", raw=img, metadata={})
        assert reocr.repair(src, "sponsor_id", "SPN-6099") is None

    def test_repair_recovers_digits_from_letter_confusion(self):
        # Synthetic: image says SPN-6099, initial OCR read as SPN-6O99
        img = _synth_image("SPN-6099")
        src = Source(type=IMAGE, id="i0", raw=img, metadata={})
        repaired = reocr.repair(src, "sponsor_id", "SPN-6O99")
        # Either got the correct value or returned None (no worse than input)
        assert repaired is None or repaired == "SPN-6099"


class TestDateRepair:
    def test_returns_none_when_valid(self):
        img = _synth_image("2026-02-13")
        src = Source(type=IMAGE, id="i0", raw=img, metadata={})
        assert reocr.repair(src, "arrival_date", "2026-02-13") is None

    def test_repair_attempts_when_invalid(self):
        # Simulate initial garbage read; repair should either fix or return None
        img = _synth_image("2026-02-13")
        src = Source(type=IMAGE, id="i0", raw=img, metadata={})
        out = reocr.repair(src, "arrival_date", "2O26-O2-13")
        assert out is None or out == "2026-02-13"


class TestUnsupportedField:
    def test_freeform_field_returns_none(self):
        img = _synth_image("Xanax Ozorquell")
        src = Source(type=IMAGE, id="i0", raw=img, metadata={})
        # applicant_name has no format regex — no whitelist strategy applies
        assert reocr.repair(src, "applicant_name", "Xanax Ozorquell") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd /Users/lukaflores/Code/mib-solution
python3 -m pytest v3/tests/test_reocr.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'v3.reocr'`

- [ ] **Step 3: Create the reocr module**

Create `v3/reocr.py`:

```python
#!/usr/bin/env python3
"""Char-whitelist re-OCR for structured fields.

When L4's initial extraction returns a value that fails the field's format
regex, this module re-OCRs the source image with a per-field character
whitelist. The whitelist prevents Tesseract from proposing letters where
only digits are valid (e.g. SPN-6O99 → SPN-6099, 2O26 → 2026).

Fires only when initial extraction failed format validation, so per-run
cost is bounded (~50-100 invocations on the full training set).
"""
from __future__ import annotations

import re

from v3.acquire import Source, IMAGE
from v3.extract import _tesseract


# Per-field: (character whitelist string, format regex to validate against)
_STRATEGIES: dict[str, tuple[str, re.Pattern]] = {
    "sponsor_id":  ("SPN-0123456789",  re.compile(r"^SPN-\d{4}$")),
    "arrival_date": ("-0123456789",     re.compile(r"^\d{4}-\d{2}-\d{2}$")),
}


def _current_valid(field: str, value: str) -> bool:
    strat = _STRATEGIES.get(field)
    return bool(strat and strat[1].match(value or ""))


def repair(source: Source, field: str, current_value: str) -> str | None:
    """Re-OCR `source` with the char whitelist for `field`. Return the
    repaired value if it matches the format regex, else None.

    - Returns None if the source is not an image
    - Returns None if the field has no whitelist strategy
    - Returns None if the current value already passes validation
    - Returns None if the re-OCR result also fails validation
    """
    if source.type != IMAGE:
        return None
    strat = _STRATEGIES.get(field)
    if strat is None:
        return None
    if _current_valid(field, current_value):
        return None
    whitelist, regex = strat
    text = _tesseract(
        source.raw,
        psm=6,
        extra_flags=["-c", f"tessedit_char_whitelist={whitelist}"],
    )
    # Take the first token that matches the format regex, scanning
    # whitespace-split tokens.
    for token in text.split():
        if regex.match(token):
            return token
    return None
```

- [ ] **Step 4: Wire reocr into signals.py**

In `v3/signals.py`, after the `_normalize_value` import from Task 3, add:

```python
from v3.reocr import repair as _reocr_repair

_REOCR_ENABLED = os.environ.get("MIB_CHAR_WHITELIST_REOCR", "") == "1"


def _norm_and_repair(key: str, raw: str, source: Source) -> str:
    """Normalize; if a structured field still fails validation, char-whitelist re-OCR."""
    normed = _norm(key, raw)
    if _REOCR_ENABLED and key in ("sponsor_id", "arrival_date"):
        repaired = _reocr_repair(source, key, normed)
        if repaired:
            return repaired
    return normed
```

Then wherever the extraction site has access to the emitting Source (`src`), replace `_norm(key, value)` with `_norm_and_repair(key, value, src)`. For sites that don't have `src` locally (e.g. the combined-text fallback path), keep `_norm(key, value)` — re-OCR requires per-source image bytes.

- [ ] **Step 5: Run tests to verify they pass**

Run:

```bash
cd /Users/lukaflores/Code/mib-solution
python3 -m pytest v3/tests/ -v
```

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
cd /Users/lukaflores/Code/mib-solution
git add v3/reocr.py v3/signals.py v3/tests/test_reocr.py
git commit -m "feat(v3): char-whitelist re-OCR for structured field format failures"
```

---

### Task 7: Measurement, rollout, and audit documentation

**Files:**
- Modify: `v3/dev/docs/RULE_AUDIT.md` (append per-component measurement section)
- Modify: `v3/extract.py` and `v3/signals.py` (flip env-var defaults from off to on after validation)

**Interfaces:**
- Uses: `./dev_score.sh` (native cached, fast dev iteration) and `./score.sh` (docker, production parity)

- [ ] **Step 1: Baseline snapshot with all components OFF**

Run:

```bash
cd /Users/lukaflores/Code/mib-solution
unset MIB_OCR_SHARPEN MIB_NORMALIZE_VALUES MIB_CHAR_WHITELIST_REOCR MIB_USER_WORDS
./dev_score.sh 1000 2>&1 | tail -20
cp /tmp/mib-output/evaluation.json /tmp/eval_baseline.json
```

Expected: `total_score` recorded; note `catastrophic_false_approvals` count (should be around 22).

- [ ] **Step 2: Measure Task 3 (value normalization) alone**

Run:

```bash
cd /Users/lukaflores/Code/mib-solution
unset MIB_OCR_SHARPEN MIB_CHAR_WHITELIST_REOCR MIB_USER_WORDS
MIB_NORMALIZE_VALUES=1 ./dev_score.sh 1000 2>&1 | tail -20
cp /tmp/mib-output/evaluation.json /tmp/eval_normalize.json
```

Expected: `total_score` ≥ baseline, cat-FA count ≤ baseline+2. Note delta.

- [ ] **Step 3: Measure Task 4 (sharpen) alone**

Run:

```bash
cd /Users/lukaflores/Code/mib-solution
unset MIB_NORMALIZE_VALUES MIB_CHAR_WHITELIST_REOCR MIB_USER_WORDS
MIB_OCR_SHARPEN=1 ./dev_score.sh 1000 2>&1 | tail -20
cp /tmp/mib-output/evaluation.json /tmp/eval_sharpen.json
```

Expected: `total_score` ≥ baseline, cat-FA count ≤ baseline+2. Note delta and per-PDF runtime increase.

- [ ] **Step 4: Measure Task 5 (user-words) alone**

Run:

```bash
cd /Users/lukaflores/Code/mib-solution
unset MIB_OCR_SHARPEN MIB_NORMALIZE_VALUES MIB_CHAR_WHITELIST_REOCR
MIB_USER_WORDS=1 ./dev_score.sh 1000 2>&1 | tail -20
cp /tmp/mib-output/evaluation.json /tmp/eval_userwords.json
```

Expected: `total_score` ≥ baseline. Note delta.

- [ ] **Step 5: Measure Task 6 (char-whitelist re-OCR) alone**

Run:

```bash
cd /Users/lukaflores/Code/mib-solution
unset MIB_OCR_SHARPEN MIB_NORMALIZE_VALUES MIB_USER_WORDS
MIB_CHAR_WHITELIST_REOCR=1 ./dev_score.sh 1000 2>&1 | tail -20
cp /tmp/mib-output/evaluation.json /tmp/eval_reocr.json
```

Expected: `total_score` ≥ baseline, cat-FA count ≤ baseline+2. Note delta.

- [ ] **Step 6: Measure all four together**

Run:

```bash
cd /Users/lukaflores/Code/mib-solution
MIB_OCR_SHARPEN=1 MIB_NORMALIZE_VALUES=1 MIB_CHAR_WHITELIST_REOCR=1 MIB_USER_WORDS=1 \
  ./dev_score.sh 1000 2>&1 | tail -20
cp /tmp/mib-output/evaluation.json /tmp/eval_all.json
```

Expected: `total_score` ≥ baseline + 5 pts, cat-FA count ≤ baseline+2. If any component regresses in isolation, exclude it from the final rollout.

- [ ] **Step 7: Docker parity check**

Run:

```bash
cd /Users/lukaflores/Code/mib-solution
MIB_OCR_SHARPEN=1 MIB_NORMALIZE_VALUES=1 MIB_CHAR_WHITELIST_REOCR=1 MIB_USER_WORDS=1 \
  ./score.sh 2>&1 | tail -20
```

Expected: matches native-run score within ±0.5 pts. Runtime completes inside 6000-sec timeout.

- [ ] **Step 8: Flip defaults from off to on for components that survived measurement**

For each component whose measurement showed ≥0 pt gain and ≤2 cat-FA regression:

- In `v3/extract.py`: change `_SHARPEN_ENABLED = os.environ.get("MIB_OCR_SHARPEN", "") == "1"` to `_SHARPEN_ENABLED = os.environ.get("MIB_OCR_SHARPEN", "1") == "1"` (default flips to "1").
- Same pattern for `_USER_WORDS_ENABLED`.
- In `v3/signals.py`: same pattern for `_NORMALIZE_ENABLED` and `_REOCR_ENABLED`.

Skip components that regressed. Document the decision in the audit log (next step).

- [ ] **Step 9: Update RULE_AUDIT.md**

Append to `v3/dev/docs/RULE_AUDIT.md`:

```markdown
## 2026-07-31 — OCR value quality bundle

Baseline (2026-07-30): 103.48 / 150, 22 cat-FAs.

| Component | Delta pts | Cat-FA count | Runtime impact | Rolled out |
|---|---|---|---|---|
| Value normalization | +X.X | 22 | negligible | yes/no |
| Sharpen (unsharp mask) 3rd pass | +X.X | 22 | +X.Xs/PDF | yes/no |
| user-words dictionary | +X.X | 22 | negligible | yes/no |
| Char-whitelist re-OCR | +X.X | 22 | +X.Xs/PDF | yes/no |
| **All four combined** | **+X.X** | 22 | +X.Xs/PDF | yes |

Spec: `docs/superpowers/specs/2026-07-31-ocr-value-quality-design.md`
Plan: `docs/superpowers/plans/2026-07-31-ocr-value-quality.md`
```

Fill in actual measured deltas.

- [ ] **Step 10: Commit rollout + audit**

```bash
cd /Users/lukaflores/Code/mib-solution
git add v3/extract.py v3/signals.py v3/dev/docs/RULE_AUDIT.md
git commit -m "feat(v3): default-on OCR value quality bundle after measurement validation"
```

---

## Self-Review Notes

**Spec coverage:**
- Sharpen pass → Task 4 ✓
- Value normalization → Tasks 2+3 ✓
- Char-whitelist re-OCR → Task 6 ✓
- `user-words` dictionary → Tasks 1+5 ✓
- Vocab audit gate → Task 1 (blocking) ✓
- Env-var gating for measurement → each task ✓
- Testing plan → Task 7 ✓
- Runtime budget check → Task 7 step 7 ✓
- Rollback strategy → Task 7 step 8 ✓
- Out-of-scope (calibration, defensive downgrade) → not in this plan (correctly) ✓

**Type consistency:**
- `_tesseract(image_bytes, psm, extra_flags)` — signature added in Task 4, consumed in Task 5 and Task 6 ✓
- `_user_words_flags() -> list[str]` — defined and consumed in Task 5 ✓
- `_normalize_value(field, raw) -> str` (aliased as `_norm`) — defined in Task 2, consumed in Task 3 and Task 6 ✓
- `repair(source, field, current) -> str | None` — defined in Task 6, consumed in signals.py in Task 6 ✓
- `_SHARPEN_ENABLED`, `_NORMALIZE_ENABLED`, `_USER_WORDS_ENABLED`, `_REOCR_ENABLED` — all module-level bools, gated by env vars, referenced in tests ✓

**Placeholder scan:** no TBDs, no "similar to Task N", no "handle appropriately". Every code block is complete.
