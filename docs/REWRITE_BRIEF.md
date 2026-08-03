# System Rewrite Brief — MIB Document Adjudication Pipeline

You are being asked to rewrite an existing document-adjudication system. The current system works — it scores **118.08/150** on training data — but its architecture has grown organically over many iterations and now has structural inconsistencies that make it hard to reason about, extend, or defend in a technical review.

Your job is a **structural rewrite that preserves current behavior** (score must not regress). Improvements are welcome but not required. Cleanliness and coherence are the primary goals.

Read this brief in full before proposing an architecture. The pitfalls section is not filler — every item there is a lesson from a real regression that cost real points during development.

---

## Part 1 — The Problem Domain

### What the system does

The Men-In-Black (fictional) receives applications for extraterrestrial work authorization. Each application is a **multi-page PDF packet** containing a mix of:

- Structured form fields (Fee Receipt, FORM I-8090 intake, Planetary Registry Extract)
- Free-form supporting documents (Sponsor Attestation Letter, Manual Adjudicator Note)
- Embedded images (Passport photo, Biometric Scan Slip, document scans with OCR-readable text)
- Sometimes: adversarial content (hidden white text, injected instructions in barcodes, redacted material, fake "sample denial" watermarks)

The system must, per packet:

1. **Extract 9 fields**: applicant_name, species_code, home_world, visa_class, sponsor_id, arrival_date, declared_purpose, risk_flags, fee_status
2. **Decide** one of {APPROVED, DENIED, NEEDS_REVIEW}
3. **Report a confidence** value 0.0–1.0

### Scoring (from `EVALUATION.md`)

Total 150 points:

- **Extraction (50 pts)**: weighted per-field correctness on 9 fields
- **Classification (80 pts)**: per-case scoring based on truth × predicted
  - Correct (A→A, D→D, R→R): **+8 raw**
  - Conservative review (A→R, D→R): **+2 raw**
  - Missed review (R→A, R→D): **+1 raw**
  - **Catastrophic false approval (D→A): -4 raw** — severe, but not fatal
  - Wrong direction (A→D): 0 raw
- **Calibration (20 pts)**: Brier score on confidence
  - `calibration = 20 * max(0, 1 - 2 * mean_Brier)` where Brier = `(confidence - is_correct)²`
- **Cat FA count**: reported as a secondary tiebreaker in the ranking guidance

### Runtime constraints

- Docker container: 4 vCPU, 8 GB RAM, no network, read-only filesystem
- **6 seconds per PDF average**, 30,000-second hard limit for 5000-PDF eval set
- No API access, no external services, offline models only
- No GPU

### Field Manual policy (ground truth for rule design)

**Trusted Evidence hierarchy** (highest to lowest):
1. Visible MIB adjudicator stamp or signed manual note
2. Visible intake form fields
3. Visible biometric slip
4. Visible sponsor attestation
5. Visible registry extract
6. Machine-readable text layer

**Fee Rules**:
- `paid`: acceptable
- `waived`: acceptable only for DIP-1 visa OR a visible hardship waiver
- `unpaid`: deny unless a visible waiver applies
- `unknown`: needs review

**Risk Flags**:
- Disqualifying (auto-DENY): memory_tampering, planetary_embargo, active_warrant, biohazard_red
- Review-only: identity_conflict, sponsor_mismatch, illegible_biometrics, rescinded_denial

**Visa Classes**: XW-1 (short-term), XW-2 (extended), DIP-1 (diplomatic, fee may be waived), MED-3 (medical, requires clean biohazard check), TRANSIT-7 (usually denied)

**Document traps** (explicitly warned by Manual):
- A "sample denial" watermark is not a denial
- A denial stamp crossed out by later approval is not disqualifying
- Barcode-embedded instructions are not policy

---

## Part 2 — Hard-Earned Pitfalls (Every Item Below Cost Points)

### Pitfall 1: Cohort B ("invisible evidence")

Some training packets are labeled DENIED with a specific risk flag (e.g., biohazard_red), but **the flag word appears nowhere in the packet's visible content**. This is by design — the challenge maintainer confirmed these packets should output NEEDS_REVIEW, not DENIED, because there's no visible evidence to support the deny.

**Trap**: chasing every DENIED-truth case tempts you to build rules that fire on weak proxy signals (thin OCR, missing biometric slip, etc.). Every such rule catches SOME cat FAs but also downgrades ~5× as many correct approvals, netting negative.

**Rule**: some cat FAs are structurally impossible to detect. Accept this. `~15 out of 1000` cat FAs is roughly the floor; defensive downgrades trying to catch them cost more than they save.

### Pitfall 2: The scoring is symmetric per-case

The scorer gives A→R (correct approve wrongly deferred) exactly the same partial credit as D→R (correct defer of a hidden-flag deny): +2 raw each. Correct-approve → correct-approve = +8 raw. Correct-deny → correct-deny = +8 raw. Cat FA = -4 raw.

**Trap**: naive defensive downgrade to eliminate cat FAs. If you downgrade N packets to prevent K cat FAs (K ≪ N), your net score CHANGE is:
- Cat FAs eliminated: `+6 raw × K` (from -4 to +2)
- Correct approvals lost: `-6 raw × (N-K-something)` per truth=APPROVED case

The math almost always favors keeping the rule and eating the cat FAs, UNLESS the ratio of caught-to-collateral is better than ~1:1.

**Rule**: verify score impact math before adding any defensive rule. The intuition "cat FAs are bad, we should prevent them" is technically true but empirically nearly always net-negative.

### Pitfall 3: Confidence should equal empirical accuracy

The Brier score is a proper scoring rule — it's mathematically minimized when your confidence equals `P(correct | features)`.

**Trap**: hand-picking confidence values (e.g., "R_A1_paid_clean feels 94% accurate, let's use 0.94"). If actual empirical accuracy is 69%, every wrong case has Brier = (0.94-0)² = 0.88 per case → massive calibration penalty.

**Rule**: measure per-rule accuracy from training data. Use empirical values. Cap at 0.99 to leave headroom for eval-set tail cases. Keep the calibration script (`v3/dev/analysis/calibrate_confidence.py`) working so any rule change triggers recalibration.

### Pitfall 4: L3 filters cascade through the whole pipeline

The illegibility filter marks images as untrusted based on OCR word-recognition ratio. If you tighten this filter (e.g., "also mark <5-token images as untrusted"), you cascade effects through L4 (fewer signals), L5 (consolidation), L7 (fewer OCR-only guard triggers).

**Trap**: extending the illegibility filter looked promising for cat FA prevention. Measurement showed it turned the `any_illegibility_excluded` signal from a discriminator (~50% of packets) to noise (~94% of packets), destroying its L7 usefulness.

**Rule**: never change L3 filter thresholds without measuring the whole-pipeline effect. Signals designed to be selective must remain selective.

### Pitfall 5: OCR trust asymmetry

The Field Manual says image-based visible evidence is MORE trusted than text streams (see hierarchy above). But our L7 "OCR-only guard" assumes the opposite (downgrade if approval driven by OCR-only fields).

**Trap**: this guard exists because "OCR can be forged/wrong" — a legitimate concern for hidden/adversarial content. But empirically, when the guard fires alone (no other guard co-firing), it's 65% wrong on correct approvals. Removing it wholesale introduced 10 new cat FAs across cascading paths.

**Rule**: OCR-vs-text-stream trust is context-dependent. Any guard using this distinction must be measured across ALL rules it affects, not just one. Default to keeping conservative safety guards on unless data proves them harmful.

### Pitfall 6: Boilerplate looks like signal

Waiver Code "DIP-WAIVER" appears on nearly EVERY non-DIP-waived packet — not just those with genuine hardship waivers. Same for Registry Status "CLEAR" and "Observed flags: none" (on many packets). If you use these as evidence, you fire on both truly-clean and truly-not-clean packets.

**Trap**: building a rule around "biometric slip present with clean flags" — the slip is boilerplate on many packets AND the "clean" reading appears even when the packet's truth is DENIED.

**Rule**: before treating any text as a positive signal, verify enrichment vs base rate. If the signal appears on 40% of correct approvals AND 40% of cat FAs, it's not a signal.

### Pitfall 7: Vocab discipline (never trust memory)

When adding closed-enum lists (user-words dictionaries, snap tables), derive them from `train_labels.csv` AND cross-reference the Field Manual. **Do not** rely on generated or recalled lists — during earlier development, someone included "refunded" as a fee_status value; it turned out to be a hallucination (actual values are only paid/waived/unpaid/unknown).

**Rule**: for any hardcoded list, run:
```bash
awk -F',' 'NR>1 {print $N}' train_labels.csv | sort -u
```
for that field, then verify against the Field Manual. Values in neither = reject.

### Pitfall 8: Feature staleness

The pipeline emits diagnostic features per case (`v3/dev/analysis/extract_features.py` writes `/tmp/mib-features.jsonl`). Any calibration or rule-analysis based on this file assumes it reflects the CURRENT pipeline. When you change any rule (especially L7), features become stale.

**Rule**: regenerate features.jsonl before ANY analysis that reads it. Add a warning if features are older than the source code.

### Pitfall 9: Pretty patterns aren't always exploitable

MED-3 visa cases are 47% of cat FAs vs 25% of correct approvals — a real 2× enrichment. Field Manual explicitly says "MED-3 requires clean biohazard check." Looked like a slam-dunk rule.

Measurement showed every "MED-3 + no biohazard clearance signal" gate loses more correct approvals than cat FAs it saves. The Manual's guidance is aspirational — the packets don't contain the biohazard clearance info we'd need to enforce it.

**Rule**: enrichment doesn't guarantee a workable rule. Test the counterfactual before implementing.

### Pitfall 10: Env-var-gated features can silently drift

We have 10 env vars gating individual features. Each has its own default. In docker parity testing, if the container doesn't inherit an env var, behavior changes silently.

**Rule**: consolidate all feature flags into ONE config module read at import. Every default must be documented with WHY it's that value.

---

## Part 3 — Current Architecture (What's There Now)

### High-level layers (already good — keep)

```
L1 acquire       → PDF parsing → Source list (TEXT_STREAM | IMAGE)
L2 extract       → Text extraction + Tesseract OCR (with caching)
L3 filter        → Injection sanitize, illegibility check, redaction
L4 signals       → Per-source field extraction → Signal list
L5 consolidate   → Signals → per-field values with source-class + agreement
L6 rules         → V1 adjudicate() → (verdict, confidence, tag)
L7 policy        → apply_policy() → maybe modify verdict via upgrades/guards
```

### What's scattered (the target of the rewrite)

1. **10 env vars across 3 files** — no single config module
2. **Confidence values in 2 places** — `v1/solution.py:CONFIDENCE` (15 rule tags) + hardcoded literals (0.80, 0.65, 0.85) in `v3/policy.py`
3. **L7 policy is a 200-line function** mixing 3 upgrade branches + 4 downgrade guards + 1 trust bypass, all in sequential if-return statements
4. **OCR probe lists duplicated** — same word lists in `v3/dev/analysis/extract_features.py`, `v3/ocr_signal.py`, and (partially) `v3/policy.py`
5. **v1/v2/v3 naming is confusing** — v1 is legacy rules still in use, v3 is the current pipeline, v2 is dead code that should be removed
6. **Multiple rules have the same 0.65 confidence** with no principled distinction between them

### Current metrics (baseline to preserve)

- Score: 118.08/150
- Extraction: 40.16 / 50
- Classification: 62.46 / 80
- Calibration: 15.46 / 20
- Cat FAs: 15
- Brier: 0.1136
- Tests: 79/79 pass
- Runtime: ~3 min per 1000 packets (host, warm OCR cache); ~15-30 min docker cold-cache

---

## Part 4 — Rewrite Requirements

### Must-haves (regression-blocking)

1. **All 79 existing tests must pass** — behavior preservation
2. **Score must not regress on either split**:
   - Train (800 cases): baseline 118.088 → post-rewrite must be ≥ 117.7 (±0.4 tolerance)
   - Val (200 cases): baseline 118.041 → post-rewrite must be ≥ 117.7 (±0.4 tolerance)
   - Measure with `v3/dev/analysis/split_score.py` after each `dev_score.sh` run
3. **Cat FA count must not increase** beyond 17 (baseline 15, tolerance 2)
4. **Runtime must fit** the 6-sec/PDF budget (measured under docker)
5. **All existing env-var-gated features preserved** (even if consolidated)

### Train/val split discipline (added 2026-08-03)

We now maintain an 800/200 held-out split (seed 20260803) in
`/tmp/mib-splits/{train.txt,val.txt}`. Current pipeline scores 118.088 on
train and 118.041 on val — essentially no overfitting.

- **Never move the split.** Same 800/200 partitions for all further work.
- **Report both scores** after every measurement pass. If train score
  improves but val doesn't, you've overfit the rule to training-only patterns
  and should back it out.
- **Do NOT tune based on val performance.** Val is a held-out estimate, not
  a second training set. Adjusting rules to improve val score turns it into
  a training set and destroys its held-out guarantee.

### Structural changes required

**1. Single config module** (`v3/config.py` or equivalent)
```python
@dataclass(frozen=True)
class Config:
    # OCR pipeline
    ocr_sharpen: bool = True
    ocr_user_words: bool = True
    ocr_normalize_values: bool = True
    ocr_reocr_char_whitelist: bool = True
    # L7 policy
    trust_finding: bool = True
    upgrade_waived_on_biometric: bool = True
    upgrade_unpaid_on_waiver: bool = False   # off — measured -0.22 pts
    fallback_ocr_upgrade: bool = True
    ocr_only_guard: bool = True
    defensive_downgrade: bool = False        # off — measured -2.45 pts

    @classmethod
    def from_env(cls) -> "Config":
        # Read all MIB_* env vars, apply overrides
        ...

CONFIG = Config.from_env()
```

**2. Unified confidence registry** — all values in one place, empirically calibrated
```python
# All 15 L6 rule tags + all L7 override tags, with empirical accuracy + fire count
CONFIDENCE = {
    # L6 tags — accuracy measured on training data
    "R_ADJUDICATOR_FINDING":    RuleConf(0.99, fires=162, accuracy=1.00),
    "R_A1_paid_clean":          RuleConf(0.69, fires=168, accuracy=0.69),
    # ... etc
    # L7 override tags
    "biometric_clean_upgrade":  RuleConf(0.80, ...),
    "fallback_ocr_denied":      RuleConf(0.85, ...),   # inherits from evaluate_ocr_signal
    "ocr_only_downgrade":       RuleConf(0.65, ...),
}
```

**3. L7 pipeline stages** — split `apply_policy` into named, testable callables
```python
UPGRADES = [
    upgrade_biometric_clean,
    upgrade_fallback_ocr,
    upgrade_unpaid_waiver,   # currently off
]

TRUST_BYPASSES = [
    bypass_adjudicator_finding,
]

GUARDS = [
    guard_ocr_risk_override,
    guard_ocr_only,           # currently on
    guard_field_conflict,
    guard_missing_required,
    guard_defensive_downgrade, # currently off
]

def apply_policy(fields, adj, conf, tag, signals):
    for upgrade in UPGRADES:
        result = upgrade(fields, adj, conf, tag, signals)
        if result: return result
    if any(bypass(tag) for bypass in TRUST_BYPASSES):
        return adj, conf, tag
    if adj != "APPROVED":
        return adj, conf, tag
    for guard in GUARDS:
        result = guard(fields, adj, conf, tag, signals)
        if result: return result
    return adj, conf, tag
```

**4. Consolidated OCR probe module** — one canonical set of word lists / regexes, imported by all consumers.

**5. Layer responsibilities documented** — a module-level docstring per layer explaining what it does and (crucially) what it does NOT do. See `v3/policy.py` current module docstring for an example of the style.

**6. Dead code removal** — the `v2/` directory contains legacy code not used by the current pipeline. Verify with a grep for imports; if unreferenced, delete.

### Non-goals for this rewrite

- **New rules or new features** — don't add anything new; only restructure existing behavior
- **Score improvements** — if the refactor incidentally reveals a bug that hurts score, fix it; but don't chase score
- **Test additions** — existing tests are enough; don't expand test surface during a refactor
- **Rewriting the OCR pipeline or L3 filters** — these are stable and any change here cascades unpredictably

---

## Part 5 — Rules You Must Not Break

1. **Preserve the CONFIDENCE table's empirical values.** They were derived from measurement (`v3/dev/analysis/calibrate_confidence.py`); do not tweak them arbitrarily during the rewrite.

2. **Preserve env-var defaults.** Every flag has a default that was chosen based on training-set score impact. Don't flip defaults during the refactor.

3. **Do not touch the OCR cache format.** Cache keys encode env-var state (see `v3/extract.py:_cache_config_tag()`) to prevent cross-config pollution. Changing this invalidates hours of cached OCR work.

4. **Do not weaken adversarial filtering.** L3 injection sanitization, redaction, and illegibility are safety-critical. Consolidate calling code if you want, but don't change filter thresholds or logic.

5. **Do not add "clever" rules.** Every rule in the current system has a measured basis. New rules require measurement before adoption. In particular, don't add rules that fire on invisible-evidence signals (see Pitfall 1).

6. **Do not change output schema.** `predictions.jsonl` fields and formats are fixed by the challenge contract (`DOCKER_SUBMISSION.md`). Any change to output format breaks scoring.

7. **Do not consolidate away the layer separation.** L1–L7 as separate concerns is right; a "big flat function" refactor would be a regression in clarity.

8. **Do not remove env-var-gated experimental features.** Features shipped with defaults=off (defensive downgrade, R3 unpaid waiver) exist as insurance in case the private eval scorer weights cat FAs differently. Keep them accessible via env var.

9. **Do not chase the last few points.** The rewrite is architectural. Score improvements come as a happy side effect (or don't) — DO NOT trade cleanliness for score.

10. **Do not skip docker parity after refactor.** After the rewrite, run `./score.sh` again to confirm no drift. If the score differs by more than 0.5 pts, something silently changed — investigate.

---

## Part 6 — Testing Strategy

### Regression coverage
79 existing unit tests. All must pass after refactor. If any test needs modification (e.g., because a fixture references a renamed module), preserve the test's INTENT — only update the mechanical parts.

### Behavior parity verification
1. Run `dev_score.sh 1000` before and after refactor. Score must match ±0.3 pts. Confusion matrix cells should differ by ≤2 each.
2. Run `docker ./score.sh` after refactor. Should match dev_score result.
3. Regenerate `/tmp/mib-features.jsonl` and re-run `calibrate_confidence.py`. Rule accuracy values should not have shifted (indicating no accidental logic changes).

### Rewrite-specific tests to add
- **Config module test**: reading an env var overrides default, defaults are the documented values, `Config` object is immutable
- **Confidence registry test**: every rule tag emitted by the pipeline has a corresponding entry in the registry (fail if we invent an untagged confidence somewhere)
- **L7 stage test**: each upgrade/guard is independently testable in isolation

---

## Part 7 — Deliverables

1. **Refactored code** meeting all Must-Haves above
2. **Migration diff** — a summary of what moved where (for the technical debrief)
3. **Architecture diagram** — one image or ASCII sketch showing the L1–L7 pipeline with the config/confidence modules called out. Suitable for inclusion in the technical debrief.
4. **A short "what did NOT change" section** in the debrief — reassures the reviewer that we preserved behavior deliberately

---

## Part 8 — Order of Operations Suggested

1. Read current code in this order: `solution.py` → `v3/acquire.py` → `v3/extract.py` → `v3/filters/*.py` → `v3/signals.py` → `v3/consolidate.py` → `v3/rules.py` → `v1/solution.py:adjudicate` → `v3/policy.py`
2. Read the `RULE_AUDIT.md` in `v3/dev/docs/` — has the measured history
3. Draft the new module structure BEFORE writing code
4. Move code without changing logic (mechanical refactor first)
5. Add config module and confidence registry as new files; migrate call sites
6. Split L7 policy into stages (last, because it's the most complex)
7. Delete dead code (v2/)
8. Run full test suite after each step (79 tests)
9. Run `dev_score.sh` before submitting the refactor for review

---

## Part 9 — What Success Looks Like

After the rewrite, a new engineer joining the project should be able to:

- **See all feature flags in one file** and understand what each controls
- **Find every confidence value in one table** and see its empirical justification
- **Read `apply_policy` in under 5 minutes** and understand the sequence of decisions
- **Reproduce any measurement** using the analysis scripts in `v3/dev/analysis/`
- **Add a new rule** by writing a new callable + a test + a confidence entry, without touching multiple files

The technical debrief should read like a coherent design document, not a war-of-attrition log. The rewrite is the last chance to make the architecture make sense before submission.

Good luck. Ask clarifying questions if any of this is ambiguous — better to check than to guess.
