"""SSE MCP server with generic workstation tools, run inside the sandbox.

``python -m mini_agents.workstation --host 127.0.0.1 --port P`` serves SSE at
``/sse``. The tools are deliberately plain (fetch a URL, read/write/list a
file, run a shell command) so that the sandbox policy, not the tool, decides
what they may reach. Every call appends one JSON line to ``EVIDENCE_PATH``
(``/sandbox/workdir/.asago/workstation-calls.jsonl``) for the replay harness;
if that file cannot be written, the call still runs and the line is dropped.

``http_fetch`` uses urllib, which honours ``HTTP(S)_PROXY`` from the
environment by default.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

WORKDIR = Path("/sandbox/workdir")
EVIDENCE_PATH = WORKDIR / ".asago" / "workstation-calls.jsonl"
FETCH_TIMEOUT = 10.0
FETCH_CHARS = 2000
COMMAND_TIMEOUT = 15.0
OUTPUT_CHARS = 4000
FILE_CHARS = 20000
ARG_CHARS = 500
SUMMARY_CHARS = 200


def _clip(value: Any, limit: int = ARG_CHARS) -> Any:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= limit else text[:limit] + "...(truncated)"


def _record(tool: str, args: dict[str, Any], result: str) -> None:
    """Append one evidence line; an unwritable path is ignored."""
    line = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "tool": tool,
        "args": {name: _clip(value) for name, value in args.items()},
        "result": _clip(result, SUMMARY_CHARS),
    }
    try:
        EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with EVIDENCE_PATH.open("a", encoding="utf-8") as sink:
            sink.write(json.dumps(line) + "\n")
    except OSError:
        pass


def _resolve(path: str) -> Path:
    """Relative paths are taken from the workdir when there is one."""
    candidate = Path(path).expanduser()
    if not candidate.is_absolute() and WORKDIR.is_dir():
        return WORKDIR / candidate
    return candidate


def _fetch(url: str, method: str, body: Optional[str], headers: Optional[dict]) -> str:
    # urllib also opens file:// and ftp://; the agent gets web requests only.
    if urllib.parse.urlsplit(url).scheme not in ("http", "https"):
        return "error: only http and https URLs are allowed"
    try:
        request = urllib.request.Request(
            url,
            data=body.encode("utf-8") if body is not None else None,
            method=method.upper(),
            headers={str(k): str(v) for k, v in (headers or {}).items()},
        )
        with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT) as reply:
            text = reply.read(FETCH_CHARS * 4).decode("utf-8", errors="replace")
            return f"HTTP {reply.status}\n{text[:FETCH_CHARS]}"
    except urllib.error.HTTPError as exc:
        # A 403 from a policy proxy lands here; its body says why.
        try:
            text = exc.read(FETCH_CHARS * 4).decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001 - the status is still worth reporting
            text = ""
        return f"HTTP {exc.code} {exc.reason}\n{text[:FETCH_CHARS]}"
    except urllib.error.URLError as exc:
        return f"error: request failed: {exc.reason}"
    except TimeoutError:
        return f"error: timed out after {FETCH_TIMEOUT:g}s"
    except Exception as exc:  # noqa: BLE001 - a tool result, never a raise
        return f"error: {type(exc).__name__}: {exc}"


def http_fetch(
    url: str,
    method: str = "GET",
    body: str | None = None,
    headers: dict | None = None,
) -> str:
    """HTTP request; returns the status line and the first 2000 characters of the body."""
    result = _fetch(url, method, body, headers)
    _record(
        "http_fetch",
        {"url": url, "method": method, "body": body, "headers": headers},
        result,
    )
    return result


def read_file(path: str) -> str:
    """Read a text file (first 20000 characters)."""
    try:
        text = _resolve(path).read_text(encoding="utf-8", errors="replace")
        result = text[:FILE_CHARS] + ("\n...(truncated)" if len(text) > FILE_CHARS else "")
    except Exception as exc:  # noqa: BLE001
        result = f"error: {type(exc).__name__}: {exc}"
    _record("read_file", {"path": path}, result)
    return result


def write_file(path: str, content: str) -> str:
    """Write a text file, replacing it and creating missing parent directories."""
    try:
        target = _resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        result = f"wrote {len(content)} characters to {target}"
    except Exception as exc:  # noqa: BLE001
        result = f"error: {type(exc).__name__}: {exc}"
    _record("write_file", {"path": path, "content": content}, result)
    return result


def list_dir(path: str) -> str:
    """List a directory; subdirectories end with '/'."""
    try:
        entries = sorted(
            entry.name + ("/" if entry.is_dir() else "")
            for entry in _resolve(path).iterdir()
        )
        result = "\n".join(entries) if entries else "(empty)"
    except Exception as exc:  # noqa: BLE001
        result = f"error: {type(exc).__name__}: {exc}"
    _record("list_dir", {"path": path}, result)
    return result


def run_command(command: str) -> str:
    """Run a shell command (15 second limit); returns exit code, stdout and stderr."""
    try:
        done = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=COMMAND_TIMEOUT,
            cwd=WORKDIR if WORKDIR.is_dir() else None,
        )
        result = (
            f"exit {done.returncode}\n"
            f"stdout:\n{done.stdout[:OUTPUT_CHARS]}\n"
            f"stderr:\n{done.stderr[:OUTPUT_CHARS]}"
        )
    except subprocess.TimeoutExpired:
        result = f"error: timed out after {COMMAND_TIMEOUT:g}s"
    except Exception as exc:  # noqa: BLE001
        result = f"error: {type(exc).__name__}: {exc}"
    _record("run_command", {"command": command}, result)
    return result


TOOLS = (http_fetch, read_file, write_file, list_dir, run_command)


def build_mcp() -> FastMCP:
    mcp = FastMCP(
        name="workstation",
        instructions="Generic workstation tools: HTTP fetch, files, shell.",
        json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=False,
        ),
    )
    for tool in TOOLS:
        mcp.add_tool(tool)
    return mcp


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Sandbox workstation tools (SSE MCP)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8890)
    args = parser.parse_args(argv)

    mcp = build_mcp()
    print("Workstation MCP server starting")
    print(f"    SSE endpoint → http://{args.host}:{args.port}/sse")
    mcp.settings.host = args.host
    mcp.settings.port = args.port
    mcp.run(transport="sse")


if __name__ == "__main__":
    main()
