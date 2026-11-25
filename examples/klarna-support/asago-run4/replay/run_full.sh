#!/bin/bash
# Full run-4 replay matrix (resumable: replay.py skips items already in results.jsonl).
# Order: readonly baseline/recommended, refunds baseline/recommended, then least-privilege for both.
# usage: ./run_full.sh            (from anywhere)
set -uo pipefail
cd "$(dirname "$0")/../../../../recommender"
R4=../asago-inputs/results/run4-fsisac-allthreats-nocap
OUT=$R4/replay/full
mkdir -p "$OUT"
for pair in readonly:baseline readonly:recommended refunds:baseline refunds:recommended \
            readonly:least-privilege refunds:least-privilege; do
  V=${pair%%:*}; C=${pair#*:}; SB=replay-$V
  echo "=== $(date -u +%FT%TZ) $V/$C"
  python3 replay.py --variant "$V" --condition "$C" --conditions-root "$R4/replay/conditions" \
    --sandbox "$SB" --out "$OUT" --artifacts "$R4/artifacts" --deploy || echo "!!! attacks failed rc=$? ($V/$C)"
  python3 replay.py --variant "$V" --condition "$C" --conditions-root "$R4/replay/conditions" \
    --sandbox "$SB" --out "$OUT" --benign || echo "!!! benign failed rc=$? ($V/$C)"
  openshell sandbox delete "$SB" </dev/null >/dev/null 2>&1 || true
  sleep 10
done
echo "=== $(date -u +%FT%TZ) done"
