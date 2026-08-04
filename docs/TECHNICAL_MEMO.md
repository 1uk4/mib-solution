# Technical Memo — Layer-by-Layer Review

Engineer-facing companion to `docs/TECHNICAL_DEBRIEF.md` (which is the
reviewer-facing summary). One section per layer, written as each layer is
reviewed. Records: what the layer does, the decisions that shaped it, and
what the review measured/changed. Improvement candidates live in
`v4/OBSERVATIONS.md`; this memo records outcomes.

Review state: **L1 done, L2 done.** L3–L7 pending.

---

## L1 — acquire.py: PDF → list[Source]

**Job:** enumerate every addressable content source in a PDF — text
streams and images — as `Source` objects carrying raw bytes + header
metadata. Interprets nothing; the only layer that knows PDFs exist.

**How:** two precompiled regexes over raw bytes. `STREAM_RE` matches
`<< dict >> stream…endstream` (deliberately fails on nested dicts;
validated 0 missed streams / 1000 PDFs). Streams fork on
`_is_image_stream`: `/Subtype /Image` or an image codec ⇒ image;
everything else ⇒ text stream (raw bytes + filter chain, decoded later
by L2). Images are reconstructed to OCR-ready bytes: DCTDecode payloads
are passed through as JPEG (magic-byte verified); FlateDecode raw pixels
are rebuilt via PIL with a strict `len(pixels) == w*h*channels` check.
Undecodable image ⇒ silently dropped; unreadable file ⇒ `[]`. L1 never
throws — missing evidence degrades to NEEDS_REVIEW downstream, a crash
would forfeit the packet.

**Metadata contract:** `width`/`height` (header-only) feed L2's gates;
`bytes`/`mode` are audit-only. Absence fails toward "do OCR".

**Review outcome (2026-08-03):**
- Corpus census: 1,956 DCT images + 2,123 Flate images; 0 CCITT streams.
- Brightness statistics (mean/std over a thumbnail, inherited from v3)
  made **zero gate decisions in 4,079 images** — blank-canvas skip never
  fired; doc-gate brightness veto never fired; the doc gate is
  empirically identical to "is a JPEG". **Removed** (user decision,
  B2-6): saved a full pixel decode per image, L1 140.6 → 110.5 ms/PDF.
- Dead `letter_sized`/`small_square` metadata removed (zero consumers);
  stream-dict regexes precompiled; contract documented.
- Parity held: mini-parity 79/79 + 25/25 vs native golden; 141 tests.

---

## L2 — extract.py: Source → .content

**Job:** populate `content` on every source. Text streams get a stdlib
decode; images get gated, cached, multi-pass Tesseract OCR. L2 judges
nothing — garbled OCR and injection text still get populated; trust is
L3's call.

**Text path:** `_decode_stream` applies the declared filter chain
(ASCII85 → `a85decode` to the `~>` terminator; Flate → `zlib`; anything
else ⇒ None) and `_extract_text_stream` pulls every `(…) Tj` text op via
`TEXT_OP_RE`, unescaping PDF strings (`_pdf_unescape`: named escapes +
octal). Survey basis: Tj is 100% of text ops in this corpus — no kerned
arrays, no hex strings.

**OCR path, three decisions per image:**
1. `_should_ocr` — skip only sub-100px icons (header dims, free).
   Blank-canvas rule removed after 0 fires / 4,079 images.
2. `_looks_like_document` — ≥800px ⇒ multi-pass; else single-pass.
   Empirically identical to "is a JPEG" on training.
3. Pass strategy by config: `ocr_sharpen=True` (default) ⇒ triple
   (psm 6 baseline + psm 3 on 2× upscale + psm 6 on unsharp-masked),
   else dual. Outputs are **concatenated, not best-of**: downstream
   regexes match whichever pass read a field correctly. Measured: dual
   +22% correct fields on intake forms; sharpen bundle +10.27 pts.
   The dual path is config-reachable insurance, not dead code.

**Tesseract invocation:** one subprocess per pass, stdin/stdout, 15 s
timeout, empty-string on any failure. `extra_flags` carries the
user-words dictionary (`v4/data/tesseract_user_words.txt`, biases
recognition toward domain enums) and L4's char-whitelist re-OCR.

**Cache:** content-addressed — `sha256(image_bytes) + tag + ".txt"` under
`config.ocr_cache_dir` (dev-only; None ⇒ no-op in production).
Architecturally safe: same bytes → same text, so caching can never change
output. The tag encodes the OCR configuration: strategy suffix
(`""`/`"_dual"`/`"_triple"`) + flag suffix (`"_uw"` when user-words on) —
so config changes miss rather than collide. **The tag strings are a
compatibility contract** with the warm cache on disk (hours of OCR);
pinned by tests.

**Review outcome (2026-08-03):** gates simplified during the L1 review
(above). Applied: Tesseract timeout named (`_TESSERACT_TIMEOUT_S`, hang
guard not budget), `_upscale_png`/`_sharpen_png` deduplicated via
`_pil_png`, user-words flags hoisted to once per multi-pass call.

Pass-composition experiment (120 packets, passes OCR'd separately,
pipeline replayed per composition; complements the 2026-07-31 full-score
bundle sweep):
- Union order: flips 9–16 fields per reordering, net ±3 = noise.
  Baseline-first kept — measured, not inherited.
- 2× upscale pass: −21 fields if removed (the workhorse); 1.5× retains
  all but −2 at ~44% less compute — runtime lever, not needed at
  3.3 s/PDF vs 6 s budget.
- Sharpen: extraction-neutral (−4±noise); its +10.27 was
  classification-side (risk stems → L7 OCR-signal path). Counterfactual
  queued in OBSERVATIONS B2-7.
No composition changes shipped — parity preserved.

---

## L3 — filters/: the trust boundary

**Job:** decide evidence admissibility, exactly once. Two filter
families: **sanitizers** mutate content in place (strip adversarial
substrings, source stays trusted — line-level surgery because mixed
streams like MIB-000115 carry real fields *and* injection); **detectors**
exclude whole sources (`trusted=False` + reason, first hit wins).
Sanitizers run first so a source is never condemned for content that
cleaning would have removed.

**Injection** (sanitizer): drop any line containing one of 8 markers.
Principle: an embedded instruction is not a data value. Measured: fires
on **98% of packets** (`SYSTEM:` lines are ambient), 1,321 lines dropped,
never via image OCR.

**Redaction** (sanitizer): strip bracketed ALL-CAPS placeholders +
`REDACTED?`. Measured: 268 sources, **197 via image OCR**. Mixed-case
brackets pass by design; L4's `_reject_placeholder` is the second lock —
**9 genuine catches** post-sanitization on training, so the depth is
real, not redundant. (`SPONSOR_ATTESTS_RE`'s placeholder alternative:
0 matches — vestigial, harmless.)

**Illegibility** (detector, images only): domain-vocab real-word ratio
< 0.30 with ≥5 tokens ⇒ exclude. Domain vocabulary, not a dictionary —
a general wordlist over-matches OCR garbage. Measured: **780/4,079
images excluded (19%)**; 703 auto-kept (<5 tokens); ~1,377 empty-OCR.
**249 images (6.1%) sit within ±0.05 of the threshold** — the knife-edge
mass behind cross-environment drift (B2-8): engine-version OCR changes
flip borderline exclusions, cascading into signals and the
`any_illegibility_excluded` bit L7 reads. Threshold untouchable per
brief Rule 4.

**Review outcome (2026-08-03):** sanitizer audit strings — previously
computed and discarded — now land in `metadata["sanitize_audit"]`
(output-inert, feeds inspection tooling); sanitizer docstrings corrected
to the actual (should_exclude, audit_reason) contract, with measured
fire rates noted. No threshold or logic changes (Rule 4). Census script:
`v3/dev/analysis/filter_census.py`.

## L4 — signals.py: documents become claims

**Job:** run the canonical field extractor over trusted content at three
scopes and emit typed, provenance-carrying, authority-weighted `Signal`
records, so L5 resolves disagreement instead of racing. ~17 signals per
packet.

**Three emission tiers:** combined text (conf 1.0 — authoritative;
cross-stream `Manual correction:` overrides only work merged), per-stream
and per-image (conf by Field Manual authority level via
`source_type.classify`: adjudicator note 0.99 → intake 0.90 → biometric
0.85 → attestation 0.80 → registry 0.75 → unrecognized 0.70). One
deliberate under-bid: combined `fee_status="unknown"` emits at 0.4 — a
missing-extraction sentinel that lets a per-source recovery win.

**The extractor's adversarial defenses** (absorbed from v1): name
precedence Registry > Sponsor-attests > Intake (intake lies in ~17
cases); fee triangulation from honest Amount/Waiver vs the lying stated
label (23 cases); visa label-before-vocab-scan; manual corrections trump
everything. Validation is reject-don't-sanitize; `risk_flags="none"` is
rejected as evidence (absence ≠ clean).

**Fuzzy recovery ladder** (fires only on strict miss, confidences lose
all ties): separator-tolerant flags @0.6, digit-lookalike sponsor repair
@0.6 (shape-only — no known-good list, 10⁴ space vs 864 seen),
SequenceMatcher label recovery (enum-snap only for Manual-listed enums),
char-whitelist re-OCR for format failures.

**Census (2026-08-03, key numbers):**
- Win rates at L5: combined_text 5,589; per_image_strict 1,186;
  fuzzy_label 172 (the workhorse recovery — 76 home_world); per_stream
  **24 of 7,997 emitted** (23 = the fee sentinel, which went 23/23);
  fuzzy_sponsor 4, fuzzy_flag 3 (tiny but verdict-relevant).
- **Per-stream tier reframed (B2-9):** a metadata + sentinel-rescue
  mechanism — its losing candidates feed `_agreement`/corroboration —
  not a value source. Not removable; never again describable as one.
- **Re-OCR is shadowed insurance (B2-10):** 2,572 invocations, 1
  Tesseract call, 0 repairs — everything it used to fix arrives valid
  under the full bundle. Kept: inert, free, and reactivates exactly when
  upstream OCR degrades (the container scenario).
- `fields_empty` maps L6's fallback feed: risk_flags empty in 768
  packets (the defensive-downgrade population), fee_status in 429
  (→ B2-1's 251-case bucket).

**Review outcome:** unused `level` param dropped from
`_fuzzy_label_signals`; tag names (`v1_extract_fields`) left — inert to
output, load-bearing for dev tooling. Census script:
`v3/dev/analysis/signal_census.py`.

## L5 — consolidate.py: claims become one answer each

**Job:** resolve L4's competing Signals per field — highest confidence
wins (confidence IS the authority encoding), source_id as deterministic
tiebreak — and record contest metadata. Three products: `fields`
(values), `_source_class` (text/ocr_only/absent — the OCR-only guard's
input, and the only place "extracted none" differs from "never
extracted"), `_agreement` (n_sources/unique_values/has_conflict — the
conflict guard's input, and why losing signals still matter). Plus the
`_finding` passthrough (162 packets).

**Census (2026-08-03):** conflicts in 305/1000 packets, dominated by
applicant_name (196 — OCR name variants; drives no rule); **114 packets
carry approve-relevant conflicts** (the L7 conflict guard's ceiling).
Tiebreak decided between *differing* values **6 times in 9,000 fields**
(0.07%) — all equal-confidence OCR-garble pairs; arbitrary-but-stable is
acceptable at that rate and now measured. Most fields corroborated by
2–3 sources. risk_flags `absent` in 768 packets (the defensive-downgrade
population); fee_status absent in 429 (→ B2-1).

**Review outcome:** three warts fixed — (1) module docstring was a v3
fossil narrating shipped features as future phases; rewritten to the
does/does-not contract; (2) unreachable risk_flags double-locks removed
from solution.py (consolidate guarantees the default; the defaults loop
covers it regardless); (3) `_agreement` absent-field entries now carry
the same dict shape as populated ones — no None-vs-dict fork for
consumers. All output-inert; 141 tests, 27/27 spot parity. Census:
`v3/dev/analysis/consolidate_census.py`.

## L6 — rules.py: evidence becomes a verdict

**Job:** strict-priority chain of 15 rules; first match returns
(verdict, confidence, tag). The ordering is the policy: adjudicator
finding trumps all (162 cases, 100%); standalone hard-denies fire on
minimum sufficient evidence BEFORE the extraction fallback (a half-read
packet can still be denied on the readable half); the review ladder has
R_R1 above the fallback purely for calibration (same verdict, 94% vs
34% bucket); approve tail only reachable with full extraction and no
flags. Every confidence is a registry lookup — the chain cannot emit an
uncalibrated number.

**Census (2026-08-03):** all fire counts match the registry (one
metadata drift: R_R2 24→20 post-reorder, still 100%). The "unreachable"
final line: 0 hits — confirmed dead, kept as floor. Shadow matrix: the
finding trump rescues 84 would-be fallback cases and 76 would-be R_R1;
49 packets have multiple deny conditions true — order shifts tag credit
only (all 0.96–0.99), so the deny block is order-insensitive in effect.

**FALLBACK decomposition (B2-1 revised):** the missing-field axis does
NOT split the bucket (35/28/26% vs pooled 32%). Instead: 65% of the
bucket is fee-only-missing and 44% of those are truth-APPROVED — the
improvement axis is fee-extraction recovery, not confidence splitting.
Visa-missing skews DENIED (43%).

**Review outcome:** no code changes — the cleanest layer. Census:
`v3/dev/analysis/rules_census.py`.

## L7 — policy/ (pending)
