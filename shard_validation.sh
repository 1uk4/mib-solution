#!/usr/bin/env bash
# Sharded Docker validation run — splits data/validation across N parallel
# containers of the SAME image, merges + sorts the outputs, validates.
#
# RESUMABLE: shard outputs are written line-by-line, so on interruption
# just re-run the same command — completed cases are archived, their PDFs
# pruned from the inputs, and only the remainder is processed. The merge
# combines archives + fresh output (parse-tolerant: a line truncated by a
# kill is skipped and its case simply re-runs).
#
# Usage: ./shard_validation.sh [N_SHARDS] [LIMIT]
#   N_SHARDS default 6. LIMIT = smoke mode: only the first LIMIT PDFs,
#   validated against a filtered manifest, separate work/output dirs.
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
CNAME=mib-val-shard${LIMIT:+-smoke}

[ -d "$VAL" ] || { echo "validation dir not found: $VAL" >&2; exit 1; }
[ -n "$LIMIT" ] && echo "*** SMOKE MODE: first $LIMIT PDFs only ***"

# Kill leftover shard containers from a previous interrupted run, and make
# Ctrl-C on THIS run take its containers down with it.
docker ps -q --filter "name=$CNAME-" | xargs docker kill >/dev/null 2>&1 || true
trap 'echo; echo "==> Stopping shard containers..."; docker ps -q --filter "name=$CNAME-" | xargs docker kill >/dev/null 2>&1 || true' INT TERM

echo "==> Building image..."
docker build -q -t "$IMAGE" . >/dev/null

if [ -d "$WORK/shard0/input" ]; then
  # RESUME: keep the existing layout; N follows the layout on disk.
  actual=$(ls -d "$WORK"/shard*/ | wc -l | tr -d ' ')
  if [ "$actual" != "$N" ]; then
    echo "==> RESUME: found $actual existing shards (overriding N=$N)"
    N="$actual"
  else
    echo "==> RESUME: reusing existing $N-shard layout"
  fi
else
  echo "==> Sharding into $N slices (hardlinks)..."
  rm -rf "$WORK"
  i=0
  for f in "$VAL"/*.pdf; do
    [ -n "$LIMIT" ] && [ "$i" -ge "$LIMIT" ] && break
    d="$WORK/shard$((i % N))/input"
    mkdir -p "$d"
    ln "$f" "$d/" 2>/dev/null || cp "$f" "$d/"
    i=$((i + 1))
  done
  echo "    sharded $i PDFs"
fi

if [ -n "$LIMIT" ]; then
  # Filtered manifest so the validator doesn't count the rest as missing
  python3 - "$WORK" "$MANIFEST" <<'PY'
import csv, sys
from pathlib import Path
work, manifest = sys.argv[1], sys.argv[2]
ids = {p.stem for p in Path(work).glob("shard*/input/*.pdf")}
# include archived/done cases so a resumed smoke's manifest stays complete
import json as _json
for f in Path(work).glob("shard*/output/*.jsonl"):
    for line in f.open():
        try:
            ids.add(_json.loads(line)["case_id"])
        except Exception:
            pass
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

# Resume prep per shard: archive any partial output, prune done PDFs.
python3 - "$WORK" <<'PY'
import json, sys, time
from pathlib import Path
work = Path(sys.argv[1])
total_done = total_left = 0
for shard in sorted(work.glob("shard*")):
    outd = shard / "output"
    outd.mkdir(exist_ok=True)
    live = outd / "predictions.jsonl"
    if live.exists() and live.stat().st_size > 0:
        live.rename(outd / f"done-{int(time.time())}-{shard.name}.jsonl")
    elif live.exists():
        live.unlink()
    done = set()
    for f in outd.glob("done-*.jsonl"):
        for line in f.open():
            try:
                done.add(json.loads(line)["case_id"])
            except Exception:
                pass  # truncated tail line: case re-runs
    pruned = 0
    for pdf in (shard / "input").glob("*.pdf"):
        if pdf.stem in done:
            pdf.unlink()
            pruned += 1
    left = len(list((shard / "input").glob("*.pdf")))
    total_done += len(done)
    total_left += left
    print(f"    {shard.name}: {len(done)} done, {left} remaining")
print(f"==> {total_done} cases already complete, {total_left} to run")
PY

echo "==> Launching containers (OMP_THREAD_LIMIT=1 per shard)..."
pids=()
for s in $(seq 0 $((N - 1))); do
  remaining=$(ls "$WORK/shard$s/input" 2>/dev/null | grep -c '\.pdf$' || true)
  if [ "$remaining" = "0" ]; then
    echo "    shard$s: nothing left, skipping"
    continue
  fi
  docker run --rm --network=none --name "$CNAME-$s" \
    -e OMP_THREAD_LIMIT=1 \
    -v "$WORK/shard$s/input:/input:ro" \
    -v "$WORK/shard$s/output:/output" \
    "$IMAGE" /input /output/predictions.jsonl \
    > "$WORK/shard$s/log.txt" 2>&1 &
  pids+=($!)
done

if [ "${#pids[@]}" -gt 0 ]; then
  echo "==> Waiting on ${#pids[@]} shards (progress: tail -f $WORK/shard0/log.txt)..."
  fail=0
  for p in "${pids[@]}"; do
    wait "$p" || fail=1
  done
  [ "$fail" = 0 ] || { echo "A shard FAILED — check $WORK/shard*/log.txt; re-run this command to resume" >&2; exit 1; }
fi

echo "==> Merging + sorting by case_id..."
rm -rf "$OUT"
mkdir -p "$OUT"
python3 - "$WORK" "$OUT/predictions.jsonl" <<'PY'
import json, sys
from pathlib import Path
work, out = sys.argv[1], sys.argv[2]
rows = {}
skipped = 0
for f in Path(work).glob("shard*/output/*.jsonl"):
    for line in f.open():
        line = line.strip()
        if not line:
            continue
        try:
            rows[json.loads(line)["case_id"]] = line
        except Exception:
            skipped += 1
with open(out, "w") as fh:
    for cid in sorted(rows):
        fh.write(rows[cid] + "\n")
print(f"merged {len(rows)} unique cases ({skipped} truncated lines skipped)")
PY
wc -l "$OUT/predictions.jsonl"

echo "==> Validating against the manifest..."
python3 "$CHALLENGE/scripts/validate_submission.py" \
  --submission "$OUT/predictions.jsonl" \
  --manifest "$MANIFEST"

echo "DONE: $OUT/predictions.jsonl"
