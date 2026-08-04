# MIB Doc Challenge — Solution

Offline Docker pipeline for the [MIB Doc Challenge](https://github.com/8090-inc/mib-doc-challenge).
Reads a directory of PDF case packets and emits one JSONL prediction per case.

## Layout

```
mib-solution/
├── Dockerfile           # python:3.12-slim + tesseract + pillow
├── .dockerignore        # excludes v3/dev/ and *.md from the shipped image
├── run.sh               # container entrypoint wrapper
├── solution.py          # thin dispatcher — imports active version
├── score.sh             # production scoring: full docker constraints on 1000 PDFs
├── dev_score.sh         # dev scoring: native mode w/ OCR cache, ~2 min run
├── tools/               # parity gate, sharded validation runner, budget probe
├── golden/              # committed reference outputs (byte-parity gates)
├── v1/, v2/, v3/        # FROZEN — prior versions, kept as reference; not shipped
└── v4/                  # ACTIVE — standalone layered pipeline (118.56 / 150)
    │
    │  === FOUNDATION (read by every layer) ===
    ├── config.py        # ALL feature flags — one frozen dataclass
    ├── confidence.py    # ALL confidence values — calibrated registry
    ├── vocab.py         # closed enums with provenance
    ├── patterns.py      # every regex + OCR word list
    │
    │  === PIPELINE ===
    ├── acquire.py       # L1 — source enumeration (text streams + images)
    ├── extract.py       # L2 — text decode + Tesseract OCR (cache + skip gate)
    ├── filters/         # L3 — trust boundary (sanitizers + detectors)
    │   ├── injection.py     # sanitizer: strips prompt-injection lines
    │   ├── redaction.py     # sanitizer: strips [NAME CUT OUT] etc.
    │   └── illegibility.py  # detector: real-word ratio < 30%
    ├── signals.py       # L4 — field extraction + typed Signal emission
    ├── consolidate.py   # L5 — signal grouping + agreement scoring
    ├── rules.py         # L6 — ordered rule chain
    ├── policy/          # L7 — 9 named stages + 12-line dispatcher
    │   ├── context.py       # Verdict, PolicyContext, make_ctx
    │   ├── upgrades.py      # 3 upgrade stages
    │   ├── bypasses.py      # 1 trust bypass
    │   └── guards.py        # 5 guard stages
    ├── normalize.py     # L4 helper — post-OCR value normalization
    ├── reocr.py         # L4 helper — char-whitelist re-OCR
    ├── source_type.py   # L4 helper — Field Manual authority classifier
    ├── evidence.py      # shared readers: OCR signal + biometric slip
    ├── solution.py      # driver: main() loop, progress logging
    ├── OBSERVATIONS.md  # bucket-2/3 ledger for the next phase
    └── tests/           # 159 tests (incl. per-stage L7 isolation)
```

Dev tooling and measured history live in `v3/dev/` (analysis scripts,
RULE_AUDIT.md); they are pointed at v4 as the first task of the next phase.
The reviewer-facing design document is `docs/TECHNICAL_DEBRIEF.md`.

## Active version

`solution.py` dispatches to `v4.solution`. v4 imports nothing from
v1/v2/v3 — the Docker image ships `v4/` alone. Any change is gated
against the committed goldens with `tools/parity.sh`.

## Run

**Production-parity scoring (full Docker constraints, slow):**
```bash
~/code/mib-solution/score.sh
```

**Dev scoring (native mode + OCR cache, ~2 min):**
```bash
~/code/mib-solution/dev_score.sh 1000     # full 1000-packet sample
~/code/mib-solution/dev_score.sh 100      # quick 100-packet iteration
MIB_DOCKER=1 ~/code/mib-solution/dev_score.sh 1000   # force Docker path
```

**Inspect one packet (dev tool):**
```bash
python3 ~/code/mib-solution/v3/dev/inspect_case.py MIB-000787
```

**Data analysis (dev tools):**
```bash
python3 ~/code/mib-solution/v3/dev/analysis/extract_features.py   # → /tmp/mib-features.jsonl
python3 ~/code/mib-solution/v3/dev/analysis/correlate.py          # ranked feature discrimination
python3 ~/code/mib-solution/v3/dev/analysis/correlate.py v1_rule_tag  # detail cross-tab
```

## Constraints (see `DOCKER_SUBMISSION.md` in the challenge repo)

- Offline (`--network none`), 4 vCPU, 8 GiB RAM, read-only root FS, `/tmp` tmpfs.
- Image ≤ 4 GiB. Model artifacts ≤ 250 MiB each, ≤ 1 GiB total.
- ~6 s/PDF avg, 30,000 s hard cap on 5k validation set.
- No LLMs, VLMs, or cloud OCR at runtime. Offline OCR + classical CV + rules
  + small task-specific models allowed within the size caps.
