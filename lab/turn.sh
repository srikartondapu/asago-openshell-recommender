#!/bin/bash
# Send ONE turn to the sandboxed mini-agents Klarna agent.
#   usage: ./turn.sh <sandbox> <turn_index> <turn_count> "<user message>"
# Agent is deliberately over-privileged (no AGENT_ALLOWED_TOOLS): all Klarna tools + workstation tools.
# Narrowing what it can actually do is the sandbox policy's job.
set -euo pipefail
cd "$(dirname "$0")"
SB=$1; IDX=$2; CNT=$3; MSG=$4
INSTR=$(grep -E '^AGENT_INSTRUCTIONS=' agent/src/mini_agents/variants/klarna-refunds.env | cut -d= -f2-)

# TOKEN_MODE=env (default): pass the real tool token as an env var (baseline; agent can read it).
# TOKEN_MODE=provider: pass nothing; the sandbox's klarna-tools-token provider supplies a placeholder and
#                      the OpenShell proxy injects the real token (secrets.provider_injection fragment).
TOKEN_ARGS=()
[ "${TOKEN_MODE:-env}" = env ] && TOKEN_ARGS=(--env "MIDOJO_TOOL_TOKEN=$(cat run/tool-token)")

openshell sandbox exec "$SB" --no-tty ${TOKEN_ARGS[@]+"${TOKEN_ARGS[@]}"} \
  --env AGENT_MODEL=qwen3:8b \
  --env AGENT_MODEL_BASE_URL=http://host.openshell.internal:11434/v1 \
  --env AGENT_MODEL_API_KEY=ollama-local-dummy \
  --env "AGENT_INSTRUCTIONS=$INSTR" \
  --env AGENT_WORKSTATION_TOOLS=1 \
  --env MIDOJO_TOOL_URL=http://host.openshell.internal:8765/mcp \
  --env MIDOJO_TURN_INDEX="$IDX" \
  --env MIDOJO_TURN_COUNT="$CNT" \
  -- /opt/app-root/.venv/bin/mini-agents-sandbox-agent --domain klarna "$MSG" </dev/null
