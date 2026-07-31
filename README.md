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
├── v1/                  # CLOSED — Version 1 (stdlib-only), 103.48 / 150
│   ├── solution.py
│   ├── EDGE_CASES.md    # rule catalog + provenance tags
│   └── ITERATIONS.md    # chronological iteration log
├── v2/                  # CLOSED — Version 2 (OCR safety guard), 104.98 / 150
│   ├── solution.py
│   ├── EDGE_CASES.md
│   └── ITERATIONS.md
└── v3/                  # ACTIVE — layered pipeline
    │
    │  === PRODUCTION (shipped in Docker image) ===
    ├── solution.py      # dispatcher, main() loop, progress logging
    ├── acquire.py       # L1 — source enumeration (text streams + images)
    ├── extract.py       # L2 — text decode + Tesseract OCR (with cache + skip gate)
    ├── filters/         # L3 — trust boundary (sanitizers + detectors)
    │   ├── injection.py     # sanitizer: strips prompt-injection lines
    │   ├── redaction.py     # sanitizer: strips [NAME CUT OUT] etc.
    │   └── illegibility.py  # detector: real-word ratio < 30%
    ├── signals.py       # L4 — typed Signal emission with validation
    ├── consolidate.py   # L5 — signal grouping + agreement scoring
    ├── rules.py         # L6 — V1 rule chain (imported as library)
    ├── policy.py        # L7 — safety guards (OCR override, OCR-only, conflict)
    ├── ocr_signal.py    # L7 helper — OCR risk-signal grader
    │
    │  === DEV (excluded from Docker via .dockerignore) ===
    └── dev/
        ├── inspect_case.py       # dump everything about one packet
        ├── analysis/             # data analysis tools (grows as rules mature)
        │   ├── extract_features.py  # → /tmp/mib-features.jsonl
        │   └── correlate.py         # ranks features by truth discrimination
        └── docs/
            ├── RULE_AUDIT.md     # ongoing rule audit log
            ├── EDGE_CASES.md     # V3-specific edge cases
            └── ITERATIONS.md     # V3 iteration history
```

## Active version

`solution.py` currently dispatches to `v3.solution`. Switch versions by
editing the import line.

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
