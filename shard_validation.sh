#!/usr/bin/env bash
# Sharded Docker validation run — splits data/validation across N parallel
# containers of the SAME image, merges + sorts the outputs.
#
# Usage: ./shard_validation.sh [N_SHARDS]   (default 6)
set -euo pipefail
cd "$( dirname "${BASH_SOURCE[0]}" )"

N="${1:-6}"
CHALLENGE="${MIB_CHALLENGE_DIR:-$HOME/Code/mib-doc-challenge}"
VAL="$CHALLENGE/data/validation"
WORK=/tmp/mib-val-shards
OUT=/tmp/mib-val-output
IMAGE=mib-submission:latest

[ -d "$VAL" ] || { echo "validation dir not found: $VAL" >&2; exit 1; }

echo "==> Building image..."
docker build -q -t "$IMAGE" . >/dev/null

echo "==> Sharding $(ls "$VAL" | grep -c '\.pdf$') PDFs into $N slices (hardlinks)..."
rm -rf "$WORK" "$OUT"
mkdir -p "$OUT"
i=0
for f in "$VAL"/*.pdf; do
  d="$WORK/shard$((i % N))/input"
  mkdir -p "$d"
  ln "$f" "$d/" 2>/dev/null || cp "$f" "$d/"
  i=$((i + 1))
done

echo "==> Launching $N containers in parallel..."
pids=()
for s in $(seq 0 $((N - 1))); do
  mkdir -p "$WORK/shard$s/output"
  docker run --rm --network=none \
    -v "$WORK/shard$s/input:/input:ro" \
    -v "$WORK/shard$s/output:/output" \
    "$IMAGE" /input /output/predictions.jsonl \
    > "$WORK/shard$s/log.txt" 2>&1 &
  pids+=($!)
done

echo "==> Waiting (progress: tail -f $WORK/shard0/log.txt)..."
fail=0
for p in "${pids[@]}"; do
  wait "$p" || fail=1
done
[ "$fail" = 0 ] || { echo "A shard FAILED — check $WORK/shard*/log.txt" >&2; exit 1; }

echo "==> Merging + sorting by case_id..."
python3 - "$WORK" "$OUT/predictions.jsonl" <<'PY'
import json, sys
from pathlib import Path
work, out = sys.argv[1], sys.argv[2]
rows = {}
for f in Path(work).glob("shard*/output/predictions.jsonl"):
    for line in f.open():
        line = line.strip()
        if line:
            rows[json.loads(line)["case_id"]] = line
with open(out, "w") as fh:
    for cid in sorted(rows):
        fh.write(rows[cid] + "\n")
print(f"merged {len(rows)} unique cases")
PY
wc -l "$OUT/predictions.jsonl"

echo "==> Validating against the manifest..."
python3 "$CHALLENGE/scripts/validate_submission.py" \
  --submission "$OUT/predictions.jsonl" \
  --manifest "$CHALLENGE/data/validation_manifest.csv"

echo "DONE: $OUT/predictions.jsonl"
