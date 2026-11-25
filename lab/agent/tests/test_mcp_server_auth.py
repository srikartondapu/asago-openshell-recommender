"""The domain MCP server over streamable HTTP, with and without a bearer token.

Starts ``python -m mini_agents`` as a subprocess on loopback. No model, no
network beyond 127.0.0.1.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time

import httpx
import pytest

INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"},
    },
}
ACCEPT = {"Accept": "application/json, text/event-stream"}
TOKEN = "tool-token-test-4f2a"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _start(tmp_path, *args, token: str | None):
    port = _free_port()
    env = {name: value for name, value in os.environ.items() if not name.startswith("MINI_AGENTS_")}
    if token is not None:
        env["MINI_AGENTS_TOOL_TOKEN"] = token
    log = (tmp_path / "server.log").open("ab")
    process = subprocess.Popen(
        [sys.executable, "-m", "mini_agents", "--domain", "klarna", "--host", "127.0.0.1",
         "--port", str(port), *args],
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    deadline = time.monotonic() + 30
    while True:
        if process.poll() is not None:
            raise RuntimeError(f"server exited early; see {tmp_path / 'server.log'}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                break
        except OSError:
            if time.monotonic() > deadline:
                process.kill()
                raise RuntimeError("server did not listen in time")
            time.sleep(0.2)
    return process, f"http://127.0.0.1:{port}"


@pytest.fixture(scope="module")
def guarded(tmp_path_factory):
    process, base = _start(
        tmp_path_factory.mktemp("guarded"),
        "--transport", "streamable-http", token=TOKEN,
    )
    yield base
    process.kill()
    process.wait(timeout=10)


def _post(url: str, authorization: str | None = None):
    headers = dict(ACCEPT)
    if authorization is not None:
        headers["Authorization"] = authorization
    return httpx.post(url, json=INITIALIZE, headers=headers, timeout=10)


def test_streamable_http_refuses_requests_without_the_bearer_token(guarded):
    url = f"{guarded}/mcp"
    assert _post(url).status_code == 401
    assert _post(url, f"Basic {TOKEN}").status_code == 401
    assert _post(url, TOKEN).status_code == 401
    assert _post(url, f"Bearer {TOKEN}x").status_code == 401
    refused = _post(url, "Bearer wrong")
    assert refused.status_code == 401
    assert refused.headers["www-authenticate"] == "Bearer"


def test_streamable_http_accepts_the_bearer_token(guarded):
    response = _post(f"{guarded}/mcp", f"Bearer {TOKEN}")
    assert response.status_code == 200
    assert response.json()["result"]["serverInfo"]["name"]


def test_sse_without_a_token_is_unchanged(tmp_path):
    process, base = _start(tmp_path, token=None)
    try:
        with httpx.stream("GET", f"{base}/sse", timeout=10) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
    finally:
        process.kill()
        process.wait(timeout=10)


def test_sse_with_a_token_needs_it(tmp_path):
    process, base = _start(tmp_path, token=TOKEN)
    try:
        assert httpx.get(f"{base}/sse", timeout=10).status_code == 401
        with httpx.stream(
            "GET", f"{base}/sse", headers={"Authorization": f"Bearer {TOKEN}"}, timeout=10
        ) as response:
            assert response.status_code == 200
    finally:
        process.kill()
        process.wait(timeout=10)
