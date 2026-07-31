# V3 Rule Audit Log

Living scratch file. As we build filters (L3), signal extractors (L4), and
consolidation (L5), we accumulate cases where V1 rules (L6) misclassify
under trustworthy inputs. Each entry is a hypothesis, not a fix — we
process this list all at once when L5 stabilizes.

## When to add an entry

Only when V3's inputs (fields at L5) are as good as we can currently make
them AND the rule at L6 still returns the wrong adjudication. If the
input is wrong, that's a filter/consolidation problem, not a rule problem.

## Format

```
### <case_id>

- Truth: <adjudication> / <flags> / <other relevant fields>
- V1 rule that fired: <tag> @ <confidence>
- V5 field snapshot: <what L5 gave to L6>
- Hypothesis: why the rule is wrong given these inputs
- Proposed fix: what would we change (rule threshold? new condition? split?)
```

---

## Entries

### 🟢 Validated by correlator — 100% accurate on training, do not touch

Rule (n cases) → target class, per correlator run on features.jsonl:

- `R_ADJUDICATOR_FINDING[DENIED]` (79) → 100% DENIED
- `R_ADJUDICATOR_FINDING[NEEDS_REVIEW]` (50) → 100% REVIEW
- `R_ADJUDICATOR_FINDING[APPROVED]` (33) → 100% APPROVED
- `R0_hard_embargo[TRAPPIST-1e]` (20) → 100% DENIED
- `R0_hard_embargo[Eris Relay]` (11) → 100% DENIED
- `R2_disqualifier[biohazard_red]` (17) → 100% DENIED
- `R2_disqualifier[planetary_embargo]` (9) → 100% DENIED
- `R2_disqualifier[active_warrant]` (5) → 100% DENIED
- `R2_disqualifier[memory_tampering]` (3) → 100% DENIED
- `R4b_embargoed_home` (7) → 100% DENIED
- `R_R2_unknown_fee` (18) → 100% REVIEW
- `R_R1_flag_present` (15) → 100% REVIEW

Total: ~275 cases (~28% of training set). Rules whose inputs came from
trusted text/OCR extraction produce reliable decisions.

### 🟡 High accuracy — small residual error, acceptable

- `R4_revoked_sponsor` (56) — 98% DENIED (1 wrong)
- `R1_transit7` (34) — 97% DENIED (1 wrong)
- `R3_unpaid` (26) — 96% DENIED (1 wrong)
- `R5_stale` (23) — 91% DENIED (2 wrong)

Not investigating further until other work is done. Marginal returns.

### 🔴 PRIORITY: R_A1_paid_clean

- Fires 165 times at confidence 0.94
- **101 correct APPROVE (61%)**
- **25 catastrophic FAs (15% D→A)**
- **40 R→A errors (24%)**

This one rule accounts for the vast majority of our catastrophic false
approvals and R→A errors. Audit goal: find subcondition(s) that
distinguish the 101 correct approvals from the 65 mispredictions.

Hypotheses to test via correlator drill-downs (sorted by expected leverage):
1. `src_fee_status == 'ocr_only'` correlates with wrong-approvals
2. Presence of ANY OCR risk-keyword (biohazard, illegible, identity,
   embargo, revoked) → indicates wrong to approve
3. Minimum-packet structure (`n_trusted_image == 0`, `n_sources <= 4`)
   correlates with adversarial-clean cases → truth DENIED

Actions after investigation:
- Add subconditions to R_A1_paid_clean (e.g., require at least one
  corroborating positive-evidence source before approving)
- OR: keep rule, refine L7 safety net to catch more of the 65

### 🟠 R_A1_dip1_waived — smaller version of paid_clean

- Fires 21 times, 3 catastrophic FAs, 4 R→A. Same audit approach.

### 🟠 FALLBACK_extraction_fail — 35% of all cases

- 353 packets fall through. Truth distribution 33/30/37 (near base rate).
- Not a rule to fix — it's a signal that extraction failed.
- **Improving extraction moves cases OUT of this bucket into higher-signal rules.**

---

## When we do the audit

Once L5 emits typed Signals with cross-reference conflict detection:

1. Run pipeline against training set
2. For each case where prediction ≠ truth AND all inputs look correct at L5:
   - Categorize by the rule that fired
   - Look at aggregate: which rules produce the most errors?
3. For each rule with errors:
   - Read the samples
   - Decide: rule needs an extra condition, tighter threshold, split into two rules, or removal
4. Update the rule (single change at L6)
5. Re-score to confirm improvement without regressions elsewhere

## Rules we already know are candidates

Based on V1 close-out and V2 findings, before any V3 audit even starts:

- **R_A1_paid_clean** approves 15 catastrophic FAs. Question: are all 15
  cases where inputs are truly clean (rule problem) or where we're missing
  extraction (input problem)? Answer requires L4/L5 to emit
  cross-referenced flags and confidence.

- **R_A1_dip1_waived** approves 3 more catastrophic FAs from the DIP-1 +
  waived combo. Same question.

- **CONFIDENCE table** values are hand-picked estimates. Once we have
  agreement/disagreement data from L5, we could tune per-rule confidence
  based on how often the rule is actually right when inputs are trusted.
