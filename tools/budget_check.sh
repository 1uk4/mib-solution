#!/usr/bin/env bash
# Runtime-budget compliance probe: 100 validation PDFs, ONE container,
# pinned to 4 vCPUs like the grading environment. Reports s/pdf and the
# 5,000-PDF extrapolation vs the 30,000s hard limit.
set -euo pipefail
cd "$( dirname "${BASH_SOURCE[0]}" )/.."

CHALLENGE="${MIB_CHALLENGE_DIR:-$HOME/Code/mib-doc-challenge}"
VAL="$CHALLENGE/data/validation"
WORK=/tmp/mib-budget-check
IMAGE=mib-submission:latest

rm -rf "$WORK"
mkdir -p "$WORK/input" "$WORK/output"
n=0
for f in "$VAL"/*.pdf; do
  ln "$f" "$WORK/input/" 2>/dev/null || cp "$f" "$WORK/input/"
  n=$((n + 1)); [ "$n" -ge 100 ] && break
done

echo "==> Timing $n PDFs, single container, --cpus=4 ..."
start=$(date +%s)
docker run --rm --network=none --cpus=4 \
  -v "$WORK/input:/input:ro" -v "$WORK/output:/output" \
  "$IMAGE" /input /output/predictions.jsonl
end=$(date +%s)

dur=$((end - start))
rows=$(wc -l < "$WORK/output/predictions.jsonl")
python3 - "$dur" "$rows" <<'PY'
import sys
dur, rows = int(sys.argv[1]), int(sys.argv[2])
spp = dur / max(rows, 1)
total = spp * 5000
print(f"rows: {rows}   {spp:.2f} s/pdf   5000-PDF projection: {total:,.0f}s "
      f"({total/3600:.1f}h) vs 30,000s limit -> "
      f"{'FITS with ' + format(30000-total, ',.0f') + 's headroom' if total < 30000 else 'OVER BUDGET'}")
PY
