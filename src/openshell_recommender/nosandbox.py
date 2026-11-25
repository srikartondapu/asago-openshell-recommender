#!/usr/bin/env python3
""""No sandbox" condition: run the same probe actions in a plain Podman container (same agent image, same binaries,
same mock services) with NO OpenShell, so we measure what the agent could do before any sandbox policy.

Writes <out>/<variant>/no-sandbox/probes.jsonl in the same format as probe.py run, with statuses:
  allowed     the action reached its target (tool server, merchant, attacker sink, file, command)
  denied      something actively refused it (OS permission error)
  unenforced  it failed for a reason that is NOT a control (e.g. the attack's fictional internet host doesn't resolve):
              nothing would have stopped it against a real host, so it counts as not protected
  gap         text-only attack (no action)
The tool token is passed to podman by name from this process's environment (not on the command line).

usage: nosandbox.py --probes probes.json [more.json] --variant refunds --out DIR
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import time
from collections import Counter
from pathlib import Path

from openshell_recommender import common, probe

IMAGE = "localhost/mini-agents-sandbox:lab"
BINS = {"mcp": "/opt/app-root/.venv/bin/python-ogx", "ws": "/opt/app-root/.venv/bin/python-mcp"}


def run_batch(kind: str, acts: list[dict], tmp: Path, secret: str | None) -> list[dict]:
    if not acts:
        return []
    f = tmp / f"actions-{kind}.json"
    f.write_text(json.dumps([dict(probe_id=p["probe_id"], **p["action"]) for p in acts]))
    env = dict(os.environ, MIDOJO_TOOL_TOKEN=secret or "", MIDOJO_TOOL_URL=probe.MIDOJO_URL)
    cmd = ["podman", "run", "--rm", "--add-host", "host.openshell.internal:host-gateway",
           "-e", "MIDOJO_TOOL_TOKEN", "-e", "MIDOJO_TOOL_URL",
           "-v", f"{probe.RUNNER}:/probe/probe_runner.py:ro", "-v", f"{f}:/probe/actions.json:ro",
           "--entrypoint", BINS[kind], IMAGE, "/probe/probe_runner.py", "--actions", "/probe/actions.json",
           "--binary-kind", kind]
    timeout = len(acts) * (probe.PAUSE_S + probe.ACTION_TIMEOUT_S) + 60
    r = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=timeout)
    recs = []
    for line in r.stdout.splitlines():
        if line.strip().startswith("{"):
            try:
                recs.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    if r.returncode not in (0,) and not recs:
        raise SystemExit(f"podman run failed rc={r.returncode}: {r.stderr.strip()[-300:]}")
    return recs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--probes", required=True, nargs="+")
    ap.add_argument("--variant", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    selected = []
    for pf in a.probes:
        doc = json.loads(Path(pf).read_text())
        if doc.get("variant") != a.variant:
            raise SystemExit(f"{pf}: variant mismatch")
        selected += doc["probes"]
    secret = common.load_secret()
    red = common.Redactor(secret)
    t0 = time.time()
    recs = {}
    with tempfile.TemporaryDirectory(prefix="nosandbox-") as td:
        for kind in ("mcp", "ws"):
            for rec in run_batch(kind, [p for p in selected if p.get("binary") == kind and p.get("action")], Path(td), secret):
                recs[rec["probe_id"]] = rec
    rows = []
    for p in selected:
        rec = recs.get(p["probe_id"])
        dec = probe.decide(p, rec, [], red)
        typ = (p.get("action") or {}).get("type")
        if rec and typ == "http_fetch" and rec.get("http_status"):
            dec.update(status="allowed", decided_by=f"reached the host (HTTP {rec['http_status']} from the remote server)")
        elif dec["status"] == "denied" and "OS permission" in (dec.get("decided_by") or ""):
            dec.update(decided_by="OS file permission of the container user (not a sandbox policy)")
        elif dec["status"] in ("error", "denied"):
            dec.update(status="unenforced",
                       decided_by=f"no control stopped it ({dec.get('decided_by')}); failure not caused by a policy")
        row = {k: p.get(k) for k in ("probe_id", "kind", "scenario_id", "threat_id", "effect", "binary", "source")}
        row["action"] = {k: v for k, v in (p.get("action") or {}).items() if k != "content"}
        row.update(dec)
        if rec:
            row["result_excerpt"] = red.text(rec.get("result_excerpt") or rec.get("error") or "")[:300]
        rows.append(red.obj(row))
    out = Path(a.out) / a.variant / "no-sandbox"
    out.mkdir(parents=True, exist_ok=True)
    with (out / "probes.jsonl").open("w") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    meta = {"variant": a.variant, "condition": "no-sandbox", "image": IMAGE, "statuses": dict(Counter(r["status"] for r in rows)),
            "wall_s": round(time.time() - t0, 1), "finished": probe.now_iso()}
    (out / "run.json").write_text(json.dumps(meta, indent=1))
    print(json.dumps(meta, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
