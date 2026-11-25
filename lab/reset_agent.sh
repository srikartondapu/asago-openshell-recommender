# Reset the sandboxed agent between conversations: stop its OGX/workstation processes and clear /tmp/agent-state.
# usage: openshell sandbox upload <sb> lab/reset_agent.sh /tmp/ && openshell sandbox exec <sb> -- sh /tmp/reset_agent.sh
python3 - <<'PY' 2>/dev/null || /opt/app-root/.venv/bin/python - <<'PY2'
PY
import json, os, signal
s = json.load(open("/tmp/agent-state/state.json"))
for k in ("ogx", "workstation", "mcp"):
    if s.get(k):
        try: os.killpg(s[k]["pid"], signal.SIGTERM)
        except Exception:
            try: os.kill(s[k]["pid"], signal.SIGTERM)
            except Exception: pass
PY2
rm -rf /tmp/agent-state && echo reset-ok
