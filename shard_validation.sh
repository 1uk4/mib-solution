#!/usr/bin/env bash
# Sharded Docker validation run — splits data/validation across N parallel
# containers of the SAME image, merges + sorts the outputs.
#
# Usage: ./shard_validation.sh [N_SHARDS] [LIMIT]
#   N_SHARDS default 6. LIMIT = smoke mode: only the first LIMIT PDFs,
#   validated against a filtered manifest, separate output dir.
set -euo pipefail
cd "$( dirname "${BASH_SOURCE[0]}" )"

N="${1:-6}"
LIMIT="${2:-}"
CHALLENGE="${MIB_CHALLENGE_DIR:-$HOME/Code/mib-doc-challenge}"
VAL="$CHALLENGE/data/validation"
MANIFEST="$CHALLENGE/data/validation_manifest.csv"
WORK=/tmp/mib-val-shards${LIMIT:+-smoke$LIMIT}
OUT=/tmp/mib-val-output${LIMIT:+-smoke$LIMIT}
IMAGE=mib-submission:latest

[ -d "$VAL" ] || { echo "validation dir not found: $VAL" >&2; exit 1; }
[ -n "$LIMIT" ] && echo "*** SMOKE MODE: first $LIMIT PDFs only ***"

echo "==> Building image..."
docker build -q -t "$IMAGE" . >/dev/null

echo "==> Sharding into $N slices (hardlinks)..."
rm -rf "$WORK" "$OUT"
mkdir -p "$OUT"
i=0
for f in "$VAL"/*.pdf; do
  [ -n "$LIMIT" ] && [ "$i" -ge "$LIMIT" ] && break
  d="$WORK/shard$((i % N))/input"
  mkdir -p "$d"
  ln "$f" "$d/" 2>/dev/null || cp "$f" "$d/"
  i=$((i + 1))
done
echo "    sharded $i PDFs"
if [ -n "$LIMIT" ]; then
  # Filtered manifest so the validator doesn't count the rest as missing
  python3 - "$WORK" "$MANIFEST" <<'PY'
import csv, sys
from pathlib import Path
work, manifest = sys.argv[1], sys.argv[2]
ids = {p.stem for p in Path(work).glob("shard*/input/*.pdf")}
with open(manifest) as fin, open(Path(work) / "manifest.csv", "w", newline="") as fout:
    r = csv.DictReader(fin)
    w = csv.DictWriter(fout, fieldnames=r.fieldnames)
    w.writeheader()
    for row in r:
        if row["case_id"] in ids:
            w.writerow(row)
PY
  MANIFEST="$WORK/manifest.csv"
fi

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
  --manifest "$MANIFEST"

echo "DONE: $OUT/predictions.jsonl"
