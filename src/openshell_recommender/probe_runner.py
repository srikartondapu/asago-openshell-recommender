#!/usr/bin/env python3
"""In-sandbox runner for deterministic direct probes (no LLM involved).

Runs inside the mock agent's OpenShell sandbox under the same binary the agent uses, so the
policy that applies to the probe is the policy that applies to the agent:
  --binary-kind mcp  -> run as /opt/app-root/.venv/bin/python-ogx  (Klarna MCP tool calls)
  --binary-kind ws   -> run as /opt/app-root/.venv/bin/python-mcp  (workstation tools, secret_check)

--actions FILE is a JSON list of {probe_id, type, ...}. Actions run sequentially, one fresh MCP session per
MCP call, with a 1.5 s pause between actions so the sandbox log windows do not overlap. Each action prints one
JSON line on stdout: {probe_id, start_epoch, end_epoch, ok, result_excerpt, error, http_status, mcp_is_error,
exit_code}. The tool token is only used for the Authorization header and is scrubbed from every printed field.
Nothing is written to disk except the workstation tool's own call log.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import threading
import time

SLEEP_S = 1.5
ACTION_TIMEOUT_S = 30.0
EXCERPT_CHARS = 300
URL = os.environ.get("MIDOJO_TOOL_URL", "http://host.openshell.internal:8765/mcp")
TOKEN = os.environ.get("MIDOJO_TOOL_TOKEN", "")
PLACEHOLDER_PREFIX = "openshell:resolve"
HTTP_STATUS_RE = re.compile(r"^HTTP (\d{3})")
EXIT_RE = re.compile(r"^exit (-?\d+)")


def scrub(text: str) -> str:
    """Never print the real token value (the placeholder form is not a secret, but is scrubbed too)."""
    if TOKEN and len(TOKEN) >= 8:
        text = text.replace(TOKEN, "[REDACTED]")
    return text


def flat_error(exc: BaseException) -> str:
    """anyio/httpx wrap failures in ExceptionGroups; flatten them into one readable string."""
    subs = getattr(exc, "exceptions", None)
    if subs:
        return "; ".join(flat_error(x) for x in subs)
    return f"{type(exc).__name__}: {exc}"


async def _mcp_call(name: str, arguments: dict) -> dict:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    headers = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}
    async with streamablehttp_client(URL, headers=headers) as (reader, writer, _):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            res = await session.call_tool(name, arguments)
    text = "\n".join((getattr(c, "text", None) or "") for c in (res.content or []))
    return {"text": text, "mcp_is_error": bool(res.isError)}


def run_mcp(action: dict) -> dict:
    return asyncio.run(asyncio.wait_for(_mcp_call(action["name"], action.get("arguments") or {}),
                                        ACTION_TIMEOUT_S))


def run_ws(action: dict) -> dict:
    """Run the workstation tool in a worker thread so a hung call cannot stall the batch."""
    from mini_agents.workstation import server as ws

    box: dict = {}

    def target() -> None:
        try:
            t = action["type"]
            if t == "http_fetch":
                out = ws.http_fetch(action["url"], action.get("method", "GET"), action.get("body"))
            elif t == "read_file":
                out = ws.read_file(action["path"])
            elif t == "write_file":
                out = ws.write_file(action["path"], action.get("content", "probe"))
            elif t == "list_dir":
                out = ws.list_dir(action["path"])
            elif t == "run_command":
                out = ws.run_command(action["command"])
            elif t == "secret_check":
                val = os.environ.get("MIDOJO_TOOL_TOKEN", "")
                out = json.dumps({"token_env_set": bool(val),
                                  "placeholder": val.startswith(PLACEHOLDER_PREFIX)})
            else:
                out = f"error: unknown action type {t}"
            box["text"] = out
        except BaseException as exc:  # noqa: BLE001 - reported as the action error
            box["error"] = flat_error(exc)

    th = threading.Thread(target=target, daemon=True)
    th.start()
    th.join(ACTION_TIMEOUT_S)
    if th.is_alive():
        raise TimeoutError(f"no result after {ACTION_TIMEOUT_S:g}s")
    if "error" in box:
        raise RuntimeError(box["error"])
    return {"text": box.get("text", "")}


def execute(action: dict, kind: str) -> dict:
    t = action["type"]
    want_mcp = t == "mcp_call"
    if want_mcp != (kind == "mcp"):
        raise RuntimeError(f"action type {t} does not belong to binary kind {kind}")
    if want_mcp:
        return run_mcp(action)
    return run_ws(action)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--actions", required=True)
    ap.add_argument("--binary-kind", choices=("mcp", "ws"), required=True)
    args = ap.parse_args(argv)
    with open(args.actions, encoding="utf-8") as fh:
        actions = json.load(fh)

    for i, action in enumerate(actions):
        if i:
            time.sleep(SLEEP_S)
        start = time.time()
        rec = {"probe_id": action["probe_id"], "start_epoch": None, "end_epoch": None, "ok": False,
               "result_excerpt": "", "error": None, "http_status": None, "mcp_is_error": None,
               "exit_code": None}
        try:
            rec["start_epoch"] = start
            res = execute(action, args.binary_kind)
            text = res.get("text", "") or ""
            rec["ok"] = True
            rec["mcp_is_error"] = res.get("mcp_is_error")
            m = HTTP_STATUS_RE.match(text)
            rec["http_status"] = int(m.group(1)) if m else None
            m = EXIT_RE.match(text)
            rec["exit_code"] = int(m.group(1)) if m else None
            if action.get("withhold_content"):
                rec["result_excerpt"] = f"[content withheld: {len(text)} chars]"
            else:
                rec["result_excerpt"] = scrub(text)[:EXCERPT_CHARS]
        except BaseException as exc:  # noqa: BLE001 - every failure becomes a record
            rec["error"] = scrub(flat_error(exc))[:EXCERPT_CHARS]
        rec["end_epoch"] = time.time()
        if rec["start_epoch"] is None:
            rec["start_epoch"] = start
        print(json.dumps(rec, ensure_ascii=True), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
