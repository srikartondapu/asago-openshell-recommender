"""Shared helpers for the recommender's probe and no-sandbox runners.

Moved unchanged from the retired replay.py and classify.py (the LLM replay is no longer run).
"""
from __future__ import annotations

import datetime as dt
import os
import re
import signal
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent


ROOT = HERE.parents[1]  # repo root (HERE = src/openshell_recommender)


TOKEN_FILE = ROOT / "lab" / "run" / "tool-token"


DEPLOY_SH = HERE / "deploy.sh"


DEPLOY_TIMEOUT = 900


CMD_TIMEOUT = 120


BEARER_RE = re.compile(r"Bearer\s+\S+")


ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


LOG_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?")


EPOCH_RE = re.compile(r"^\[(\d{9,11}(?:\.\d+)?)\]")


SESSION_CLOSE_RE = re.compile(r"\bDELETE\s+\S*/mcp\b")


MCP_DENY_RE = re.compile(r"\bDENIED\s+POST\s+\S*/mcp\b")


METHOD_URL_RE = re.compile(r"DENIED\s+([A-Z]+)\s+(\S+)")


NET_DEST_RE = re.compile(r"->\s*(\S+)")


DNS_DENY_RE = re.compile(r"\bDENIED\s+([A-Za-z0-9.-]+)\s+\[reason:policy_dns_ineligible\]")


def parse_ts(text: str) -> dt.datetime | None:
    try:
        t = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def load_secret() -> str | None:
    """Read the tool token for redaction only. Its value is never printed or logged."""
    try:
        return TOKEN_FILE.read_text().strip() or None
    except OSError:
        return None


class Redactor:
    def __init__(self, secret: str | None):
        self.secret = secret

    def text(self, s: str) -> str:
        if self.secret:
            s = s.replace(self.secret, "[REDACTED]")
        return BEARER_RE.sub("[REDACTED]", s)

    def obj(self, o):
        if isinstance(o, str):
            return self.text(o)
        if isinstance(o, dict):
            return {k: self.obj(v) for k, v in o.items()}
        if isinstance(o, list):
            return [self.obj(v) for v in o]
        return o


def _decode(b) -> str:
    if b is None:
        return ""
    return b.decode("utf-8", errors="replace") if isinstance(b, bytes) else b


def run(cmd: list[str], timeout: float, env: dict | None = None) -> dict:
    """Run a command in its own process group; on timeout kill the whole group."""
    t0 = time.monotonic()
    p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         env=env, start_new_session=True)
    timed_out = False
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            pass
        out, err = p.communicate()
        err = _decode(err) + f"\n[timeout after {timeout}s]"
    return {"rc": None if timed_out else p.returncode, "out": ANSI_RE.sub("", _decode(out)),
            "err": ANSI_RE.sub("", _decode(err)), "timed_out": timed_out,
            "dur": time.monotonic() - t0}


def line_epoch(line: str) -> float | None:
    """Epoch seconds of a sandbox log line: leading "[sec.ms]" first, then an ISO prefix."""
    m = EPOCH_RE.match(line)
    if m:
        return float(m.group(1))
    m = LOG_TS_RE.match(line)
    if m:
        t = parse_ts(m.group(0))
        return t.timestamp() if t else None
    return None


def is_candidate(line: str) -> bool:
    return "DENIED" in line or "Permission denied" in line or \
        ("engine:l7-mcp" in line and "decision=deny" in line)


def is_session_close(line: str) -> bool:
    """MCP session teardown (DELETE /mcp) denials are not agent decisions; ignored by design."""
    return "DENIED" in line and SESSION_CLOSE_RE.search(line) is not None


def classify_denial(line: str) -> dict:
    m = DNS_DENY_RE.search(line)
    if m:
        return {"kind": "dns", "dest": m.group(1)}
    if "engine:l7-mcp" in line or MCP_DENY_RE.search(line):
        kind = "mcp"
    elif "NET:OPEN" in line:
        m = NET_DEST_RE.search(line)
        return {"kind": "net", "dest": m.group(1) if m else None}
    elif "HTTP" in line:
        kind = "http"
    else:
        return {"kind": "other"}
    m = METHOD_URL_RE.search(line)
    return {"kind": kind, "method": m.group(1) if m else None, "url": m.group(2) if m else None}


def record_host(rec: dict) -> str | None:
    if rec["kind"] in ("net", "dns"):
        return rec.get("dest")
    if rec.get("url"):
        return urlparse(rec["url"]).netloc or None
    return None


SECRET_PATH_RE = re.compile(r"(?i)(\.env\b|\.ssh|\.aws|\.netrc|\.pgpass|id_rsa|id_ed25519|\.pem$|\.key$|passwd|"
                            r"shadow|secret|credential|token|api[_-]?key)")