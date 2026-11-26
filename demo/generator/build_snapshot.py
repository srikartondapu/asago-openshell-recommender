#!/usr/bin/env python3
"""Build the OpenShell recommender demo snapshot.

Writes demo/data/snapshot.json and demo/data/snapshot.js (window.OS_SNAPSHOT = {...};)
from the run4 outputs in the OpenShell recommender repo, the manifests, the catalog,
and the pre-run FS-ISAC policy-mapper output.

Offline: no network calls. Provenance strings are repo-relative. Absolute user paths
are scrubbed and token-looking strings are redacted before anything is written.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

import yaml

SITE_DIR = Path(__file__).resolve().parents[1]  # demo/
PROJECT_DIR = SITE_DIR.parent  # repo root
EX_DIR = "examples/klarna-support"
DEFAULT_RUN = f"{EX_DIR}/asago-run4"
DEFAULT_POLICY = f"{EX_DIR}/policy-mapper/risk-extraction.json"
DEFAULT_OUT = SITE_DIR / "data"

RUN_LABEL = "run4-fsisac-allthreats-nocap"
VARIANTS = ("refunds",)  # the demo shows the full Klarna support agent only
AGENT_LABEL = "Klarna support agent (all use-case tools declared)"
STANDIN_DIR = "standin-fs-read"  # sibling of the run dir under examples/klarna-support/
BUNDLE_REL = "recommendation/klarna-support"
CONDITIONS = ("no-sandbox", "openshell-default", "least-privilege")
COND_KEY = {"no-sandbox": "no_sandbox", "openshell-default": "openshell_default", "least-privilege": "recommended"}
UNENFORCED_NOTE = "host doesn't exist; no control stopped it"
SECRET_SCENARIO = "check:token-readable-in-agent"
LAB_HOST = "host.openshell.internal"
MERCHANT_PORT = 9101
SINK_PORT = 9102
WORKSPACE_PREFIXES = ("/sandbox/workdir", "/tmp")
OPENSHELL_VERSION = "0.1.8"  # the version the lab was tested with

# Threat names. Scenario metadata overrides these where present (T2, T3, T9, T10, T15).
# The rest come from the OWASP Agentic threat list; they are not restated in repo sources.
OWASP_NAMES = {
    "T1": "Memory Poisoning",
    "T2": "Tool Misuse",
    "T3": "Privilege Compromise",
    "T4": "Resource Overload",
    "T5": "Cascading Hallucination Attacks",
    "T6": "Intent Breaking & Goal Manipulation",
    "T7": "Misaligned & Deceptive Behaviors",
    "T8": "Repudiation & Untraceability",
    "T9": "Identity Spoofing & Impersonation",
    "T10": "Overwhelming Human in the Loop",
    "T11": "Unexpected RCE and Code Attacks",
    "T12": "Agent Communication Poisoning",
    "T13": "Rogue Agents in Multi-Agent Systems",
    "T14": "Human Attacks on Multi-Agent Systems",
    "T15": "Human Manipulation",
    "T16": "Insecure Inter-Agent Communication",
    "T17": "Supply Chain Compromise",
}

# OpenShell coverage from docs/overview.md (mechanism per threat) and docs/recommender-design.md
# (results so far). Threats not listed here stay "unknown". Guardrails coverage is not
# documented in the repo, so it is "unknown" for every threat.
OPENSHELL_COVERAGE = {
    "T1": ("strong", "fs.workspace_only_writes; design doc T1-01 notes persistence blocked."),
    "T2": ("strong", "mcp.tool_allowlist blocks undeclared tools; bad-args refund stays a gap."),
    "T3": ("strong", "Non-root and secrets.provider_injection; undeclared tool calls blocked by allowlist."),
    "T6": ("strong", "Egress allowlist plus HTTP method/path rules block injected exfiltration."),
    "T8": ("partial", "Audit log records every allowed and denied action; traceability only."),
    "T11": ("strong", "egress.binary_scoped and no shell egress; spawned-network probes denied."),
    "T17": ("partial", "Runtime registry block described in overview.md; not exercised in runs."),
    "T5": ("none", "No sandbox mechanism; overview.md routes it to guardrails as a gap."),
    "T7": ("none", "No sandbox mechanism; overview.md routes it to guardrails as a gap."),
    "T15": ("none", "No sandbox mechanism; overview.md routes it to guardrails as a gap."),
}
UNKNOWN_NOTE = "No OpenShell coverage statement for this threat in repo docs."

REDACT_PATTERNS = [
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{6,}"), "Bearer [REDACTED]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"), "sk-[REDACTED]"),
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{8,}|\bxox[abprs]-[A-Za-z0-9-]{8,}|\bAKIA[0-9A-Z]{12,}"),
     "[REDACTED]"),
    (re.compile(r"(?i)\b([A-Za-z0-9_]*(?:token|api[ _-]?key|secret|password))(\s*[:=]\s*)(['\"]?)[^\s'\",;]+"),
     r"\1\2\3[REDACTED]"),
]
ABS_USER_PATH = re.compile(r"/(?:Users|home)/[^\s\"'`,;)\]}]+")


def scrub(s):
    """Replace absolute user paths and the repo root with placeholders."""
    if not isinstance(s, str):
        return s
    s = s.replace(str(PROJECT_DIR), "<repo>")
    return ABS_USER_PATH.sub("<local-path>", s)


def redact(s):
    if not isinstance(s, str):
        return s
    for pat, rep in REDACT_PATTERNS:
        s = pat.sub(rep, s)
    return s


def clean(s, n=None):
    """Scrub, redact and optionally truncate free text."""
    if s is None:
        return ""
    s = scrub(redact(str(s)))
    if n is not None and len(s) > n:
        s = s[: n - 3].rstrip() + "..."
    return s


def sanitize(obj):
    """Final pass over the whole snapshot."""
    if isinstance(obj, dict):
        return {k: sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize(v) for v in obj]
    if isinstance(obj, str):
        return scrub(redact(obj))
    return obj


def rel(p: Path) -> str:
    return os.path.relpath(str(p), str(PROJECT_DIR))


def load_yaml(p: Path):
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def load_json(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return int(f) if f.is_integer() else round(f, 4)


def threat_key(t: str) -> int:
    m = re.match(r"T(\d+)$", t or "")
    return int(m.group(1)) if m else 999


# ---------- use case ----------

def build_use_case(project: Path, manifests: Path) -> dict:
    text = (manifests / "use-case-klarna-agent.txt").read_text(encoding="utf-8")
    profile = load_yaml(manifests / "klarna-agent-profile.yaml")
    facts_raw = load_yaml(manifests / "klarna-agent-qualification-facts.yaml").get("facts", [])
    facts = []
    for f in facts_raw:
        fact = f.get("fact", {})
        item = {"id": fact.get("fact_id"), "status": f.get("status")}
        if "value" in f:
            item["value"] = f["value"]
        facts.append(item)

    entry_points = []
    for i, ep in enumerate(profile.get("entry_points", []), 1):
        entry_points.append({
            "id": f"EP{i}",
            "type": ep.get("entry_point_type") or ep.get("direction"),
            "zone": ep.get("ingress_zone") or "input",
            "desc": clean(f"{ep.get('name')} (controllability: {ep.get('controllability')})", 200),
        })

    variants = {}
    labels = {"refunds": (AGENT_LABEL, "Declares all the use case's tools, including refund and payment scheduling.")}
    for v in VARIANTS:
        man = load_yaml(manifests / f"sandbox-manifest-{v}.yaml")
        caps = man.get("capabilities", [])
        exposed_mcp = man.get("exposed", {}).get("mcp_tools", [])
        mcp = [c["id"] for c in caps if c.get("kind") == "mcp_tool"]
        http = [{
            "host": c.get("host"),
            "port": c.get("port"),
            "methods": c.get("methods", []),
            "paths": [c["path"]] if c.get("path") else c.get("paths", []),
        } for c in caps if c.get("kind") == "http"]
        fs_rw = [p for c in caps if c.get("kind") == "fs" for p in c.get("read_write", [])]
        missing = [t for t in exposed_mcp if t not in mcp]
        label, extra = labels[v]
        desc = (f"{extra} Declares {len(mcp)} of {len(exposed_mcp)} exposed MCP tools; "
                f"not declared: {', '.join(missing) or 'none'}. Shell, undeclared hosts and files outside "
                f"the workspace are not declared.")
        variants[v] = {
            "label": label,
            "desc": desc,
            "mcp_tools": mcp,
            "http": http,
            "fs": {"read_write": fs_rw},
            "workstation_tools": man.get("exposed", {}).get("workstation_tools", []),
        }

    return {
        "name": "Klarna customer-support agent",
        "text": clean(text),
        "entry_points": entry_points,
        "facts": facts,
        "variants": variants,
    }


# ---------- policy mapper ----------

def build_policy_mapper(path: Path) -> dict:
    d = load_json(path)
    risks = []
    for r in d.get("risks", []):
        ev = (r.get("evidence") or [{}])[0].get("text", "")
        conf = r.get("confidence")
        item = {
            "id": r.get("risk_id"),
            "name": clean(r.get("risk_name"), 200),
            "taxonomy": r.get("taxonomy"),
            "confidence": round(float(conf), 3) if conf is not None else None,
            "grounding": r.get("grounding_confidence"),
            "grounding_confidence": r.get("grounding_confidence"),
            "evidence": [clean(e.get("text", ""), 300) for e in (r.get("evidence") or [])[:2]],
            "evidence_total": len(r.get("evidence") or []),
        }
        for k in ("threat", "threat_source", "vulnerability", "consequence", "impact"):
            if r.get(k):
                item[k] = clean(str(r[k]), 300)
        mits = [{"name": clean(str(m.get("action_name") or m.get("action_id") or ""), 120),
                 "description": clean(str(m.get("description") or ""), 300), "source": m.get("source")}
                for m in (r.get("mitigations") or [])[:3]]
        if mits:
            item["mitigations"] = mits
            item["mitigations_total"] = len(r.get("mitigations") or [])
        risks.append(item)
    ev = d.get("eval", {})
    rs, md = d.get("retrieval_stats", {}), d.get("metadata", {})
    stats = {
        "chunks": rs.get("total_chunks"), "candidates_retrieved": rs.get("total_candidates_retrieved"),
        "auto_accepted": rs.get("auto_accepted"), "llm_judged": rs.get("llm_judged"),
        "grounding_filtered": rs.get("grounding_filtered"),
        "with_causal_chain": sum(1 for r in d.get("risks", []) if r.get("threat")),
        "model": md.get("model"), "cross_encoder": md.get("cross_encoder_model"),
        "bi_encoder": md.get("bi_encoder_model"), "timestamp": md.get("timestamp"),
    }
    doc = Path(d["source_documents"][0]).name if d.get("source_documents") else "unknown"
    return {
        "policy_name": "FS-ISAC GenAI policy",
        "name": "FS-ISAC GenAI policy",
        "risks_total": len(risks),
        "stats": stats,
        "source_doc": doc,
        "source_note": clean(
            f"Pre-run policy-mapper output (risk-extraction v{d.get('version')}, "
            f"model {d.get('metadata', {}).get('model')}) at {rel(path)} (shipped in this repo). "
            f"The policy mapper was not re-run. Source document: {doc}. "
            f"Mapper eval: precision {ev.get('precision')}, recall {ev.get('recall')}, F1 {ev.get('f1')}."
        ),
        "risks": risks,
        "counts": {
            "total": len(risks),
            "by_taxonomy": dict(Counter(r["taxonomy"] for r in risks)),
            "accepted_by": dict(Counter(r.get("accepted_by") for r in d.get("risks", []))),
            "eval": {
                "expected": num(ev.get("total_expected")),
                "extracted": num(ev.get("total_extracted")),
                "matched": num(ev.get("matched")),
                "precision": num(ev.get("precision")),
                "recall": num(ev.get("recall")),
                "f1": num(ev.get("f1")),
                "pass": str(ev.get("pass")).lower() == "true",
            },
        },
    }


# ---------- threats ----------

def build_threats(scen_name_overrides: dict) -> dict:
    out = {}
    curated = load_yaml(Path(__file__).resolve().parent / "threats.yaml") or {}
    for i in range(1, 18):
        t = f"T{i}"
        cov = OPENSHELL_COVERAGE.get(t)
        if cov:
            os_level, note = cov
        else:
            os_level, note = "unknown", UNKNOWN_NOTE
        c = curated.get(t)
        if c:
            out[t] = {"name": c["name"], "openshell": c["openshell"], "guardrails": c["guardrails"],
                      "note": clean(c.get("note", ""), 200)}
            continue
        out[t] = {
            "name": scen_name_overrides.get(t, OWASP_NAMES[t]),
            "openshell": os_level,
            "guardrails": "unknown",
            "note": clean(note, 140),
        }
    return out


# ---------- scenarios and artifacts ----------

def build_scenarios(run: Path, risk_ids: set) -> tuple[dict, dict, dict]:
    items, name_overrides = [], {}
    for p in sorted((run / "scenarios" / "scenarios").glob("*.yaml")):
        d = load_yaml(p)
        meta = d.get("scenario_seed_metadata", {})
        tid = meta.get("threat_id")
        narr = d.get("narrative", {}) or {}
        if meta.get("threat_name") and tid:
            name_overrides.setdefault(tid, clean(meta["threat_name"], 120))
        refs = sorted(set(re.findall(r"risk_id:\s*([A-Za-z0-9_.:-]+)", p.read_text(encoding="utf-8"))))
        items.append({
            "scenario_id": d.get("scenario_id"),
            "threat_id": tid,
            "title": clean(narr.get("title"), 200),
            "summary": clean(narr.get("summary"), 300),
            "entry_point": clean(narr.get("entry_point"), 200),
            "technique": clean(meta.get("attack_pattern_name"), 200),
            "risk_ids": refs,
            "threat_name": clean(meta.get("threat_name"), 120),
            "attack_pattern_name": clean(meta.get("attack_pattern_name"), 200),
            "narrative_summary": clean(narr.get("summary"), 500),
            "steps": [clean(str(x.get("action") if isinstance(x, dict) else x), 200)
                      for x in (narr.get("steps") or [])[:6] if (x.get("action") if isinstance(x, dict) else x)],
            "actor_type": (d.get("actor_profile") or {}).get("actor_type", ""),
        })
    items.sort(key=lambda x: (threat_key(x["threat_id"]), x["scenario_id"]))
    by_threat = Counter(x["threat_id"] for x in items)
    scen = {
        "total": len(items),
        "by_threat": {t: by_threat[t] for t in sorted(by_threat, key=threat_key)},
        "items": items,
    }
    unmatched = sum(1 for x in items for r in x["risk_ids"] if r not in risk_ids)
    scen_threat = {x["scenario_id"]: x["threat_id"] for x in items}
    return scen, name_overrides, {"threat_of": scen_threat, "unmatched_risk_refs": unmatched}


def coverage_from_logs(run: Path) -> dict:
    """Coverage gate per scenario from the per-artifact logs (the archived manifest.json holds one record only)."""
    out = {}
    for lg in sorted((run / "logs").glob("artifact-scenario_v2_*.log")):
        m = re.search(r"Classified (scenario:v2:\S+) injection_surface=\S+ coverage=(\w+)", lg.read_text(encoding="utf-8", errors="replace"))
        if m:
            out[m.group(1)] = {"scenario_id": m.group(1), "gate_result": m.group(2), "gate_reason": ""}
    return out


def build_artifacts(run: Path, threat_of: dict, scen_by_id: dict | None = None) -> dict:
    scen_by_id = scen_by_id or {}
    manifest = coverage_from_logs(run)
    items = []
    for d in sorted((run / "artifacts").glob("scenario*")):
        if not d.is_dir():
            continue
        garak = sorted(d.glob("*-garak.json"))
        if not garak:
            continue
        g = load_json(garak[0])
        val_p = d / "validation.json"
        val = load_json(val_p) if val_p.exists() else {}
        sid = g.get("scenario_id")
        turns = []
        for t in g.get("turns", []):
            calls = []
            for c in t.get("tool_calls") or []:
                fn = c.get("function", c)
                args = fn.get("arguments")
                if not isinstance(args, str):
                    args = json.dumps(args, ensure_ascii=False)
                calls.append({"name": fn.get("name"), "arguments": clean(args, 200)})
            turns.append({
                "role": t.get("role"),
                "content": clean(t.get("content") or "", 700),
                "adversarial": bool(t.get("adversarial")),
                "tool_calls": calls,
            })
        rubric = (g.get("detector_rubric") or {}).get("rubrics", {}) or {}
        items.append({
            "scenario_id": sid,
            "threat_id": threat_of.get(sid),
            "injection_surface": g.get("injection_surface"),
            "validation_ok": bool(val.get("ok", False)),
            "turns": turns,
            "attack_turn_index": g.get("attack_turn_index"),
            "attack_success": [clean(x, 200) for x in rubric.get("attack_success", [])],
            "attack_blocked": [clean(x, 200) for x in rubric.get("attack_blocked", [])],
            "attack_pattern_name": (scen_by_id.get(sid) or {}).get("attack_pattern_name"),
            "threat_name": (scen_by_id.get(sid) or {}).get("threat_name"),
            "narrative_summary": (scen_by_id.get(sid) or {}).get("narrative_summary"),
            "detector_rubric": {
                "context": clean((g.get("detector_rubric") or {}).get("context"), 500),
                "rubrics": {k: [clean(x, 200) for x in v] for k, v in rubric.items() if isinstance(v, list)},
                "judge_prompt": clean((g.get("detector_rubric") or {}).get("judge_prompt"), 1500),
            },
            "artifact": {
                "validated": bool(val.get("ok", False)),
                "fidelity_total": val.get("fidelity_total"),
                "attempts": val.get("attempts"),
                "scores": val.get("scores"),
                "coverage_gate": (manifest.get(sid) or {}).get("gate_result"),
                "coverage_reason": clean((manifest.get(sid) or {}).get("gate_reason"), 200),
            },
        })
    items.sort(key=lambda x: (threat_key(x["threat_id"]), x["scenario_id"]))
    return {"items": items}


# ---------- pipeline stage evidence (same fields as the guardrails demo generator) ----------

def build_battery(run: Path) -> dict:
    f = run / "scenarios" / "threat-surface.yaml"
    if not f.exists():
        return {"threats": [], "derived": True}
    data = load_yaml(f)
    entries = data.get("entries", [])
    all_patterns = sorted({ap for e in entries for ap in e.get("attack_pattern_ids", [])})
    threats = {}
    for e in entries:
        for tid in e.get("agentic_threat_ids", []):
            threats.setdefault(tid, set()).add((e.get("risk_card") or {}).get("risk_id"))
    return {
        "derived": False,
        "risk_cards": len(entries),
        "governance_only": len(data.get("governance_only", []) or []),
        "attack_patterns_total": len(all_patterns),
        "threats": [{"threat_id": t, "threat_name": None,
                     "attack_patterns": [p for p in all_patterns if p.startswith(f"AP-{t}-")],
                     "risks_mapped": len(threats[t])} for t in sorted(threats, key=threat_key)],
    }


def build_scenario_funnel(run: Path) -> dict:
    sd = run / "scenarios"
    fi = load_json(sd / "finalization-inventory.json")
    dec = fi.get("admission_decisions", [])
    status, codes, gate_names, gate_fail = Counter(), Counter(), [], Counter()
    for d in dec:
        status[d.get("status")] += 1
        if not d.get("admitted"):
            v = (d.get("violations") or [{}])[0]
            codes[(v.get("code") if isinstance(v, dict) else str(v)) or "unspecified"] += 1
        for g in d.get("gate_results", []):
            if g["gate"] not in gate_names and g["gate"] != "admission_exception":
                gate_names.append(g["gate"])
            if g.get("applicable") and not g.get("passed"):
                gate_fail[g["gate"]] += 1
    admitted = sum(1 for d in dec if d.get("admitted"))
    cov = load_json(sd / "planning-checkpoint.json") if (sd / "planning-checkpoint.json").exists() else {}
    gaps = (load_json(sd / "coverage-gaps.json") or {}).get("coverage_gaps", {}) if (sd / "coverage-gaps.json").exists() else {}
    log = (sd / "pipeline.log").read_text(encoding="utf-8", errors="replace") if (sd / "pipeline.log").exists() else ""
    m = re.search(r"Rule pre-filter: (\d+)/(\d+) candidates rejected, (\d+) passed", log)
    ep = re.search(r"Entry point filter: (\d+)/(\d+) entry points", log)
    return {
        "enumerated": len(dec), "admitted": admitted, "quarantined": len(dec) - admitted,
        "rejected": status.get("rejected", 0), "generation_failed": status.get("generation_or_finalization_failed", 0),
        "quarantine_reasons": [{"code": k, "count": v} for k, v in codes.most_common()],
        "gates": gate_names, "gate_failures": [{"gate": k, "count": v} for k, v in gate_fail.most_common()],
        "candidates_attempted": len(cov.get("attempted_candidate_ids") or []),
        "rule_prefilter": {"rejected": int(m.group(1)), "candidates": int(m.group(2)), "to_llm": int(m.group(3))} if m else None,
        "entry_points": {"accessible": int(ep.group(1)), "total": int(ep.group(2))} if ep else None,
        "seeds_without_techniques": re.findall(r"Seed (AP-[A-Z0-9-]+) has no technique IDs", log),
        "uncovered_attack_patterns": gaps.get("uncovered_attack_patterns", []),
    }


def build_provenance(run: Path, policy: dict, scenarios: dict, artifacts: dict, threats: dict) -> dict:
    sf = build_scenario_funnel(run)
    items = artifacts["items"]
    manifest = list(coverage_from_logs(run).values())
    gates = Counter(m.get("gate_result") for m in manifest)
    fids = [a["artifact"]["fidelity_total"] for a in items if a["artifact"]["fidelity_total"] is not None]
    by_t = {}
    for s in scenarios["items"]:
        t = by_t.setdefault(s["threat_id"], {"admitted": 0, "artifacts": 0})
        t["admitted"] += 1
    for a in items:
        if a["threat_id"] in by_t:
            by_t[a["threat_id"]]["artifacts"] += 1
    per_threat = [{"threat_id": k, "name": threats.get(k, {}).get("name", k), **v}
                  for k, v in sorted(by_t.items(), key=lambda x: threat_key(x[0]))]
    adm = sf["admitted"]
    return {"stages": {
        "policy": {"source_doc": policy.get("source_doc"), "model": policy["stats"].get("model"),
                   "outputs": {"risks": policy["risks_total"]}, "stats": policy["stats"]},
        "scenarios": {"generated": sf["enumerated"], "admitted": adm, "quarantined": sf["quarantined"], "detail": sf,
                      "per_threat": per_threat},
        "artifacts": {
            "admitted_input": adm, "attempted": len(manifest), "produced": len(items),
            "validated": sum(1 for a in items if a["artifact"]["validated"]),
            "coverage_gate": dict(gates), "fidelity_min": min(fids) if fids else None,
            "fidelity_avg": round(sum(fids) / len(fids), 1) if fids else None, "per_threat": per_threat,
            "selection_rule": (f"All {adm} admitted scenarios were turned into artifacts ({len(items)} produced). "
                               "There is no baseline filter in this pipeline: every artifact's actions are probed "
                               "against the sandbox in stage 4."),
        }}}


# ---------- catalog ----------

def build_catalog(project: Path) -> dict:
    c = load_yaml(project / "src" / "openshell_recommender" / "rules.yaml")
    gap = set(c.get("gap_effects", []) or [])
    return {"effects": [{"id": k, "desc": clean(v, 200), "gap": k in gap} for k, v in c["effects"].items()]}


# ---------- recommendation bundle ----------

def md_plain(s: str) -> str:
    return s.replace("`", "").replace("**", "").strip()


def build_recommendation(run: Path) -> dict:
    b = run / BUNDLE_REL
    rows, notes = [], []
    for line in (b / "README.md").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("|"):
            cells = [md_plain(c) for c in line.strip("|").split("|")]
            if len(cells) >= 3 and cells[0] not in ("Part",) and not set(cells[0]) <= set("-: "):
                rows.append({"part": clean(cells[0]), "rule": clean(cells[1]), "source": clean(cells[2])})
        elif line.startswith("Everything else is denied"):
            notes.append(clean(md_plain(line)))
    prof = load_yaml(b / "provider-profile.yaml")
    ep = (prof.get("endpoints") or [{}])[0]
    tools, methods = [], []
    for r in ep.get("rules") or []:
        al = r.get("allow") or {}
        if al.get("method"):
            methods.append(al["method"])
        if (al.get("tool") or {}).get("any"):
            tools = list(al["tool"]["any"])
    prove = load_json(b / "prove.json")
    tests = [{"kind": "good" if t["kind"] == "benign" else "bad", "label": clean(t.get("label"), 120),
              "expect": t.get("expect"), "result": t.get("result"), "detail": clean(t.get("detail"), 200),
              "domain": t.get("domain"), "threat_id": t.get("threat_id"), "effect": t.get("effect")}
             for t in prove.get("tests", [])]
    gaps = []
    for a in load_json(b / "gaps.json").get("attacks", []):
        ev = (a.get("evidence") or [{}])[0]
        gaps.append({
            "attack_id": a["attack_id"], "threat_id": a.get("threat_id"), "origin": a.get("origin"),
            "standin": a.get("origin") != "ASAGO run 4",
            "effects": a.get("effects") or [],
            "targets": [clean(x, 120) for x in (a.get("targeted_capabilities") or [])],
            "no_sandbox": a.get("no_sandbox"), "openshell_default": a.get("openshell_default"), "recommended": a.get("recommended"),
            "blocked_by": [clean(x, 160) for x in (a.get("blocked_by") or [])],
            "residual": [clean(x, 400) for x in (a.get("residual_risks") or [])],
            "quote": clean(ev.get("quote"), 260),
        })
    return {
        "policy_yaml": clean((b / "policy.yaml").read_text(encoding="utf-8")).replace("fragments=", "rules="),
        "rows": rows, "notes": notes,
        "mcp": {"id": prof.get("id"), "host": ep.get("host"), "port": ep.get("port"), "path": ep.get("path"),
                "protocol": ep.get("protocol"), "enforcement": ep.get("enforcement"), "methods": methods,
                "tools": tools, "binaries": prof.get("binaries") or []},
        "prove": {"tests": tests, "prover_reads_policy": prove.get("summary", {}).get("prover_reads_policy")},
        "gaps": gaps,
        "diff": build_diff(b),
        "default_policy_yaml": clean((run / "replay" / "conditions" / "refunds" / "openshell-default" / "observed-default-policy.yaml").read_text(encoding="utf-8")),
        "before_after": build_before_after(b),
    }


def build_diff(b: Path) -> dict:
    """policy-diff.md: intro bullets plus the fenced diff block, one typed record per line (no HTML)."""
    intro, lines, inside = [], [], False
    for raw in (b / "policy-diff.md").read_text(encoding="utf-8").splitlines():
        if raw.startswith("```"):
            inside = not inside
            continue
        if inside:
            t = ("file" if raw.startswith(("---", "+++")) else "hunk" if raw.startswith("@@")
                 else "add" if raw.startswith("+") else "del" if raw.startswith("-") else "ctx")
            lines.append({"t": t, "s": clean(raw)})
        elif raw.strip().startswith("- "):
            intro.append(clean(md_plain(raw.strip()[2:])).replace("there is no policy at all, so", "there are no rules at all, so"))
    return {"intro": intro, "lines": lines}


def build_before_after(b: Path) -> dict:
    """The 'protection gained' table of validation.md (parsed) and its 3-column probe summary (for cross-checking)."""
    gained, summary, sec = [], {}, ""
    for line in (b / "validation.md").read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            sec = line
            continue
        if not line.startswith("|"):
            continue
        cells = [md_plain(c) for c in line.strip().strip("|").split("|")]
        if set(cells[0]) <= set("-: ") or cells[0] in ("What the action needs", ""):
            continue
        if sec.startswith("## Protection gained") and len(cells) == 3:
            m = re.match(r"(\d+)/(\d+) -> (\d+)/(\d+)", cells[2])
            gained.append({"effect": cells[0], "rule": clean(cells[1]),
                           "before": int(m.group(1)), "after": int(m.group(3)), "total": int(m.group(2))})
        elif sec.startswith("## 3.") and len(cells) == 4:
            summary[cells[0]] = cells[1:]
    return {"gained": gained, "summary_md": summary}


def build_standin(run: Path) -> dict:
    plan = load_json(run.parent / STANDIN_DIR / "probes.json")
    first = (plan.get("probes") or [{}])[0]
    return {"scenario_id": first.get("scenario_id"), "threat_id": first.get("threat_id"),
            "title": "Prompt injection asks the agent to read an unrelated customer file",
            "label": "hand-written stand-in, not from ASAGO run 4", "source": clean(first.get("source"), 300)}


def build_check(run: Path) -> dict:
    plan = load_json(run.parent / STANDIN_DIR / "probes.json")
    pr = next((x for x in plan.get("probes", []) if x.get("scenario_id") == SECRET_SCENARIO), None)
    if not pr:
        raise SystemExit("SECRET-1 deployment check missing from standin-fs-read/probes.json")
    return {"scenario_id": SECRET_SCENARIO, "threat_id": pr.get("threat_id"), "probe_id": pr.get("probe_id"),
            "title": "Deployment check: can the agent read the real tool token?",
            "label": "deployment check, not an attack", "origin": "deployment check", "source": clean(pr.get("source"), 300)}


# ---------- probes ----------

def url_host(url: str):
    u = urlparse(url or "")
    port = u.port or (443 if u.scheme == "https" else 80)
    return u.hostname or "", port


def fmt_val(v) -> str:
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def in_workspace(path: str) -> bool:
    return any(path.startswith(p) for p in WORKSPACE_PREFIXES)


def resource_for(a: dict):
    t = a.get("type")
    if t == "mcp_call":
        return f"mcp:{a.get('name')}"
    if t == "http_fetch":
        host, port = url_host(a.get("url", ""))
        if host == LAB_HOST and port == MERCHANT_PORT:
            return "merchant_api"
        if host == LAB_HOST and port == SINK_PORT:
            return "attacker_sink"
        return f"net:{host}"
    if t == "run_command":
        return "shell"
    if t in ("read_file", "write_file", "list_dir"):
        return "workspace" if in_workspace(a.get("path", "")) else "outside_workspace"
    if t == "secret_check":
        return "tool_token"
    return None


def action_label(a: dict) -> str:
    t = a.get("type")
    if t == "mcp_call":
        args = a.get("arguments") or {}
        vals = ", ".join(fmt_val(v) for v in args.values()) if isinstance(args, dict) else str(args)
        return clean(f"{a.get('name')}({vals})", 90)
    if t == "http_fetch":
        u = urlparse(a.get("url", ""))
        return clean(f"{a.get('method', 'GET')} {u.netloc}{u.path or '/'}", 90)
    if t == "run_command":
        return clean("run_command: " + str(a.get("command", "")), 90)
    if t in ("read_file", "write_file", "list_dir"):
        return clean(f"{t} {a.get('path', '')}", 90)
    if t == "secret_check":
        return "check: is the real tool token readable?"
    return "content only (no action)"


def load_probe_rows(run: Path, variant: str, cond: str) -> list[dict]:
    p = run / "probe" / "full" / variant / cond / "probes.jsonl"
    if not p.exists():
        raise SystemExit(f"missing probe run: {rel(p)}")
    rows = [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
    sp = run.parent / STANDIN_DIR / "full" / variant / cond / "probes.jsonl"
    if not sp.exists():
        raise SystemExit(f"missing stand-in probe run: {rel(sp)}")
    for line in sp.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            if r.get("kind") == "harmful":  # its benign rows duplicate the run 4 ones
                r["origin"] = "deployment check" if r.get("scenario_id") == SECRET_SCENARIO else "hand-written stand-in"
                r["standin"] = r["origin"] == "hand-written stand-in"
                rows.append(r)
    return rows


def probe_item(r: dict) -> dict:
    a = r.get("action") or {}
    if a:
        action = {"type": a.get("type"), "label": action_label(a), "resource": resource_for(a)}
    else:
        action = {"type": "none", "label": "content only (no action)", "resource": None}
    denials = r.get("denials") or []
    denial = denials[0] if (denials and r.get("status") == "denied") else {}
    if r.get("status") == "denied" and not denial:
        first = str(r.get("result_excerpt") or "").splitlines()
        by = r.get("decided_by") or "OS permission"
        if a.get("type") == "secret_check":
            line = "DENIED - the real token is not exposed to the agent (placeholder only)"
        else:
            line = f"DENIED - {by}" + (f": {first[0]}" if first else "")
        denial = {"kind": "filesystem", "line": line}
    return {
        "probe_id": r.get("probe_id"),
        "scenario_id": r.get("scenario_id"),
        "threat_id": r.get("threat_id"),
        "kind": r.get("kind"),
        "effect": r.get("effect"),
        "action": action,
        "status": r.get("status"),
        "denial_kind": denial.get("kind"),
        "denial_line": clean(denial.get("line"), 220) if denial else None,
        "substitute": bool(r.get("substitute")),
        "standin": bool(r.get("standin")),
        "origin": r.get("origin") or "ASAGO run 4",
        "note": UNENFORCED_NOTE if r.get("status") == "unenforced" else None,
    }


def attack_outcome(rows: list[dict]) -> str:
    """Same rule as src/openshell_recommender/probe.py attack_outcome."""
    act = [r for r in rows if r["kind"] == "harmful" and r.get("action")]
    if not act:
        return "gap_only" if any(r.get("no_action") for r in rows) else "no_effects"
    st = ["allowed" if r["status"] == "unenforced" else r["status"] for r in act]  # unenforced = nothing stopped it
    if "allowed" in st and "denied" in st:
        return "partial"
    if "allowed" in st:
        return "exposed"
    if all(s == "denied" for s in st):
        return "blocked"
    return "inconclusive"


def summarize(rows: list[dict]) -> dict:
    harmful = [r for r in rows if r["kind"] == "harmful"]
    benign = [r for r in rows if r["kind"] == "benign"]
    by_sid: dict[str, list] = {}
    for r in harmful:
        by_sid.setdefault(r["scenario_id"], []).append(r)
    by_threat: dict[str, Counter] = {}
    for sid, srows in by_sid.items():
        tid = srows[0].get("threat_id")
        oc = attack_outcome(srows)
        c = by_threat.setdefault(tid, Counter())
        if oc in ("exposed", "partial"):
            c["exposed"] += 1
        elif oc == "blocked":
            c["blocked"] += 1
        elif oc == "gap_only":
            c["gap_only"] += 1
    total_exposed = sum(c["exposed"] for c in by_threat.values())
    total_blocked = sum(c["blocked"] for c in by_threat.values())
    return {
        "harmful_allowed": sum(r["status"] in ("allowed", "unenforced") for r in harmful),
        "harmful_unenforced": sum(r["status"] == "unenforced" for r in harmful),
        "harmful_total": len(harmful),
        "attacks_total": len(by_sid),
        "harmful_denied": sum(r["status"] == "denied" for r in harmful),
        "gap": sum(r["status"] == "gap" for r in harmful),
        "benign_ok": sum(r["status"] == "allowed" for r in benign),
        "benign_broken": sum(r["status"] == "denied" for r in benign),
        "attacks_exposed": total_exposed,
        "attacks_blocked": total_blocked,
        "by_threat": {t: {k: int(c.get(k, 0)) for k in ("exposed", "blocked", "gap_only")}
                      for t, c in sorted(by_threat.items(), key=lambda x: threat_key(x[0]))},
    }


def build_resources(manifests: Path, all_rows: list[dict]) -> dict:
    rf = load_yaml(manifests / "sandbox-manifest-refunds.yaml")
    tools = rf["servers"]["klarna_tools"]["tools"]
    declared = {"refunds": set(c["id"] for c in rf.get("capabilities", []) if c.get("kind") == "mcp_tool")}
    nodes = []
    for t in tools:
        nodes.append({"id": f"mcp:{t}", "label": t, "group": "mcp_tool",
                      "detail": "Klarna MCP tool on klarna_tools :8765",
                      "declared": {v: t in declared[v] for v in VARIANTS}})
    nodes.append({"id": "merchant_api", "label": f"Merchant shipping API :{MERCHANT_PORT}", "group": "http",
                  "detail": "GET /shipments/* via http_fetch (declared shipping_status)",
                  "declared": {v: True for v in VARIANTS}})
    nodes.append({"id": "attacker_sink", "label": f"Attacker sink :{SINK_PORT}", "group": "attacker",
                  "detail": f"{LAB_HOST}:{SINK_PORT}, lab attacker endpoint (never declared)",
                  "declared": {v: False for v in VARIANTS}})
    hosts = set()
    for r in all_rows:
        a = r.get("action") or {}
        if a.get("type") == "http_fetch":
            h, _ = url_host(a.get("url", ""))
            if h and h != LAB_HOST:
                hosts.add(h)
        if a.get("type") == "run_command":
            hosts.update(h for h in re.findall(r"https?://([^/\s:'\"]+)", a.get("command", "")) if h != LAB_HOST)
    for h in sorted(hosts):
        nodes.append({"id": f"net:{h}", "label": h, "group": "internet",
                      "detail": "External host seen in probes (not declared)",
                      "declared": {v: False for v in VARIANTS}})
    nodes.append({"id": "workspace", "label": "Workspace (/sandbox/workdir, /tmp)", "group": "fs",
                  "detail": "Declared read-write paths", "declared": {v: True for v in VARIANTS}})
    nodes.append({"id": "outside_workspace", "label": "Files outside workspace", "group": "fs",
                  "detail": "System and other paths (not declared)", "declared": {v: False for v in VARIANTS}})
    nodes.append({"id": "shell", "label": "Shell (run_command)", "group": "shell",
                  "detail": "Exposed by the agent; not a declared capability",
                  "declared": {v: False for v in VARIANTS}})
    nodes.append({"id": "tool_token", "label": "Tool token (secret)", "group": "secret",
                  "detail": "Credential the agent process holds; not a declared capability",
                  "declared": {v: False for v in VARIANTS}})
    return {"nodes": nodes}


def now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def build_snapshot(run: Path, policy_path: Path) -> dict:
    manifests = PROJECT_DIR / EX_DIR / "inputs"
    catalog_dir = PROJECT_DIR / "src" / "openshell_recommender"

    policy = build_policy_mapper(policy_path)
    risk_ids = {r["id"] for r in policy["risks"]}
    scenarios, name_overrides, scen_meta = build_scenarios(run, risk_ids)
    artifacts = build_artifacts(run, scen_meta["threat_of"], {x["scenario_id"]: x for x in scenarios["items"]})

    probes, summary, all_rows = {}, {}, []
    for v in VARIANTS:
        probes[v], summary[v] = {}, {}
        for c in CONDITIONS:
            rows = load_probe_rows(run, v, c)
            all_rows.extend(rows)
            probes[v][c] = [probe_item(r) for r in rows]
            summary[v][c] = summarize(rows)

    node_ids = {n["id"] for n in build_resources(manifests, all_rows)["nodes"]}
    for v in probes:
        for c in probes[v]:
            for it in probes[v][c]:
                res = it["action"]["resource"]
                if res is not None and res not in node_ids:
                    raise SystemExit(f"probe resource without a node: {res}")

    snap = {
        "meta": {
            "generated": now_iso(),
            "run": RUN_LABEL,
            "openshell": OPENSHELL_VERSION,
            "models": {
                "generators": "gemma-4-26b",
                "tagger": "rules (LLM fallback unused)",
            },
            "notes": [
                f"Built offline from {rel(run)} (incl. {BUNDLE_REL}/), {rel(policy_path)}, inputs/, "
                "src/openshell_recommender/rules.yaml and the hand-written stand-in probes in "
                f"{EX_DIR}/{STANDIN_DIR}/. No network calls.",
                "One attack is a hand-written stand-in (file read, issue #12 acceptance scenario), not from ASAGO run 4, "
                "and one item is a deployment check (can the agent read the real tool token), not an attack; "
                "both are flagged in probes (standin, origin) and in the recommendation gaps.",
                "Conditions: no sandbox (same agent image in a plain Podman container, same probe actions), the OpenShell "
                "default (sandbox created with no policy supplied, so OpenShell applies its own default) and the recommended policy. 'unenforced' probes (attack host does not exist) "
                "count as not protected: nothing stopped them.",
                "Policy mapper output is a pre-run artifact (not re-run). Its evidence excerpts are quoted "
                "from the FS-ISAC GenAI policy document; they belong to that document's authors.",
                "Attack-level outcomes mirror src/openshell_recommender/probe.py attack_outcome. attacks_exposed and "
                "by_threat.exposed count exposed plus partial (some action allowed).",
                "Threat names and coverage: generator/threats.yaml (OWASP Agentic list, hand-maintained).",
                f"Scenario run: exhaustive generation, no per-pattern cap, max_scenario_techniques 1, "
                f"71 scenarios (73 items probed with the stand-in and the token check). Qualification: 115 qualified, 71 admitted, 44 quarantined.",
                "Proof of concept shared for review.",
            ],
        },
        "use_case": build_use_case(PROJECT_DIR, manifests),
        "policy_mapper": policy,
        "threats": build_threats(name_overrides),
        "scenarios": scenarios,
        "artifacts": artifacts,
        "battery": build_battery(run),
        "provenance": None,
        "catalog": build_catalog(PROJECT_DIR),
        "recommendation": build_recommendation(run),
        "standin": build_standin(run),
        "check": build_check(run),
        "resources": build_resources(manifests, all_rows),
        "probes": probes,
        "summary": summary,
    }
    verify_numbers(snap)
    snap["threats"] = build_threats(name_overrides)
    snap["provenance"] = build_provenance(run, policy, scenarios, artifacts, snap["threats"])
    for t in snap["battery"]["threats"]:
        t["threat_name"] = snap["threats"].get(t["threat_id"], {}).get("name", t["threat_id"])
    return sanitize(snap)


def verify_numbers(snap: dict) -> None:
    """The three-column probe summary must reproduce validation.md exactly."""
    sm = snap["summary"]["refunds"]
    def col(c):
        s = sm[c]
        return [f"{s['benign_ok']}/{s['benign_ok'] + s['benign_broken']}", f"{s['harmful_denied']}/{s['harmful_total']}",
                f"{s['attacks_blocked']}/{s['attacks_total']}", str(s["attacks_exposed"]), str(s["gap"])]
    cols = {c: col(c) for c in CONDITIONS}
    md = snap["recommendation"]["before_after"]["summary_md"]
    rows = ["Good actions allowed", "Bad actions denied", "Attacks fully blocked",
            "Attacks with an action still allowed", "Text-only attacks (nothing to block)"]
    for i, name in enumerate(rows):
        want = md.get(name)
        got = [cols[c][i] for c in CONDITIONS]
        if want != got:
            raise SystemExit(f"snapshot does not reproduce validation.md row '{name}': {got} != {want}")
    # protection gained per effect, from the probe data
    by_eff = {}
    for c, key in (("no-sandbox", "before"), ("least-privilege", "after")):
        for p in snap["probes"]["refunds"][c]:
            if p["kind"] == "harmful" and p["effect"]:
                e = by_eff.setdefault(p["effect"], {"before": 0, "after": 0, "total": 0})
                e[key] += p["status"] == "denied"
                if key == "before":
                    e["total"] += 1
    for g in snap["recommendation"]["before_after"]["gained"]:
        e = by_eff.get(g["effect"])
        if not e or (e["before"], e["after"], e["total"]) != (g["before"], g["after"], g["total"]):
            raise SystemExit(f"protection gained '{g['effect']}' differs from probes: {e} vs {g}")
    print("numbers match validation.md:", cols)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run-dir", default=DEFAULT_RUN,
                    help=f"run output dir relative to the repo (default: {DEFAULT_RUN})")
    ap.add_argument("--policy-mapper", default=DEFAULT_POLICY,
                    help="policy-mapper risk-extraction.json, relative to the repo or absolute")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT,
                    help="output dir for snapshot.json and snapshot.js (default: demo/data)")
    a = ap.parse_args(argv)

    run = (PROJECT_DIR / a.run_dir).resolve()
    policy_path = Path(a.policy_mapper)
    if not policy_path.is_absolute():
        policy_path = (PROJECT_DIR / policy_path).resolve()
    for p in (run, policy_path):
        if not p.exists():
            print(f"missing input: {rel(p)}", file=sys.stderr)
            return 2

    snap = build_snapshot(run, policy_path)
    out = a.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    # keep the old "generated" timestamp when nothing else changed, so a rebuild leaves the committed files clean
    prev_path = out / "snapshot.json"
    if prev_path.exists():
        try:
            prev = json.loads(prev_path.read_text(encoding="utf-8"))
            old_ts = prev.get("meta", {}).get("generated")
            cur = json.loads(json.dumps(snap, ensure_ascii=False))  # normalise tuples etc. to JSON types
            prev["meta"]["generated"] = cur["meta"]["generated"] = None
            if old_ts and prev == cur:
                snap["meta"]["generated"] = old_ts
        except (ValueError, KeyError, TypeError):
            pass
    text = json.dumps(snap, indent=1, ensure_ascii=False) + "\n"
    (out / "snapshot.json").write_text(text, encoding="utf-8")
    (out / "snapshot.js").write_text("window.OS_SNAPSHOT = " + text.rstrip("\n") + ";\n", encoding="utf-8")
    print(f"wrote {rel(out / 'snapshot.json')} ({len(text.encode('utf-8')) // 1024} KB) and snapshot.js")
    return 0


if __name__ == "__main__":
    sys.exit(main())
