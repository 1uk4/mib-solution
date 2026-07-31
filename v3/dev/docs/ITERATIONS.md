# Version 3 — Iteration Log

## Score progression

| Iter | Score | Δ | Notes |
|---:|---:|---:|---|
| V1 close | 103.48 | — | starting point |
| V2 close | 104.98 | +1.50 | selective OCR + strong/weak signal split |
| V3 iter 0 | *pending* | — | scaffolding — ensemble mirrors V2 exactly |

## Iteration 0: architectural scaffolding

Goal: get to a working V3 pipeline that produces byte-for-byte the same
output as V2, but through the new features → ensemble architecture.
Ensures no regression before we start adding signals.

### Files
- `v3/features.py` — calls V1 and V2 as libraries, produces feature dict
- `v3/ensemble.py` — decision engine (initially mirrors V2 logic exactly)
- `v3/solution.py` — dispatcher: features → ensemble → prediction
- `v3/inspect.py` — dev tool. Renders + extracts + dumps everything about
  one packet so Claude Code and human can inspect together.

### How to use the inspector
```bash
python3 v3/inspect.py MIB-000068
```
Output goes to `/tmp/mib-inspect/MIB-000068/` (rendered pages, embedded
images, full text dump). The console output prints artifact paths that
can be Read by Claude Code (multimodal) or `open`ed by a human.

## Discovery workflow

1. Use inspector to look at samples from each truth class and each
   confusion-matrix cell. Build intuition.
2. Enumerate candidate features (structural, image-content, cross-refs)
   as hypotheses in v3/EDGE_CASES.md.
3. Add features to `features.py`.
4. Run correlator (to be built) — sort by discrimination power.
5. Add strongly-discriminating features to `ensemble.py`.
6. Re-score. Measure whether the addition moved things without breaking
   the zero-regression baseline.
