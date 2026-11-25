"""Workstation tools against a local mock merchant and attacker, offline.

The mock services run in this process on port 0; the tools fetch from them
over loopback. Files, the evidence line and the workdir live in ``tmp_path``.
"""

from __future__ import annotations

import json
import threading
import urllib.request

import pytest

from mini_agents import mock_services
from mini_agents.workstation import server as workstation
from mini_agents.workstation.server import build_mcp


@pytest.fixture
def services(tmp_path):
    ledger = mock_services.Ledger(tmp_path / "ledger.jsonl")
    merchant = mock_services.make_server("127.0.0.1", 0, "merchant", ledger)
    attacker = mock_services.make_server("127.0.0.1", 0, "attacker", ledger)
    for server in (merchant, attacker):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    yield {
        "merchant": f"http://127.0.0.1:{merchant.server_address[1]}",
        "attacker": f"http://127.0.0.1:{attacker.server_address[1]}",
        "ledger": tmp_path / "ledger.jsonl",
    }
    for server in (merchant, attacker):
        server.shutdown()
        server.server_close()


@pytest.fixture
def tools(tmp_path, monkeypatch):
    """Evidence and workdir under tmp_path; no proxy in the way of loopback."""
    monkeypatch.setattr(workstation, "EVIDENCE_PATH", tmp_path / "evidence" / "calls.jsonl")
    monkeypatch.setattr(workstation, "WORKDIR", tmp_path / "workdir")
    (tmp_path / "workdir").mkdir()
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setattr(urllib.request, "_opener", None, raising=False)
    return workstation


def _evidence(tools):
    path = tools.EVIDENCE_PATH
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_http_fetch_returns_status_and_body(services, tools):
    result = tools.http_fetch(f"{services['merchant']}/shipments/ORD-102")
    assert result.startswith("HTTP 200\n")
    assert '"status": "delivered"' in result


def test_http_fetch_posts_a_body_and_reports_a_404(services, tools):
    posted = tools.http_fetch(
        f"{services['attacker']}/collect", method="POST", body="hello", headers={"X-Test": "1"}
    )
    assert posted == "HTTP 200\nok"
    missing = tools.http_fetch(f"{services['merchant']}/shipments/NOPE")
    assert missing.startswith("HTTP 404 Not Found\n")

    entries = [json.loads(line) for line in services["ledger"].read_text().splitlines()]
    collected = [entry for entry in entries if entry["path"] == "/collect"]
    assert [(entry["method"], entry["body"]) for entry in collected] == [("POST", "hello")]


def test_http_fetch_truncates_the_body_to_2000_characters(services, tools):
    big = mock_services.BODY_CHARS * 3
    result = tools.http_fetch(f"{services['attacker']}/bulk", method="POST", body="x" * big)
    assert result == "HTTP 200\nok"
    (entry,) = [
        json.loads(line)
        for line in services["ledger"].read_text().splitlines()
        if json.loads(line)["path"] == "/bulk"
    ]
    assert len(entry["body"]) == mock_services.BODY_CHARS


def test_http_fetch_errors_come_back_as_text(services, tools):
    refused = tools.http_fetch(f"http://127.0.0.1:{_closed_port()}/")
    assert refused.startswith("error: request failed:")
    local = tools.http_fetch("file:///etc/hosts")
    assert local == "error: only http and https URLs are allowed"


def test_file_tools_read_write_and_list_inside_the_workdir(tools):
    assert tools.write_file("notes/a.txt", "hello").startswith("wrote 5 characters")
    assert tools.read_file("notes/a.txt") == "hello"
    (tools.WORKDIR / "notes" / "sub").mkdir()
    assert tools.list_dir("notes") == "a.txt\nsub/"


def test_file_tools_return_errors_instead_of_raising(tools, tmp_path):
    assert tools.read_file(str(tmp_path / "absent.txt")).startswith("error: FileNotFoundError")
    assert tools.list_dir(str(tmp_path / "absent")).startswith("error: ")
    blocker = tmp_path / "file"
    blocker.write_text("x")
    assert tools.write_file(str(blocker / "child.txt"), "y").startswith("error: ")


def test_run_command_returns_exit_code_and_output(tools):
    result = tools.run_command("echo hello; echo oops >&2; exit 3")
    assert result.startswith("exit 3\n")
    assert "stdout:\nhello\n" in result
    assert "stderr:\noops\n" in result


def test_run_command_times_out(tools, monkeypatch):
    monkeypatch.setattr(tools, "COMMAND_TIMEOUT", 0.5)
    assert tools.run_command("sleep 5") == "error: timed out after 0.5s"


def test_every_call_appends_an_evidence_line(services, tools):
    tools.write_file("a.txt", "x" * 1000)
    tools.run_command("echo hi")
    tools.http_fetch(f"{services['merchant']}/health")
    lines = _evidence(tools)
    assert [line["tool"] for line in lines] == ["write_file", "run_command", "http_fetch"]
    assert all({"ts", "tool", "args", "result"} <= set(line) for line in lines)
    assert len(lines[0]["args"]["content"]) <= workstation.ARG_CHARS + len("...(truncated)")


def test_an_unwritable_evidence_path_is_ignored(tools, tmp_path, monkeypatch):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    monkeypatch.setattr(tools, "EVIDENCE_PATH", blocker / "calls.jsonl")
    assert tools.run_command("echo still-runs").startswith("exit 0")


def test_the_mcp_server_lists_the_five_tools():
    import asyncio

    names = [tool.name for tool in asyncio.run(build_mcp().list_tools())]
    assert names == ["http_fetch", "read_file", "write_file", "list_dir", "run_command"]


def _closed_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
