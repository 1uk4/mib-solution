#!/usr/bin/env bash
# Dev-mode scoring: pick N random PDFs from train, filter labels to match,
# run the pipeline, then score. For fast iteration during V2+.
#
# Default runs NATIVELY (no Docker) with an OCR result cache — subsequent
# runs on the same PDFs hit cache and are near-instant. Set MIB_DOCKER=1
# to run in Docker for production-parity validation.
#
# Usage:
#   ./dev_score.sh                # 100 random PDFs, native, cached
#   ./dev_score.sh 20             # 20 random PDFs, native
#   MIB_DOCKER=1 ./dev_score.sh   # Force Docker path (matches production)
#
# Overrides via env:
#   MIB_CHALLENGE_DIR       path to challenge repo (default: ../mib-doc-challenge)
#   MIB_SEED                random seed for reproducible sampling (default: 42)
#   MIB_DOCKER              set to 1 to run in Docker instead of native
#   MIB_OCR_CACHE_DIR       OCR cache location (default: ~/.cache/mib-ocr)
#   DEV_WORK_DIR            where to stage sampled inputs (default: /tmp/mib-dev)
set -euo pipefail

# Kill any active mib-score-* Docker container on interrupt or exit. This
# prevents orphaned containers from previous runs continuing to write to
# disk when the user hits Ctrl+C (Docker doesn't proxy SIGINT through
# run_docker_submission.py).
cleanup() {
  local containers
  containers=$(docker ps --filter "name=mib-score-" -q 2>/dev/null || true)
  if [ -n "$containers" ]; then
    echo
    echo "==> Killing orphaned mib-score containers..." >&2
    echo "$containers" | xargs docker kill 2>/dev/null || true
  fi
}
trap cleanup INT TERM

N="${1:-100}"
SEED="${MIB_SEED:-42}"

# Kill any already-running mib-score-* containers from prior interrupted
# runs, so they don't interleave with this run.
_orphans=$(docker ps --filter "name=mib-score-" -q 2>/dev/null || true)
if [ -n "$_orphans" ]; then
  echo "==> Killing orphaned mib-score containers from previous runs..." >&2
  echo "$_orphans" | xargs docker kill 2>/dev/null || true
fi

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
CHALLENGE_DIR="${MIB_CHALLENGE_DIR:-$( cd "$SCRIPT_DIR/../mib-doc-challenge" && pwd )}"
DEV_WORK_DIR="${DEV_WORK_DIR:-/tmp/mib-dev}"

TRAIN_DIR="$CHALLENGE_DIR/data/train"
TRAIN_LABELS="$CHALLENGE_DIR/data/train_labels.csv"
SAMPLE_INPUT="$DEV_WORK_DIR/pdfs"
SAMPLE_LABELS="$DEV_WORK_DIR/labels.csv"

if [ ! -d "$TRAIN_DIR" ]; then
  echo "Train dir not found: $TRAIN_DIR" >&2
  exit 1
fi

echo "==> Sampling $N PDFs (seed=$SEED)"
rm -rf "$DEV_WORK_DIR"
mkdir -p "$SAMPLE_INPUT"

# Deterministic random pick of N filenames.
# Pipe supplies stdin (filenames); the script is passed via `python3 -c` so
# stdin and script don't collide. Args: $N (count) and $SEED.
ls "$TRAIN_DIR" | grep '\.pdf$' | python3 -c '
import sys, random
n = int(sys.argv[1]); random.seed(int(sys.argv[2]))
lines = [l.strip() for l in sys.stdin if l.strip()]
random.shuffle(lines)
for l in lines[:n]:
    print(l)
' "$N" "$SEED" > "$DEV_WORK_DIR/selected.txt"

# Hardlink the sampled PDFs into a scratch dir (instant, no copy)
while read pdf; do
  ln "$TRAIN_DIR/$pdf" "$SAMPLE_INPUT/$pdf"
done < "$DEV_WORK_DIR/selected.txt"

# Filter labels to just the sampled case_ids
python3 -c '
import csv, sys
labels_in, selected_txt, labels_out = sys.argv[1:4]
selected = {l.strip().removesuffix(".pdf") for l in open(selected_txt) if l.strip()}
with open(labels_in) as f:
    r = csv.DictReader(f)
    fields = r.fieldnames
    rows = [row for row in r if row["case_id"] in selected]
with open(labels_out, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=fields)
    w.writeheader()
    w.writerows(rows)
print(f"  wrote {len(rows)} label rows to {labels_out}")
' "$TRAIN_LABELS" "$DEV_WORK_DIR/selected.txt" "$SAMPLE_LABELS"

# Use a unique output dir per run so Docker Desktop's file-sharing layer
# never has stale bind-mount cache for the target. Also symlink to a stable
# name so we can find the latest run quickly.
RUN_ID="$(date +%s)-$$"
OUTPUT_DIR="${OUTPUT_DIR:-/tmp/mib-dev-runs/$RUN_ID}"
mkdir -p "$OUTPUT_DIR"

# Housekeeping: prune older run dirs > 1 hour old to avoid /tmp bloat
find /tmp/mib-dev-runs -maxdepth 1 -type d -mmin +60 -exec rm -rf {} \; 2>/dev/null || true

# Convenience symlink so tools that expect /tmp/mib-dev-output keep working.
# rm first: if the path already exists as a DIRECTORY, `ln -sfn` would drop
# the new symlink INSIDE it instead of replacing it (bug found 2026-08-03 —
# the path had been a dir of ~54 stale symlinks since Jul 30).
rm -rf /tmp/mib-dev-output
ln -sfn "$OUTPUT_DIR" /tmp/mib-dev-output

if [ "${MIB_DOCKER:-0}" = "1" ]; then
  # Docker path — matches production submission environment exactly.
  echo "==> Invoking score.sh (Docker mode, matches production)"
  OUTPUT_DIR="$OUTPUT_DIR" "$SCRIPT_DIR/score.sh" "$SAMPLE_INPUT" "$SAMPLE_LABELS"
else
  # Native path — fast dev iteration with persistent OCR cache.
  # Passes MIB_OCR_CACHE_DIR through to v3/extract.py so cache is populated.
  export MIB_OCR_CACHE_DIR="${MIB_OCR_CACHE_DIR:-$HOME/.cache/mib-ocr}"
  mkdir -p "$MIB_OCR_CACHE_DIR"
  echo "==> Running V3 natively (cache: $MIB_OCR_CACHE_DIR)"
  PREDICTIONS="$OUTPUT_DIR/predictions.jsonl"
  python3 "$SCRIPT_DIR/solution.py" "$SAMPLE_INPUT" "$PREDICTIONS"

  echo "==> Validating submission"
  python3 "$CHALLENGE_DIR/scripts/validate_submission.py" \
    --submission "$PREDICTIONS" \
    --manifest "$SAMPLE_LABELS"

  echo "==> Scoring"
  python3 "$CHALLENGE_DIR/scripts/evaluate.py" \
    --truth "$SAMPLE_LABELS" \
    --submission "$PREDICTIONS" \
    --output-json "$OUTPUT_DIR/evaluation.json" \
    --case-scores-jsonl "$OUTPUT_DIR/case_scores.jsonl"

  echo
  echo "Predictions: $PREDICTIONS"
  echo "Evaluation:  $OUTPUT_DIR/evaluation.json"
  echo "Per-case:    $OUTPUT_DIR/case_scores.jsonl"
fi
