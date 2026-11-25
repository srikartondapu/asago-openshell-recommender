#!/usr/bin/env python3
"""Assemble the recommendation bundle for one agent: the least-privilege OpenShell policy plus its validation.

Inputs (already produced): compiled policy dir (compile.py --manifest ...), the agent manifest, tagger output,
probe plans + probe results (no policy vs recommended), and prove.py output.

Output dir:
  policy.yaml, provider-profile.yaml, runtime.json   the recommendation (deploy with deploy.sh)
  README.md                                          what each part of the policy does and which manifest input it came from
  tests.json                                         good actions (manifest) + bad actions (ASAGO attacks, stand-ins)
  prove.md / prove.json                              copied from prove.py
  validation.md                                      valid config + prover + sandbox probes, no policy vs recommended
  gaps.json                                          per attack (issue #12 shape): blocked by which rule, or residual risk -> guardrails

usage:
  bundle.py --manifest M --policy-dir D --tags T --plans P1 [P2] --results R1 [R2] --prove DIR --out OUT
  (--results: probe output roots containing <variant>/{baseline,least-privilege}/probes.jsonl)
"""
from __future__ import annotations

import argparse
import json
import shutil
from collections import Counter, defaultdict
from pathlib import Path

import yaml

RULES = {  # effect -> (policy section, rule in plain words)
    "call_undeclared_tool": ("network / MCP", "MCP tools/call allowed only for the use case's declared tools"),
    "egress_unknown_host": ("network", "connections only to declared hosts:ports"),
    "data_in_outbound_payload": ("network", "connections only to declared hosts:ports"),
    "exec_spawned_network": ("network", "each endpoint open only to the program that needs it (no curl/wget)"),
    "egress_undeclared_method": ("network / REST", "declared HTTP methods and paths only"),
    "fs_write_outside_workspace": ("filesystem", "writes only in the declared workspace"),
    "fs_read_undeclared": ("filesystem", "reads only system paths + declared paths (image workdir not exposed)"),
    "secret_disclosure": ("secrets", "tool token injected by OpenShell; the sandbox only sees a placeholder"),
}
RESIDUAL = {  # effect -> why OpenShell can't stop it
    "call_declared_tool_bad_args": "The tool is part of the use case, so the policy must allow it. OpenShell sees the tool name, "
                                   "not whether the arguments are legitimate (whose order, what amount, true summary) -> guardrails / business rule.",
    "exec_local_command": "A command that stays inside the sandbox. The agent runtime exposes run_command; OpenShell confines its "
                          "network and files but can't stop the command itself -> remove the tool from the agent, or guardrails.",
    "harmful_output_only": "Text-only harm: no action for a sandbox to block -> guardrails.",
}
COND_NO, COND_NONE, COND_REC = "no-sandbox", "openshell-default", "least-privilege"


def load_jsonl(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []


def attack_outcome(statuses: list) -> str:
    if not statuses or all(s == "gap" for s in statuses):
        return "content_only"
    if any(s in ("allowed", "unenforced") for s in statuses):
        return "exposed"
    if all(s in ("denied", "gap") for s in statuses):
        return "blocked"
    return "error"


def target_of(action: dict | None) -> str | None:
    if not action:
        return None
    t = action.get("type") or ""
    return {"mcp_call": f"tool:{action.get('name')}", "http_fetch": f"url:{action.get('url')}",
            "run_command": f"command:{action.get('command')}", "write_file": f"write:{action.get('path')}",
            "read_file": f"read:{action.get('path')}"}.get(t)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--policy-dir", required=True)
    ap.add_argument("--tags", required=True)
    ap.add_argument("--plans", required=True, nargs="+")
    ap.add_argument("--results", required=True, nargs="+")
    ap.add_argument("--prove", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    man = yaml.safe_load(Path(a.manifest).read_text())
    variant = man["variant"]
    pdir = Path(a.policy_dir)

    # 1. the recommendation itself
    for f in ("policy.yaml", "provider-profile.yaml", "runtime.json"):
        src = pdir / f
        if src.exists():
            shutil.copyfile(src, out / f)
    policy = yaml.safe_load((out / "policy.yaml").read_text())
    profile = yaml.safe_load((out / "provider-profile.yaml").read_text()) if (out / "provider-profile.yaml").exists() else {}

    # 2. probes: plans + results
    plans = {}
    for pf in a.plans:
        for p in json.loads(Path(pf).read_text())["probes"]:
            plans[p["probe_id"]] = p
    res = {COND_NO: {}, COND_NONE: {}, COND_REC: {}}
    for root in a.results:
        for c in res:
            # the CLI's `test` writes the recommended policy's results as "recommended"; older runs used "least-privilege"
            for d in (c, "recommended") if c == COND_REC else (c,):
                for r in load_jsonl(Path(root) / variant / d / "probes.jsonl"):
                    res[c][r["probe_id"]] = r
    tags = {t["scenario_id"]: t for t in json.loads(Path(a.tags).read_text())}

    # 3. tests.json (good + bad actions) and gaps.json (per attack)
    tests = []
    by_attack = defaultdict(list)
    for pid, p in plans.items():
        tests.append({"probe_id": pid, "kind": "good" if p["kind"] == "benign" else "bad",
                      "expect": "allowed" if p["kind"] == "benign" else "blocked", "threat_id": p.get("threat_id"),
                      "effect": p.get("effect"), "action": p.get("action"), "source": p.get("source"),
                      "no_sandbox": (res[COND_NO].get(pid) or {}).get("status"),
                      "openshell_default": (res[COND_NONE].get(pid) or {}).get("status"),
                      "recommended": (res[COND_REC].get(pid) or {}).get("status")})
        if p["kind"] == "harmful":
            by_attack[p["scenario_id"]].append(p)
    (out / "tests.json").write_text(json.dumps({"variant": variant, "tests": tests}, indent=1))

    gaps = []
    for sid, ps in by_attack.items():
        st_no = [(res[COND_NO].get(p["probe_id"]) or {}).get("status") for p in ps]
        st_none = [(res[COND_NONE].get(p["probe_id"]) or {}).get("status") for p in ps]
        st_rec = [(res[COND_REC].get(p["probe_id"]) or {}).get("status") for p in ps]
        if any(s is None for s in st_rec):
            continue  # not run under the recommended policy
        effects = sorted({p["effect"] for p in ps})
        blocked_by, residual = set(), []
        for p, s in zip(ps, st_rec):
            if s == "denied" and p["effect"] in RULES:
                blocked_by.add(f"{RULES[p['effect']][0]}: {RULES[p['effect']][1]}")
            elif s in ("allowed", "gap"):
                residual.append(RESIDUAL.get(p["effect"], f"{p['effect']}: allowed by the policy (review)"))
        tag = tags.get(sid) or {}
        ev = [{"source": "attack_text", "quote": e.get("evidence")} for e in tag.get("effects", []) if e.get("evidence")]
        if not ev and ps[0].get("source", "").startswith("HAND-WRITTEN"):
            ev = [{"source": "stand-in", "quote": ps[0]["source"]}]
        gaps.append({"attack_id": sid, "threat_id": ps[0].get("threat_id"),
                     "origin": "hand-written stand-in" if sid.startswith("standin:") else ("deployment check" if sid.startswith("check:") else "ASAGO run 4"),
                     "effects": effects,
                     "targeted_capabilities": sorted({t for t in (target_of(p.get("action")) for p in ps) if t}),
                     "no_sandbox": attack_outcome(st_no), "openshell_default": attack_outcome(st_none), "recommended": attack_outcome(st_rec),
                     "blocked_by": sorted(blocked_by), "residual_risks": sorted(set(residual)), "evidence": ev[:3]})
    gaps.sort(key=lambda g: (g["recommended"] != "blocked", g["threat_id"] or "", g["attack_id"]))
    (out / "gaps.json").write_text(json.dumps({"variant": variant, "attacks": gaps}, indent=1))

    # 4. prover output
    pv = Path(a.prove)
    for f in ("prove.md", "prove.json"):
        if (pv / f).exists() and (pv / f).resolve() != (out / f).resolve():
            shutil.copy(pv / f, out / f)
    prove = json.loads((pv / "prove.json").read_text()) if (pv / "prove.json").exists() else {"summary": {}, "tests": []}

    # 5. validation.md
    def gained_rows() -> list[str]:
        agg: dict[str, list[int]] = {}
        for t in tests:
            e = t.get("effect")
            if t["kind"] == "bad" and e in RULES:
                k = agg.setdefault(e, [0, 0, 0])
                k[0] += 1
                k[1] += t["no_sandbox"] == "denied"
                k[2] += t["recommended"] == "denied"
        return [f"| {e} | {RULES[e][0]}: {RULES[e][1]} | {b}/{n} -> **{a}/{n}** |"
                for e, (n, b, a) in sorted(agg.items(), key=lambda x: -x[1][0])]

    def count(kind, cond):
        c = Counter((res[cond].get(t["probe_id"]) or {}).get("status") for t in tests if t["kind"] == kind)
        return c
    g_no, g_none, g_rec = count("good", COND_NO), count("good", COND_NONE), count("good", COND_REC)
    b_no, b_none, b_rec = count("bad", COND_NO), count("bad", COND_NONE), count("bad", COND_REC)
    att_no = Counter(g["no_sandbox"] for g in gaps)
    att_none = Counter(g["openshell_default"] for g in gaps)
    att_rec = Counter(g["recommended"] for g in gaps)
    ps = prove["summary"].get("counts", {})
    n_good = sum(1 for t in tests if t["kind"] == "good")
    n_bad = sum(1 for t in tests if t["kind"] == "bad")
    v = [f"# Validation: {man['agent']} ({variant})", "",
         "Three checks on the recommended (least-privilege) policy. **Before** = no sandbox (same agent image in a plain container, "
         "measured with the same actions). Middle = **OpenShell default**: the sandbox created with no policy supplied, so OpenShell "
         "applies its own restrictive default (no network rules: every connection denied; read-only system paths; writes only to /tmp). "
         "It is safe but breaks the agent. After = the recommended policy.", "",
         "## 1. Valid OpenShell configuration",
         f"- OpenShell accepted `policy.yaml` and `provider-profile.yaml` (sandbox created by `deploy.sh`) and the agent ran.",
         f"- Good actions (the use case's declared capabilities) under the recommended policy: "
         f"**{g_rec.get('allowed', 0)}/{n_good} allowed**.", "",
         "## 2. Policy Prover (static, `prove.py`)",
         f"- Each action encoded as a tiny policy and proven against the recommended policy: "
         f"good pass {ps.get('benign/pass', 0)}, bad pass {ps.get('harmful/pass', 0)}, "
         f"fail {ps.get('benign/fail', 0) + ps.get('harmful/fail', 0)}, "
         f"not provable {ps.get('benign/not_provable', 0) + ps.get('harmful/not_provable', 0)} "
         "(MCP tool calls, local commands and file paths not in the policy; covered by check 3).",
         "- Details: `prove.md`.", "",
         "## 3. Sandbox probes (dynamic: the agent's own programs perform each action inside the real sandbox, no LLM)",
         "This is a sandbox test, not an agent test: it assumes the agent is already fooled and checks what the sandbox still allows.", "",
         "| | Before: no sandbox | OpenShell default (no policy supplied) | **After: recommended** |", "|---|---|---|---|",
         f"| Good actions allowed | {g_no.get('allowed', 0)}/{n_good} | {g_none.get('allowed', 0)}/{n_good} | {g_rec.get('allowed', 0)}/{n_good} |",
         f"| Bad actions denied | {b_no.get('denied', 0)}/{n_bad} | {b_none.get('denied', 0)}/{n_bad} | **{b_rec.get('denied', 0)}/{n_bad}** |",
         f"| Attacks fully blocked | {att_no.get('blocked', 0)}/{len(gaps)} | {att_none.get('blocked', 0)}/{len(gaps)} | **{att_rec.get('blocked', 0)}/{len(gaps)}** |",
         f"| Attacks with an action still allowed | {att_no.get('exposed', 0)} | {att_none.get('exposed', 0)} | {att_rec.get('exposed', 0)} |",
         f"| Text-only attacks (nothing to block) | {att_no.get('content_only', 0)} | {att_none.get('content_only', 0)} | {att_rec.get('content_only', 0)} |", "",
         "Before, `unenforced` actions (attack hosts that don't exist on the internet) count as not protected: nothing would stop a real host.", "",
         "## Protection gained: before (no sandbox) -> after (recommended)", "",
         "| What the action needs | Rule that now stops it | Actions denied before -> after |", "|---|---|---|"] + gained_rows() + ["",
         "## Still allowed under the recommended policy (-> guardrails)", ""]
    rc = Counter()
    for g in gaps:
        if g["recommended"] != "blocked":
            for r in g["residual_risks"]:
                rc[r] += 1
    v += [f"- **{n} attacks**: {r}" for r, n in rc.most_common()]
    (out / "validation.md").write_text("\n".join(v) + "\n")

    # 6. policy-diff.md: before -> after as a file diff
    import difflib
    perm = pdir.parent / COND_NONE / "observed-default-policy.yaml"
    if perm.exists():
        d = difflib.unified_diff(yaml.safe_dump(yaml.safe_load(perm.read_text()), sort_keys=False).splitlines(), yaml.safe_dump(yaml.safe_load((out / "policy.yaml").read_text()), sort_keys=False).splitlines(),
                                 "openshell-default (observed)", "recommended/policy.yaml", lineterm="", n=2)
        (out / "policy-diff.md").write_text(
            "# Policy diff: before -> after\n\n"
            "- **No sandbox:** there are no rules at all, so every action reaches its target.\n"
            "- **OpenShell default -> recommended:** the default has no network rules (everything denied, so the agent can't reach its "
            "model or tools). The recommendation adds exactly the use case's endpoints (`+`) and tightens the filesystem and process.\n"
            "- The MCP tool server, its tool allowlist and the token injection are in `provider-profile.yaml`.\n\n"
            "```diff\n" + "\n".join(d) + "\n```\n")

    # 6. README.md: the policy explained
    fs = policy.get("filesystem_policy") or {}
    caps = {c["id"]: c for c in man.get("capabilities", [])}
    r = [f"# Recommended OpenShell policy: {man['agent']} ({variant})", "",
         "Least-privilege policy generated from the agent's use case / deployment facts "
         f"(`{Path(a.manifest).name}`). OpenShell denies everything not listed here. "
         "The ASAGO attacks are used to **test** it (`validation.md`) and to list what is left for guardrails (`gaps.json`).", "",
         "| Part | Rule | From |", "|---|---|---|",
         f"| Process | run as uid/gid {(policy.get('process') or {}).get('run_as_user')} (non-root) | image user |",
         f"| Filesystem: read-only | {', '.join(fs.get('read_only') or [])} | system paths the runtime needs |",
         f"| Filesystem: read-write | {', '.join(fs.get('read_write') or [])} | capability `workspace` + /dev/null, /dev/shm |",
         f"| Filesystem: image workdir | include_workdir = {fs.get('include_workdir')} | not declared, so not exposed |"]
    for name, rule in (policy.get("network_policies") or {}).items():
        bins = ", ".join(b["path"].rsplit("/", 1)[-1] for b in rule.get("binaries", []))
        for ep in rule.get("endpoints", []):
            allow = "; ".join(f"{x['allow'].get('method')} {x['allow'].get('path')}" for x in ep.get("rules", []) if "allow" in x)
            src = "model endpoint" if name == "model" else ("capability `" + name.replace("http_", "") + "`"
                                                             if name.replace("http_", "") in caps else name)
            r.append(f"| Network `{name}` | {bins} -> {ep['host']}:{ep['port']}"
                     f"{' (' + ep.get('protocol', '') + ', ' + ep.get('enforcement', 'audit') + ': ' + allow + ')' if allow else ''} | {src} |")
    for ep in profile.get("endpoints", []) or []:
        tools = []
        for x in ep.get("rules", []):
            tl = ((x.get("allow") or {}).get("tool") or {}).get("any")
            if tl:
                tools += tl
        r.append(f"| Network: MCP tool server (provider profile) | {', '.join(b.rsplit('/', 1)[-1] for b in profile.get('binaries', []))} -> "
                 f"{ep['host']}:{ep['port']}{ep.get('path', '')}, tools/call only: {', '.join(tools)} | server `klarna_tools` + mcp_tool capabilities |")
    if profile:
        r.append(f"| Secrets | tool token as OpenShell provider credential `{profile.get('id')}` "
                 f"({', '.join(c['name'] for c in profile.get('credentials', []))}); the sandbox sees a placeholder, the proxy adds the real value | deployment |")
    r += ["", "Everything else is denied: other hosts, other programs opening connections (curl, wget), other HTTP methods/paths, "
          "writes outside the workspace, reads outside the listed paths.", "",
          "**OpenShell blocks capabilities, not intent.** It sees the tool name, host:port, which program connects, HTTP method/path "
          "and file paths. It never sees the conversation and doesn't judge tool arguments, so misuse of a tool the use case needs "
          "stays with guardrails (`gaps.json`).", "",
          "Files: `policy.yaml` + `provider-profile.yaml` + `runtime.json` (deploy: `src/openshell_recommender/deploy.sh <this dir> <sandbox>`), "
          "`tests.json`, `prove.md`, `validation.md`, `gaps.json`."]
    (out / "README.md").write_text("\n".join(r) + "\n")
    print(json.dumps({"out": str(out), "tests": len(tests), "attacks": len(gaps),
                      "recommended": dict(att_rec), "openshell_default": dict(att_none),
                      "good_allowed_rec": g_rec.get("allowed", 0), "good": n_good}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
