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

## L3 — filters/ (pending)

## L4 — signals.py (pending)

## L5 — consolidate.py (pending)

## L6 — rules.py (pending)

## L7 — policy/ (pending)
