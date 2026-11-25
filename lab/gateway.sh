#!/bin/bash
# Run the local OpenShell 0.1.8 gateway in the FOREGROUND (Ctrl-C to stop). No brew service / LaunchAgent.
#   - compute driver: Podman (rootless, inside podman-machine-default VM)
#   - listens on 127.0.0.1:17670 only; CLI gateway name: "openshell"
# Prereq: podman machine start   (stop it afterwards with: podman machine stop)
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p logs

podman machine inspect --format '{{.State}}' | grep -q running || { echo "Start the VM first: podman machine start"; exit 1; }

export OPENSHELL_COMPUTE_DRIVER=podman
export OPENSHELL_PODMAN_SOCKET="$(podman machine inspect --format '{{.ConnectionInfo.PodmanSocket.Path}}')"
export OPENSHELL_BIND_ADDRESS=127.0.0.1

openshell gateway select openshell </dev/null >/dev/null
echo "Gateway on https://127.0.0.1:17670 (driver=podman). Logs: lab/logs/gateway.log"
exec /opt/homebrew/opt/openshell/libexec/openshell-gateway-homebrew-service 2>&1 | tee logs/gateway.log
