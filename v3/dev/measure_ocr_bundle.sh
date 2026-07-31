#!/usr/bin/env bash
# One-shot measurement sweep for the OCR value quality bundle.
#
# Runs ./dev_score.sh on 1000 training PDFs with 6 different env-var
# configurations, copies each evaluation.json to a named backup, and prints
# a summary table at the end.
#
# Does NOT clear the OCR cache between runs — the cache tags in
# v3/extract.py encode env-var state so configs don't collide.
#
# NOTE on housekeeping: dev_score.sh writes each run's output to
# /tmp/mib-dev-runs/<timestamp>-<pid>/ and symlinks /tmp/mib-dev-output
# to that directory. It also auto-prunes /tmp/mib-dev-runs dirs older
# than 60 minutes. On long sweeps (>60 min total), early configs' raw
# run dirs will be pruned before the sweep finishes. The /tmp/eval_<name>.json
# copies made by this script are the durable record — rely on those, not
# on the raw dirs still being present.
#
# Usage (from /Users/lukaflores/Code/mib-solution):
#     ./v3/dev/measure_ocr_bundle.sh
# Or from anywhere:
#     bash /Users/lukaflores/Code/mib-solution/v3/dev/measure_ocr_bundle.sh
#
# Output artifacts (all in /tmp):
#     /tmp/eval_baseline.json      — all features off (control)
#     /tmp/eval_normalize.json     — MIB_NORMALIZE_VALUES=1
#     /tmp/eval_sharpen.json       — MIB_OCR_SHARPEN=1
#     /tmp/eval_userwords.json     — MIB_USER_WORDS=1
#     /tmp/eval_reocr.json         — MIB_CHAR_WHITELIST_REOCR=1
#     /tmp/eval_all.json           — all four on
#     /tmp/measure_ocr_bundle.log  — full stdout/stderr of all 6 runs
#     /tmp/measure_ocr_bundle_summary.txt — final summary table

set -u   # unset var = error, but don't exit on non-zero (we want to keep running)

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
REPO_DIR="$( cd "$SCRIPT_DIR/../.." && pwd )"
LOG=/tmp/measure_ocr_bundle.log
SUMMARY=/tmp/measure_ocr_bundle_summary.txt

: > "$LOG"        # truncate
: > "$SUMMARY"

# Config table: name -> env-var assignments (space-separated NAME=VAL pairs)
declare -a CONFIGS=(
  "baseline|"
  "normalize|MIB_NORMALIZE_VALUES=1"
  "sharpen|MIB_OCR_SHARPEN=1"
  "userwords|MIB_USER_WORDS=1"
  "reocr|MIB_CHAR_WHITELIST_REOCR=1"
  "all|MIB_OCR_SHARPEN=1 MIB_NORMALIZE_VALUES=1 MIB_CHAR_WHITELIST_REOCR=1 MIB_USER_WORDS=1"
)

echo "==============================================================" | tee -a "$LOG"
echo "OCR value quality measurement sweep started at $(date)" | tee -a "$LOG"
echo "Repo: $REPO_DIR" | tee -a "$LOG"
echo "==============================================================" | tee -a "$LOG"

cd "$REPO_DIR"

TOTAL_START=$(date +%s)

for entry in "${CONFIGS[@]}"; do
  name="${entry%%|*}"
  envs="${entry#*|}"
  eval_out="/tmp/eval_${name}.json"

  echo "" | tee -a "$LOG"
  echo "--------------------------------------------------------------" | tee -a "$LOG"
  echo "[$(date +%H:%M:%S)] CONFIG '$name'  env: [${envs:-<none>}]" | tee -a "$LOG"
  echo "--------------------------------------------------------------" | tee -a "$LOG"
  CFG_START=$(date +%s)

  # Wipe env vars we care about, then set only what this config wants.
  unset MIB_OCR_SHARPEN MIB_NORMALIZE_VALUES MIB_CHAR_WHITELIST_REOCR MIB_USER_WORDS

  # Run dev_score.sh with the config's env vars inlined.
  # env -S splits the assignments; falls back to eval-ing if env -S unsupported.
  if env -S "${envs} ./dev_score.sh 1000" >>"$LOG" 2>&1; then
    status="OK"
  else
    status="FAILED (exit $?)"
  fi

  # Copy evaluation.json to config-named backup (if produced).
  # dev_score.sh writes to /tmp/mib-dev-runs/<timestamp>-<pid>/evaluation.json
  # and symlinks /tmp/mib-dev-output -> that dir. Use the symlink as the source.
  if [ -f /tmp/mib-dev-output/evaluation.json ]; then
    cp /tmp/mib-dev-output/evaluation.json "$eval_out"
    echo "[$(date +%H:%M:%S)] saved $eval_out ($status)" | tee -a "$LOG"
  else
    echo "[$(date +%H:%M:%S)] WARNING: /tmp/mib-dev-output/evaluation.json missing after run ($status)" | tee -a "$LOG"
  fi

  CFG_END=$(date +%s)
  echo "[$(date +%H:%M:%S)] config '$name' took $((CFG_END - CFG_START))s" | tee -a "$LOG"
done

TOTAL_END=$(date +%s)

# Build summary table
{
  echo "=============================================================="
  echo "OCR value quality measurement sweep — SUMMARY"
  echo "Completed at $(date), total wall time $((TOTAL_END - TOTAL_START))s"
  echo "=============================================================="
  echo ""
  printf "%-12s  %8s  %6s  %8s  %8s  %8s\n" "config" "total" "catFA" "extract" "classif" "calibr"
  printf "%-12s  %8s  %6s  %8s  %8s  %8s\n" "------" "-----" "-----" "-------" "-------" "------"
  for entry in "${CONFIGS[@]}"; do
    name="${entry%%|*}"
    f="/tmp/eval_${name}.json"
    if [ -f "$f" ]; then
      python3 -c "
import json
d = json.load(open('$f'))
print(f'{\"$name\":<12}  {d[\"scores\"][\"total_score\"]:8.2f}  {d[\"raw\"][\"catastrophic_false_approvals\"]:6d}  {d[\"scores\"][\"extraction_score\"]:8.2f}  {d[\"scores\"][\"classification_score\"]:8.2f}  {d[\"scores\"][\"calibration_score\"]:8.2f}')
" 2>/dev/null || printf "%-12s  %s\n" "$name" "(parse error)"
    else
      printf "%-12s  %s\n" "$name" "(no eval file)"
    fi
  done
  echo ""
  echo "Raw evaluation files: /tmp/eval_{baseline,normalize,sharpen,userwords,reocr,all}.json"
  echo "Full log:             $LOG"
} | tee "$SUMMARY"

echo ""
echo "Done. Summary also in $SUMMARY."
