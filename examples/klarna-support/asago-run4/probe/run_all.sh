#!/bin/bash
# Run-4 direct probes: every variant x condition, then the report. ~2-3 min per condition.
set -uo pipefail
cd "$(dirname "$0")/../../../../recommender"
R4=../asago-inputs/results/run4-fsisac-allthreats-nocap
OUT=$R4/probe/full
for V in readonly refunds; do
  for C in baseline least-privilege recommended; do
    echo "=== $(date -u +%FT%TZ) $V/$C"
    python3 probe.py run --variant "$V" --condition "$C" --conditions-root "$R4/replay/conditions" \
      --sandbox "probe-$V" --probes "$R4/probe/probes-$V.json" --out "$OUT" --deploy || echo "!!! failed rc=$? ($V/$C)"
  done
  openshell sandbox delete "probe-$V" </dev/null >/dev/null 2>&1 || true
done
python3 probe.py report --out "$OUT"
echo "=== $(date -u +%FT%TZ) done"
