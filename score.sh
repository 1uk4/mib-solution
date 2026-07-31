#!/usr/bin/env bash
# One-shot: build image, run container under the exact scoring constraints,
# then score the predictions against the training labels.
#
# Usage:
#   ./score.sh                  # defaults to the training split
#   ./score.sh <input_dir> [<truth_csv>]
#
# Overrides via env:
#   MIB_CHALLENGE_DIR   path to the challenge repo (default: ../mib-doc-challenge)
#   OUTPUT_DIR          where predictions + scores land (default: /tmp/mib-output)
#   IMAGE_TAG           docker tag (default: mib-submission)
set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
CHALLENGE_DIR="${MIB_CHALLENGE_DIR:-$( cd "$SCRIPT_DIR/../mib-doc-challenge" && pwd )}"
OUTPUT_DIR="${OUTPUT_DIR:-/tmp/mib-output}"
IMAGE_TAG="${IMAGE_TAG:-mib-submission}"

INPUT_DIR="${1:-$CHALLENGE_DIR/data/train}"
TRUTH_CSV="${2:-$CHALLENGE_DIR/data/train_labels.csv}"
PREDICTIONS="$OUTPUT_DIR/predictions.jsonl"

if [ ! -d "$CHALLENGE_DIR" ]; then
  echo "Challenge repo not found at $CHALLENGE_DIR. Set MIB_CHALLENGE_DIR." >&2
  exit 1
fi
if [ ! -d "$INPUT_DIR" ]; then
  echo "Input dir not found: $INPUT_DIR" >&2
  exit 1
fi

mkdir -p "$OUTPUT_DIR"
rm -f "$PREDICTIONS"

echo "==> Building $IMAGE_TAG from $SCRIPT_DIR"
docker build -t "$IMAGE_TAG" "$SCRIPT_DIR"

echo "==> Running against $INPUT_DIR"
python3 "$CHALLENGE_DIR/scripts/run_docker_submission.py" \
  --repo "$SCRIPT_DIR" \
  --input-dir "$INPUT_DIR" \
  --output "$PREDICTIONS" \
  --manifest "$TRUTH_CSV" \
  --skip-build --image-tag "$IMAGE_TAG"

if [ ! -f "$TRUTH_CSV" ]; then
  echo "No truth CSV at $TRUTH_CSV; skipping scoring."
  exit 0
fi

# Scoring only makes sense when the truth CSV has adjudication labels
# (train_labels.csv does; validation_manifest.csv does not).
if ! head -1 "$TRUTH_CSV" | grep -q adjudication; then
  echo "$TRUTH_CSV has no adjudication column; skipping scoring."
  exit 0
fi

echo "==> Scoring"
python3 "$CHALLENGE_DIR/scripts/evaluate.py" \
  --truth "$TRUTH_CSV" \
  --submission "$PREDICTIONS" \
  --output-json "$OUTPUT_DIR/evaluation.json" \
  --case-scores-jsonl "$OUTPUT_DIR/case_scores.jsonl"

echo
echo "Predictions: $PREDICTIONS"
echo "Evaluation:  $OUTPUT_DIR/evaluation.json"
echo "Per-case:    $OUTPUT_DIR/case_scores.jsonl"
