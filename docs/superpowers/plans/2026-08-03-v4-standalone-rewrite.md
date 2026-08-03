# V4 Standalone Rewrite — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `v4/` — a standalone pipeline importing nothing from v1/v2/v3 — that reproduces the committed golden outputs byte-for-byte.

**Architecture:** Seven layers (L1–L7) plus four foundation modules (config, confidence, vocab, patterns), per `docs/superpowers/specs/2026-08-03-v4-standalone-rewrite-design.md`. This plan executes the spec; the spec is the design authority.

**Tech Stack:** Python 3.12 stdlib + Pillow (optional) + tesseract subprocess. No new dependencies.

## Global Constraints

- **Bucket 1 only.** Zero behavior change. The only permitted transformations during transcription: (a) import-path rewrites, (b) env-var reads → `Config` injection, (c) confidence literals → registry lookups, (d) the spec §6 bucket-1 fixes (drop dead symbols, drop `FLAG_DECLARATION`, correct the stale `policy.py:229`-class comments). Everything else is transcribed **verbatim, including comments**.
- **All confidence values preserved exactly** (brief Rule 1). All flag defaults preserved exactly (brief Rule 2).
- **Cache tags byte-identical**: `"_uw"`, `"_dual_uw"`, `"_triple_uw"` under default config (brief Rule 3).
- **Output schema untouched** (brief Rule 6). Emission order: sorted case_id, `json.dumps(..., sort_keys=True)`.
- **Parity gates compare like against like**: native run ↔ `golden/native-92eb104-seed42-n1000.jsonl`; docker run ↔ `golden/docker-92eb104-n1000.jsonl`.
- Full-set scoring is always `./dev_score.sh 1000` — the bare command samples 100.
- v1/v2/v3 are frozen: **read-only** throughout (exception: none). Test files under `v3/tests/` are *copied* to `v4/tests/`, originals untouched.
- Every v4 module ships with a does/does-not docstring before its milestone closes.

## Sequencing delta vs spec §9 (documented, deliberate)

Spec §9 places `config.py` at milestone 4 and `confidence.py` at milestone 5 — after the modules that consume them. That ordering forces transcribing L2/L4 with env-var reads and then migrating them, doubling transcription risk, and the milestone 2–3 gates ("ported tests green") already require Config injection to exist. **This plan builds all four foundation modules first (P1).** Every later module is written config-native and registry-native on first transcription. Gates are unchanged; spec milestones map as: P1 = M1+M4+M5, P2 = M2, P3 = M3, P4 = M6, P5 = M7, P6 = M8.

## Public-surface decision (refines spec §5, C6 dissolved)

`apply_policy` keeps v3's exact public signature `apply_policy(fields, adj, conf, tag, signals) -> Verdict`, building `PolicyContext` internally. Consequence: the 30 policy tests port **mechanically** (imports only); `make_ctx` is needed only by the new per-stage tests. `Verdict = NamedTuple("Verdict", adj, conf, tag)` stays unpack-compatible.

---

### Task P1: Foundation — skeleton, config, vocab, patterns, confidence

**Files:**
- Create: `v4/__init__.py` (empty), `v4/config.py`, `v4/vocab.py`, `v4/patterns.py`, `v4/confidence.py`
- Create: `v4/tests/__init__.py` (empty), `v4/tests/test_config.py`, `v4/tests/test_confidence_registry.py`
- Create: `parity.sh` (repo root); Modify: `dev_score.sh:110` (symlink bug)

**Interfaces produced (later tasks rely on these exact names):**
- `v4.config.Config` (frozen dataclass), `v4.config.CONFIG` (module singleton)
- `v4.vocab`: `SPECIES`, `HOME_WORLDS`, `VISA_CLASSES`, `FEE_VALUES`, `DISQUALIFYING_FLAGS`, `REVIEW_ONLY_FLAGS`, `ALL_FLAGS`, `REVOKED_SPONSORS`, `EMBARGOED_HOMES_SOFT`, `EMBARGOED_HOMES_HARD`, `RECEIPT_DATE_PROXY`, `STALE_DAYS`
- `v4.patterns`: `STREAM_RE`, `FILTER_RE`, `TEXT_OP_RE`, `INJECTION_MARKERS`, `SPONSOR_RE`, `ISO_DATE_RE`, `VISA_LABEL_RE`, `NAME_LABEL_RE`, `REGISTRY_NAME_RE`, `SPONSOR_ATTESTS_RE`, `PURPOSE_LABEL_RE`, `SPONSOR_PURPOSE_RE`, `FEE_LABEL_RE`, `AMOUNT_LABEL_RE`, `WAIVER_CODE_RE`, `FLAG_LABEL_RE`, `PLACEHOLDER_RE`, `FINDING_RE`, `CORRECTION_RE`, `DECISION_DENY_STEMS`, `DECISION_REVIEW_STEMS`, `DIAGNOSTIC_PROBES`
- `v4.confidence`: `RuleConf` (NamedTuple: value, fires, accuracy, note), `CONFIDENCE: dict[str, RuleConf]`, `conf(key) -> float`, `EMITTED_TAG_FAMILIES` (the §4.2 matching table: exact set, prefix tuple, alias map)

- [ ] **Step 1: `v4/config.py`** — exactly this shape:

```python
"""Feature configuration — the ONLY place v4 behavior flags live.

DOES: define every feature flag with its measured justification; read the
single surviving env var (MIB_OCR_CACHE_DIR) in from_env().
DOES NOT: gate anything itself — consumers receive a Config and decide.
History: v3 had 11 env vars across 3 files; 10 were retired to literals
because no production path ever set them (no ENV in Dockerfile, no export
in run.sh). See spec §4.1.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    # --- L2 OCR pipeline ---
    ocr_sharpen: bool = True          # +10.27 pts, 1000-PDF sweep 2026-07-31
    user_words: bool = True           # measured OCR quality gain; cache tag "uw"
    # --- L4 signal emission ---
    normalize_values: bool = True     # post-extraction value normalization
    reocr_char_whitelist: bool = True # structured-field format repair
    # --- L7 policy ---
    trust_finding: bool = True        # evidence precedence #1; 162 cases, 100%
    upgrade_waived_on_biometric: bool = True   # 6/22 A-truth, 0/6 D-truth, 0 catFA
    upgrade_unpaid_on_waiver: bool = False     # measured -0.22 pts, +10 catFA
    fallback_ocr_upgrade: bool = True          # +14 D->D, 0 catFA introduced
    ocr_only_guard: bool = True                # disabling cost +10 catFA
    defensive_downgrade: bool = False          # measured -2.45 pts
    # --- Dev infrastructure (not a behavior flag) ---
    ocr_cache_dir: str | None = None  # MIB_OCR_CACHE_DIR; None = cache off

    @classmethod
    def from_env(cls) -> "Config":
        cache = os.environ.get("MIB_OCR_CACHE_DIR", "").strip() or None
        return cls(ocr_cache_dir=cache)


CONFIG = Config.from_env()
```

- [ ] **Step 2: `v4/tests/test_config.py`** — assert: 8 bool fields default True (name them), 2 default False, `ocr_cache_dir is None` by default; `dataclasses.FrozenInstanceError` on assignment; `from_env` honors `MIB_OCR_CACHE_DIR` set/unset/whitespace (monkeypatch is fine here — `from_env` is call-time, no reload needed).
- [ ] **Step 3: run** `python3 -m pytest v4/tests/test_config.py -q` → PASS.
- [ ] **Step 4: `v4/vocab.py`** — transcribe `v1/solution.py:48-88` verbatim (all 12 names in Interfaces). Keep `[DOC]`/`[INFERRED-STRONG]` provenance comments; add per-enum provenance header (train_labels.csv column / Field Manual §). `ALL_FLAGS = DISQUALIFYING_FLAGS | REVIEW_ONLY_FLAGS` exactly as `v1:70`. `RECEIPT_DATE_PROXY = date(2026, 7, 12)`.
- [ ] **Step 5: `v4/patterns.py`** — transcribe from `v1/solution.py`: PDF regexes `:99-110`; `INJECTION_MARKERS :155-158`; field regexes `:220-289` **minus** `CASE_ID_RE`/`VISA_RE` (dead, spec §6); `FINDING_RE :444`, `CORRECTION_RE :452`. Add OCR word lists with the §4.4 do-not-merge comment: `DECISION_DENY_STEMS = ("BIOHAZ", "EMBARG", "DENIE")`, `DECISION_REVIEW_STEMS = ("REVOK", "RESCIN")` (from `v3/ocr_signal.py:82-83`), `DIAGNOSTIC_PROBES` (from `v3/dev/analysis/extract_features.py:45-49`, dev-only consumer, kept for the bucket-2 tooling port).
- [ ] **Step 6: `v4/confidence.py`** — all 31 entries: the 15 from `v1/solution.py:552-568` (values AND fire/accuracy comments become `RuleConf` fields), 6 policy values (`R_A1_non_dip_waived_biometric_clean` 0.80, `R3_unpaid_biometric_waiver` 0.80, `ocr_only_downgrade` 0.65, `field_conflict` 0.65, `missing_required` 0.65, `defensive_downgrade_thin_evidence` 0.65), 10 evidence values (`ocr_finding:DENIED` 0.95, `ocr_finding:REVIEW` 0.85, `ocr_reason:damaged_registry` 0.92, `ocr_reason:visible_policy_notes` 0.92, `ocr_disq_flag` 0.90, `ocr_revoked_sponsor` 0.90, `ocr_embargo_home` 0.90, `ocr_deny_stem` 0.85, `ocr_review_flag` 0.85, `ocr_review_stem` 0.65). `conf(key)` = `CONFIDENCE[key].value` (KeyError = registry bug, deliberate). `EMITTED_TAG_FAMILIES` per spec §4.2: `EXACT` frozenset (12 plain L6 tags + 2 policy exact + 4 colon-constant evidence tags + 2 upgrade tags), `PREFIX` tuple (the 13 families), `ALIASES = {"R_A1_non_dip_waived_TO_REVIEW": "R_A1_non_dip_waived"}`.
- [ ] **Step 7: `v4/tests/test_confidence_registry.py`** — static half of the completeness test: every `EXACT` tag and every `PREFIX` family base resolves through `conf()` (via alias map where applicable); all 15 v1 values match `v1.solution.CONFIDENCE` (imported *in the test only* — tests may reference v3/v1 as oracle; production v4 may not); spot-check the 10 evidence values against `v3/ocr_signal.py` literals.
- [ ] **Step 8: `parity.sh`** (repo root, executable):

```bash
#!/usr/bin/env bash
# Byte-parity gate for the v4 rewrite. Usage:
#   ./parity.sh native  [predictions.jsonl]   (default: newest /tmp/mib-dev-runs run)
#   ./parity.sh docker  [predictions.jsonl]   (default: /tmp/mib-output/predictions.jsonl)
set -euo pipefail
MODE="${1:?usage: parity.sh native|docker [predictions.jsonl]}"
case "$MODE" in
  native) GOLDEN="golden/native-92eb104-seed42-n1000.jsonl"
          DEFAULT="$(ls -td /tmp/mib-dev-runs/*/ 2>/dev/null | head -1)predictions.jsonl" ;;
  docker) GOLDEN="golden/docker-92eb104-n1000.jsonl"
          DEFAULT="/tmp/mib-output/predictions.jsonl" ;;
  *) echo "unknown mode: $MODE" >&2; exit 2 ;;
esac
PRED="${2:-$DEFAULT}"
[ -f "$PRED" ] || { echo "predictions not found: $PRED" >&2; exit 2; }
if cmp -s "$PRED" "$GOLDEN"; then
  echo "PARITY OK ($MODE): $PRED == $GOLDEN"
else
  echo "PARITY FAIL ($MODE): $PRED vs $GOLDEN" >&2
  diff <(sort "$PRED") <(sort "$GOLDEN") | head -20 >&2
  echo "...differing rows: $(diff <(sort "$PRED") <(sort "$GOLDEN") | grep -c '^<' || true)" >&2
  exit 1
fi
```

- [ ] **Step 9: fix `dev_score.sh:110`** — replace `ln -sfn "$OUTPUT_DIR" /tmp/mib-dev-output` with `rm -rf /tmp/mib-dev-output && ln -sfn "$OUTPUT_DIR" /tmp/mib-dev-output`.
- [ ] **Step 10: gate + commit** — `python3 -m pytest v4/tests -q` all green; `python3 -c "import v4.config, v4.vocab, v4.patterns, v4.confidence"` clean; commit `feat(v4): foundation modules — config, vocab, patterns, confidence registry`.

---

### Task P2: L1–L3 — acquire, extract, filters + user-words data

**Files:**
- Create: `v4/acquire.py`, `v4/extract.py`, `v4/filters/__init__.py`, `v4/filters/injection.py`, `v4/filters/redaction.py`, `v4/filters/illegibility.py`, `v4/data/tesseract_user_words.txt` (byte-copy of `v3/data/tesseract_user_words.txt`)
- Create: `v4/tests/test_ocr_triple_pass.py`, `v4/tests/test_user_words_wiring.py` (ported from v3)

**Interfaces:**
- Consumes: `v4.config.Config/CONFIG`, `v4.patterns` (PDF regexes, `INJECTION_MARKERS`), `v4.vocab` (illegibility vocabulary)
- Produces: `Source` dataclass + `TEXT_STREAM`/`IMAGE` constants + `acquire_sources(pdf_path)` (acquire); `extract_content(sources, config=CONFIG)`, `_tesseract`, `_user_words_flags(config=CONFIG)`, `_cache_config_tag(config=CONFIG)` (extract); `apply_filters(sources)` (filters)

- [ ] **Step 1: `v4/acquire.py`** — transcribe `v3/acquire.py` whole; only change: `from v1.solution import STREAM_RE, FILTER_RE` → `from v4.patterns import STREAM_RE, FILTER_RE`.
- [ ] **Step 2: `v4/extract.py`** — transcribe `v3/extract.py` whole with these transformations only: imports (`v1.solution` → `v4.patterns` for `TEXT_OP_RE`; keep `_decode_stream`/`_pdf_unescape` — they move INTO this module verbatim from `v1/solution.py:113-149`; `v3.acquire` → `v4.acquire`); the three module-level env reads (`:51,:56,:59`) become config parameters: `_user_words_flags(config)`, `_cache_config_tag(config)`, and `extract_content(sources, config=CONFIG)` threading `config` to `_ocr_image*`. Cache functions take `config` for `ocr_cache_dir`. `_USER_WORDS_PATH` resolves `Path(__file__).parent / "data" / "tesseract_user_words.txt"`.
- [ ] **Step 3: `v4/filters/`** — transcribe all four v3 filter files; only import rewrites (`v1.solution` → `v4.patterns` / `v4.vocab`, `v3.acquire` → `v4.acquire`).
- [ ] **Step 4: port tests.** Copy `test_ocr_triple_pass.py`, `test_user_words_wiring.py` → `v4/tests/`; rewrite env/reload patterns as Config injection (e.g. `_SHARPEN_ENABLED` assertions become behavior tests: `extract_content` with `Config(ocr_sharpen=False)` calls `_ocr_image_dual`, with `True` calls `_ocr_image_triple` — spy on `_tesseract` as the v3 tests already do). Cache-tag pins: `_cache_config_tag(CONFIG) == "_uw"`, `_cache_config_tag(Config(user_words=False)) == ""`, triple-pass cache file glob `*_triple_uw.txt`.
- [ ] **Step 5: user-words guard test** (in `test_user_words_wiring.py`): default config → `_user_words_flags(CONFIG)` non-empty and the referenced file exists.
- [ ] **Step 6: gate + commit** — `python3 -m pytest v4/tests -q` green; commit `feat(v4): L1-L3 — acquire, extract, filters, user-words data`.

---

### Task P3: L4–L5 — signals, consolidate + helpers + evidence

**Files:**
- Create: `v4/normalize.py`, `v4/reocr.py`, `v4/source_type.py`, `v4/signals.py`, `v4/consolidate.py`, `v4/evidence.py`
- Create: `v4/tests/test_normalize.py`, `v4/tests/test_reocr.py` (ported)

**Interfaces:**
- Produces: `extract_signals(sources, config=CONFIG) -> dict` (bundle keys: `signals`, `image_ocr`, `combined_text`, `any_illegibility_excluded`); `consolidate(bundle, case_id) -> dict`; `evidence.evaluate_ocr_signal(ocr_text) -> Verdict | None`; `evidence.has_clean_biometric(signals) -> bool`; `signals.fuzzy_flag_pattern(flag)`; `extract_fields(text, case_id)` lives in `v4/signals.py`

- [ ] **Step 1: `v4/normalize.py`, `v4/source_type.py`** — transcribe verbatim; import rewrites only.
- [ ] **Step 2: `v4/reocr.py`** — transcribe; `from v4.extract import _tesseract`.
- [ ] **Step 3: `v4/signals.py`** — transcribe `v3/signals.py` whole, PLUS absorb v1's extraction chain verbatim (`extract_fields` `v1:491-519` and its helpers `_reject_placeholder`, `_first_match`, `_find_vocab`, `_extract_flags`, `_extract_purpose`, `_extract_applicant_name`, `_extract_fee`, `_extract_visa`, `_extract_finding`, `_apply_corrections`, `_filter_injection` from `v1:161-175` — its only live caller is `extract_fields`' text path… **verify against v3**: `v3/signals.py` calls `extract_fields`, which does NOT call `_filter_injection`; transcribe exactly what the v3 call graph reaches, nothing more). Transformations: env reads `:42-43` → `config` params (`extract_signals(sources, config=CONFIG)` threads to `_norm`/`_norm_and_repair`); **drop `FLAG_DECLARATION`** (bucket 1); regex/vocab imports from `v4.patterns`/`v4.vocab`.
- [ ] **Step 4: `v4/consolidate.py`** — transcribe; drop the `FLAG_DECLARATION` import; everything else verbatim.
- [ ] **Step 5: `v4/evidence.py`** — merge `v3/ocr_signal.py` (whole) + the biometric-slip reader from `v3/policy.py:98-156` (`_OBS_VALUE_RX`, `_is_clean_hit`, `_all_biometric_reads_clean`, `_has_clean_biometric` — public name `has_clean_biometric`). Confidence literals → `conf("ocr_finding:DENIED")` etc. Stems from `v4.patterns.DECISION_*`. Returns `Verdict` (define `Verdict` in `v4/evidence.py`? No — define in `v4/policy/context.py` at P5. Until then evidence returns plain tuples; v3 shape). Keep plain 3-tuples through P4; `Verdict` lands at P5 (NamedTuple is unpack-compatible, so evidence switching to `Verdict` at P5 is invisible).
- [ ] **Step 6: port `test_normalize.py`, `test_reocr.py`** — Config-injection rewrites for `_NORMALIZE_ENABLED`/`_REOCR_ENABLED` gate tests (assert behavior via `extract_signals`/`_norm(..., config=...)`, not module attributes).
- [ ] **Step 7: gate + commit** — pytest green; commit `feat(v4): L4-L5 — signals (incl. field extraction), consolidate, evidence`.

---

### Task P4: L6 + verbatim L7 + driver — first golden diff

**Files:**
- Create: `v4/rules.py`, `v4/policy.py` (verbatim port, single file — split happens at P5), `v4/solution.py`
- Create: `v4/tests/test_policy_biometric_upgrade.py`, `test_policy_defensive_downgrade.py`, `test_policy_fallback_ocr_upgrade.py`, `test_policy_finding_trust.py`, `test_policy_unpaid_waiver.py` (ported)

**Interfaces:**
- Produces: `rules.apply_rules(fields) -> (adj, conf, tag)` (absorbs v1 `adjudicate` `v1:571-662` + `_flag_set` + `_is_stale`, `CONFIDENCE[...]` → `conf(...)`); `policy.apply_policy(fields, adj, conf, tag, signals)` — v3 signature exactly; `solution.predict_case(pdf_path)`, `solution.main(input_dir, output_path)` (transcribe `v3/solution.py` whole: same progress logging, same crash fallback using `conf("FALLBACK_extraction_fail")`, same `_default_field` absorbed from `v1:669-681`, `VERSION = "v4_standalone"`... **NO** — `VERSION` string appears only in a stderr log line and usage message; verify nothing output-bearing changes. stderr is not part of predictions.jsonl; safe).
- Env reads in policy (5) → `config` param with default `CONFIG`.
- Bucket-1 comment fix: rewrite the stale block at `v3/policy.py:229-252` to state the guard is ON by default with the measured basis (keep the history note).

- [ ] **Step 1: `v4/rules.py`** — transcribe; gate: unit-test spot checks (adjudicate on synthetic field dicts hits every rule tag — write `v4/tests/test_rules_chain.py` with one case per L6 tag, 15 asserts, values from reading the rule conditions).
- [ ] **Step 2: `v4/policy.py`** — verbatim port with the four permitted transformations only.
- [ ] **Step 3: `v4/solution.py`** — driver; `python3 -m v4.solution <in> <out>` works (guard the relative import: use absolute `from v4.x import ...` so both `python3 v4/solution.py` via path-hack and `-m` work — copy v3's pattern exactly, v3 relies on repo-root cwd; keep that).
- [ ] **Step 4: port the 5 policy test files** — mechanical (imports + `monkeypatch.setenv` → `Config(...)` passed to `apply_policy(config=...)`).
- [ ] **Step 5: pytest green** (all ported + new; expect ~79-equivalent coverage + new config/registry/rules tests).
- [ ] **Step 6: GOLDEN GATE (native).** Run v4 end-to-end over the staged 1000-PDF sample with warm cache; then `./parity.sh native <out>`. **Coordinate with user** (their terminal, ~3.5 min):
  `export MIB_OCR_CACHE_DIR=~/.cache/mib-ocr && python3 v4/solution.py /tmp/mib-dev/pdfs /tmp/v4-parity/predictions.jsonl && ./parity.sh native /tmp/v4-parity/predictions.jsonl`
  (If `/tmp/mib-dev/pdfs` is stale/missing, re-stage via `./dev_score.sh 1000` first — it rebuilds the sample deterministically at seed 42.)
  Expected: `PARITY OK`. Any diff → fix before proceeding; the diff's case_ids point at the defective layer (compare per-field vs golden row).
- [ ] **Step 7: commit** `feat(v4): L6 rules + verbatim L7 + driver — native golden parity`.

---

### Task P5: Split L7 into stages — second golden diff

**Files:**
- Create: `v4/policy/__init__.py` (dispatcher + public `apply_policy` wrapper), `v4/policy/context.py` (`Verdict` NamedTuple, `PolicyContext` frozen dataclass, `make_ctx` factory), `v4/policy/upgrades.py` (3), `v4/policy/bypasses.py` (1), `v4/policy/guards.py` (5)
- Delete: `v4/policy.py` (the single-file port it replaces)
- Create: `v4/tests/test_policy_stages.py` (9 per-stage isolation tests via `make_ctx`)

**Interfaces:**
- `PolicyContext`: fields, adj, conf, tag, signals, config; `.verdict` property → `Verdict(adj, conf, tag)`
- Dispatcher exactly as spec §5, including: upgrades before the `adj != "APPROVED"` gate; fallback-OCR stage returns `None` on fall-through; bypass after the gate (live-code order); guard 1 accesses `ctx.signals["image_ocr"]` (KeyError semantics preserved); guards 2+5 share `_source_class` reads.

- [ ] **Step 1:** write `context.py`, then split the P4 `policy.py` function bodies into stage callables **by cut-and-paste, not rewrite** — each stage's body is the corresponding P4 block with `ctx.` prefixes.
- [ ] **Step 2:** public wrapper `apply_policy(fields, adj, conf, tag, signals, config=CONFIG)` builds ctx, runs dispatcher — v3 signature preserved; all P4 policy tests pass **unchanged**.
- [ ] **Step 3:** `test_policy_stages.py` — each of the 9 stages: one firing case, one non-firing case, constructed via `make_ctx`; the two default-off stages exercised with `Config(upgrade_unpaid_on_waiver=True)` / `Config(defensive_downgrade=True)`.
- [ ] **Step 4:** pytest green → **GOLDEN GATE (native)** again, same command as P4 Step 6. Expected: `PARITY OK` — this isolates the split itself.
- [ ] **Step 5: commit** `refactor(v4): L7 as 9 named stages behind unchanged public surface — parity held`.

---

### Task P6: Cutover + final gates

**Files:**
- Modify: `solution.py` (root: import v4), `Dockerfile` (COPY `run.sh solution.py v4/` only — dropping v1/v2/v3 from the image proves standalone-ness), `README.md` (pipeline pointer)
- Create: `v4/OBSERVATIONS.md` (seed with B2-1..B2-4 from spec §7), `docs/TECHNICAL_DEBRIEF.md` (assembled from per-milestone sections)

- [ ] **Step 1:** root `solution.py` → `from v4.solution import main, VERSION`; grep the repo for any remaining production import of v1/v2/v3 outside frozen dirs (`grep -rn "from v[123]\|import v[123]" --include="*.py" . | grep -v "^./v[123]/" | grep -v tests | grep -v dev`) — must be empty.
- [ ] **Step 2:** Dockerfile trimmed; `docker build` clean.
- [ ] **Step 3:** full suite: `python3 -m pytest v4/tests v3/tests -q` (v4 ≥ 83, v3's 79 still green as frozen reference).
- [ ] **Step 4: FINAL GATES (user's terminal):** `./dev_score.sh 1000` → `./parity.sh native` → expect PARITY OK + score 118.08 + split 118.088/118.041; then `MIB_DOCKER=1 ./dev_score.sh 1000` (~55 min) → `./parity.sh docker /tmp/mib-output/predictions.jsonl` → expect PARITY OK + 117.98.
- [ ] **Step 5:** write the debrief's migration map + "what did NOT change" from the spec §11 table + git history; commit `feat(v4): cutover — docker parity confirmed` and tag `v4-parity`.

---

## Self-review checklist (run before P1)
- Spec coverage: P1→§3/§4, P2→§3/§4.1 hazards, P3→§3/§4.4, P4→§5/§6/§8, P5→§5, P6→§8/§9/§10/§11. Bucket-1 fix list §6: items 1,2 (P3), 3 (P3 — `_filter_injection` resolution), 4 (P1/P3 evidence), 5 (P4 comment fix), 6 (P4/P5), 7 (P1), 8 (P1 config docstring). ✓
- No placeholder steps; every created file has either full code or an exact source→dest mapping with line ranges and enumerated transformations. ✓
- Names consistent: `conf()`, `Config`, `CONFIG`, `Verdict`, `PolicyContext`, `make_ctx`, `has_clean_biometric`, bundle keys. ✓
