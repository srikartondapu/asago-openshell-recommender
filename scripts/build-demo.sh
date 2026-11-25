#!/usr/bin/env bash
# Regenerate demo/data/snapshot.json and snapshot.js from the OpenShell recommender run4 outputs.
# Offline. Reads examples/klarna-support/ (run4 outputs, inputs, policy-mapper output) and src/openshell_recommender/rules.yaml.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PYTHON:-python3}"
"$PY" -c "import yaml" 2>/dev/null || { echo "PyYAML is missing: run 'pip install pyyaml' (or set PYTHON=/path/to/python)"; exit 2; }
"$PY" "$ROOT/demo/generator/build_snapshot.py" \
  --run-dir examples/klarna-support/asago-run4 \
  --policy-mapper examples/klarna-support/policy-mapper/risk-extraction.json \
  --out "$ROOT/demo/data"
