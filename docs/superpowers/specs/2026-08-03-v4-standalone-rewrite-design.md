# V4 Standalone Rewrite — Design

**Date:** 2026-08-03
**Status:** Approved for implementation planning
**Baseline commit:** `92eb104` (score 118.08/150, 79 tests passing)
**Supersedes approach in:** `docs/REWRITE_BRIEF.md` (see *Deviations from the Brief*)

---

## 1. Goal

Produce `v4/` — a standalone, self-contained adjudication pipeline that reproduces
the current system's output **byte for byte** while being structurally coherent
enough to defend in a technical review.

`v4/` imports nothing from `v1/`, `v2/`, or `v3/`. Those directories remain in the
repository permanently as frozen historical reference and are not modified.

### What "improvement" means in this phase

This phase does not change behavior. Improvement here means the system becomes
*reasonable about* — every flag in one file, every confidence value in one table,
every decision stage independently testable, and a description that cannot drift
from the implementation because it is generated alongside it.

Behavioral improvement is the *next* phase, and this phase's second deliverable is
the evidenced backlog that makes it possible.

---

## 2. Scope: the three-bucket rule

Every finding made during the rewrite is classified into exactly one bucket.

| Bucket | Definition | Action in this phase |
|--------|------------|----------------------|
| **1** | Provably zero behavior change — dead code, stale comments, ordering no-ops, scattered constants, duplicated concerns | **Fixed.** The golden diff proves no behavior changed. |
| **2** | Behavior-changing, but measurable against training data | **Logged only.** Recorded in `v4/OBSERVATIONS.md` with file:line and a proposed counterfactual. Measured in a later phase, one at a time. |
| **3** | Behavior-changing and *not* measurable — anything keying on absence of evidence | **Rejected.** Per Pitfall 1 of the brief. Not a later phase; a reject pile. |

**This phase implements bucket 1 only.** No exceptions, including for changes that
look obviously correct. Rationale: the byte-identical golden diff is the only thing
that makes this rewrite verifiable at all. A single "obvious" behavioral fix forfeits
that guarantee for every other change in the same commit, and we lose the ability to
attribute any future score movement to any specific cause.

---

## 3. Architecture

Seven layers, unchanged. The layering is the part of the current system that works.

```
                 ┌───────────────────────────────────────────┐
  foundation ──▶ │  config.py     confidence.py              │
                 │  vocab.py      patterns.py                │
                 └───────────────────────────────────────────┘
                        │ read by every layer below
   ┌────────────────────┴──────────────────────────────────┐
   │  L1 acquire      PDF bytes   → list[Source]           │
   │  L2 extract      Source      → .content  (text / OCR) │
   │  L3 filters      Source      → .trusted  (trust ∂)    │
   │  L4 signals      Sources     → signal bundle          │
   │  L5 consolidate  bundle      → field dict             │
   │  L6 rules        fields      → (adj, conf, tag)       │
   │  L7 policy       verdict     → final verdict          │
   └───────────────────────────────────────────────────────┘
                        │
                  predictions.jsonl
```

**The organising idea, stated for the debrief:** L1–L5 establish what the packet
*says*; L6–L7 decide what to *do* about it. Trust is determined exactly once, at L3,
and is never revisited downstream. A source is either trusted or excluded; there is
no partial trust and no re-litigation of trust inside the decision layers.

### Module layout

```
v4/
  config.py         ALL feature flags — frozen dataclass, read once at import
  confidence.py     ALL confidence values + empirical basis
  vocab.py          Closed enums: flags, sponsors, homes, visas, fee values
  patterns.py       PDF regexes, field-label regexes, OCR word lists

  acquire.py        L1  PDF bytes  → list[Source]
  extract.py        L2  Source     → .content
  filters/          L3  Source     → .trusted
    __init__.py         sanitizer/detector dispatch
    injection.py
    redaction.py
    illegibility.py
  signals.py        L4  Sources    → signal bundle
  consolidate.py    L5  bundle     → field dict
  rules.py          L6  fields     → (adj, conf, tag)
  policy/           L7  verdict    → final verdict
    __init__.py         the dispatcher
    context.py          PolicyContext
    upgrades.py         3 upgrade stages
    bypasses.py         1 trust bypass
    guards.py           4 guard stages

  normalize.py      value normalization  (L4 helper)
  reocr.py          char-whitelist re-OCR (L4 helper)
  source_type.py    evidence-hierarchy classifier (L4 helper)
  evidence.py       shared readers: OCR signal evaluator, biometric slip reader
  solution.py       entry point
```

The file list reads as the pipeline, so the debrief's diagram and the directory
listing are the same artifact.

---

## 4. Foundation modules

### 4.1 `config.py`

A frozen dataclass holding all feature settings. **Ten of the eleven current env
vars are retired to literal defaults; one is kept.**

#### Why the env vars go

The env-var mechanism has no production value, established by inspection:

- `Dockerfile` contains no `ENV` lines.
- `run.sh` exports nothing; it invokes `solution.py` with input/output paths only.
- The eval harness runs the image without `-e` flags.
- No script anywhere sets a behavior flag. `dev_score.sh` sets only
  `MIB_OCR_CACHE_DIR`, on the native path. (`MIB_DOCKER`, `MIB_SEED`,
  `MIB_CHALLENGE_DIR` are shell variables of the scoring scripts, not pipeline flags.)

Therefore **every behavior flag has always taken its compiled-in default in every
submission run.** The claim at `v3/policy.py:84` — that a flag can be flipped "at
runtime with no rebuild" — does not hold for the submission path: enabling one
requires adding an `ENV` line or an `export` and rebuilding the image, which is the
same effort as changing a constant and rebuilding. Their only real consumer is
`v3/dev/measure_ocr_bundle.sh`, a development sweep whose measurements are complete
and committed.

#### Disposition

| Env var | Currently in | v4 form | Basis |
|---|---|---|---|
| `MIB_OCR_CACHE_DIR` | `v3/extract.py:51` | **kept as env var** | Dev infrastructure; no-op in production; 3.5 min vs 15–30 min iteration |
| `MIB_OCR_SHARPEN` | `v3/extract.py:56` | `= True` | +10.27 pts on 1000 training PDFs (2026-07-31 sweep) |
| `MIB_USER_WORDS` | `v3/extract.py:59` | `= True` | measured OCR quality gain |
| `MIB_NORMALIZE_VALUES` | `v3/signals.py:42` | `= True` | post-extraction value normalization |
| `MIB_CHAR_WHITELIST_REOCR` | `v3/signals.py:43` | `= True` | structured-field format repair |
| `MIB_TRUST_FINDING` | `v3/policy.py:38` | `= True` | Evidence precedence #1; 162 cases, 100% correct |
| `MIB_UPGRADE_WAIVED_ON_BIOMETRIC` | `v3/policy.py:40` | `= True` | 6/22 A-truth, 0/6 D-truth; 0 cat FA risk |
| `MIB_FALLBACK_OCR_UPGRADE` | `v3/policy.py:53` | `= True` | +14 D→D wins, 0 cat FAs introduced |
| `MIB_OCR_ONLY_GUARD` | `v3/policy.py:62` | `= True` | disabling cost +10 cat FAs (−0.22 pts) |
| `MIB_UPGRADE_UNPAID_ON_WAIVER` | `v3/policy.py:50` | `= False`, path retained | measured −0.22 pts, +10 cat FAs |
| `MIB_DEFENSIVE_DOWNGRADE` | `v3/policy.py:89` | `= False`, path retained | measured −2.45 pts |

The two default-off features keep their **code paths** in `v4/policy/`. This
preserves the intent of brief Rule 8 — the feature stays reachable if the private
eval weights cat FAs differently — while dropping a toggle mechanism that never
provided runtime access. Enabling one is a one-line edit plus a rebuild, exactly as
it always was in practice.

#### Requirements

- `Config` is `@dataclass(frozen=True)`; a single `CONFIG = Config.from_env()` at import.
- `from_env()` reads exactly one variable: `MIB_OCR_CACHE_DIR`.
- Every field carries its measured justification as an inline comment.
- **No default value changes** (brief Rule 2) — only the mechanism that supplies it.
- Flag-reading functions accept `config: Config = CONFIG`; L7 stages read
  `ctx.config`. Tests inject `Config(defensive_downgrade=True)` rather than
  manipulating the environment. This also removes the `importlib.reload(extract)`
  pattern in `v3/tests/test_ocr_triple_pass.py:79`, which mutates module globals
  mid-suite and creates test-ordering dependencies.

Net effect: 11 env vars across 3 files → **1 env var, one config module**. Pitfall 10
(env-gated features silently drifting under Docker) is eliminated rather than merely
consolidated — there is no longer a drift surface.

**Cache-key hazard.** `v3/extract.py:73 _cache_config_tag()` appends `"uw"` when
`MIB_USER_WORDS` is on. With `user_words` hardcoded `True`, the tag is always
`"_uw"` — which is what the existing warm cache on disk is keyed by. v4 must emit
byte-identical tag strings, pinned by an explicit test (brief Rule 3).

**Cache-key hazard.** `v3/extract.py:73 _cache_config_tag()` builds an OCR cache-key
suffix from `MIB_USER_WORDS` (appending `"uw"`). Changing when or how that tag is
computed silently invalidates the entire warm OCR cache (brief Rule 3). v4 must
produce byte-identical tag strings, pinned by an explicit test over every flag
combination.

### 4.2 `confidence.py`

One registry, one place. Approximately 30 entries drawn from **three** current
locations — the brief accounts for two:

1. `v1/solution.py:552` — `CONFIDENCE`, 15 L6 rule tags (empirically calibrated).
2. `v3/policy.py` — 6 literals: `0.80` ×2 (upgrades), `0.65` ×4 (guards).
3. `v3/ocr_signal.py` — 10 literals: `0.95`, `0.92` ×2, `0.90` ×3, `0.85` ×3, `0.65`.

Each entry records: value, fire count, measured accuracy, and basis. Values are
transcribed exactly — they were derived by measurement and are not to be adjusted
during a refactor (brief Rule 1).

A completeness test asserts every tag the pipeline can emit has a registry entry, so
an untagged confidence fails the suite rather than shipping silently.

### 4.3 `vocab.py`

Closed enums, each carrying a provenance comment naming its source — the
`train_labels.csv` column, the Field Manual section, or both. This makes Pitfall 7
(vocab discipline) structural rather than a habit.

Covers: `DISQUALIFYING_FLAGS`, `REVIEW_ONLY_FLAGS`, `REVOKED_SPONSORS`,
`EMBARGOED_HOMES_HARD`, `EMBARGOED_HOMES_SOFT`, `VISA_CLASSES`, `FEE_VALUES`,
`SPECIES`, `HOME_WORLDS`, plus `RECEIPT_DATE_PROXY` / `STALE_DAYS`.

### 4.4 `patterns.py`

PDF stream regexes, field-label regexes, and OCR word lists.

**Decision: the OCR word lists are NOT merged.** The brief asks for "one canonical
set," but the two lists are not duplicates:

- `v3/ocr_signal.py` matches **uppercase stems** (`BIOHAZ`, `EMBARG`, `DENIE`,
  `REVOK`, `RESCIN`) to **make decisions**.
- `v3/dev/analysis/extract_features.py:45` matches **lowercase whole words**
  (`biohazard`, `denied`, `revoked`, …) to **emit diagnostics**.

They have different casing, different matching semantics, and different consumers.
Merging them changes what fires. They live in `patterns.py` side by side as
`DECISION_STEMS` and `DIAGNOSTIC_PROBES`, with a comment recording that the
divergence is deliberate and must not be unified.

---

## 5. L7 decomposition

**Nine stages** — 3 upgrades, 1 bypass, 5 guards — each a callable
`PolicyStage = Callable[[PolicyContext], Verdict | None]`, with a dispatcher short
enough to read in one pass:

```python
def apply_policy(ctx: PolicyContext) -> Verdict:
    for stage in UPGRADES:                       # 3 stages
        if (r := stage(ctx)) is not None: return r
    if ctx.adj != "APPROVED":  return ctx.verdict
    for stage in BYPASSES:                       # 1 stage
        if (r := stage(ctx)) is not None: return r
    for stage in GUARDS:                         # 5 stages
        if (r := stage(ctx)) is not None: return r
    return ctx.verdict
```

The count is 9, not the 8 the brief describes. The brief's prose says "4 downgrade
guards" while its own `GUARDS` list holds 5 entries; the live code numbers its guards
(1) through (5) at `v3/policy.py:223-309`. Two of the nine (`upgrade_unpaid_waiver`,
`guard_defensive_downgrade`) are disabled by default and do not execute in production.

Stage registry:

```
UPGRADES  = [upgrade_biometric_clean, upgrade_unpaid_waiver, upgrade_fallback_ocr]
BYPASSES  = [bypass_adjudicator_finding]
GUARDS    = [guard_ocr_risk_override, guard_ocr_only,
             guard_field_conflict, guard_missing_required,
             guard_defensive_downgrade]
```

### Semantics that MUST be preserved

**An upgrade returns immediately and skips every guard.** A biometric-clean upgrade
to `APPROVED` today never faces the OCR-only guard, the conflict guard, or the
missing-required guard. Any restructuring into "apply upgrades, then validate with
guards" silently changes verdicts. The early-return chain is load-bearing.

**Upgrade order matters and is preserved:** biometric-clean → unpaid-waiver →
fallback-OCR. The fallback-OCR upgrade is conditional — when it finds no usable OCR
signal it *falls through* rather than returning, which is why it cannot be modelled
as a plain "first match wins" list without care.

**The trust bypass is load-bearing; its position relative to the `adj != APPROVED`
gate is not.** The bypass fires on `R_ADJUDICATOR_FINDING[APPROVED]` — 33 training
cases, 100% correct — and without it those approvals would face the guards and
some would be downgraded. The brief's sketch places the bypass *before* the gate and
the live code places it *after*; both admit the identical set to the guards and both
early exits return an unmodified verdict, so the ordering is observationally a no-op.
**v4 matches the live code** and records the equivalence in the debrief.

### `PolicyContext`

Stages take a frozen `PolicyContext` (fields, adj, conf, tag, signals) rather than
five positional arguments, purely so each stage is constructible in isolation for
per-stage tests. No behavior change.

---

## 6. Bucket 1 fix list

Confirmed, and included in v4:

| # | Finding | Evidence |
|---|---------|----------|
| 1 | `FLAG_DECLARATION` signal type is defined and imported but **never emitted or consumed** | defined `v3/signals.py:94`, imported `v3/consolidate.py:25`, zero other references |
| 2 | 7 dead `v1` symbols (~96 of 734 lines) not carried into v4 | `extract_text`, `predict_case`, `main`, `_filter_injection`, `CASE_ID_RE`, `VISA_RE`, `VERSION` — by transitive reachability from v3's imports |
| 3 | `_filter_injection` is a second implementation of `v3/filters/injection.py`; only `INJECTION_MARKERS` is actually shared | `v1/solution.py:161-175` |
| 4 | 10 confidence literals invisible to any registry | `v3/ocr_signal.py` |
| 5 | Stale comment contradicting live code and the file's own header | `v3/policy.py:229` says OCR-only guard is "DEFAULT OFF"; `v3/policy.py:55` and the default say ON |
| 6 | Trust-bypass / gate ordering normalised to match live behavior | `v3/policy.py:210-221` |
| 7 | 11 env vars across 3 files → 1 env var + literal defaults in `config.py` | see §4.1 |
| 8 | False claim in a code comment: flags can be "flipped at runtime with no rebuild" | `v3/policy.py:84`; `Dockerfile` has no `ENV`, `run.sh` exports nothing |

None of these change behavior. The golden diff is the proof.

---

## 7. Bucket 2 ledger (logged, NOT actioned this phase)

Maintained in `v4/OBSERVATIONS.md`, updated at each milestone. Opening entries:

**B2-1 — the `FALLBACK_extraction_fail` bucket.** Fires on **251 of 1000 packets at
34% accuracy**: a quarter of all decisions land in one undifferentiated tag that is
wrong roughly two times in three. Every L7 guard debated in the brief concerns tens
of cases; this concerns 251. Almost certainly several distinct populations sharing
one tag and one confidence value. Splitting it is a calibration win before it is ever
a classification win. *Proposed counterfactual: partition by which extraction failed
(visa vs fee vs both) and measure per-partition accuracy.*

**B2-2 — crash confidence reuses extraction-failure confidence.** `v3/solution.py:93`
assigns `CONFIDENCE["FALLBACK_extraction_fail"]` (0.34) to packets that raised an
exception. A crashed packet and an under-extracted packet are different populations;
sharing a calibration bucket has no measured justification. *Proposed counterfactual:
measure empirical accuracy of the exception path separately.*

**B2-3 — `R_A1_non_dip_waived` at 0.37.** A rule asserting it is wrong 63% of the
time. Brier-optimal *for that bucket*, which is the tell that the bucket wants
splitting rather than tuning. *Proposed counterfactual: measure accuracy split by
presence of biometric evidence, since the L7 upgrade already partitions on it.*

---

## 8. Verification

### The golden oracle

Because `v3/` remains runnable, parity is checkable **per case**, not in aggregate.

1. **Milestone 0:** run v3 over the 1000-packet dev sample, commit its
   `predictions.jsonl` as a golden file.
2. **After every milestone:** run v4 over the same sample and `diff` against golden.
   **Zero bytes of difference is the bar.**
3. `dev_score.sh` as final confirmation only — a byte-identical file scores
   identically by construction.

This is strictly stronger than the brief's ±0.3-point gate. Two verdicts can swap and
still total 118.08; a byte diff catches that, an aggregate score does not.

Sampling must be pinned: same `MIB_SEED` (42) and same N (1000) for golden and every
comparison run.

### Tests

With v4 standalone and v3 frozen, v3's 79 tests pass trivially and prove nothing
about v4. They are therefore **ported** to v4 — mechanical import rewrites, intent
preserved (brief Part 6) — while v3's copies stay green as frozen reference.

Added, per brief Part 6:

- **Config test** — defaults are exactly the documented values; `Config` is immutable;
  `MIB_OCR_CACHE_DIR` (the one remaining env var) is read correctly when set and when unset.
- **Cache-tag test** — `_cache_config_tag()` output pinned to `"_uw"`, and pinned for
  a `Config(user_words=False)` injection, so the warm cache cannot be silently invalidated.
- **Confidence registry test** — every tag the pipeline can emit has an entry.
- **L7 stage tests** — each of the 9 upgrade/bypass/guard stages exercised in
  isolation via an injected `PolicyContext`, including the two default-off stages
  enabled through `Config(...)`.

Target: 79 ported + at least 4 new = **83+ tests against v4**.

Ported tests that currently manipulate the environment (`monkeypatch.setenv` plus
`importlib.reload`) are rewritten to inject a `Config`. This is a mechanical change
that preserves each test's intent while removing global-state mutation from the suite.

### Runtime

Must remain within the 6 s/PDF budget under Docker. v4 introduces no new
per-packet work, so this is a confirmation rather than a risk.

---

## 9. Build sequence

Each milestone ends with a golden diff, so drift is attributed to the step that
caused it.

| # | Milestone | Gate |
|---|-----------|------|
| 0 | Capture golden `predictions.jsonl` from v3 (seed 42, N=1000) | committed |
| 1 | `v4/` skeleton + `vocab.py`, `patterns.py` | imports clean |
| 2 | L1–L3: `acquire`, `extract`, `filters/` | ported unit tests green |
| 3 | L4–L5: `signals`, `consolidate` (+ `normalize`, `reocr`, `source_type`) | ported unit tests green |
| 4 | `config.py`; all call sites migrated; 10 env vars retired | config defaults + cache-tag tests |
| 5 | `confidence.py`; all ~30 values consolidated | registry completeness test |
| 6 | L6 `rules.py` — standalone rule chain | **golden diff = 0** |
| 7 | L7 split into 9 stages | **golden diff = 0** |
| 8 | `solution.py` entry; `run.sh` / `Dockerfile` cutover | **golden diff = 0**, then `dev_score.sh`, then Docker parity |

A section of the debrief is written at each milestone, while the reasoning is fresh.

---

## 10. Deliverables

1. **`v4/`** — standalone pipeline, byte-identical output.
2. **`docs/TECHNICAL_DEBRIEF.md`** — reviewer-facing design document, written
   incrementally per milestone. Audience: a challenge reviewer who never sees the
   code. Explains the classification approach, the evidence hierarchy, the pipeline
   a packet travels, and why each decision stage exists, citing training measurements.
   Includes the architecture diagram and a "what did NOT change" section.
3. **`v4/OBSERVATIONS.md`** — the bucket 2/3 ledger, with file:line evidence and a
   proposed counterfactual per entry.
4. **Migration map** — what moved where, for the debrief.

---

## 11. What does NOT change

Stated explicitly so the reviewer can see preservation was deliberate:

- **Output schema** — `predictions.jsonl` fields and formats are fixed by the
  challenge contract (brief Rule 6). Enforced by the golden diff.
- **OCR pipeline and L3 filter thresholds** — stable, and changes cascade
  unpredictably (Pitfalls 4 and 5; brief Rule 4). Transcribed verbatim.
- **OCR cache key format** — see §4.1 (brief Rule 3).
- **All confidence values** — empirically derived (brief Rule 1).
- **All flag default *values*** — chosen by measured score impact (brief Rule 2).
  Only the mechanism supplying them changes (env var → literal); every effective
  value is identical, which is why output stays byte-identical.
- **Both default-off feature code paths** — retained and reachable (brief Rule 8's
  intent), see §4.1.
- **Layer separation** — L1–L7 remain distinct concerns (brief Rule 7).
- **Rule set** — no rules added, removed, or modified (brief Rule 5).
- **Error handling** — per-PDF `try/except` in `main()` emitting a safe
  `NEEDS_REVIEW`, schema-valid defaults for unextracted fields, internal
  `_finding` / `_source_class` / `_agreement` keys stripped before write.
- **`v1/`, `v2/`, `v3/`** — untouched, kept as frozen reference.

---

## 12. Deviations from the brief

| Brief says | This design | Why |
|---|---|---|
| Delete `v2/` as dead code | Keep `v1/`–`v3/` permanently | User decision; also enables the golden-diff oracle |
| Refactor `v3/` in place | Build standalone `v4/` | User decision; only structure that removes version naming from the live path |
| Confidence in 2 places | 3 places | `v3/ocr_signal.py` holds 10 more literals |
| 10 env vars | 11 | The 10 booleans plus `MIB_OCR_CACHE_DIR` |
| L7 has "4 downgrade guards" (8 stages) | 5 guards (9 stages) | The brief's own `GUARDS` list has 5 entries; live code numbers them (1)–(5) |
| Consolidate OCR probe lists into one canonical set | Keep two, named and documented | They are divergent, not duplicated; merging changes behavior |
| Consolidate all flags into one config module, keep env-var gating (Rules 8, 10) | Retire 10 of 11 env vars to literal defaults; retain both feature code paths | The mechanism never provided runtime access — no `ENV` in `Dockerfile`, no export in `run.sh`. Rule 8's intent (feature stays reachable) is preserved; the drift surface is removed |
| Score within ±0.3 pts | Byte-identical output | Strictly stronger; aggregate score hides compensating errors |
| All 79 tests pass | 79 ported to v4 + 4 new | v3's tests prove nothing about v4 once v3 is frozen |
| Trust bypass before the `adj != APPROVED` gate | After, matching live code | Equivalent; code is the authority |

---

## 13. Risks

| Risk | Mitigation |
|---|---|
| Transcription error in a 605-line module (`signals.py`) | Golden diff after each milestone localises it to one step |
| OCR cache invalidated by a changed cache tag | Explicit test pinning tag strings per flag combination |
| Ported tests silently weakened during import rewrite | Intent preserved; diff each ported test against its v3 original |
| Golden file captured from a dirty tree | Milestone 0 runs from committed `92eb104` and the golden file is committed |
| Scope creep from bucket 2 findings | Three-bucket rule; ledger captures without actioning |
