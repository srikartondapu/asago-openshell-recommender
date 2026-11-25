# Recommended OpenShell policy: klarna-support (refunds)

Least-privilege policy generated from the agent's use case / deployment facts (`sandbox-manifest-refunds.yaml`). OpenShell denies everything not listed here. The ASAGO attacks are used to **test** it (`validation.md`) and to list what is left for guardrails (`gaps.json`).

| Part | Rule | From |
|---|---|---|
| Process | run as uid/gid 1500 (non-root) | image user |
| Filesystem: read-only | /usr, /lib, /proc, /dev/urandom, /etc, /var/log, /opt | system paths the runtime needs |
| Filesystem: read-write | /sandbox/workdir, /tmp, /dev/null, /dev/shm | capability `workspace` + /dev/null, /dev/shm |
| Filesystem: image workdir | include_workdir = False | not declared, so not exposed |
| Network `model` | python-ogx -> host.openshell.internal:11434 | model endpoint |
| Network `http_shipping_status` | python-mcp -> host.openshell.internal:9101 (rest, enforce: GET /shipments/*) | capability `shipping_status` |
| Network: MCP tool server (provider profile) | python-ogx -> host.openshell.internal:8765/mcp, tools/call only: lookup_order, get_account_details, retrieve_policy, escalate_to_human, process_refund, schedule_payment | server `klarna_tools` + mcp_tool capabilities |
| Secrets | tool token as OpenShell provider credential `klarna-support-refunds-tools-token` (tool_token); the sandbox sees a placeholder, the proxy adds the real value | deployment |

Everything else is denied: other hosts, other programs opening connections (curl, wget), other HTTP methods/paths, writes outside the workspace, reads outside the listed paths.

**OpenShell blocks capabilities, not intent.** It sees the tool name, host:port, which program connects, HTTP method/path and file paths. It never sees the conversation and doesn't judge tool arguments, so misuse of a tool the use case needs stays with guardrails (`gaps.json`).

Files: `policy.yaml` + `provider-profile.yaml` + `runtime.json` (deploy: `recommender/deploy.sh <this dir> <sandbox>`), `tests.json`, `prove.md`, `validation.md`, `gaps.json`.
