#!/bin/bash
# Deploy a compiled recommender output (policy.yaml [+ provider-profile.yaml] + runtime.json) as a NEW sandbox
# on the active (local) OpenShell gateway.
#   usage: ./deploy.sh <compiled_out_dir> <sandbox_name>
# Replaces an existing sandbox of the same name. The tool token is read from lab/run/tool-token, never printed.
set -euo pipefail
trap 'echo "deploy.sh failed at line $LINENO" >&2' ERR
cd "$(dirname "$0")"
OUT=$1; SB=$2
TOKEN_FILE=../../lab/run/tool-token
IMAGE=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['image'])" "$OUT/runtime.json")
PROVIDERS=$(python3 -c "import json,sys; print(' '.join(json.load(open(sys.argv[1]))['providers']))" "$OUT/runtime.json")
clean() { sed -E 's/\x1b\[[0-9;]*m//g'; }

# 1. Remove the previous sandbox of this name (it may be using the provider we are about to recreate).
if openshell sandbox get "$SB" </dev/null >/dev/null 2>&1; then
  openshell sandbox delete "$SB" </dev/null >/dev/null 2>&1 || true
  for i in $(seq 1 60); do openshell sandbox get "$SB" </dev/null >/dev/null 2>&1 || break; sleep 2; done
fi

# 2. Generated provider profile: recreate from scratch each deploy (profile update needs an exported
#    resource_version; these profiles are owned by the recommender, so delete + import is simpler).
if [ -f "$OUT/provider-profile.yaml" ]; then
  P=$PROVIDERS
  openshell provider delete "$P" </dev/null >/dev/null 2>&1 || true
  openshell profile delete "$P" </dev/null >/dev/null 2>&1 || true
  openshell profile lint -f "$OUT/provider-profile.yaml" </dev/null 2>&1 | clean | tail -3
  openshell profile import -f "$OUT/provider-profile.yaml" </dev/null 2>&1 | clean | tail -1
  MIDOJO_TOOL_TOKEN="$(cat "$TOKEN_FILE")" openshell provider create --name "$P" --type "$P" \
    --credential MIDOJO_TOOL_TOKEN </dev/null 2>&1 | clean | tail -1
fi

ARGS=(--name "$SB" --from "$IMAGE" --no-tty --no-auto-providers --detach)
# No policy.yaml = OpenShell's own default policy (condition "openshell-default").
[ -f "$OUT/policy.yaml" ] && ARGS+=(--policy "$OUT/policy.yaml")
for p in $PROVIDERS; do ARGS+=(--provider "$p"); done
openshell sandbox create "${ARGS[@]}" </dev/null 2>&1 | clean | grep -E 'Created|rror' || true
for i in $(seq 1 60); do
  phase=$(openshell sandbox get "$SB" 2>/dev/null | clean | awk '/Phase:/ {print $2}')
  case "$phase" in Ready|Error|Failed) break;; esac; sleep 3
done
echo "sandbox $SB: phase=$phase token_mode=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['token_mode'])" "$OUT/runtime.json")"
