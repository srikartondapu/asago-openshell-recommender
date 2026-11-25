#!/usr/bin/env python3
"""Static validation of a recommended OpenShell policy with NVIDIA's Policy Prover (openshell-prover).

The prover only answers "does candidate allow anything beyond boundary?". To run positive/negative tests with it, each
test action is encoded as a tiny policy that allows just that action, and checked against the recommended policy as
the boundary:
  within_boundary  -> the recommended policy ALLOWS the action
  exceeds_boundary -> the recommended policy BLOCKS it
Expected: good actions (from the manifest's declared capabilities) allowed, bad actions (from ASAGO attacks) blocked.

Limits of openshell-prover 0.1.8, reported per test as "not provable" (covered by the sandbox probes instead):
  - MCP rules (tool allowlist) are outside its model: they are removed from the prover copy of the policy;
  - file paths that do not appear in the policy can't be resolved statically.

usage:
  prove.py --policy policy.yaml --probes probes.json --out DIR
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import shlex
import subprocess
import tempfile
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

import yaml

PROVER = "openshell-prover"
TIMEOUT_S = 20
BINARY_PATHS = {"ws": "/opt/app-root/.venv/bin/python-mcp", "mcp": "/opt/app-root/.venv/bin/python-ogx"}
SHELL_CLIENTS = {"curl": "/usr/bin/curl", "wget": "/usr/bin/wget"}


def prover_copy(policy: dict) -> tuple[dict, list[str]]:
    """Copy of the policy the prover can read, plus notes on what was removed."""
    p = copy.deepcopy(policy)
    notes = []
    nets = p.get("network_policies") or {}
    for name in list(nets):
        eps = nets[name].get("endpoints") or []
        if any((e.get("protocol") or "").lower() == "mcp" for e in eps):
            nets.pop(name)
            notes.append(f"network rule '{name}' (MCP tool allowlist) removed: MCP is outside the prover's model")
    fs = p.get("filesystem_policy") or {}
    if fs.get("include_workdir"):
        fs["include_workdir"] = False
        notes.append("include_workdir set to false: the prover can't resolve the image workdir")
    return p, notes


def url_parts(url: str) -> tuple[str, int, str] | None:
    u = urlparse(url)
    if not u.hostname:
        return None
    port = u.port or (443 if u.scheme == "https" else 80)
    return u.hostname, port, u.path or "/"


def test_from_probe(p: dict) -> dict | None:
    """One prover test (or a not-provable record) from a probe action."""
    a = p.get("action") or {}
    t = a.get("type")
    base = {"probe_id": p["probe_id"], "kind": p["kind"], "threat_id": p.get("threat_id"),
            "effect": p.get("effect"), "expect": "allowed" if p["kind"] == "benign" else "blocked"}
    if t == "http_fetch":
        parts = url_parts(a.get("url", ""))
        if not parts:
            return None
        host, port, path = parts
        return {**base, "domain": "network", "label": f"{BINARY_PATHS['ws'].rsplit('/', 1)[-1]} {a.get('method', 'GET')} {host}:{port}{path}",
                "net": {"binary": BINARY_PATHS["ws"], "host": host, "port": port,
                        "method": (a.get("method") or "GET").upper(), "path": path}}
    if t == "run_command":
        cmd = a.get("command", "")
        try:
            argv = shlex.split(cmd)
        except ValueError:
            argv = cmd.split()
        url = next((x for x in argv if re.match(r"https?://", x)), None)
        parts = url_parts(url) if url else None
        if argv and argv[0] in SHELL_CLIENTS and parts:
            host, port, path = parts
            return {**base, "domain": "network", "label": f"{argv[0]} -> {host}:{port}{path}",
                    "net": {"binary": SHELL_CLIENTS[argv[0]], "host": host, "port": port, "method": None, "path": None}}
        return {**base, "domain": "process", "label": cmd[:80], "not_provable": "local command inside the sandbox: no network/file rule to prove (probe only)"}
    if t in ("write_file", "read_file"):
        return {**base, "domain": "filesystem", "label": f"{t.split('_')[0]} {a.get('path')}",
                "fs": {"mode": "read_write" if t == "write_file" else "read_only", "path": a.get("path")}}
    if t == "mcp_call":
        return {**base, "domain": "mcp", "label": f"MCP tools/call {a.get('name')}",
                "not_provable": "MCP is outside the prover's model (probe only)"}
    return None


def tiny_policy(test: dict, candidate: dict) -> dict:
    d = {"version": 1, "landlock": copy.deepcopy(candidate.get("landlock") or {"compatibility": "best_effort"}),
         "process": copy.deepcopy(candidate.get("process") or {}),
         "filesystem_policy": {"include_workdir": False, "read_only": [], "read_write": []},
         "network_policies": {}}
    if "net" in test:
        n = test["net"]
        ep = {"host": n["host"], "port": n["port"]}
        if n["method"]:
            ep.update(protocol="rest", enforcement="enforce", rules=[{"allow": {"method": n["method"], "path": n["path"]}}])
        d["network_policies"]["test"] = {"binaries": [{"path": n["binary"]}], "endpoints": [ep]}
    if "fs" in test:
        f = test["fs"]
        path = f["path"]
        # the prover decides paths that match the policy's own entries; use the policy entry that would cover it if any
        fsp = candidate.get("filesystem_policy") or {}
        entries = list(fsp.get(f["mode"]) or [])
        if f["mode"] == "read_only":  # a read-write entry also grants reads
            entries += list(fsp.get("read_write") or [])
        cover = next((e for e in entries if path == e or path.startswith(e.rstrip("/") + "/")), None)
        d["filesystem_policy"][f["mode"]] = [cover or path]
    return d


def run_prover(cand_path: Path, boundary_path: Path) -> dict:
    r = subprocess.run([PROVER, "check", str(cand_path), "--boundary", str(boundary_path), "--output", "json",
                        "--timeout", f"{TIMEOUT_S}s"], capture_output=True, text=True, timeout=TIMEOUT_S + 30)
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return {"result": "error", "reason": (r.stderr or r.stdout).strip()[:300]}


def verdict(test: dict, res: dict) -> tuple[str, str]:
    """(pass|fail|not_provable, detail)."""
    if test.get("not_provable"):
        return "not_provable", test["not_provable"]
    r = res.get("result")
    if r == "within_boundary":
        got = "allowed"
    elif r == "exceeds_boundary":
        got = "blocked"
    else:
        return "not_provable", f"{r}: {res.get('reason') or res.get('reason_code') or ''}".strip()
    detail = got
    if got == "blocked" and res.get("counterexample"):
        ce = res["counterexample"]
        detail += f" (outside the policy: {ce.get('domain')} {ce.get('binary') or ''} {ce.get('host') or ce.get('path') or ''}:{ce.get('port') or ''})".replace("  ", " ")
    return ("pass" if got == test["expect"] else "fail"), detail


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--policy", required=True)
    ap.add_argument("--probes", required=True, nargs="+", help="probe plan(s): good + bad actions")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    policy = yaml.safe_load(Path(a.policy).read_text())
    pcopy, notes = prover_copy(policy)

    tests, seen = [], set()
    for pf in a.probes:
        for p in json.loads(Path(pf).read_text())["probes"]:
            t = test_from_probe(p)
            if not t:
                continue
            key = (t["kind"], t["label"])
            if key in seen:
                continue
            seen.add(key)
            tests.append(t)

    with tempfile.TemporaryDirectory() as td:
        bpath = Path(td) / "boundary.yaml"
        bpath.write_text(yaml.safe_dump(pcopy))
        whole = run_prover(bpath, bpath)  # sanity: the prover can read the policy at all
        for i, t in enumerate(tests):
            res = {}
            if not t.get("not_provable"):
                cpath = Path(td) / f"t{i}.yaml"
                cpath.write_text(yaml.safe_dump(tiny_policy(t, policy)))
                res = run_prover(cpath, bpath)
            t["result"], t["detail"] = verdict(t, res)
            t["prover"] = res.get("result")

    counts = Counter((t["kind"], t["result"]) for t in tests)
    summary = {"policy": a.policy, "prover_reads_policy": whole.get("result"), "prover_copy_notes": notes,
               "tests": len(tests), "counts": {f"{k}/{r}": n for (k, r), n in sorted(counts.items())}}
    (out / "prove.json").write_text(json.dumps({"summary": summary, "tests": tests}, indent=1))

    lines = ["# Policy Prover validation", "",
             f"Policy: `{a.policy}`. Tool: `openshell-prover check <test> --boundary <policy>` (one tiny policy per action).",
             "`within_boundary` = the policy allows the action; `exceeds_boundary` = it blocks it.", ""]
    lines += [f"- Prover copy: {n}" for n in notes] + [f"- Prover reads the policy: `{whole.get('result')}`", ""]
    lines += ["| Kind | Result | Count |", "|---|---|---|"]
    lines += [f"| {k} | {r} | {n} |" for (k, r), n in sorted(counts.items())]
    lines += ["", "| Kind | Action | Expect | Result | Detail |", "|---|---|---|---|---|"]
    order = {"fail": 0, "pass": 1, "not_provable": 2}
    for t in sorted(tests, key=lambda t: (t["kind"] != "benign", order[t["result"]], t["domain"])):
        lines.append(f"| {t['kind']} | `{t['label']}` | {t['expect']} | **{t['result']}** | {t['detail']} |")
    (out / "prove.md").write_text("\n".join(lines) + "\n")
    print(json.dumps(summary, indent=1))
    return 1 if counts.get(("benign", "fail")) or counts.get(("harmful", "fail")) else 0


if __name__ == "__main__":
    raise SystemExit(main())
