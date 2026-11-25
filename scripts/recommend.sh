#!/bin/bash
# Rebuild the recommendation bundle for the Klarna support agent (full use case: all tools declared) from run 4.
# Inputs already measured: probe/full (run 4 attacks) and examples/klarna-support/standin-fs-read/full (hand-written file-read stand-in).
# To re-measure first (needs the local lab): probe/run_all.sh under the run dir (OpenShell conditions) and
#   src/openshell_recommender/nosandbox.py --probes <plan> --variant refunds --out <probe root>   ("before": no sandbox, plain podman)
set -euo pipefail
cd "$(dirname "$0")/.."   # repo root
EX=examples/klarna-support
R4=$EX/asago-run4
S=$EX/standin-fs-read
OUT=${1:-$R4/recommendation/klarna-support}   # optional arg: output dir
REC=src/openshell_recommender
POLICY_DIR=$R4/replay/conditions/refunds/least-privilege   # = python3 $REC/compile.py --manifest $EX/inputs/sandbox-manifest-refunds.yaml --out <dir> (reproduces it)
python3 $REC/prove.py --policy $POLICY_DIR/policy.yaml --probes $R4/probe/probes-refunds.json $S/probes.json --out $OUT
python3 $REC/bundle.py --manifest $EX/inputs/sandbox-manifest-refunds.yaml --policy-dir $POLICY_DIR \
  --tags $R4/recommender/run4-only/refunds/tags.json --plans $R4/probe/probes-refunds.json $S/probes.json \
  --results $R4/probe/full $S/full --prove $OUT --out $OUT
