# MIB Doc Challenge — Edge Case Catalog (Version 1)

Everything we learned about the task, the policy, the data, and the traps
while building the Version 1 stdlib solution.

**Final V1 score: 103.48 / 150** — see [ITERATIONS.md](ITERATIONS.md) for
the chronological journey from 50.77 baseline through 9 iterations.

---

# Version 1: Stdlib — CLOSED

**Constraint:** Python 3.12 standard library only. No `pypdf`, no OCR, no
external CV. We can inspect PDF bytes, but rendering images and doing
robust text extraction is out of scope.

## V1 close-out snapshot

| Metric | Result | Interview bar |
|---|---:|---:|
| **Total score** | **103.48 / 150** | 105 |
| Extraction | 34.46 / 50 | — |
| Classification | 56.12 / 80 | 55 ✓ |
| Calibration | 12.90 / 20 | — |
| Missing-case penalty | 0.00 / 10 | — |
| Catastrophic false approvals | 22 | flagged |
| Valid rows | 100 % | 90 % ✓ |

**Confusion matrix at V1 close:**
```
              →APPROVED   →DENIED   →REVIEW
APPROVED       132        0         157
DENIED          22        254       155
REVIEW          36        0         244
```

**Field-level extraction status (audited against training truth):**

| Field | CORRECT | WRONG | S_EMPTY | EMPTY | Notes |
|---|---:|---:|---:|---:|---|
| applicant_name  | 787 | 9   | 0 | 204 | 9 wrongs are all sponsor-attest disagreements w/ image-only truth |
| species_code    | 741 | 0   | 0 | 259 | perfect precision |
| home_world      | 677 | 0   | 0 | 323 | perfect precision |
| visa_class      | 695 | 0   | 0 | 305 | perfect precision |
| sponsor_id      | 697 | 0   | 0 | 303 | perfect precision |
| arrival_date    | 669 | 0   | 0 | 331 | perfect precision |
| declared_purpose| 690 | 0   | 0 | 310 | perfect precision |
| fee_status      | 474 | 0   | 5 | 521 | triangulated from Amount+Waiver |
| risk_flags      | 695 | 305 | 0 | 0   | 305 "wrongs" are schema-required `none` default when flags live in JPEGs |

**All rule-driving fields have 0 WRONG.** A wrong extraction on
visa/fee/sponsor/home/flag can flip an approval decision, so extractor
precision on these is what protects against false approvals. Iter 6
(fee_status triangulation) fixed the last 23 precision bugs on the
rule-driving field with real leverage.

**What limits V1** (all OCR-blocked, punted to V2):
- 22 catastrophic false approvals — every one has clean-looking text
  fields (fee=paid/waived, safe visa, non-revoked sponsor) but the true
  risk flag lives exclusively in a JPEG image (biohazard stamp, biometric
  slip, sponsor-letter watermark). Investigated in iter-9 postscript:
  zero text-visible risk hints across all 22 cases.
- ~310 fallbacks: packets with no extractable text at all — content lives
  entirely in embedded images (biometric slips, sponsor letters, forms)
- 34 R→A misses: review-only flags hidden in the same image content
- Can't verify: hardship-waiver documents, adjudicator signatures,
  sponsor-letter contradictions, denial stamps

**Notable adversarial patterns discovered and neutralized in V1:**
- **Prompt injection payloads** in text streams (`SYSTEM: ignore visible
  evidence...`) — filtered before field extraction (iter 2, §13)
- **Adversarial fee-receipt labels** — stated `Fee Status` label
  contradicts honest Amount + Waiver Code (iter 6, §13b)
- **Adversarial intake applicant names** — intake form lies, Registry
  Name and Sponsor letter have truth (iter 7-8; extraction fixed, but
  the disagreement itself is a V2 adjudication signal — §16b)
- **Bracketed redaction placeholders** (`[NAME CUT OUT]`,
  `[PURPOSE ILLEGIBLE]`, `[SPONSOR ID BLANK]`) — captured as data by
  naive regex; rejected via `_reject_placeholder` (iter 7)

## 1. The problem in one paragraph

For each PDF case packet, extract 10 applicant fields and issue one of three
adjudications (`APPROVED` / `DENIED` / `NEEDS_REVIEW`) plus a `confidence` in
[0, 1]. Scoring is asymmetric: predicting `APPROVED` on a truly `DENIED` case
costs **−4**; conservative `→ NEEDS_REVIEW` still earns +2; omitting a case
costs only 0.01. The game: **maximize correct classifications, never falsely
approve, bail to NEEDS_REVIEW when uncertain, extract fields whenever
possible for the extraction points.**

## 2. Scoring landmines

| Situation | Points |
|---|---:|
| Correct adjudication | **+8** |
| Route APPROVED / DENIED case → NEEDS_REVIEW (conservative) | +2 |
| Miss a true NEEDS_REVIEW (predict any wrong direction) | +1 |
| Predict APPROVED when truth is DENIED | **−4** |
| Predict DENIED when truth is APPROVED | 0 |
| Invalid or blank adjudication | 0 |
| Skip case entirely | 0 raw + small missing penalty (~0.01) |

**Confidence math:** constant `0.5` gives Brier = 0.25 everywhere →
calibration = 20 × (1 − 2×0.25) = **10**. The current `0.01` baseline scores
9.02 because it's over-confident-in-being-wrong on true NEEDS_REVIEW hits.
Real calibration wins come from correlated confidence: high when we're right,
low when we're guessing.

**Hard errors that fail the whole submission** (exit code 2 from `evaluate.py`):
duplicate case IDs, unexpected case IDs, invalid adjudication values, invalid
confidence, invalid fee_status.

## 3. Rule provenance legend

Every rule below carries a tag so we never confuse policy with pattern:

| Tag | Meaning |
|---|---|
| `[DOC]` | Written verbatim in `FIELD_MANUAL.md`, `PRD.md`, or `EVALUATION.md`. Ground truth. |
| `[DOC+CONFIRMED]` | Documented AND cleanly reproduced by training labels. |
| `[INFERRED-STRONG]` | Not in docs, but a deterministic pattern in training labels (e.g. 100% of a bucket, n ≥ 10). Needs cross-reference before we hardcode. |
| `[INFERRED-WEAK]` | Suggestive pattern with small n or exceptions. Do not encode as a rule yet. |
| `[PENDING]` | Open question / needs packet visual to confirm. |

**Every `[INFERRED-*]` rule must be manually cross-referenced before it lands in code.**

### Critical caveat for Version 2 (OCR pass)

**A 100% label-fit rule is not the same as the true policy rule.** A pattern
we inferred purely from labels may be a proxy for something visible in the
packet that we couldn't see in stdlib mode — a stamp, a note, a signature,
an image overlay. When Version 2 introduces OCR:

1. **Every `[INFERRED-*]` rule from Version 1 must be cross-referenced
   against the visual content of representative packets** before we trust it
   to generalize to the private test set.
2. **If a visible signal explains the same outcome more directly, prefer the
   visible signal.** For example, if Wolf-1061c cases carry a visible
   "planetary embargo" stamp that we missed, the true rule is "denial stamp
   present → DENY", not "home_world == Wolf-1061c → DENY". The label
   correlation was a shadow of the visible fact.
3. **Confidence architecture:** the final solution should assign confidence
   based on how many independent signals agree. A rule confirmed by BOTH
   the text layer AND the visible stamp gets high confidence. A rule that
   only fires on the text layer gets lower confidence (may be spoofed by
   hidden text). A rule that only fires visually gets its own tier.
4. **The private test set may have layouts / sponsors / worlds not seen in
   training.** Rules like "revoked sponsor list = these 6 SPN IDs" are
   guaranteed incomplete. The visible packet is the source of truth.

## 4. Decision safety principle

The −4 false-approval penalty means every APPROVE decision must be justified by
**positive evidence**, not the absence of negative signals. Concretely:

- **APPROVE** requires all approval preconditions to be positively confirmed
  from the packet (e.g. we *saw* `fee_status=paid`, we *saw* a valid non-revoked
  sponsor, etc.). If a required field cannot be positively read, we do NOT
  approve — even if nothing negative is visible.
- **DENY** can safely fire on a positive negative signal (a disqualifying flag
  is visible, a revoked sponsor is visible, `fee=unpaid` is visible). Denials
  are not penalized for being wrong (0 pts, not −4) unless we misread the case
  as approvable.
- **NEEDS_REVIEW** is the safe fallback for ambiguity, contradiction, or
  missing evidence. +2 pts vs. the −4 landmine.

Caveat: this principle applies to *approval* logic. Some legitimate rules
*do* hinge on the absence of a signal (e.g. `risk_flags == none` is a
necessary precondition for approval — the absence of any flag is itself the
positive signal). The distinction is: we can trust absences that are
observable and structured (an explicit "no flags" field in the packet), but
we cannot trust absences that are just "we couldn't find it" (a missing OCR
read).

## 5. Documented policy (from `FIELD_MANUAL.md`) `[DOC]`

### Evidence precedence — adversarial-aware
1. Visible MIB adjudicator stamp / signed manual note
2. Visible intake form fields
3. Visible biometric slip
4. Visible sponsor attestation
5. Visible registry extract
6. Machine-readable text layer

**Untrusted:** hidden white text, off-crop text, barcode/QR *instructions*
(metadata is fine), fake "answer key" fields, PDF text stream when it
contradicts visible content.

### Visa classes
| Class | Meaning | Cap | Special |
|---|---|---|---|
| `XW-1` | short-term technical | 30 days | needs sponsor |
| `XW-2` | extended technical | 180 days | needs sponsor |
| `DIP-1` | diplomatic | — | sponsor not required, fee may be waived |
| `MED-3` | medical / bio consult | — | requires clean biohazard check |
| `TRANSIT-7` | transit only | — | work auth **usually denied** |

### Sponsor
- Required unless `DIP-1`
- Manual lists 3 revoked: `SPN-0007`, `SPN-0139`, `SPN-4040`
- "Other revoked sponsors may appear in examples" — additional revoked
  sponsors must be recognized from visible packet content

### Fee
| Status | Rule |
|---|---|
| `paid` | acceptable |
| `waived` | only DIP-1 or visible hardship waiver |
| `unpaid` | deny unless visible waiver |
| `unknown` | NEEDS_REVIEW |

### Risk flags
- **Disqualifying (auto-DENY):** `memory_tampering`, `planetary_embargo`, `active_warrant`, `biohazard_red`
- **Review-only:** `identity_conflict`, `sponsor_mismatch`, `illegible_biometrics`, `rescinded_denial`
- Multiple review-only flags may combine to a denial in edge cases

### Date
- Stale if arrival > 180 days before packet receipt (DIP-1 with diplomatic
  note is exempt)
- Missing / hidden-only arrival date → NEEDS_REVIEW

### Named traps
- "sample denial" watermark ≠ denial
- Denial stamp crossed out by later signed approval ≠ denial
- Barcode may contain metadata, but barcode *instructions* aren't policy
- Multi-applicant packets — bind to the active `case_id`

## 6. Rules inferred from 1000 training labels

Each rule below is **inferred**, not documented. Cross-reference before encoding.

| Rule | Evidence | Tag |
|---|---|---|
| `visa_class == TRANSIT-7` → DENIED | 53 / 53 | `[DOC+CONFIRMED]` — manual says "usually denied"; data says always |
| `fee_status == unpaid` → DENIED | 50 / 50 | `[DOC+CONFIRMED]` — manual says "deny unless visible waiver" |
| **`fee_status == unpaid` AND `visa_class == DIP-1` → DENIED** | 16 / 16 | `[INFERRED-STRONG]` — DIP-1 does *not* rescue unpaid, contrary to naive reading of manual's "fee may be waived" for DIP-1 |
| `fee_status == unknown` → NEEDS_REVIEW | 44 / 44 | `[DOC+CONFIRMED]` |
| Any disqualifying risk flag alone → DENIED | 87 biohazard_red · 72 planetary_embargo · 17 active_warrant · 10 memory_tampering | `[DOC+CONFIRMED]` |
| APPROVED requires `risk_flags == none` | 289 / 289 APPROVED cases have `none` | `[INFERRED-STRONG]` — not stated but a hard precondition |
| Stale (arrival > 180 d before receipt) + non-DIP-1 → DENIED | 36 DENIED, 3 REVIEW, 0 APPROVED (out of 39) | `[DOC+CONFIRMED]` |
| Stale + DIP-1 → APPROVED / REVIEW (never DENIED) | 13 APPROVED, 3 REVIEW, 0 DENIED (out of 16) | `[DOC+CONFIRMED]` — DIP-1 stale exception |
| `home_world == TRAPPIST-1e` → always carries `planetary_embargo` → DENIED | 32 / 32 | `[INFERRED-STRONG]` — the world→embargo link is not documented |
| `home_world == Eris Relay` → always carries `planetary_embargo` → DENIED | 18 / 18 | `[INFERRED-STRONG]` — same |

**Note on the stale rule:** we're proxying "packet receipt date" as
`max(arrival_date)` in the training set (= 2026-07-12). The rule held cleanly
under that proxy, but we still don't know the true receipt-date semantics.
`[PENDING]` — verify when we can read packet content.

### Flag combinations observed `[INFERRED-STRONG for outcomes]`
| Combo | n | Outcome |
|---|---:|---|
| `illegible_biometrics + planetary_embargo` | 14 | 14 DENIED (disqualifier dominates) |
| `biohazard_red + illegible_biometrics` | 14 | 14 DENIED |
| `illegible_biometrics + rescinded_denial` | 12 | 11 REVIEW · 1 DENIED |
| `identity_conflict + illegible_biometrics` | 9 | 7 REVIEW · 2 DENIED |
| `illegible_biometrics + sponsor_mismatch` | 8 | 6 REVIEW · 2 DENIED |
| `active_warrant + illegible_biometrics` | 3 | 3 DENIED |
| `illegible_biometrics + memory_tampering` | 3 | 3 DENIED |

**Pattern:** two review-only flags together lean toward NEEDS_REVIEW (~7:1) but
occasionally escalate to DENIED — the visible packet contains the deciding
signal. Any review-only + any disqualifier is DENIED. Documentation says
"multiple review-only flags may combine into a denial in edge cases" `[DOC]` —
so the escalation itself is expected, but *which* combinations escalate is
`[INFERRED-WEAK]` given small n.

## 7. Sponsor revocation `[INFERRED-STRONG]`

### What the docs say `[DOC]`
> "An applicant needs a valid `SPN-####` sponsor unless they are applying under `DIP-1`.
> Known revoked sponsors in the public manual: `SPN-0007`, `SPN-0139`, `SPN-4040`.
> Other revoked sponsors may appear in examples."

### What we inferred from labels

Training data reveals a deterministic pattern for 6 sponsors — the 3 manual
ones and 3 additional ones — that behave identically:

| Sponsor | Total | Non-DIP-1 outcomes | DIP-1 outcomes | Source |
|---|---:|---|---|---|
| `SPN-4040` | 20 | 15 → **all DENIED** | 5 → all APPROVED | `[DOC]` |
| `SPN-7331` | 19 | 14 → **all DENIED** | 5 → all APPROVED | `[INFERRED-STRONG]` |
| `SPN-0139` | 18 | 13 → **all DENIED** | 5 → 4 APPROVED, 1 REVIEW | `[DOC]` |
| `SPN-2718` | 18 | 13 → **all DENIED** | 5 → 3 APPROVED, 2 REVIEW | `[INFERRED-STRONG]` |
| `SPN-0007` | 15 | 13 → **all DENIED** | 2 → all APPROVED | `[DOC]` |
| `SPN-9090` | 13 | 11 → **all DENIED** | 2 → all APPROVED | `[INFERRED-STRONG]` |

**Combined rule (inferred):**
`sponsor_id ∈ revoked_list  AND  visa_class ≠ DIP-1  →  DENIED`

**Zero DIP-1 cases with any of these 6 sponsors were DENIED**, confirming the
DIP-1 sponsor-exemption is airtight *and* symmetric across manual + inferred
revoked sponsors. This is a specific instance of the safety principle in
section 4: DIP-1 does not require a sponsor `[DOC]`, so a revoked sponsor on
a DIP-1 case is simply irrelevant — the absence of the *requirement* (not
the absence of evidence) is what makes DIP-1 approvable.

### Low-confidence candidates `[INFERRED-WEAK]`

Six more sponsors show the same pattern but with only 2 non-DIP-1
appearances each — not enough evidence to call them revoked:

`SPN-1720`, `SPN-1934`, `SPN-3417`, `SPN-4699`, `SPN-6368`, `SPN-4146`

**Do not encode these as revoked** until we see either (a) more cases in the
private validation/test set, or (b) a visible revocation signal in the packet.
Two DENIED cases could be coincidence — the sponsor might be valid and both
cases denied for other reasons.

### What this changes about the visual-inspection hunt

Originally I asked you to look for revocation stamps in these packets. Given
the label pattern is deterministic, **the packet may not carry any special
visual signal at all** — the sponsor ID alone is the disqualifier. Still
worth confirming with one packet each of `SPN-7331`, `SPN-2718`, `SPN-9090`
just to check whether a stamp/note exists (would be nice-to-have redundant
evidence).

## 8. Home-world embargo `[INFERRED-STRONG]`

### What the docs say `[DOC]`
The manual defines a `planetary_embargo` risk flag as disqualifying but does
NOT enumerate which home worlds are embargoed. That mapping must be inferred.

### What we inferred from labels

Three home worlds show 100% DENY rates on non-DIP-1 cases. Two enforce it via
the `planetary_embargo` flag on every case; one does NOT flag every case but
still denies deterministically.

| Home world | Total | Pattern | Structure |
|---|---:|---|---|
| `TRAPPIST-1e` | 32 | All 32 flagged `planetary_embargo` → all DENIED | Hard embargo. DIP-1 does **not** exempt. R2 catches them via the flag. |
| `Eris Relay` | 18 | All 18 flagged `planetary_embargo` → all DENIED | Hard embargo. DIP-1 does **not** exempt. |
| `Wolf-1061c` | 77 | non-DIP-1 always DENIED (many not flagged); DIP-1 exempt | **Soft embargo.** 5 of the 5 DIP-1 DENIED cases have a personal disqualifying flag (R2 catches them); no DIP-1 case was denied for the world alone. |

**Combined rule (inferred):**
`home_world == "Wolf-1061c" AND visa_class != "DIP-1" → DENIED`

TRAPPIST-1e and Eris Relay don't need a separate rule because R2 catches every
case via the flag. Encoding them into an embargoed-home list would be
defensive but not additive.

### Interaction with other rules
- **Wolf-1061c DIP-1 exemption is only from the world embargo.** A DIP-1
  applicant from Wolf-1061c with a personal disqualifier (e.g. `active_warrant`,
  `biohazard_red`) is still DENIED via R2. This matches the docs: personal
  disqualifiers override diplomatic exemption.
- **Wolf-1061c review-only flags escalate to DENY when the applicant is
  non-DIP-1.** Previously I flagged this as R6 (multi review-only combos
  causing DENY). It's actually R4b firing — those cases are Wolf-1061c
  cases and the review-only flags are incidental. **R6 is rejected as a
  standalone rule.**

## 9. Rejected inference candidates

Rules we investigated and explicitly rejected because they would cause
false-approvals or false-denials on training data:

| Candidate rule | Why rejected |
|---|---|
| MED-3 + `fee=waived` → DENY | Fires on 11 APPROVED + 16 REVIEW cases; catches 0 additional DENIED (all MED-3+waived DENIEDs already caught by other rules). |
| Multi review-only flags → DENY | Fires on 24 REVIEW cases across 3 flag combos. The DENIED-in-those-combos were all Wolf-1061c cases now caught by R4b. Route these to REVIEW instead. |
| `home_world` = high-DENY world → DENY (any world other than the 3 embargoed) | Highest non-embargo DENY rate is Mars Dome-7 at 47%, still leaves 53% APPROVED/REVIEW. Too high FP rate. |
| Sponsor whitelist / trusted-sponsor rules | 864 unique sponsors across 1000 cases means most sponsors have n=1. Insufficient evidence for whitelisting any specific sponsor. |

## 10. APPROVE vs REVIEW rules `[INFERRED-STRONG]`

Applied to the 569 cases where no DENY rule fires:

### Deterministic REVIEW rules

| Rule | Text | Coverage in survivor pool | False positives |
|---|---|---:|---|
| R_R1 | Any `risk_flags` value present → REVIEW | **218 / 218 (100%)** | 0 |
| R_R2 | `fee_status == "unknown"` → REVIEW | **44 / 44 (100%)** | 0 |

Both hold with zero exceptions across every combination of visa, sponsor,
home, and purpose. `R_R2` overlaps with `[DOC]` policy but is deterministic
on labels too.

### APPROVE rule

The 316 "clean" survivors (no flags, fee ∈ {paid, waived}) split
**289 APPROVED / 27 REVIEW / 0 DENIED**. The 27 hidden REVIEW cases are
indistinguishable from APPROVED using labels alone; their deciding signal
must be visible in the packet.

**Rule (with waived-fee caveat below):**
`no DENY rule fires AND risk_flags == none AND fee_status ∈ {paid, waived} → APPROVE`

**Expected training outcome from this rule:**
- 289 correct APPROVED (×8 = 2,312 raw)
- 27 A→R misses (missing true REVIEW = +1 raw each) = 27 raw
- 0 catastrophic false approvals (truth=DENIED = 0 in this bucket)
- **Net from this bucket: 2,339 raw**

Routing all 316 to REVIEW instead would score only 794 raw. APPROVING wins
by 1,545 raw with **no false-approval risk on training**.

### Waived-fee treatment `[INFERRED-STRONG, PENDING-V2-VERIFY]`

Manual says fee waivers are only valid for DIP-1 or a visible hardship
waiver. In training:
- DIP-1 + waived + clean: 33 APPROVED / 3 REVIEW → clean approval
- Non-DIP-1 + waived + clean: 37 APPROVED / 9 REVIEW → **assumes visible hardship waiver**

Version 1 decision: **APPROVE the non-DIP-1 waived cases** matching training
frequency. Each such decision is tagged in the audit log as
`ASSUMED_HARDSHIP_WAIVER` so Version 2 can revisit these cases with OCR to
verify the visible waiver actually exists.

- If Version 2 OCR sees a hardship waiver stamp/note → confidence stays high
- If Version 2 OCR sees no hardship waiver → downgrade to REVIEW
- If Version 2 OCR is inconclusive → keep APPROVED but lower confidence

## 11. Coverage of the full rule pipeline

Applying all rules to 1000 training labels:

| Bucket | Rule | Cases correctly classified | Raw pts |
|---|---|---:|---:|
| DENIED | R1–R5 + R4b | 431 / 431 | 3,448 |
| REVIEW | R_R1, R_R2 | 253 / 253 (all with-flags + no-flag-unknown-fee) | 2,024 |
| APPROVED | R_A1 | 289 / 316 in the clean bucket | 2,312 |
| APPROVED misses | 27 hidden REVIEW cases | (scored as missing_review +1 each) | 27 |
| **Total** | | | **7,811 / 8,000 raw = 97.6% = ~78 / 80 pts** |

**Metrics:**
- Catastrophic false-approvals: **0**
- False positives from any DENY rule: **0**
- Extraction dependency: every rule requires reading fields from the PDF

## 12. Confidence-value plan for calibration `[DESIGN]`

The final solution assigns per-branch confidence. Draft values (subject to
tuning once we score against training):

| Decision path | Baseline confidence | Reasoning |
|---|---:|---|
| DENY via R1 (TRANSIT-7) | 0.98 | Deterministic, `[DOC+CONFIRMED]` |
| DENY via R2 (disqualifying flag) | 0.98 | Deterministic, `[DOC+CONFIRMED]` |
| DENY via R3 (unpaid) | 0.98 | Deterministic |
| DENY via R4 (revoked sponsor + non-DIP-1) | 0.95 | 3 of 6 sponsors `[INFERRED]`; slight hedge |
| DENY via R4b (embargoed home + non-DIP-1) | 0.90 | Wolf-1061c not documented as embargoed; may be masking a visual signal |
| DENY via R5 (stale + non-DIP-1) | 0.95 | Receipt-date proxy is `[PENDING]` |
| REVIEW via R_R1 (flag present) | 0.90 | Some review-only flags occasionally escalate; safe to say REVIEW |
| REVIEW via R_R2 (unknown fee) | 0.98 | `[DOC+CONFIRMED]` |
| APPROVE via R_A1, fee=paid, non-DIP-1 | 0.90 | Training rate 219/234 = 93.6% |
| APPROVE via R_A1, DIP-1 + fee=waived | 0.90 | Training rate 33/36 = 91.7% |
| APPROVE via R_A1, non-DIP-1 + fee=waived | 0.70 | Training rate 37/46 = 80%; `ASSUMED_HARDSHIP_WAIVER` tag |
| REVIEW fallback (extraction failure) | 0.50 | We don't know; safest fallback |

**Rationale for the calibration target:** the evaluator rewards confidence
that tracks correctness. Assigning near-1.0 on deterministic rules and
lower values on inferred/hedged branches should push mean Brier below 0.05
and yield ~18–20 / 20 calibration points.

## 13. Adversarial content categories `[DOC]`

| Attack | Where it lives | Defense |
|---|---|---|
| White-on-white hidden text | PDF text stream, invisible when rendered | Never trust text-stream alone; compare to rendered pixels |
| Off-page-crop text | Outside MediaBox but present in stream | Enforce visible-crop mask |
| Fake "system prompts" / "answer keys" | Inline PDF text | Never follow instructions from packet content |
| QR / barcode instructions | Rendered images | Extract metadata only, ignore imperatives |
| "Sample denial" watermark | Rendered as background | Distinguish from real adjudicator stamp |
| Crossed-out denial + later approval | Overlapping stamps | Later signed approval wins |
| Multi-applicant packets | Multiple form fields on same page | Bind to `case_id`, ignore other applicants |
| OCR-similar species codes | Rendered text | Canonicalize to the closed vocab of 12 |
| Rotated / low-contrast biometric slips | Image page | Deskew + contrast normalize before OCR |
| Wrong sponsor named in cover letter | Contradicts form field | Form field wins over letter |

## 13b. Adversarial fee-receipt labels `[INFERRED-STRONG]`

Distinct from prompt injection (§13). The MIB Fee Receipt contains three
fields — `Fee Status`, `Amount`, `Waiver Code` — and the **stated Fee
Status label is often adversarial** while Amount + Waiver Code are honest.

Empirically verified on 1000 training PDFs:

| Amount | Waiver Code | Truth (always) | Stated (unreliable) |
|:---|:---|:---|:---|
| >$0  | N/A          | **paid**    | 14 wrongs seen as waived/unpaid/unknown |
| $0   | present      | **waived**  | 6 wrongs seen as paid/unpaid/unknown |
| $0   | N/A + stated=paid/waived | **unknown** (label is impossible) | 3 wrongs |
| $0   | N/A + stated=unpaid/unknown | trust stated (honest case) | — |
| missing | —          | — (fall back to stated only) | — |

**Rule:** triangulate `fee_status` from Amount + Waiver Code when both are
visible; only trust the stated label when Amount is missing or when the
Amount/Waiver combo is genuinely ambiguous ($0 + N/A). Verified 31 fixes,
0 regressions when added to V1 in iter 6.

Safety impact: prevents R3_unpaid (confidence 0.98 DENY) from firing on
cases like MIB-000158 where stated=unpaid but Amount=$809 + N/A → truth is
paid. Also prevents R_A1_paid_clean (0.94 APPROVE) from firing on
paid-labeled cases with $0 receipts.

## 14. Extraction schema quirks `[DOC+CONFIRMED]`

- **Closed vocabularies (bound your outputs to these):**
  - Species: 12 codes (TRIANGULAN, JOVIAN_GASFORM, CENTAURI_SYNTH, LUNA_SECURID, KAIJU_MICRO, ORION_GRAYS, ALPHA_DRACONIAN, SIRIUS_AVIAN, VENUSIAN_MYCELIAL, AQUARIAN_MANTIS, ARCTURIAN, ANDROMEDAN)
  - Home worlds: 13 (Luyten-b, Europa Station, Titan Freeport, Barnard-c, Gliese-581g, Mars Dome-7, Kepler-186f, Sirius Outpost, Wolf-1061c, Proxima-b, Zeta Reticuli, TRAPPIST-1e, Eris Relay)
  - Visa classes: 5 (XW-1, XW-2, DIP-1, MED-3, TRANSIT-7)
  - Fee statuses: 4 (paid, waived, unpaid, unknown)
  - Risk flags: 8 named values + `none`
  - Adjudication: 3 values (APPROVED, DENIED, NEEDS_REVIEW)
- **Open vocabularies:**
  - `applicant_name`: unique per case
  - `sponsor_id`: 864 unique across 1000 cases; format `^SPN-\d{4}$`
  - `declared_purpose`: ~15 common phrases (reactor maintenance, field repair, medical consult, research, cultural exchange, translation, archive audit, xenobotany, diplomatic, transit, …)
- **Format contracts:**
  - `case_id`: `^MIB-\d{6}$`
  - `arrival_date`: ISO `YYYY-MM-DD`, must parse
  - `risk_flags`: pipe-delimited; evaluator normalizes by alphabetical sort so input order doesn't matter
  - `adjudication`: UPPERCASE
  - `fee_status`: lowercase
- **Unrecoverable fields:** the evaluator's private admin metadata marks
  some fields as genuinely unrecoverable (evidence destroyed). Those fields
  are dropped from both the numerator and denominator — a wrong guess costs
  the same as no guess on those specific cells.

## 15. PDF-layer facts `[INFERRED-STRONG from byte inspection]`

- 1000 packets, 50 KB – 1 MB, mean ~470 KB
- PDF 1.4, ReportLab-generated, mixed digital text + embedded `/Image` objects
- Streams chained through `ASCII85Decode` (likely also `FlateDecode`)
- Typical packets are ~3 pages; multi-page packets exist (up to 16+ streams
  in one file observed)
- Contains real fonts AND images → **cannot rely solely on text-stream
  extraction; scanned pages / stamps / biometric portraits need OCR**
  (out of scope for Version 1)

## 16. Open questions still to resolve `[PENDING]`

1. **What's the actual "packet receipt date"?** The manual says stale is
   >180 days before receipt. Labels don't include receipt date. I used
   `max(arrival_date)` = `2026-07-12` as proxy and the DIP-1 stale exception
   held cleanly, but this needs verification when we can read packets.
2. **Full revoked-sponsor list.** Must be recoverable from visible packet
   evidence, not a hardcoded list.
3. **What visible signal in the 2 alone-`rescinded_denial` DENIED cases
   pushed them past review?** Suggests the rescission stamp can be rejected
   in some packets.
4. **How do multi-applicant packets bind to `case_id`?** Need packet visuals.
5. **Can we get readable PDF text with stdlib?** Need to chain
   ASCII85 → Flate → parse text operators. If we skip stdlib rendering,
   we're OCR-blind for images and text-stream-only for digital.

## 16b. V2 adjudication rules to validate `[FLAGGED — DO NOT ENABLE IN V1]`

Signals we discovered during V1 that could plausibly become adjudication
rules, but which we have NOT validated against the training-truth
adjudication distribution. Each needs a cross-tab before implementing.

1. **Intake-vs-Registry name disagreement → NEEDS_REVIEW.** During iter 8
   we found ~17 cases where the intake form Applicant differs from
   Registry Name (external database). The truth always matches Registry,
   meaning the intake form is lying about the applicant's identity. A
   packet that lies about identity arguably belongs in NEEDS_REVIEW
   regardless of other signals. **Before enabling in V2:** measure how
   the training-truth adjudication distributes across these 17+ cases —
   if truth is mostly REVIEW/DENY, it's a safe rule; if truth is mostly
   APPROVE, adding this rule would create false-review regressions.

2. **Fee-receipt label vs. Amount/Waiver disagreement → REVIEW.** Same
   family as #1 — the stated Fee Status label disagrees with the honest
   Amount/Waiver Code fields on ~31 cases. We currently correct the
   extraction (iter 6) but do not flag the disagreement. Same validation
   needed.

3. **Sponsor letter vs. intake form contradiction.** Sponsor letters
   contain the applicant name, sponsor ID, and sometimes purpose or
   dates. Cross-referencing all mentions against the intake form and
   flagging disagreements is a "packet integrity" signal V2 could use
   for REVIEW routing.

## 17. Version-1 policy engine (what stdlib can implement)

Even without reading PDF content, the label-only rules above let us
implement a policy engine that:
- **Auto-DENIED** whenever we can extract: TRANSIT-7 visa, unpaid fee, any
  disqualifying flag, revoked sponsor from the known list of 3.
- **Auto-NEEDS_REVIEW** whenever we detect: unknown fee, missing arrival
  date, two-or-more review-only flags, or extraction failure.
- **Cautious APPROVED** only when we can positively confirm all of: paid
  fee, no risk flags, non-TRANSIT visa, non-revoked sponsor, in-window
  arrival date, or DIP-1 exempted from staleness.
- Otherwise → NEEDS_REVIEW (safe default).

But this requires *actually extracting* those fields. Without decoding PDF
streams we have no field values. So Version 1 is really "route everything
to NEEDS_REVIEW with honest confidence" — the current baseline.

The next unlock is decoding the PDF text stream with stdlib (chained
ASCII85 + Flate + PDF text-operator parsing). That's the pivot point from
50.77 → ~75+.

## 18. Cases flagged for visual inspection

The following case IDs are the most informative packets to open and look
at. Grouped by the edge-case they exemplify.

### A. `rescinded_denial` DENIED (surprising — flag alone usually → REVIEW)
- **MIB-000067** — visa=XW-1, sp=SPN-1494, flags=illegible_biometrics|rescinded_denial, home=Wolf-1061c
- **MIB-000399** — visa=XW-1, sp=SPN-2677, flags=rescinded_denial (alone!), home=Wolf-1061c
- **MIB-000711** — visa=XW-2, sp=SPN-6368, fee=waived, flags=rescinded_denial (alone!), home=Wolf-1061c

### B. Inferred-revoked sponsors — confirmation only (see section 7)

The label pattern is now deterministic (revoked sponsor + non-DIP-1 → DENIED
in 79/79 cases across 6 sponsors). Visual inspection is only needed to
confirm whether a redundant stamp/note exists in the packet. Pick one from
each cluster:

- `SPN-7331` — MIB-000117 (denied) vs MIB-000092 (DIP-1 approved) — same
  sponsor, opposite outcome; compare visually
- `SPN-2718` — MIB-000127 (denied) vs one of its 3 DIP-1 APPROVED
- `SPN-9090` — MIB-000069 (denied) vs MIB-000362 (both denied; both non-DIP-1)

### C. Review-only flag combos that escalated to DENIED
- **MIB-000003** — flags=illegible_biometrics|sponsor_mismatch
- **MIB-000016** — flags=illegible_biometrics|sponsor_mismatch
- **MIB-000067** — flags=illegible_biometrics|rescinded_denial
- **MIB-000261** — flags=identity_conflict|illegible_biometrics
- **MIB-000862** — flags=identity_conflict|illegible_biometrics

### D. MED-3 DENIED with NO risk flags (sample of 44) — the visible packet holds the reason
- **MIB-000013** — sp=SPN-6818 (unfamiliar)
- **MIB-000040** — sp=SPN-1336, arrival 2025-08-06 (borderline stale)
- **MIB-000041** — sp=SPN-0007 (manual-revoked)
- **MIB-000105** — sp=SPN-4040 (manual-revoked)
- **MIB-000127** — sp=SPN-2718 (suspected revoked)
- **MIB-000147** — sp=SPN-1720, fee=unpaid (explains it)
- **MIB-000199** — sp=SPN-7331 (suspected revoked)
- **MIB-000217** — sp=SPN-4040 fee=waived (manual-revoked + non-DIP-1 waiver)

### E. Stale + DIP-1 APPROVED (all 13) — visible diplomatic note in play
- MIB-000305, MIB-000396, MIB-000434, MIB-000451, MIB-000598, MIB-000606,
  MIB-000631, MIB-000654, MIB-000741, MIB-000840, MIB-000886, MIB-000953, MIB-001000

### F. Reference: clean APPROVED (template baseline)
- MIB-000005, MIB-000024, MIB-000027 — XW-2, paid, no flags

### G. Reference: NEEDS_REVIEW (typical triggers)
Unknown fee:
- MIB-000008, MIB-000025, MIB-000043
illegible_biometrics alone:
- MIB-000009, MIB-000045, MIB-000056

---

## Suggested review pass

Open these packets side-by-side and look for:
1. **Optional / low priority:** does a visible revocation stamp exist in the
   revoked-sponsor packets (Category B)? If yes, it's redundant confirmation
   for our sponsor rule; if no, the sponsor ID alone is doing the work.
2. **What does the visible packet show for MIB-000399 vs a review-only
   `rescinded_denial` case (Category A vs G)?** — the difference is the
   deciding signal.
3. **What does the DIP-1 diplomatic note look like in stale-approved
   packets (Category E)?** — needed to implement the stale exception.
4. **How is the same field laid out across a clean APPROVED (Category F)
   vs a messy DENIED (Category D)?** — informs template parsing.
5. **Are there visible watermarks / crossed-out stamps / hidden text
   layers you can spot by eye?** — validates the adversarial threat model.

Feed anything you notice back and it'll update this doc.

---

## Pending cross-reference before implementation

Before any `[INFERRED-*]` rule lands in `solution.py`, cross-reference it
against:
- A packet visual for the corresponding case IDs (section 14)
- The negative bucket (do any cases *break* the rule? — my analysis has
  already checked this for the top rules; re-verify anything with n < 10)
- The scoring impact if the rule turns out to be wrong (false-approval risk?
  false-denial cost 0, so denial-side inference is low-risk)

**Rules currently blocked on cross-reference:**
- Revoked-sponsor list expansion beyond the 6 confirmed ones (section 7)
- MED-3 waived-fee denial trigger (section 8) — need packet visual
- 2 `rescinded_denial`-alone DENIED cases (section 8) — need packet visual
