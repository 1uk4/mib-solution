#!/usr/bin/env bash
# Byte-parity gate for the v4 rewrite (spec §8). Compares a predictions.jsonl
# against the committed golden FOR THE SAME ENVIRONMENT — never cross-compare
# (145/1000 rows differ between host and container with zero code change).
#
# Usage:
#   ./parity.sh native  [predictions.jsonl]   default: newest /tmp/mib-dev-runs/<run>/
#   ./parity.sh docker  [predictions.jsonl]   default: /tmp/mib-output/predictions.jsonl
set -euo pipefail
cd "$( dirname "${BASH_SOURCE[0]}" )"

MODE="${1:?usage: parity.sh native|docker [predictions.jsonl]}"
case "$MODE" in
  native)
    GOLDEN="golden/native-92eb104-seed42-n1000.jsonl"
    DEFAULT="$(ls -td /tmp/mib-dev-runs/*/ 2>/dev/null | head -1)predictions.jsonl"
    ;;
  docker)
    GOLDEN="golden/docker-92eb104-n1000.jsonl"
    DEFAULT="/tmp/mib-output/predictions.jsonl"
    ;;
  *) echo "unknown mode: $MODE (want native|docker)" >&2; exit 2 ;;
esac
PRED="${2:-$DEFAULT}"

[ -f "$PRED" ] || { echo "predictions not found: $PRED" >&2; exit 2; }
[ -f "$GOLDEN" ] || { echo "golden not found: $GOLDEN (run from repo root)" >&2; exit 2; }

if cmp -s "$PRED" "$GOLDEN"; then
  echo "PARITY OK ($MODE): $PRED == $GOLDEN"
else
  echo "PARITY FAIL ($MODE): $PRED vs $GOLDEN" >&2
  diff <(sort "$PRED") <(sort "$GOLDEN") | head -20 >&2
  n=$(diff <(sort "$PRED") <(sort "$GOLDEN") | grep -c '^<' || true)
  echo "...differing rows: $n" >&2
  exit 1
fi
