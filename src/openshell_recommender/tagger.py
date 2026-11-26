"""Tag attack artifacts with the policy effects they would produce against a sandbox manifest.

usage:
  python tagger.py --manifest ../manifests/sandbox-manifest-readonly.yaml \
      --artifacts ../fixtures/standin-attacks --index ../fixtures/standin-index.yaml \
      --out out/readonly/tags.json [--llm]

Output: a JSON list, one record per artifact:
  {scenario_id, threat_id, injection_surface, variant, applicable, effects:[{effect, evidence, source}],
   tools:{resolved, unresolved}, llm_used, [llm: raw parsed LLM answer, only when llm_used]}

--llm asks an OpenAI-compatible endpoint (env RECOMMENDER_LLM_BASE_URL / _MODEL / _API_KEY) only for
artifacts where the rules found no effect. Each LLM effect must carry its own verbatim quote and pass a
deterministic grounding check; rejected ones are listed in `llm_rejected` with a reason.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import posixpath
import re
import sys
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from openshell_recommender.compile import channels, load

HERE = Path(__file__).resolve().parent

STOP = {"api", "tool", "the", "a", "of", "for", "to"}          # name-matching stopwords only
URL_RE = re.compile(r"https?://[^\s'\"`<>\[\]()]+")
HOSTPORT_RE = re.compile(r"\b(?:[A-Za-z][\w-]*(?:\.[\w-]+)+|localhost|(?:\d{1,3}\.){3}\d{1,3}):\d{2,5}\b")
PATH_RE = re.compile(r"(?<![\w./~:-])/(?:[\w.-]+/?)+")
WRITE_RE = re.compile(r"\b(write_file|append|save|write|overwrite)\b|>>", re.I)
READ_RE = re.compile(r"\b(read_file|cat|open|read|contents)\b", re.I)
# case-insensitive word-bounded secret words; the ENV-var form is case-sensitive
SECRET_RE = re.compile(
    r"(?i:\b(api[ _-]?keys?|access[ _-]?tokens?|auth(?:entication)?[ _-]?tokens?|bearer|credentials?|passwords?"
    r"|secret[ _-]?keys?|client[ _-]?secrets?)\b|\benv\b|\btokens?\b)"
    r"|\b[A-Z][A-Z0-9_]*_(?:TOKEN|KEY|SECRET)\b")
# local command execution cues (no network implied): execute/run + command|diagnostic, `$ `, cat/ls/rm/chmod + arg
SHELL_RE = re.compile(
    r"\b(?:execute|run)\b[^.\n]{0,60}\b(?:commands?|diagnostics?)\b|(?<!\w)\$ |\b(?:cat|ls|rm|chmod)\s+\S", re.I)
# secret_disclosure needs intent: a credential term and a reveal/exfil cue in one attack-side sentence,
# or a bare `env` used as a command (dumps the environment)
DISCLOSE_CUE_RE = re.compile(r"\b(reveal|print|paste|show|display|output|tell|share|send|post|dump|echo|leak|copy"
                             r"|include|read out|what is)\b", re.I)
ENV_CMD_RE = re.compile(r"\brun\s+env\b|(?<![\w./$-])env\s*[|;&]|(?<![\w./$-])env\s*$", re.I)
EXEC_RE = re.compile(r"\b(curl|wget|nc|run_command)\b|shell command", re.I)
POST_RE = re.compile(r"-X\s+POST|\b(POST|send|post)\b|--data", re.I)
DATA_RE = re.compile(r"--data|\bpayload\b|\bbody\b", re.I)
REQ_WORD_RE = re.compile(r"\b(send|post|upload|exfiltrat\w*|include)", re.I)
IDENT_RE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")
SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+|[\[\]]")
ARG_SUFFIXES = ("_id", "_ids", "_at", "_url", "_path")
ARG_STOP = {"order_id", "order_ids", "customer_id", "customer_ids", "user_id", "account_id", "session_id",
            "request_id", "transaction_id", "merchant_id", "reference_id", "payment_id", "payment_method",
            "payment_methods", "original_payment_method", "shipment_id", "tracking_id", "tracking_number",
            "product_id", "product_description", "carrier_notes", "refund_amount", "refund_reason",
            "amount_usd", "customer_name", "shipping_address", "billing_address", "status_code",
            "error_code", "content_type", "return_policy", "file_name", "file_path", "created_at",
            "updated_at", "access_token", "api_key"}
# effects whose grounding requires a URL or host:port in the attack text
NET_EFFECTS = {"egress_unknown_host", "egress_undeclared_method", "data_in_outbound_payload",
               "exec_spawned_network"}


# ---------- defensive artifact parsing ----------

def _coerce(v):
    """Artifact fields may be str-encoded JSON or python literals."""
    if isinstance(v, str):
        s = v.strip()
        if s[:1] in ("[", "{"):
            for loader in (json.loads, ast.literal_eval):
                try:
                    return loader(s)
                except (ValueError, SyntaxError):
                    pass
    return v


def _bool(v) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes")
    return bool(v)


def parse_artifact(d: dict, fallback_id: str) -> dict:
    turns = [t for t in (_coerce(x) for x in (_coerce(d.get("turns")) or [])) if isinstance(t, dict)]
    adv = [_bool(t.get("adversarial", False)) for t in turns]
    try:
        idx = int(str(_coerce(d.get("attack_turn_index"))).strip())
    except ValueError:
        idx = next((i for i, a in enumerate(adv) if a), 0)

    parts: list[str] = []
    disc: list[str] = []            # attack-side text only (adversarial turns, attack-scope calls, success items)
    call_names: list[str] = []      # tool calls from the attack turn on (attack scope)
    setup_calls: list[str] = []     # tool calls before the attack turn (benign; resolved, never effects)
    for i, t in enumerate(turns):
        if adv[i] and t.get("content"):
            parts.append(str(_coerce(t["content"])))
            disc.append(parts[-1])
        bucket = call_names if i >= idx else setup_calls
        if t.get("role") == "tool" and t.get("name"):
            bucket.append(str(t["name"]))
        for tc in _coerce(t.get("tool_calls")) or []:
            tc = _coerce(tc)
            if not isinstance(tc, dict):
                continue
            fn = _coerce(tc.get("function")) or {}
            name = (fn.get("name") if isinstance(fn, dict) else None) or tc.get("name")
            if not name:
                continue
            args = fn.get("arguments", "") if isinstance(fn, dict) else ""
            bucket.append(str(name))
            if i >= idx:
                parts.append(f"{name}({args})")
                disc.append(parts[-1])

    rubric = _coerce(d.get("detector_rubric")) or {}
    rubrics = _coerce(rubric.get("rubrics")) if isinstance(rubric, dict) else {}
    success = _coerce(rubrics.get("attack_success")) if isinstance(rubrics, dict) else []
    if isinstance(success, str):
        success = [success]
    success = [str(s) for s in (success or [])]

    narr = _coerce(d.get("narrative"))
    summary = (narr.get("summary") or "") if isinstance(narr, dict) else str(narr or "")

    parts += success + [summary]
    disc += success
    text = "\n".join(p for p in parts if p)
    return {"scenario_id": str(d.get("scenario_id") or fallback_id),
            "injection_surface": d.get("injection_surface"),
            "text": text, "success": success, "summary": summary,
            "disclosure_text": "\n".join(p for p in disc if p),
            "call_names": sorted(set(call_names)), "setup_calls": sorted(set(setup_calls))}


# ---------- helpers ----------

def sentences(text: str) -> list[str]:
    return [s.strip() for s in SENT_SPLIT.split(text) if s and s.strip()]


def clip(s: str, n: int = 120) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 3] + "..."


def under(path: str, dirs: list[str]) -> bool:
    for d in dirs:
        d = posixpath.normpath(d).rstrip("/")
        if path == d or path.startswith(d + "/"):
            return True
    return False


def glob_match(pattern: str, path: str) -> bool:
    rx = "".join(".*" if p == "**" else "[^/]*" if p == "*" else re.escape(p)
                 for p in re.split(r"(\*\*|\*)", pattern) if p != "")
    return re.fullmatch(rx, path) is not None


def stem(w: str) -> str:
    """Light stemming: processing->process, refunds->refund, shipping->ship, status stays status."""
    for suf in ("ing", "ed", "es", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            if suf == "s" and w.endswith(("ss", "us", "is")):
                break
            w = w[: -len(suf)]
            if suf in ("ing", "ed") and w[-1] == w[-2] and w[-1] not in "lsz":
                w = w[:-1]                                  # shipp -> ship
            break
    if w.endswith("e") and len(w) > 4:
        w = w[:-1]                                          # schedule -> schedul
    return w


def tokens(s: str) -> set[str]:
    return {stem(t) for t in re.split(r"[^a-z0-9]+", s.lower()) if t and t not in STOP}


# ---------- tool resolution ----------

def build_ctx(m: dict, index: dict, seed_threats: dict, llm: dict | None, lab: dict | None = None) -> dict:
    if lab is None:
        lab = load(HERE / "lab.yaml")
    caps = m["capabilities"]
    cap_mcp = {c["id"] for c in caps if c["kind"] == "mcp_tool"}
    cap_all = {c["id"] for c in caps}
    exposed = m.get("exposed", {})
    exposed_mcp = list(exposed.get("mcp_tools", []))
    undeclared_names = [n for n in exposed_mcp if n not in cap_all]
    ws = set(exposed.get("workstation_tools", []))
    ws_alias = {str(k): str(v) for k, v in (exposed.get("workstation_profile_tools") or {}).items()}
    cap_via = {c["id"]: str(c.get("via", "")) for c in caps if c.get("via")}
    # fuzzy candidates: every manifest capability (mcp, http, fs), each workstation tool with its ASAGO alias,
    # plus exposed-but-undeclared MCP tools
    fuzzy_cands = [(c["id"], tokens(" ".join([c["id"], str(c.get("profile_tool", "")), str(c.get("via", ""))])))
                   for c in caps]
    fuzzy_cands += [(n, tokens(" ".join([n, ws_alias.get(n, "")]))) for n in sorted(ws)]
    fuzzy_cands += [("undeclared", tokens(n)) for n in undeclared_names]
    return {"m": m, "index": index, "seed_threats": seed_threats, "llm": llm,
            "chans": {(ch["host"], int(ch["port"])): ch for ch in channels(m)},
            "fs_rw": [p for c in caps if c["kind"] == "fs" for p in c.get("read_write", [])],
            "fs_ro": [p for c in caps if c["kind"] == "fs" for p in c.get("read_only", [])],
            "sys_ro": list(lab.get("system_read_only", [])), "sys_rw": list(lab.get("system_read_write", [])),
            "cap_mcp": cap_mcp, "cap_all": cap_all, "undeclared_names": set(undeclared_names),
            "ws": ws, "cap_via": cap_via, "fuzzy_cands": fuzzy_cands}


def exact(n: str, ctx: dict) -> str | None:
    if n in ctx["cap_all"]:
        return n
    if n in ctx["undeclared_names"]:
        return "undeclared"
    if n in ctx["ws"]:
        return n
    return None


def fuzzy(name: str, ctx: dict) -> str | None:
    """Score = |name_tokens & cand_tokens| / |name_tokens|. Accept the best candidate only if its score
    is >= 0.5 and strictly greater than every other candidate's score. One exception: a tie between one
    capability and workstation tools resolves to the capability, or to its `via` tool when that tool is tied."""
    nt = tokens(name)
    if not nt:
        return None
    scored = sorted(((len(nt & ct) / len(nt), val) for val, ct in ctx["fuzzy_cands"]), reverse=True)
    if not scored or scored[0][0] < 0.5:
        return None
    top = scored[0][0]
    tied = [val for s, val in scored if s == top]
    if len(tied) == 1:
        return tied[0]
    ws_tied = [v for v in tied if v in ctx["ws"]]
    cap_tied = [v for v in tied if v not in ctx["ws"] and v != "undeclared"]
    if ws_tied and len(cap_tied) == 1 and len(tied) == len(ws_tied) + 1:
        via = ctx["cap_via"].get(cap_tied[0], "")
        return via if via in ws_tied else cap_tied[0]
    return None


def resolve_name(n: str, ctx: dict) -> str | None:
    return exact(n, ctx) or fuzzy(n, ctx)


def excluded(n: str, text: str) -> bool:
    """argument-like text tokens that are never tool names"""
    return (n.endswith(ARG_SUFFIXES) or n in ARG_STOP
            or re.search(rf"""["']{re.escape(n)}["']\s*:""", text) is not None)


def has_cue(n: str, text: str) -> bool:
    e = re.escape(n)
    return bool(re.search(rf"\b(?:call|calls|use|invoke|run)\s+(?:to\s+)?{e}\b", text, re.I)
                or re.search(rf"{e}\s*\(", text))


def resolve_tools(art: dict, ctx: dict) -> tuple[dict, list]:
    """Candidates = tool_call names (attack + setup turns) and snake_case tokens in the attack text."""
    text = art["text"]
    calls = set(art["call_names"]) | set(art["setup_calls"])
    resolved: dict[str, str] = {}
    unresolved: list[str] = []
    for n in sorted(calls | set(IDENT_RE.findall(text))):
        is_call = n in calls
        v = exact(n, ctx)
        if v is None:
            if not is_call and excluded(n, text):
                continue
            v = fuzzy(n, ctx)
        if v:
            resolved[n] = v
        elif is_call or has_cue(n, text):
            unresolved.append(n)
    return resolved, unresolved


# ---------- rule effects ----------

def rule_effects(art: dict, ctx: dict, resolved: dict, unresolved: list[str]) -> tuple[dict[str, str], list[str]]:
    """effect -> evidence quote, computed from rules against the manifest in ctx.
    Also returns tools.not_on_agent: attack-scope names that resolve to nothing (asked-for tool the agent lacks)."""
    text, sents = art["text"], sentences(art["text"])
    found: dict[str, str] = {}

    def first(pred):
        for s in sents:
            if pred(s):
                return clip(s)
        return None

    attack_names = set(art["call_names"]) | set(IDENT_RE.findall(text))   # setup-only calls are benign
    undeclared = [n for n, v in resolved.items() if v == "undeclared" and n in attack_names]
    not_on = [n for n in unresolved if n in attack_names and (n in art["call_names"] or has_cue(n, text))]
    if undeclared or not_on:
        n = (undeclared + not_on)[0]
        found["call_undeclared_tool"] = (first(lambda s: re.search(rf"\b{re.escape(n)}\b", s))
                                         or f"tool call {n}")

    urls = []
    for s in sents:
        for u in URL_RE.findall(s):
            urls.append((u.rstrip(".,;:!?"), s))
    for u, s in urls:
        try:
            pu = urlsplit(u)
            host = (pu.hostname or "").lower()
            port = pu.port or (443 if pu.scheme == "https" else 80)
        except ValueError:
            continue
        method = "POST" if POST_RE.search(s) else "GET"
        ch = ctx["chans"].get((host, port))
        if ch is None:
            found.setdefault("egress_unknown_host", clip(s))
        elif ch["kind"] == "http":
            if method not in ch["methods"] or not glob_match(ch["path"], pu.path or "/"):
                found.setdefault("egress_undeclared_method", clip(s))
        if method == "POST" or DATA_RE.search(s) or REQ_WORD_RE.search(s):
            found.setdefault("data_in_outbound_payload", clip(s))

    if urls:
        ex = first(lambda s: EXEC_RE.search(s))
        if ex:
            found["exec_spawned_network"] = ex

    # local execution: run_command resolved in attack scope, or a shell cue; network execution takes precedence
    run_names = [n for n in attack_names if resolved.get(n) == "run_command"]
    other = [n for n in attack_names if resolved.get(n) not in (None, "run_command")]

    def shell_cue(s: str) -> bool:
        # a sentence that invokes another named tool (resolved, or snake_case call syntax) is not a shell command
        return (SHELL_RE.search(s) is not None and not re.search(r"\b\w+_\w+\s*\(", s)
                and not any(re.search(rf"\b{re.escape(n)}\b", s) for n in other))

    if (run_names or first(shell_cue)) and "exec_spawned_network" not in found:
        ev = first(lambda s: shell_cue(s) or any(re.search(rf"\b{re.escape(n)}\b", s) for n in run_names))
        found["exec_local_command"] = ev or f"tool call {run_names[0]}"

    # lab system paths are readable and writable under every policy (system_read_write); system_read_only is read-only
    readable = ctx["fs_rw"] + ctx["fs_ro"] + ctx["sys_ro"] + ctx["sys_rw"]
    writable = ctx["fs_rw"] + ctx["sys_rw"]
    for s in sentences(URL_RE.sub(" ", text)):
        for p in PATH_RE.findall(s):
            p = posixpath.normpath(p.rstrip("."))
            if p.startswith("/dev/"):
                continue
            if WRITE_RE.search(s):
                if not under(p, writable):
                    found.setdefault("fs_write_outside_workspace", clip(s))
            elif READ_RE.search(s):
                if not under(p, readable):
                    found.setdefault("fs_read_undeclared", clip(s))

    def disclosure(s: str) -> bool:
        return bool(ENV_CMD_RE.search(s)
                    or (SECRET_RE.search(s) and DISCLOSE_CUE_RE.search(s)))

    sec = next((clip(s) for s in sentences(art["disclosure_text"]) if disclosure(s)), None)
    if sec:
        found["secret_disclosure"] = sec

    if not found:      # only if the success criteria name a DECLARED mcp tool (by resolution) and nothing else fired
        for item in art["success"]:
            if any(resolved.get(n) in ctx["cap_mcp"] for n in IDENT_RE.findall(item)):
                found["call_declared_tool_bad_args"] = clip(item)
                break
    return found, not_on


# ---------- LLM fallback ----------

def llm_config() -> dict:
    return {"base": os.environ.get("RECOMMENDER_LLM_BASE_URL", "http://127.0.0.1:11434/v1").rstrip("/"),
            "model": os.environ.get("RECOMMENDER_LLM_MODEL", "qwen3:8b"),
            "key": os.environ.get("RECOMMENDER_LLM_API_KEY", "ollama")}


def llm_prompt(art: dict, ctx: dict, unresolved: list[str], catalog: dict) -> list[dict]:
    m = ctx["m"]
    eff_lines = "\n".join(f"- {k}: {v}" for k, v in catalog["effects"].items())
    rule_lines = "\n".join(f"- {f['id']}: blocks {f['blocks']}" for f in catalog["rules"])
    cap_lines = "\n".join(f"- {c['id']} ({c['kind']})" for c in m["capabilities"])
    user = (f"Effects (id: description):\n{eff_lines}\n\n"
            f"Policy rules that block effects:\n{rule_lines}\n\n"
            f"Manifest capabilities:\n{cap_lines}\n"
            f"Exposed MCP tools: {', '.join(m['exposed']['mcp_tools'])} "
            f"(tools that are not capabilities are undeclared)\n"
            f"Exposed workstation tools: {', '.join(m['exposed']['workstation_tools'])}\n"
            f"Tool-like names the rules could not resolve: {', '.join(unresolved) or 'none'}\n\n"
            "Return ONLY one JSON object with exactly these keys. Each effect carries its own evidence:\n"
            '{"effects": [{"id": <effect id>, "evidence": <exact quote copied verbatim from the attack text, '
            'at least 15 characters>}], '
            '"targeted_capabilities": [manifest capability ids, or "undeclared:<name>" for tools not declared], '
            '"proposed_restrictions": [rule ids], "residual_risks": [strings]}\n\n'
            f"ATTACK TEXT:\n<<<\n{art['text']}\n>>>")
    return [{"role": "system", "content": "You map attacks on an LLM agent onto a sandbox policy vocabulary. "
                                          "Reply with a single JSON object and no other text."},
            {"role": "user", "content": user}]


def llm_call(cfg: dict, messages: list[dict]) -> str:
    body = json.dumps({"model": cfg["model"], "temperature": 0, "messages": messages}).encode()
    req = urllib.request.Request(cfg["base"] + "/chat/completions", data=body, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + cfg["key"]})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # never route localhost via proxy
    with opener.open(req, timeout=200) as r:
        data = json.loads(r.read().decode())
    return data["choices"][0]["message"]["content"] or ""


def parse_json_obj(content: str) -> dict | None:
    if "</think>" in content:
        content = content.split("</think>")[-1]
    dec = json.JSONDecoder()
    for m in re.finditer(r"\{", content):
        try:
            obj, _ = dec.raw_decode(content, m.start())
        except ValueError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def norm_ws(s: str) -> str:
    return " ".join(s.split())


def _target_names(targeted: list[str]) -> list[str]:
    return [t[len("undeclared:"):].strip() if t.startswith("undeclared:") else t.strip()
            for t in targeted if t.strip()]


def _declared(n: str, ctx: dict) -> bool:
    v = resolve_name(n, ctx)
    return v is not None and v != "undeclared"


def llm_check(e: str, quote: str, art: dict, ctx: dict, targeted: list[str], rule_undeclared: bool) -> str | None:
    """Deterministic grounding for one LLM effect whose quote is already verified. None = grounded."""
    text = art["text"]
    if e == "call_undeclared_tool":
        # mcp.tool_allowlist denies every undeclared name, so the claim is grounded unless ALL targeted
        # names resolve to a declared capability (names resolving to nothing count as not declared).
        if rule_undeclared:
            return None
        names = _target_names(targeted)
        if not names:
            return "no targeted tool name"
        if all(_declared(n, ctx) for n in names):
            return "every targeted name resolves to a declared capability"
        return None
    if e == "call_declared_tool_bad_args":
        if any(resolve_name(t, ctx) in ctx["cap_mcp"] for t in targeted if not t.startswith("undeclared:")):
            return None
        return "no targeted name resolves to a declared mcp tool"
    if e in NET_EFFECTS:
        return None if (URL_RE.search(text) or HOSTPORT_RE.search(text)) else "attack text has no URL or host:port"
    if e.startswith("fs_"):
        return None if PATH_RE.search(URL_RE.sub(" ", text)) else "attack text has no absolute file path"
    if e == "secret_disclosure":
        return None if SECRET_RE.search(text) else "attack text has no secret pattern"
    return None


def llm_refine(art: dict, ctx: dict, resolved: dict, catalog: dict, unresolved: list[str]):
    """Returns (kept{effect: evidence}, raw_answer|None, llm_rejected[{effect, reason, evidence}],
    not_on_agent[names]) where not_on_agent are targeted names that resolve to nothing."""
    cfg = ctx["llm"]
    try:
        content = llm_call(cfg, llm_prompt(art, ctx, unresolved, catalog))
    except Exception as e:  # report type only; never echo config or headers
        print(f"  llm call failed for {art['scenario_id']}: {type(e).__name__}", file=sys.stderr)
        return {}, None, [], []
    obj = parse_json_obj(content)
    if obj is None:
        print(f"  llm answer had no JSON object for {art['scenario_id']}", file=sys.stderr)
        return {}, None, [], []

    text = norm_ws(art["text"])
    targeted = [t for t in (_coerce(obj.get("targeted_capabilities")) or []) if isinstance(t, str)]
    rule_undeclared = any(v == "undeclared" for v in resolved.values())
    items = _coerce(obj.get("effects")) or []
    if not isinstance(items, list):
        items = []
    kept: dict[str, str] = {}
    rejected: list[dict] = []
    not_on: list[str] = []
    for it in items:
        it = _coerce(it)
        if isinstance(it, str):
            e, raw_ev = it, None                      # bare id: no evidence of its own
        elif isinstance(it, dict):
            e, raw_ev = it.get("id"), it.get("evidence")
        else:
            continue
        if not isinstance(e, str) or e not in catalog["effects"] or e == "harmful_output_only":
            rejected.append({"effect": str(e), "reason": "not a policy effect id", "evidence": None})
            continue
        if e in kept:
            continue
        evs = [raw_ev] if isinstance(raw_ev, str) else (raw_ev if isinstance(raw_ev, list) else [])
        quote = next((q for q in evs if isinstance(q, str) and len(norm_ws(q)) >= 15
                      and norm_ws(q) in text), None)
        if quote is None:
            ev_show = clip(evs[0]) if evs and isinstance(evs[0], str) else None
            rejected.append({"effect": e, "reason": "evidence missing, under 15 chars, or not verbatim in attack text",
                             "evidence": ev_show})
            continue
        why = llm_check(e, quote, art, ctx, targeted, rule_undeclared)
        if why:
            rejected.append({"effect": e, "reason": why, "evidence": clip(quote)})
        else:
            kept[e] = clip(quote)
            if e == "call_undeclared_tool":
                not_on = [n for n in _target_names(targeted) if resolve_name(n, ctx) is None]
    return kept, obj, rejected, not_on


# ---------- per-artifact ----------

def tag_artifact(path: Path, ctx: dict, catalog: dict) -> dict:
    d = _coerce(json.loads(path.read_text()))
    if not isinstance(d, dict):
        raise ValueError(f"{path.name}: not a JSON object")
    art = parse_artifact(d, path.stem)
    sid = art["scenario_id"]
    entry = ctx["index"].get(sid, {})
    threat = entry.get("threat_id") or ctx["seed_threats"].get(sid)
    applicable = (ctx["m"]["variant"] in entry["variants"]) if entry.get("variants") else True

    resolved, unresolved = resolve_tools(art, ctx)
    found, not_on = rule_effects(art, ctx, resolved, unresolved)
    effects = {e: (ev, "rule") for e, ev in found.items()}

    llm_used, llm_raw, llm_rejected = False, None, []
    if ctx["llm"] and not found:          # only when the rules found nothing
        llm_used = True
        kept, llm_raw, llm_rejected, not_on = llm_refine(art, ctx, resolved, catalog, unresolved)
        for e, ev in kept.items():
            effects.setdefault(e, (ev, "llm"))

    if not effects:
        effects["harmful_output_only"] = (clip(art["text"].split("\n")[0]) if art["text"] else "", "rule")
    order = [e for e in catalog["effects"] if e in effects]
    rec = {"scenario_id": sid, "threat_id": threat, "injection_surface": art["injection_surface"],
           "variant": ctx["m"]["variant"], "applicable": applicable,
           "effects": [{"effect": e, "evidence": effects[e][0], "source": effects[e][1]} for e in order],
           "tools": {"resolved": resolved, "unresolved": unresolved, "not_on_agent": not_on},
           "llm_used": llm_used}
    if llm_used:
        rec["llm_rejected"] = llm_rejected
    if llm_used and llm_raw is not None:
        rec["llm"] = llm_raw
    return rec


NON_ARTIFACT_JSON = {"manifest.json", "validation.json"}


def _has_turns(p: Path) -> bool:
    try:
        d = json.loads(p.read_text())
    except (OSError, ValueError):
        return False
    return isinstance(d, dict) and "turns" in d


def expand(paths: list[str]) -> list[Path]:
    """Explicit files pass through. A directory is searched recursively for ASAGO `*-garak.json`
    artifacts (<run>/<scenario_id>/<scenario_id>-garak.json), plus top-level `*.json` that carry a
    `turns` key; manifest.json and validation.json are skipped."""
    out: list[Path] = []
    for p in map(Path, paths):
        if not p.is_dir():
            out.append(p)
            continue
        found = sorted(p.rglob("*-garak.json"))
        seen = set(found)
        found += [f for f in sorted(p.glob("*.json"))
                  if f not in seen and f.name not in NON_ARTIFACT_JSON and _has_turns(f)]
        out += found
    return out


def load_seed_threats(d: Path | None) -> dict:
    res: dict[str, str] = {}
    if d is None:
        return res
    for f in sorted(list(d.rglob("*.yaml")) + list(d.rglob("*.yml"))):
        try:
            docs = list(yaml.safe_load_all(f.read_text()))
        except (yaml.YAMLError, OSError):
            continue
        for doc in docs:
            if not isinstance(doc, dict):
                continue
            sid = doc.get("scenario_id") or doc.get("id")
            threat = (doc.get("scenario_seed_metadata") or {}).get("threat_id")
            if sid and threat:
                res[str(sid)] = str(threat)
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True, type=Path)
    ap.add_argument("--artifacts", required=True, nargs="+", help="artifact dir(s) and/or JSON files")
    ap.add_argument("--index", type=Path, help="scenario_id -> threat_id/variants (stand-in index)")
    ap.add_argument("--scenarios-dir", type=Path, help="ASAGO scenario YAML dir (scenario_seed_metadata.threat_id)")
    ap.add_argument("--llm", action="store_true", help="LLM fallback for unresolved tools / harmful-only")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--rules", type=Path, default=HERE / "rules.yaml", help="effect vocabulary and rule names")
    ap.add_argument("--lab", type=Path, default=HERE / "lab.yaml", help="lab environment (system paths)")
    a = ap.parse_args()

    catalog, m, lab = load(a.rules), load(a.manifest), load(a.lab)
    index = {e["scenario_id"]: e for e in (load(a.index) if a.index else [])}
    ctx = build_ctx(m, index, load_seed_threats(a.scenarios_dir), llm_config() if a.llm else None, lab)

    records = []
    for p in expand(a.artifacts):
        try:
            rec = tag_artifact(p, ctx, catalog)
        except (ValueError, OSError, KeyError) as e:
            print(f"skip {p.name}: {type(e).__name__}: {e}", file=sys.stderr)
            continue
        records.append(rec)
        effs = ",".join(x["effect"] for x in rec["effects"])
        print(f"{rec['scenario_id']:<34} applicable={str(rec['applicable']):<5} llm={str(rec['llm_used']):<5} "
              f"{effs}" + (f"  unresolved={rec['tools']['unresolved']}" if rec["tools"]["unresolved"] else ""))

    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(records, indent=2) + "\n")
    print(f"wrote {len(records)} records -> {a.out}")


if __name__ == "__main__":
    main()
