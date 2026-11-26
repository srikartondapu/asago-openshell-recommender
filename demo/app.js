/* ASAGO -> OpenShell sandbox-policy recommender demo (static replay). Vanilla JS, no build step, no network.
   Data comes from data/snapshot.js (window.OS_SNAPSHOT) so the page works from file://.
   All data is untrusted text: everything goes through esc() before innerHTML (or textContent in the SVG).
   Deep links (URL hash): #stage=4&tab=policy|validate|probe|gaps&cond=no-sandbox|openshell-default|least-privilege&attack=12&play=attack|all|benign|story&instant=1&speed=16&theme=light */
(function () {
  "use strict";

  const SNAP = (typeof window !== "undefined" && window.OS_SNAPSHOT) || {};
  const NOSB = "no-sandbox";
  const DEF = "openshell-default";
  const CONDS = [NOSB, DEF, "least-privilege"];
  const CLABEL = { [NOSB]: "No sandbox (before)", [DEF]: "OpenShell default", "least-privilege": "Recommended (after)" };
  const CSHORT = { [NOSB]: "NS", [DEF]: "OD", "least-privilege": "RC" };
  const UNENF = "host doesn't exist — nothing stopped it";
  const normC = (c) => (c === "recommended" ? "least-privilege" : c === "baseline" ? DEF : c); // old deep links
  /* What OpenShell can inspect, and the rule in the compiled policy that uses it (effect -> rule). */
  const RULES = [
    { id: "mcp", name: "MCP tool allowlist", sees: "Tool name in tools/call (not the arguments)", effects: ["call_undeclared_tool"] },
    { id: "hosts", name: "Declared hosts only", sees: "Destination host:port", effects: ["egress_unknown_host", "data_in_outbound_payload"] },
    { id: "bin", name: "Binary-scoped egress", sees: "Which program opened the connection", effects: ["exec_spawned_network"] },
    { id: "http", name: "HTTP methods and paths", sees: "HTTP method + path", effects: ["egress_undeclared_method"] },
    { id: "fsw", name: "Workspace-only writes", sees: "File path being written", effects: ["fs_write_outside_workspace"] },
    { id: "fsr", name: "Declared read paths", sees: "File path being read", effects: ["fs_read_undeclared"] },
    { id: "root", name: "Non-root process", sees: "User and group the process runs as", effects: [] },
    { id: "tok", name: "Provider-injected token", sees: "Credential injection point (the sandbox only sees a placeholder)", effects: ["secret_disclosure"] },
  ];
  const ruleFor = (eff) => RULES.find((r) => r.effects.includes(eff));

  /* ---------- helpers ---------- */
  const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  const esc = (v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ESC[c]);
  const chip = (t, cls = "") => `<span class="chip ${cls}">${esc(t)}</span>`;
  const chips = (arr, cls) => (arr || []).map((t) => chip(t, cls)).join("") || '<span class="muted">none</span>';
  const clip = (s, n) => { s = String(s == null ? "" : s); return s.length > n ? s.slice(0, n - 1) + "…" : s; };
  const note = (title, text) => `<div class="note" role="note"><b>${esc(title)}</b> ${esc(text)}</div>`;
  const kv = (k, v) => `<div class="kv"><span class="k">${esc(k)}</span><span class="v">${v}</span></div>`;
  const metric = (value, label, cls) => `<div class="metric ${cls === true ? "mint" : cls || ""}"><strong>${esc(value)}</strong><span>${esc(label)}</span></div>`;
  const metrics = (arr) => `<div class="metrics">${arr.join("")}</div>`;
  const det = (key, title, body) =>
    `<details class="deep" data-k="${esc(key)}" ${state.open.has(key) ? "open" : ""}><summary>${esc(title)}</summary><div class="deep-body">${body}</div></details>`;
  const bars = (rows, max) =>
    `<div class="bars">${rows.map((r) => `<div class="barrow"><span class="bl" title="${esc(r.label)}">${esc(r.label)}</span><span class="bt"><i class="${r.cls || ""}" style="width:${max ? Math.max((r.v != null ? r.v : r.value) ? 2 : 0, Math.round((100 * (r.v != null ? r.v : r.value)) / max)) : 0}%"></i></span><span class="bv">${esc(r.text != null ? r.text : r.v != null ? r.v : r.value)}</span></div>`).join("")}</div>`;
  const pill = (status) => {
    const m = { unenforced: ["bad", "exposed"], allowed: ["bad", "allowed"], denied: ["ok", "denied"], gap: ["na", "content only"], exposed: ["bad", "exposed"], blocked: ["ok", "blocked"], none: ["na", "n/a"], broken: ["bad", "broken"] };
    const [cls, txt] = m[status] || ["na", status];
    return `<span class="pill ${cls}">${esc(txt)}</span>`;
  };
  const cov = (lvl) => `<span class="cov ${esc(lvl)}">${esc(lvl)}</span>`;
  const $ = (id) => document.getElementById(id);
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const pct = (x) => (x == null ? "n/a" : Math.round(x * 100) + "%");

  /* ---------- data accessors (no DOM) ---------- */
  const memo = {};
  const Data = {
    attacks: () => (SNAP.artifacts && SNAP.artifacts.items) || [],
    /* artifacts plus the hand-written stand-in (file read, issue #12), which is not an ASAGO run 4 artifact */
    sbAttacks() {
      if (!memo.sba) {
        const si = SNAP.standin, ck = SNAP.check;
        memo.sba = Data.attacks().concat(si ? [{ scenario_id: si.scenario_id, threat_id: si.threat_id, standin: true, attack_pattern_name: si.title, turns: [] }] : [],
          ck ? [{ scenario_id: ck.scenario_id, threat_id: ck.threat_id, check: true, attack_pattern_name: ck.title, turns: [] }] : []);
      }
      return memo.sba;
    },
    scenario(sid) { return memo.scen ? memo.scen[sid] : (memo.scen = Object.fromEntries(((SNAP.scenarios && SNAP.scenarios.items) || []).map((s) => [s.scenario_id, s])))[sid]; },
    nodes: () => (SNAP.resources && SNAP.resources.nodes) || [],
    node(id) {
      if (id === "internet") {
        const hs = Data.nodes().filter((n) => n.group === "internet");
        return { id: "internet", group: "internet", label: `Internet — unknown hosts (${hs.length})`, detail: hs.map((h) => h.label.replace(/^net:/, "")).join(", "), hosts: hs, declared: { refunds: hs.some((h) => h.declared.refunds) } };
      }
      return Data.nodes().find((n) => n.id === id);
    },
    probes: (v, c) => ((SNAP.probes || {})[v] || {})[c] || [],
    harmful: (v, c) => Data.probes(v, c).filter((p) => p.kind === "harmful"),
    benign: (v, c) => Data.probes(v, c).filter((p) => p.kind === "benign"),
    byScen(v, c) {
      const k = `bs:${v}:${c}`;
      if (!memo[k]) { const m = {}; Data.harmful(v, c).forEach((p) => (m[p.scenario_id] = m[p.scenario_id] || []).push(p)); memo[k] = m; }
      return memo[k];
    },
    outcome(v, c, sid) {
      const ps = Data.byScen(v, c)[sid] || [];
      if (!ps.length) return "none";
      if (ps.some((p) => p.status === "allowed" || p.status === "unenforced")) return "exposed"; // unenforced: nothing stopped it
      if (ps.some((p) => p.status === "denied")) return "blocked";
      return "gap";
    },
    byNode(v, c) {
      const k = `bn:${v}:${c}`;
      if (!memo[k]) { const m = {}; Data.probes(v, c).forEach((p) => { const r = p.action && p.action.resource; if (r) { (m[r] = m[r] || []).push(p); if (r.startsWith("net:")) (m.internet = m.internet || []).push(p); } }); memo[k] = m; }
      return memo[k];
    },
    nodeState(v, c, id) {
      const n = Data.node(id), ps = Data.byNode(v, c)[id] || [];
      const harmful = ps.filter((p) => p.kind === "harmful");
      return {
        declared: !!(n && n.declared && n.declared[v]),
        tested: ps.length > 0,
        locked: ps.length > 0 && ps.every((p) => p.status === "denied"),
        exposed: harmful.some((p) => p.status === "allowed" || p.status === "unenforced"),
      };
    },
    sum: (v, c) => ((SNAP.summary || {})[v] || {})[c] || {},
    effect(id) { return ((SNAP.catalog || {}).effects || []).find((e) => e.id === id); },
    threatIds: () => Object.keys(SNAP.threats || {}),
    tname: (t) => ((SNAP.threats || {})[t] || {}).name || t,
    byThreat: (t) => ((SNAP.scenarios || {}).by_threat || {})[t] || 0,
    vlabel: (v) => (((SNAP.use_case || {}).variants || {})[v] || {}).label || v,
  };
  const tnum = (t) => +String(t).replace(/\D/g, "") || 0;

  /* ---------- state ---------- */
  const state = {
    done: 0, running: -1, sub: 0, sel: 0, runAll: false, timer: null,
    tab: [0, 0, 0, 0], variant: "refunds", cond: "no-sandbox", pf: "all", gf: "all",
    attack: null, open: new Set(), sfilter: "", sshown: 20, scn: null, risk: null,
    sbThreat: "", speed: 4, instant: false, bench: null,
    live: false, counted: new Set(), cnt: null, busy: false, log: [],
  };


  /* ---------- stages 1-3: same renderers as the guardrails demo, fed from this run's snapshot ---------- */
  const UC = {
    policy: SNAP.policy_mapper || {}, battery: SNAP.battery || { threats: [] }, scenarios: (SNAP.scenarios || {}).items || [],
    attacks: (SNAP.artifacts || {}).items || [], provenance: SNAP.provenance || {},
  };
  UC.attacks.forEach((a, n, all) => {
    a.short_id = `${a.threat_id}-${String(all.filter((x, m) => m <= n && x.threat_id === a.threat_id).length).padStart(2, "0")}`;
    a.turns.forEach((t) => { if (t.tool_calls && t.tool_calls.length && typeof t.tool_calls[0] === "object") t.tool_calls = t.tool_calls.map((c) => c.name); });
  });
  const pol = (c) => c.uc.policy || {};
  const polStats = (c) => pol(c).stats || {};
  const num = (v) => (v == null ? "n/a" : Number(v).toLocaleString("en-US"));
  const PM = () => SNAP.policy_mapper || {};
  const curUC = () => UC;
  Object.assign(Data, {
    risks: (uc) => ((uc.policy && uc.policy.risks) || []).slice(0, 15), // the reference demo also shows the first 15
    threats: (uc) => (uc.battery && uc.battery.threats) || [],
    patternCount: (uc) => Data.threats(uc).reduce((n, t) => n + (t.attack_patterns || []).length, 0),
    scenarios: (uc) => uc.scenarios || [],
    prov: (uc, k) => ((uc.provenance && uc.provenance.stages) || {})[k] || {},
    threatName(uc, id) { const t = Data.threats(uc).find((x) => x.threat_id === id); return t ? t.threat_name : Data.tname(id); },
    perThreat(uc) {
      const ids = [];
      const add = (id) => { if (id && !ids.includes(id)) ids.push(id); };
      Data.threats(uc).forEach((t) => add(t.threat_id));
      Data.scenarios(uc).forEach((s) => add(s.threat_id));
      ids.sort((a, b) => tnum(a) - tnum(b));
      return ids.map((id) => {
        const th = Data.threats(uc).find((x) => x.threat_id === id);
        return { id, name: Data.threatName(uc, id), patterns: th ? (th.attack_patterns || []).length : 0,
          generated: Data.scenarios(uc).filter((s) => s.threat_id === id).length, gated: Data.attacks(uc).filter((a) => a.threat_id === id).length };
      });
    },
  });
  const shortId = (a) => a.short_id || String(a.scenario_id).slice(-8);
  const curAttack = () => Data.attacks().find((a) => a.scenario_id === state.attack) || Data.attacks()[0] || null;
  const curSbAttack = () => Data.sbAttacks().find((a) => a.scenario_id === state.attack) || Data.sbAttacks()[0] || null;
  /* badge state of an attack under a policy condition (stage 4 result by default) */
  const stateOf = (a, cond) => { const o = Data.outcome(state.variant, cond || "least-privilege", a.scenario_id); return o === "blocked" ? "blocked" : o === "exposed" ? "open" : "untested"; };
  const stateLabel = (a, cond) => ({ blocked: "Blocked", open: "Exposed", untested: "Content only" })[stateOf(a, cond)];
  function attackPicker(c, badges, cond) {
    const list = Data.attacks(c.uc);
    return `<div class="picker" role="group" aria-label="Attack"><span class="picker-label">Attack</span>${list.map((a) => {
      const st = badges ? stateOf(a, cond) : "plain";
      const label = badges ? ` (${stateLabel(a, cond)})` : "";
      const on = a.scenario_id === (curAttack() || {}).scenario_id;
      return `<button type="button" class="pick ${on ? "on" : ""} ${st}" data-act="attack" data-sid="${esc(a.scenario_id)}" aria-pressed="${on}" title="${esc(a.threat_id + " · " + a.attack_pattern_name + label)}"><i></i>${esc(shortId(a))}</button>`;
    }).join("")}${badges ? '<span class="picker-legend"><i class="dot blocked"></i>Blocked <i class="dot open"></i>Exposed <i class="dot na"></i>Content only</span>' : ""}</div>`;
  }
  const BAR_PLACEHOLDER = 0;
  /* ---- 01 Policy Mapper ---- */
  function parseHTML(c) {
    const p = pol(c), st = polStats(c);
    return metrics([metric(num(st.chunks), "policy chunks"), metric(num(p.risks_total), "risks in the output", true), metric(st.model || "n/a", "LLM")]) +
      `<p class="muted small">Policy document: ${esc(p.name || Data.prov(c.uc, "policy").source_doc || "")}. The document is parsed and split into chunks; each chunk is matched against the risk taxonomies in the next step.</p>` +
      note("Output of this stage.", "The policy mapper produces risk-extraction.json and report.html only: risks with evidence, causal chains and mitigations. It does not build a threat surface.");
  }
  function retrieveHTML(c) {
    const st = polStats(c);
    return `<div class="pair"><div><strong>${esc(num(st.candidates_retrieved))}</strong><span>candidate matches</span></div><span class="arrow" aria-hidden="true">→</span><div><strong>${esc(num(Data.prov(c.uc, "policy").outputs && Data.prov(c.uc, "policy").outputs.risks))}</strong><span>risks kept</span></div></div>` +
      metrics([metric(num(st.auto_accepted), "auto-accepted by score"), metric(num(st.llm_judged), "borderline, judged by the LLM", true), metric(num(st.grounding_filtered), "dropped for weak grounding")]) +
      '<p class="muted small">Candidates come from BM25, embeddings and a cross-encoder over about 524 Nexus risks. Borderline ones go to an LLM judge.</p>';
  }
  function groundHTML(c) {
    const rs = Data.risks(c.uc), p = pol(c);
    const tax = new Set(rs.map((r) => r.taxonomy));
    const rows = rs.map((r) => {
      const open = state.risk === r.id;
      return `<tr class="click ${open ? "sel" : ""}" data-act="risk" data-id="${esc(r.id)}" tabindex="0"><td>${esc(r.name)}</td><td>${chip(r.taxonomy)}</td><td><span class="badge ${r.grounding_confidence === "high" ? "pass" : "saved"}">${esc(r.grounding_confidence || "n/a")}</span></td><td>${esc(r.evidence_total != null ? r.evidence_total : (r.evidence || []).length)}</td></tr>` +
        (open ? `<tr class="detail"><td colspan="4">${(r.evidence || []).map((e) => `<blockquote>${esc(e)}</blockquote>`).join("") || '<p class="muted">No evidence quoted.</p>'}</td></tr>` : "");
    }).join("");
    return metrics([metric(num(p.risks_total != null ? p.risks_total : rs.length), "risks extracted"), metric(rs.filter((r) => r.grounding_confidence === "high").length + " of " + rs.length, "shown with high grounding", true), metric(tax.size, "taxonomies shown")]) +
      `<p class="muted small">Showing the first ${rs.length} risks. Click a row for the policy text each risk is grounded in.</p>` +
      `<div class="tablewrap"><table class="t"><thead><tr><th>Risk</th><th>Taxonomy</th><th>Grounding</th><th>Evidence</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }
  function chainHTML(c) {
    const rs = Data.risks(c.uc), st = polStats(c);
    const part = (k, v) => (v ? `<div class="chain-step"><b>${esc(k)}</b><span>${esc(v)}</span></div>` : "");
    return metrics([metric(num(st.with_causal_chain), "risks with a causal chain", true), metric(num(pol(c).risks_total), "risks in the output")]) +
      '<p class="muted small">Each risk is expanded into threat → vulnerability → consequence, with its impact and suggested mitigations.</p>' +
      rs.map((r) => {
        const has = r.threat || r.vulnerability || r.consequence;
        const mits = (r.mitigations || []).map((m) => `<li><b>${esc(m.name)}</b> ${esc(m.description)}</li>`).join("");
        return `<details class="row-card"><summary><span class="rc-title">${esc(r.name)}</span><span class="rc-meta">${chip(r.taxonomy)}${has ? "" : '<span class="muted small">no chain</span>'}</span></summary><div class="rc-body">${has ? `<div class="chain">${part("Threat", r.threat)}<span class="arrow" aria-hidden="true">→</span>${part("Vulnerability", r.vulnerability)}<span class="arrow" aria-hidden="true">→</span>${part("Consequence", r.consequence)}</div>${part("Threat source", r.threat_source)}${part("Impact", r.impact)}` : '<p class="muted">No causal chain recorded.</p>'}${mits ? `<h4 class="sub">Mitigations${r.mitigations_total > (r.mitigations || []).length ? " (first " + r.mitigations.length + " of " + esc(r.mitigations_total) + ")" : ""}</h4><ul>${mits}</ul>` : ""}</div></details>`;
      }).join("");
  }

  /* ---- 02 Scenario Generator ---- */
  const scnDetail = (c) => Data.prov(c.uc, "scenarios").detail || {};
  function mapHTML(c) {
    const ts = Data.threats(c.uc), b = c.uc.battery || {};
    return metrics([metric(ts.length, "threats"), metric(Data.patternCount(c.uc), "attack patterns", true), metric(b.risk_cards != null ? b.risk_cards : Data.risks(c.uc).length, "risk cards mapped")]) +
      '<p class="muted small">Input: the policy mapper output, the use-case profile and qualification facts. Risks are mapped to threats (SSSOM), giving threat-surface.yaml: the threats and their attack patterns.</p>' +
      ts.map((t) => `<details class="row-card"><summary><span class="rc-title">${esc(t.threat_name)}</span><span class="rc-meta">${chip(t.threat_id)}<span class="muted small">${(t.attack_patterns || []).length} patterns</span></span></summary><div class="rc-body">${chips(t.attack_patterns)}<p class="muted small">Mapped from ${esc(t.risks_mapped != null ? t.risks_mapped : "?")} risk card(s).</p></div></details>`).join("");
  }
  function qualifyHTML(c) {
    const d = scnDetail(c), b = c.uc.battery || {};
    const pf = d.rule_prefilter, ep = d.entry_points;
    return `<div class="pair"><div><strong>${esc(b.attack_patterns_total != null ? b.attack_patterns_total : Data.patternCount(c.uc))}</strong><span>attack patterns</span></div><span class="arrow" aria-hidden="true">→</span><div><strong>${esc(num(d.candidates_attempted))}</strong><span>candidate scenarios</span></div></div>` +
      metrics([metric(ep ? ep.accessible + " of " + ep.total : "n/a", "attacker-accessible entry points"), metric(pf ? pf.rejected + " of " + pf.candidates : "n/a", "rejected by deterministic rules", true), metric((d.uncovered_attack_patterns || []).length, "patterns left uncovered")]) +
      '<p class="muted small">Attack patterns are qualified against the use-case profile, then crossed with entry points and techniques to enumerate candidate scenarios. Deterministic rules reject impossible candidates before the LLM sees them.</p>' +
      ((d.seeds_without_techniques || []).length ? `<h4 class="sub">Skipped: no technique IDs</h4><div class="chiprow">${chips(d.seeds_without_techniques)}</div>` : "") +
      ((d.uncovered_attack_patterns || []).length ? `<h4 class="sub">Uncovered attack patterns</h4><div class="chiprow">${chips(d.uncovered_attack_patterns)}</div>` : "");
  }
  function scenariosHTML(c) {
    const all = Data.scenarios(c.uc), d = scnDetail(c);
    const pt = Data.perThreat(c.uc).filter((p) => p.generated);
    const max = Math.max(1, ...pt.map((p) => p.generated));
    const list = all.filter((s) => !state.sfilter || s.threat_id === state.sfilter);
    const shown = list.slice(0, state.sshown);
    const rows = shown.map((s) => {
      const id = s.scenario_id;
      const open = state.scn === id;
      return `<tr class="click ${open ? "sel" : ""}" data-act="scn" data-id="${esc(id)}" tabindex="0"><td class="mono">${esc(String(id).slice(-8))}</td><td>${chip(s.threat_id)}</td><td>${esc(clip(s.attack_pattern_name, 70))}</td><td>${esc(s.actor_type || "")}</td></tr>` +
        (open ? `<tr class="detail"><td colspan="4"><p>${esc(s.narrative_summary)}</p><ol>${(s.steps || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ol><p class="muted small">Entry point: ${esc(s.entry_point || "n/a")}</p></td></tr>` : "");
    }).join("");
    return metrics([metric(num(d.enumerated), "candidates attempted"), metric(all.length, "scenarios generated and admitted", true), metric(pt.length, "threats covered")]) +
      '<h4 class="sub">Admitted scenarios per threat</h4>' +
      `<div class="bars">${pt.map((p) => `<button type="button" class="bar-button" data-act="sfilter" data-v="${esc(p.id)}" aria-pressed="${state.sfilter === p.id}"><span title="${esc(p.name)}">${esc(p.id)}</span><b style="width:${Math.max(3, Math.round((100 * p.generated) / max))}%"></b><strong>${p.generated}</strong></button>`).join("")}</div>` +
      `<div class="filter-row"><label class="muted small" for="sf">Threat</label><select id="sf" data-act="sfilter-sel"><option value="">All threats (${all.length})</option>${pt.map((p) => `<option value="${esc(p.id)}" ${state.sfilter === p.id ? "selected" : ""}>${esc(p.id + " · " + clip(p.name, 50))} (${p.generated})</option>`).join("")}</select><small>${list.length} shown of ${all.length}. Click a row for the narrative.</small></div>` +
      `<div class="tablewrap"><table class="t"><thead><tr><th>Scenario</th><th>Threat</th><th>Attack pattern</th><th>Actor</th></tr></thead><tbody>${rows}</tbody></table></div>` +
      (list.length > shown.length ? `<p><button type="button" class="secondary" data-act="more">Show more (${list.length - shown.length} left)</button></p>` : "");
  }
  function admissionHTML(c) {
    const p = Data.prov(c.uc, "scenarios"), d = p.detail || {};
    const pt = Data.perThreat(c.uc).filter((x) => x.generated);
    const max = Math.max(1, ...pt.map((x) => x.generated));
    const reasons = d.quarantine_reasons || [];
    return metrics([metric(num(p.generated), "candidates"), metric(num(p.admitted), "admitted", true), metric(num(p.quarantined), "quarantined")]) +
      '<p class="muted small">Every candidate goes through the admission gates. Admitted scenarios are kept; rejected or failed candidates are quarantined.</p>' +
      '<h4 class="sub">Admitted per threat</h4>' +
      bars(pt.map((x) => ({ label: x.id + " " + clip(x.name, 38), value: x.generated })), max) +
      '<p class="muted small">Quarantined candidates are not tagged with a threat in the run output, so only admitted scenarios are split by threat.</p>' +
      `<h4 class="sub">Why candidates were quarantined (${esc(num(p.quarantined))}: ${esc(num(d.rejected))} rejected, ${esc(num(d.generation_failed))} failed generation)</h4>` +
      `<div class="tablewrap"><table class="t"><thead><tr><th>Reason</th><th>Candidates</th></tr></thead><tbody>${reasons.map((r) => `<tr><td class="mono">${esc(r.code)}</td><td>${esc(r.count)}</td></tr>`).join("")}</tbody></table></div>` +
      det("gates", "Gates checked (" + (d.gates || []).length + ")", `<div class="chiprow">${chips(d.gates)}</div>`);
  }

  /* ---- 03 Artifact Generator ---- */
  const artProv = (c) => Data.prov(c.uc, "artifacts");
  function funnelHTML(c) {
    const p = artProv(c);
    const pt = (p.per_threat || []).filter((t) => t.admitted);
    const max = Math.max(1, ...pt.map((t) => t.admitted));
    return metrics([metric(num(p.admitted_input), "admitted scenarios in"), metric(num(p.attempted), "artifacts attempted", true), metric(num(p.produced), "artifacts produced")]) +
      note("Where the cut happens.", p.selection_rule || "") +
      '<h4 class="sub">Admitted scenarios vs artifacts, per threat</h4>' +
      `<div class="bars gate">${pt.map((t) => `<div class="barrow"><span class="bl" title="${esc(t.name)}">${esc(t.threat_id)}</span><span class="bt"><i class="gen" style="width:${Math.max(3, Math.round((100 * t.admitted) / max))}%"></i>${t.artifacts ? `<i class="pass" style="width:${Math.max(2, Math.round((100 * t.artifacts) / max))}%"></i>` : ""}</span><span class="bv">${esc(t.artifacts)} / ${esc(t.admitted)}</span></div>`).join("")}</div>` +
      '<p class="legend"><i class="dot gen"></i>admitted scenarios <i class="dot pass"></i>with an artifact</p>';
  }
  /* attack_turn_index is the 0-based index into a.turns (taken from the garak artifact); fall back to a text match */
  function attackTurn(a) {
    const ts = a.turns || [];
    if (Number.isInteger(a.attack_turn_index) && ts[a.attack_turn_index]) return a.attack_turn_index;
    const key = String(a.adversarial_text || "").slice(0, 40);
    const m = key ? ts.findIndex((t) => String(t.content || "").includes(key)) : -1;
    if (m >= 0) return m;
    for (let i = ts.length - 1; i >= 0; i--) if (ts[i].role === "user") return i;
    return -1;
  }
  function turnsHTML(a) {
    const hot = attackTurn(a);
    return `<div class="conversation">${(a.turns || []).map((t, i) => `<div class="turn ${i === hot ? "attack" : ""}"><header><span>${esc(t.role)}${t.tool_calls && t.tool_calls.length ? " · calls " + esc(t.tool_calls.join(", ")) : ""}</span>${i === hot ? '<span class="badge">attack turn</span>' : ""}</header><pre>${esc(t.content)}</pre></div>`).join("") || '<p class="muted">No transcript.</p>'}</div>`;
  }
  function rubricHTML(a) {
    const r = a.detector_rubric;
    if (!r) return '<p class="muted">No detector rubric recorded.</p>';
    if (typeof r === "string") return `<p>${esc(r)}</p>`;
    let h = "";
    if (r.context) h += `<h4 class="sub">Context</h4><p>${esc(r.context)}</p>`;
    const rb = r.rubrics || {};
    const keys = Object.keys(rb);
    if (keys.length) h += `<div class="rubrics">${keys.map((k) => `<div><h3>${esc(k.replace(/_/g, " "))}</h3><ul>${(Array.isArray(rb[k]) ? rb[k] : [rb[k]]).map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>`).join("")}</div>`;
    if (r.judge_prompt) h += det("judge", "Judge prompt", `<pre class="code">${esc(r.judge_prompt)}</pre>`);
    return h || '<p class="muted">Rubric is empty.</p>';
  }
  function conversationHTML(c) {
    const a = c.a;
    if (!a) return funnelHTML(c) + '<p class="muted">No attacks.</p>';
    return funnelHTML(c) + '<h4 class="sub">Attack conversations</h4>' + attackPicker(c, state.done >= 4) +
      `<div class="kvs">${kv("Threat", chip(a.threat_id) + " " + esc(a.threat_name))}${kv("Attack pattern", esc(a.attack_pattern_name))}${kv("Injection surface", chip(a.injection_surface || "unknown"))}</div>` +
      `<h4 class="sub">Multi-turn conversation (with tool context)</h4>${turnsHTML(a)}` +
      det("narr", "Narrative", `<p>${esc(a.narrative_summary)}</p>`);
  }
  function validationHTML(c) {
    const a = c.a, p = artProv(c);
    if (!a) return '<p class="muted">No attacks.</p>';
    const v = a.artifact || {};
    const cg = p.coverage_gate || {};
    return metrics([metric(num(p.validated) + " of " + num(p.attempted), "pass fidelity validation", true), metric(p.fidelity_min != null ? p.fidelity_min + " / 12" : "n/a", "lowest fidelity score"), metric(Object.keys(cg).map((k) => cg[k] + " " + k).join(", ") || "n/a", "coverage gate")]) +
      '<p class="muted small">Validation checks the conversation structure and scores fidelity to the scenario (acceptance needs at least 10 of 12 and no critical violations). The coverage gate says how well Garak can observe the attack.</p>' +
      attackPicker(c, state.done >= 4) +
      `<div class="kvs">${kv("Validation", chip(v.validated ? "passed" : "not validated", v.validated ? "ess" : "bad"))}${kv("Fidelity", esc(v.fidelity_total != null ? v.fidelity_total + " / 12" : "n/a"))}${kv("Attempts", esc(v.attempts != null ? v.attempts : "n/a"))}${kv("Coverage gate", chip(v.coverage_gate || "n/a") + " " + esc(v.coverage_reason || ""))}</div>` +
      (v.scores ? `<div class="chiprow">${Object.keys(v.scores).map((k) => chip(k.replace(/_/g, " ") + " " + v.scores[k] + "/2")).join("")}</div>` : "") +
      `<h4 class="sub">Detector rubric (how success is judged)</h4>${rubricHTML(a)}`;
  }

  /* Baseline (stage 3): what the agent can do with no sandbox */
  function baselineHTML(c) {
    const v = state.variant, list = Data.attacks(c.uc), a = c.a;
    const out = (x) => Data.outcome(v, NOSB, x.scenario_id);
    const cnt = { exposed: 0, blocked: 0, gap: 0 };
    list.forEach((x) => { const o = out(x); if (cnt[o] != null) cnt[o]++; });
    const label = { exposed: "reaches its target", blocked: "blocked", gap: "content only" };
    const tag = (o) => `<span class="pill ${o === "exposed" ? "bad" : o === "blocked" ? "ok" : "na"}">${esc(label[o] || o)}</span>`;
    const ps = a ? Data.byScen(v, NOSB)[a.scenario_id] || [] : [];
    return note("Baseline.", `What the agent can do with no sandbox: every action reaches its target. Each attack's concrete actions are replayed in a plain container for the ${Data.vlabel(v)}.`) +
      metrics([metric(cnt.exposed, "attacks with an action that reaches its target", true), metric(cnt.blocked, "blocked"), metric(cnt.gap, "content only (no action)"), metric(list.length, "attacks probed")]) +
      (a ? attackPicker(c, true, NOSB) + `<div class="kvs">${kv(CLABEL[NOSB], tag(out(a)))}${kv("Probed actions", ps.length ? ps.map((p) => `<div><span class="mono">${esc(p.action.label)}</span> ${pill(p.status)} <span class="muted small">${esc(nodeLabel(p.action.resource))}</span></div>`).join("") : '<span class="muted">none</span>')}</div>` : "") +
      `<div class="tablewrap"><table class="t"><thead><tr><th>Attack</th><th>Pattern</th><th>${esc(CLABEL[NOSB])}</th></tr></thead><tbody>${list.map((x) => `<tr class="click ${a && x.scenario_id === a.scenario_id ? "sel" : ""}" data-act="attack" data-sid="${esc(x.scenario_id)}" tabindex="0"><td class="mono">${esc(shortId(x))}</td><td>${esc(clip(x.attack_pattern_name, 70))}</td><td>${tag(out(x))}</td></tr>`).join("")}</tbody></table></div>`;
  }
  function nodeLabel(id) { const n = id && Data.node(id); return n ? n.label : id || "content only"; }


  /* ---------- stage 4: OpenShell recommender ---------- */
  const R = () => SNAP.recommendation || {};
  const DEF_EXPL = "OpenShell default (no policy supplied): OpenShell denies everything not allowed. Its default has no network rules and doesn't even allow the agent's own program folder (/opt/app-root), so the agent can't start: safe, but useless (0/9 good actions).";
  const OUTCOME_LABEL = { blocked: "blocked", exposed: "exposed", content_only: "text-only" };
  const outPill = (o, tip) => `<span class="pill ${o === "blocked" ? "ok" : o === "exposed" ? "bad" : "na"}"${tip ? ` title="${esc(tip)}"` : ""}>${esc(OUTCOME_LABEL[o] || o)}</span>`;
  const originChip = (o) => o === "hand-written stand-in" ? chip("hand-written stand-in, not from ASAGO run 4", "bad") : o === "deployment check" ? chip("deployment check, not an attack", "chk") : chip(o || "ASAGO run 4", "ess");
  const resPill = (r) => `<span class="pill ${r === "pass" ? "ok" : r === "fail" ? "bad" : "na"}">${esc(String(r).replace(/_/g, " "))}</span>`;
  const tally = (list) => { const m = {}; list.forEach((x) => (m[x] = (m[x] || 0) + 1)); return m; };
  /* Sandbox-probe numbers, computed from the probe data (they must match validation.md). */
  function probeNums(c) {
    const v = state.variant, ben = Data.benign(v, c), har = Data.harmful(v, c), at = Data.sbAttacks();
    const oc = tally(at.map((a) => Data.outcome(v, c, a.scenario_id)));
    return {
      goodOk: ben.filter((p) => p.status === "allowed").length, good: ben.length,
      badDenied: har.filter((p) => p.status === "denied").length, bad: har.length,
      blocked: oc.blocked || 0, attacks: at.length, exposed: oc.exposed || 0, text: oc.gap || 0,
    };
  }
  const WHAT_STOPS = {
    egress_unknown_host: "Connections to unknown hosts (data sent out)", exec_spawned_network: "curl or another spawned program reaching the network",
    fs_write_outside_workspace: "Writes outside the workspace (before: only the container user's file permissions)",
    fs_read_undeclared: "Reads of unrelated files", secret_disclosure: "Reading the real tool token",
  };
  /* Before -> after: 3-column summary, "protection gained" table, policy diff (all from snapshot data, no raw HTML). */
  function beforeAfterHTML() {
    const r = R(), ba = r.before_after || {}, d = r.diff || {}, N = probeNums(NOSB), P = probeNums(DEF), L = probeNums("least-privilege");
    const row = (label, a, b, c, warn) => `<tr><td>${esc(label)}</td><td>${esc(a)}</td><td${warn ? ' class="broken"' : ""}>${esc(b)}</td><td class="after"><b>${esc(c)}</b></td></tr>`;
    const gained = (ba.gained || []).map((g) => {
      const w = (n) => (g.total ? Math.round((100 * n) / g.total) : 0);
      return `<tr><td>${esc(WHAT_STOPS[g.effect] || g.effect)}</td><td>${esc(g.rule)}</td><td class="nw">${esc(g.before)}/${esc(g.total)} <span aria-hidden="true">→</span> <b class="after">${esc(g.after)}/${esc(g.total)}</b></td>` +
        `<td class="gbar-cell"><span class="gbar" role="img" aria-label="denied before ${esc(g.before)} of ${esc(g.total)}, after ${esc(g.after)} of ${esc(g.total)}"><i class="aft" style="width:${w(g.after)}%"></i>${g.before < g.after ? `<i class="bef" style="width:${w(g.before)}%"></i>` : ""}</span></td></tr>`;
    }).join("");
    const diff = (d.lines || []).map((l) => `<span class="dl ${esc(l.t)}">${esc(l.s) || " "}</span>`).join("");
    return `<h4 class="opt-head">Before → after</h4>` +
      `<p class="muted small">Same agent, same probe actions, three conditions. Before = the agent image in a plain container with no OpenShell. OpenShell default = the sandbox created with no policy supplied. Attack hosts that don't exist count as not protected: nothing stopped them.</p>` +
      `<div class="tablewrap"><table class="t ba-table"><thead><tr><th></th><th>${esc(CLABEL[NOSB])}</th><th>${esc(CLABEL[DEF])}</th><th class="after">${esc(CLABEL["least-privilege"])}</th></tr></thead><tbody>` +
      row("Good actions allowed", `${N.goodOk}/${N.good}`, `${P.goodOk}/${P.good}`, `${L.goodOk}/${L.good}`, P.goodOk < P.good) + row("Bad actions denied", `${N.badDenied}/${N.bad}`, `${P.badDenied}/${P.bad}`, `${L.badDenied}/${L.bad}`) +
      row("Attacks fully blocked", `${N.blocked}/${N.attacks}`, `${P.blocked}/${P.attacks}`, `${L.blocked}/${L.attacks}`) + row("Attacks with an action still allowed", N.exposed, P.exposed, L.exposed) + row("Text-only attacks (nothing to block)", N.text, P.text, L.text) +
      `</tbody></table></div>` +
      `<p class="muted small">The default is safe because it blocks everything, including the agent. The recommendation keeps the agent working (9/9) while still blocking every attack that needs something outside the use case.</p>` +
      `<h4 class="opt-head">Protection gained</h4><div class="tablewrap"><table class="t"><thead><tr><th>What it stops</th><th>Rule</th><th>Denied before → after</th><th></th></tr></thead><tbody>${gained}</tbody></table></div>` +
      `<h4 class="opt-head">Policy diff</h4>` + (d.intro || []).map((n) => `<p class="muted small">${esc(n)}</p>`).join("") +
      `<pre class="code diff" tabindex="0" aria-label="Policy diff, OpenShell default (observed) to recommended">${diff}</pre>` +
      `<h4 class="opt-head">The recommended policy</h4>`;
  }
  const s4 = {
    policy() {
      const r = R(), m = r.mcp || {};
      return beforeAfterHTML() + `<p class="muted small">Least-privilege policy generated from the agent's use case and deployment facts (<b>${esc(Data.vlabel(state.variant))}</b>). One policy, no variants. The attacks are used to test it, not to write it.</p>` +
        note("What it can and can't do.", "OpenShell denies everything not listed. It blocks capabilities, not intent: it sees tool name, host:port, which program connects, HTTP method/path and file paths, never the conversation or tool arguments.") +
        `<h4 class="opt-head">Policy, explained</h4><div class="tablewrap"><table class="t"><thead><tr><th>Part</th><th>Rule</th><th>From</th></tr></thead><tbody>${(r.rows || []).map((x) => `<tr><td><b>${esc(x.part)}</b></td><td>${esc(x.rule)}</td><td>${esc(x.source)}</td></tr>`).join("")}</tbody></table></div>` +
        (r.notes || []).map((n) => `<p class="muted small">${esc(n)}</p>`).join("") +
        `<h4 class="opt-head">policy.yaml</h4><pre class="code yaml" tabindex="0" aria-label="Policy YAML">${esc(r.policy_yaml || "")}</pre>` +
        det("defpol", "OpenShell default policy (observed)", `<p class="muted small">What OpenShell applies when no policy is supplied. No network rules, and /opt/app-root (the agent's program folder) is not readable, so the agent can't start.</p><pre class="code yaml" tabindex="0" aria-label="OpenShell default policy YAML">${esc(r.default_policy_yaml || "")}</pre>`) +
        `<h4 class="opt-head">MCP tool allowlist (provider profile)</h4>` +
        `<div class="kvs">${kv("Profile", `<span class="mono">${esc(m.id)}</span>`)}${kv("Endpoint", `<span class="mono">${esc(m.host)}:${esc(m.port)}${esc(m.path)}</span> ${chip(m.protocol || "", "ess")} ${chip(m.enforcement || "")}`)}${kv("MCP methods", chips(m.methods))}${kv("tools/call allowed only for", chips(m.tools, "ess"))}${kv("Programs allowed to connect", chips(m.binaries))}</div>`;
    },
    validate() {
      const tests = (R().prove || {}).tests || [], N = probeNums(NOSB), P = probeNums(DEF), L = probeNums("least-privilege");
      const rank = { fail: 0, pass: 1, not_provable: 2 };
      const cnt = (k, res) => tests.filter((t) => t.kind === k && t.result === res).length;
      const tot = (res) => tests.filter((t) => t.result === res).length;
      const shown = tests.filter((t) => state.pf === "all" || t.result === state.pf).slice().sort((a, b) => (rank[a.result] - rank[b.result]) || a.kind.localeCompare(b.kind));
      const fb = (k, label) => `<button type="button" data-act="pfilter" data-v="${k}" aria-pressed="${state.pf === k}">${esc(label)}</button>`;
      const row = (label, a, b, c, warn) => `<tr><td>${esc(label)}</td><td>${esc(a)}</td><td${warn ? ' class="broken"' : ""}>${esc(b)}</td><td class="after"><b>${esc(c)}</b></td></tr>`;
      return `<div class="vcard"><h4><span class="n">1</span>Valid config</h4>` +
        `<p>OpenShell accepted the policy; good actions allowed <b>${esc(L.goodOk)}/${esc(L.good)}</b>.</p><p class="muted small">The sandbox was created from policy.yaml and the provider profile and the agent ran. Good actions are the use case's declared capabilities (tools, shipping API, workspace files).</p></div>` +
        `<div class="vcard"><h4><span class="n">2</span>Policy Prover</h4>` +
        metrics([metric(`${cnt("good", "pass")} / ${cnt("bad", "pass")}`, "pass (good / bad)", true), metric(tot("fail"), "fail", tot("fail") ? "" : "mint"), metric(tot("not_provable"), "not provable")]) +
        `<p class="muted small">Each action is encoded as a tiny policy and proven against the recommended policy: within_boundary = allowed, exceeds_boundary = blocked. MCP tool calls, local commands and unresolvable file paths are not provable in openshell-prover 0.1.8 and are covered by the sandbox probes.</p>` +
        `<div class="seg" role="group" aria-label="Prover result filter">${fb("all", `All (${tests.length})`)}${fb("pass", `Pass (${tot("pass")})`)}${fb("not_provable", `Not provable (${tot("not_provable")})`)}</div>` +
        `<div class="tablewrap"><table class="t"><thead><tr><th>Kind</th><th>Action</th><th>Expect</th><th>Result</th><th>Detail</th></tr></thead><tbody>${shown.map((t) => `<tr><td class="nw">${chip(t.kind, t.kind === "good" ? "ess" : "bad")}</td><td class="mono">${esc(t.label)}</td><td class="nw">${esc(t.expect === "blocked" ? "blocked" : "allowed")}</td><td class="nw">${resPill(t.result)}</td><td>${esc(t.detail)}</td></tr>`).join("")}</tbody></table></div></div>` +
        `<div class="vcard"><h4><span class="n">3</span>Sandbox probes</h4>` +
        `<p class="muted small">Dynamic: the agent's own programs perform each action inside the real sandbox, no LLM. This tests the sandbox, not the agent: it assumes the agent is already fooled and checks what the sandbox still allows. ${esc(DEF_EXPL)}</p>` +
        `<div class="tablewrap"><table class="t"><thead><tr><th></th><th>${esc(CLABEL[NOSB])}</th><th>${esc(CLABEL[DEF])}</th><th class="after">${esc(CLABEL["least-privilege"])}</th></tr></thead><tbody>` +
        row("Good actions allowed", `${N.goodOk}/${N.good}`, `${P.goodOk}/${P.good}`, `${L.goodOk}/${L.good}`, P.goodOk < P.good) + row("Bad actions denied", `${N.badDenied}/${N.bad}`, `${P.badDenied}/${P.bad}`, `${L.badDenied}/${L.bad}`) +
        row("Attacks fully blocked", `${N.blocked}/${N.attacks}`, `${P.blocked}/${P.attacks}`, `${L.blocked}/${L.attacks}`) + row("Attacks with an action still allowed", N.exposed, P.exposed, L.exposed) + row("Text-only attacks (nothing to block)", N.text, P.text, L.text) +
        `</tbody></table></div><p class="muted small">Before = the same agent image in a plain container, no OpenShell. Attack hosts that don't exist count as not protected: nothing stopped them.</p></div>`;
    },
    probe: () => sandboxHTML(),
    gaps() {
      const G = R().gaps || [], at = tally(G.map((g) => g.recommended));
      const resid = tally(G.flatMap((g) => g.residual));
      const shown = G.filter((g) => state.gf === "all" || g.recommended === state.gf);
      const fb = (k, label) => `<button type="button" data-act="gfilter" data-v="${k}" aria-pressed="${state.gf === k}">${esc(label)}</button>`;
      const nRes = (at.exposed || 0) + (at.content_only || 0);
      const nsB = G.filter((g) => g.no_sandbox === "blocked").length;
      return metrics([metric(at.blocked || 0, "blocked by the policy", true), metric(nsB, "blocked with no sandbox"), metric(at.exposed || 0, "exposed: an allowed action"), metric(at.content_only || 0, "text-only"), metric(nRes, "go to guardrails")]) +
        `<p class="muted small">Outcome per attack under the recommended policy (${esc(G.length)} items incl. 1 hand-written stand-in and 1 deployment check). What the sandbox can't block is handed to the guardrail recommender.</p>` +
        `<h4 class="opt-head">Residual risks (what is left for guardrails)</h4><div class="tablewrap"><table class="t"><thead><tr><th>Why OpenShell can't stop it</th><th>Attacks</th></tr></thead><tbody>${Object.keys(resid).sort((a, b) => resid[b] - resid[a]).map((k) => `<tr><td>${esc(k)}</td><td>${esc(resid[k])}</td></tr>`).join("")}</tbody></table></div>` +
        `<h4 class="opt-head">Per attack</h4><div class="seg" role="group" aria-label="Outcome filter">${fb("all", `All (${G.length})`)}${fb("blocked", `Blocked (${at.blocked || 0})`)}${fb("exposed", `Exposed (${at.exposed || 0})`)}${fb("content_only", `Text-only (${at.content_only || 0})`)}</div>` +
        `<div class="tablewrap"><table class="t"><thead><tr><th>Threat</th><th>Origin</th><th>Harmful goal (evidence)</th><th>Targeted capability</th><th>${esc(CLABEL[NOSB])}</th><th>${esc(CLABEL[DEF])}</th><th>${esc(CLABEL["least-privilege"])}</th><th>Blocked by / residual</th></tr></thead><tbody>${shown.map((g) =>
          `<tr><td class="mono" title="${esc(Data.tname(g.threat_id))}">${esc(g.threat_id)}</td><td>${originChip(g.origin)}</td><td>${esc(clip(g.quote, 150))}</td><td class="mono">${esc(clip((g.targets || []).join(", "), 90)) || '<span class="muted">none</span>'}</td><td>${outPill(g.no_sandbox, (Data.byScen(state.variant, NOSB)[g.attack_id] || []).some((p) => p.status === "unenforced") ? UNENF : "")}</td><td>${outPill(g.openshell_default)}</td><td>${outPill(g.recommended)}</td><td>${esc(clip((g.blocked_by || []).concat(g.residual || []).join("; "), 170))}</td></tr>`).join("")}</tbody></table></div>`;
    },
  };

  /* ---------- stages (stepper, progress and sub-tabs follow the guardrails demo) ---------- */
  const sub = (key, label, hint, msg, render) => ({ key, label, hint, msg, render });
  const STAGES = [
    {
      key: "policy", name: "Policy Mapper", blurb: "Policy → risks + causal chains", title: "Risks and causal chains",
      desc: "Parse the policy, find the risks it implies, ground each in quoted text and explain how it could cause harm.", verb: "policy mapper",
      subs: [
        sub("parse", "Parse policy", "policy doc → chunks", "parsing & chunking policy…", parseHTML),
        sub("retrieve", "Retrieve & judge risks", "BM25 + embeddings + cross-encoder, LLM judge", "retrieving candidate risks…", retrieveHTML),
        sub("ground", "Ground evidence", "risks + quoted policy text", "grounding evidence…", groundHTML),
        sub("chains", "Causal chains", "threat → vulnerability → consequence", "synthesising causal chains…", chainHTML),
      ],
      summary: (c) => `${num(pol(c).risks_total)} risks`,
    },
    {
      key: "scenarios", name: "Scenario Generator", blurb: "Risks → threats → admitted scenarios", title: "Threat surface and scenarios",
      desc: "Map the risks to threats and attack patterns, enumerate candidate scenarios, generate them and admit those that pass the gates.", verb: "scenario generator",
      subs: [
        sub("map", "Map risks to threats", "threat surface: threats + attack patterns", "mapping risks to threats…", mapHTML),
        sub("qualify", "Qualify & enumerate", "patterns → candidate scenarios", "qualifying attack patterns and enumerating candidates…", qualifyHTML),
        sub("generate", "Generate scenarios", "narratives per threat", "generating scenarios…", scenariosHTML),
        sub("admit", "Admission gates", "admitted vs quarantined", "running admission gates…", admissionHTML),
      ],
      summary: (c) => `${Data.threats(c.uc).length} threats · ${Data.scenarios(c.uc).length} scenarios`,
    },
    {
      key: "artifacts", name: "Artifact Generator", blurb: "Scenarios → attack conversations", title: "Attack artifacts",
      desc: "Turn each admitted scenario into a multi-turn attack conversation with a rubric, validate it, and see what the attacks can do with no sandbox.", verb: "artifact generator",
      subs: [
        sub("build", "Build conversations", "scenario → multi-turn conversation", "building conversations…", conversationHTML),
        sub("validate", "Rubric & validation", "fidelity + coverage gate", "writing rubrics and validating…", validationHTML),
        sub("baseline", "Baseline", "attack actions with no sandbox", "replaying attack actions with no sandbox…", baselineHTML),
      ],
      summary: (c) => `${Data.attacks(c.uc).length} attacks`,
    },
    {
      key: "recommender", name: "OpenShell Recommender", blurb: "Use case → least-privilege policy → validate", title: "Recommended OpenShell policy",
      desc: "Generate the least-privilege OpenShell policy from the use case and deployment facts, then validate it: valid config, Policy Prover tests, and every attack's action probed in the real sandbox. What it can't block goes to guardrails.", verb: "OpenShell recommender",
      subs: [
        sub("policy", "Recommended policy", "the one least-privilege policy and its MCP allowlist", "generating the least-privilege policy…", s4.policy),
        sub("validate", "Validate", "valid config, Policy Prover, sandbox probes", "validating the policy…", s4.validate),
        sub("probe", "Apply & probe", "apply the policy, replay each attack's actions", "applying the policy and probing…", s4.probe),
        sub("gaps", "Gaps → guardrails", "what the policy can't block", "listing the gaps…", s4.gaps),
      ],
      summary: () => { const a = probeNums(NOSB), b = probeNums("least-privilege"); return `blocked ${a.blocked} → ${b.blocked} · ${b.goodOk}/${b.good} tasks OK`; },
    },
  ];
  const ctx = () => ({ uc: curUC(), a: curAttack() });
  const statusOf = (i) => (state.running === i ? "running" : i < state.done ? "completed" : "pending");
  const STATUS_LABEL = { pending: "Not started", running: "Running", completed: "Completed" };
  const revealed = (i) => (state.running === i ? state.sub : i < state.done ? STAGES[i].subs.length : 0);

  /* ---------- engine (same timing as the guardrails demo) ---------- */
  const STEP_MS = 1200;
  function clearTimer() { if (state.timer) { clearTimeout(state.timer); state.timer = null; } }
  function startStage(i) {
    if (state.running !== -1 || i !== state.done || i >= STAGES.length) return;
    state.running = i; state.sub = 0; state.sel = i; state.tab[i] = 0;
    render();
    schedule();
  }
  function schedule() {
    clearTimer();
    if (state.running < 0) return;
    state.timer = setTimeout(() => {
      const i = state.running;
      state.sub++;
      state.tab[i] = state.sub - 1;
      if (state.sub >= STAGES[i].subs.length) {
        state.running = -1; state.sub = 0; state.done = i + 1; state.tab[i] = 0;
        render();
        if (state.runAll && state.done < STAGES.length) state.timer = setTimeout(() => startStage(state.done), 500);
        else state.runAll = false;
        return;
      }
      render();
      schedule();
    }, STEP_MS);
  }
  function reset() {
    clearTimer(); stopAnim();
    Object.assign(state, { done: 0, running: -1, sub: 0, sel: 0, tab: [0, 0, 0, 0], attack: null, runAll: false, sfilter: "", sshown: 20, scn: null, risk: null, live: false, bench: null, log: [] });
    render();
  }

  function renderCards() {
    const c = ctx();
    $("pipeline").innerHTML = STAGES.map((s, i) => {
      const st = statusOf(i);
      const nextable = st === "pending" && i === state.done && state.running === -1;
      const sm = st === "completed" ? s.summary(c) : "";
      return `<div class="stage-cell"><button type="button" class="stage ${state.sel === i ? "active" : ""}" data-act="sel" data-i="${i}" data-status="${st}" aria-pressed="${state.sel === i}"><span class="step">0${i + 1}</span><span class="stage-copy"><strong>${esc(s.name)}</strong><small>${esc(s.blurb)}</small><small class="stage-result">${esc(sm)}</small></span><span class="stage-state">${STATUS_LABEL[st]}</span></button>${nextable ? `<button type="button" class="secondary mini" data-act="run" data-i="${i}">Run</button>` : ""}</div>`;
    }).join("");
  }
  function renderControls() {
    const busy = state.running !== -1;
    const next = STAGES[state.done];
    const b = $("run-next");
    b.disabled = busy || !next;
    b.innerHTML = next ? `Run ${esc(next.verb)} <span aria-hidden="true">→</span>` : "All stages complete";
    $("run-all").disabled = busy || !next;
    $("reset").disabled = !busy && state.done === 0;
  }
  function renderProgress() {
    const el = $("progress");
    const i = state.running;
    if (i < 0) { el.hidden = true; return; }
    const s = STAGES[i];
    el.hidden = false;
    $("progress-text").textContent = `Running ${s.verb} — ${s.subs[Math.min(state.sub, s.subs.length - 1)].msg}`;
    $("progress-bar").style.width = Math.round((100 * (state.sub + 1)) / s.subs.length) + "%";
  }
  function emptyPanel(i) {
    const s = STAGES[i];
    const canRun = i === state.done && state.running === -1;
    const prev = i > 0 ? STAGES[state.done].name : "";
    return `<div class="empty"><span class="empty-number">0${i + 1}</span><h3>${esc(s.name)}</h3><p>${esc(s.desc)}</p><ul class="sublist">${s.subs.map((x) => `<li><b>${esc(x.label)}</b><span>${esc(x.hint)}</span></li>`).join("")}</ul>${canRun ? `<button type="button" class="secondary" data-act="run" data-i="${i}">Run ${esc(s.verb)}</button>` : `<p class="muted small">${state.running !== -1 ? "Waiting for the current stage to finish." : "Run " + esc(prev.toLowerCase()) + " first."}</p>`}</div>`;
  }
  function renderPanel() {
    const i = state.sel, s = STAGES[i];
    const st = statusOf(i), rev = revealed(i);
    $("panel-kicker").textContent = `0${i + 1} / ${s.name.toUpperCase()}`;
    $("panel-title").textContent = s.title;
    $("panel-description").textContent = s.desc;
    const badge = $("panel-badge");
    badge.textContent = STATUS_LABEL[st];
    badge.className = "badge " + (st === "completed" ? "pass" : "saved");
    const tabs = $("subtabs"), body = $("results");
    if (rev === 0) {
      tabs.hidden = true; tabs.innerHTML = "";
      body.innerHTML = emptyPanel(i);
      return;
    }
    const active = Math.min(state.tab[i] == null ? 0 : state.tab[i], rev - 1);
    tabs.hidden = false;
    tabs.innerHTML = s.subs.map((x, k) => {
      const ready = k < rev;
      const running = st === "running" && k === rev;
      return `<button type="button" role="tab" class="subtab ${k === active ? "on" : ""} ${ready ? "" : "wait"}" data-act="tab" data-i="${k}" data-tab="${x.key}" aria-selected="${k === active}" ${ready ? "" : "disabled"}><span class="n">${k + 1}</span>${esc(x.label)}<small>${ready ? "done" : running ? "running…" : "waiting"}</small></button>`;
    }).join("");
    const keep = document.querySelector(".sb-list"), sc = keep ? keep.scrollTop : 0;
    stopAnim();
    body.innerHTML = `<div class="results-inner" role="tabpanel">${s.subs[active].render(ctx())}</div>`;
    if (i === 3 && s.subs[active].key === "probe") sandboxMount(sc);
  }
  function render() {
    if (typeof document === "undefined") return;
    const y = window.scrollY;
    renderCards(); renderControls(); renderProgress(); renderPanel();
    window.scrollTo(0, y);
  }

  /* =====================================================================
     The animated sandbox (stage 4d). Inline SVG built with DOM APIs.
     ===================================================================== */
  const NS = "http://www.w3.org/2000/svg";
  const sv = (tag, attrs, text) => {
    const e = document.createElementNS(NS, tag);
    for (const k in attrs || {}) e.setAttribute(k, attrs[k]);
    if (text != null) e.textContent = text;
    return e;
  };
  const ICON = { mcp_tool: "🛠", http: "🗄", attacker: "☠", internet: "🌐", fs: "📁", shell: "⌨", secret: "🔑" };
  const TOOL_ORDER = ["lookup_order", "get_account_details", "retrieve_policy", "escalate_to_human", "process_refund", "schedule_payment"];
  const TYPE_SHORT = { secret_check: "token check", mcp_call: "tool call", http_fetch: "http", run_command: "shell", write_file: "file write", read_file: "file read", none: "content" };
  const POS = {}; // node id -> {x,y,w,h,cy,g,zone}
  const VB = { w: 1120, h: 620 };
  const ENTRY = { x: 112, y: 307 }, AGENT = { x: 476, y: 307 }, AGL = 400, AGR = 552, LBUS = 380, RBUS = 578, BX = 606, LANE = 548, ILANE = 942, RES_X = 146, RES_W = 216, TOOL_X = 702, TOOL_W = 216;
  const nrm = () => { try { return window.matchMedia("(prefers-reduced-motion: reduce)").matches; } catch (e) { return false; } };
  const instant = () => state.instant || nrm();
  let runId = 0;

  function stopAnim() { runId++; state.busy = false; const r = document.getElementById("sb-root"); if (r) r.dataset.busy = "0"; }

  function sandboxHTML() {
    const v = state.variant;
    const threats = [...new Set(Data.sbAttacks().map((a) => a.threat_id))].sort((a, b) => tnum(a) - tnum(b));
    const hosts = Data.node("internet").hosts.map((h) => h.label.replace(/^net:/, ""));
    return `<div id="sb-root" data-busy="0">` +
      `<p class="muted small">Pick a condition, then play an attack. The dot travels from the user turn through the agent toward its target; the OpenShell boundary stops what the policy denies. With no sandbox there is no boundary. The sandbox test assumes the agent is already fooled. Agent: <b>${esc(Data.vlabel(v))}</b>. ${esc(DEF_EXPL)}</p>` +
      `<div class="sb-controls"><div class="seg" role="group" aria-label="Policy condition">${CONDS.map((c) => `<button type="button" data-act="cond" data-v="${c}" aria-pressed="${state.cond === c}">${esc(CLABEL[c])}</button>`).join("")}</div>` +
      `<label class="sb-inl">Threat <select id="sb-threat" aria-label="Filter attacks by threat"><option value="">All threats</option>${threats.map((t) => `<option value="${esc(t)}" ${state.sbThreat === t ? "selected" : ""}>${esc(t)} ${esc(Data.tname(t))}</option>`).join("")}</select></label>` +
      `<label class="sb-inl">Speed <select id="sb-speed" aria-label="Animation speed">${[1, 2, 4, 16].map((x) => `<option value="${x}" ${state.speed === x ? "selected" : ""}>${x}×</option>`).join("")}</select></label>` +
      `<span class="sb-btns"><button type="button" class="primary" id="sb-story" data-act="play-story">▶ Guided demo</button><button type="button" class="secondary" id="sb-play" data-act="play-attack">Play attack</button><button type="button" class="secondary" id="sb-all" data-act="play-all">Play all</button><button type="button" class="secondary" id="sb-ben" data-act="play-benign">Play good actions</button><button type="button" class="quiet" data-act="stop">Stop</button></span></div>` +
      `<div class="sb-grid"><div class="sb-listwrap"><div class="sb-list" id="sb-list" role="listbox" aria-label="Attacks" tabindex="-1"></div><p class="legend"><span class="spill blocked">✓</span> blocked <span class="spill exposed">!</span> exposed <span class="spill gap">~</span> content only · NS no sandbox, OD OpenShell default, RC recommended</p></div>` +
      `<div class="sb-main"><p class="sb-capline" id="sb-capline"></p><p class="sb-standin" id="sb-standin" hidden></p><div class="sb-story" id="sb-story-bar" role="status" aria-live="polite" hidden></div><div id="sb-svgwrap"></div><p class="legend"><span class="dot blocked"></span>blocked / protected <span class="dot open"></span>exposed / open <span class="dot gen"></span>benign / allowed legitimate <span class="dot untested"></span>untested / gap</p>` +
      det("sb-hosts", `Internet hosts in the probe set (${hosts.length})`, `<div class="chiprow">${chips(hosts, "bad")}</div><p class="muted small">None of these hosts is declared, so a policy that allows egress only to declared hosts denies all of them.</p>`) + `</div></div>` +
      `<div class="sb-bottom"><div><div class="sb-counters" id="sb-counters" aria-live="polite"></div><p class="sb-explain" id="sb-explain"></p></div><div><h4 class="opt-head">OpenShell log</h4><pre class="sb-log" id="sb-log" role="log" aria-live="off" tabindex="0"></pre></div></div></div>`;
  }

  function padlock(closed, scale) {
    const g = sv("g", { class: `padlock ${closed ? "closed" : "open"}`, transform: `scale(${scale})` });
    g.append(sv("path", { class: "shackle", d: "M-5.5,-2 V-6.5 a5.5,5.5 0 0 1 11,0 V-2", transform: closed ? "" : "rotate(-34 -5.5 -2)" }),
      sv("rect", { class: "pbody", x: -9, y: -3, width: 18, height: 14, rx: 3 }), sv("circle", { class: "pkey", cx: 0, cy: 3.5, r: 2.2 }), sv("rect", { class: "pkey", x: -0.9, y: 4, width: 1.8, height: 4.5 }));
    return g;
  }
  function badge(cls, x, y, closed, r, k) {
    const b = sv("g", { class: cls, transform: `translate(${x},${y})` });
    const p = sv("g", { class: "pop" });
    p.append(sv("circle", { class: "halo", r }), padlock(closed, k));
    b.append(p);
    return b;
  }
  function buildSvg() {
    const root = sv("svg", { viewBox: `0 0 ${VB.w} ${VB.h}`, class: "sb-svg", role: "img", "aria-label": "Diagram of the OpenShell sandbox: attacker turn, agent, resources inside the sandbox, and tool server, APIs and internet outside" });
    Object.keys(POS).forEach((k) => delete POS[k]);
    const wires = sv("g", { class: "sb-wires" });
    const wire = (d) => wires.append(sv("path", { class: "sb-wire", d }));
    // sandbox
    const sb = sv("g", { class: "sb-sandbox" });
    sb.append(sv("rect", { class: "sb-zone", x: 126, y: 36, width: 480, height: 554, rx: 22 }), sv("rect", { class: "sb-boundary", id: "sb-boundary", x: 126, y: 36, width: 480, height: 554, rx: 22 }));
    sb.append(sv("rect", { class: "sb-pilltag", x: 150, y: 22, width: 176, height: 28, rx: 14 }), sv("text", { id: "sb-t-top", class: "sb-t1", x: 238, y: 41, "text-anchor": "middle" }, "OpenShell sandbox"));
    sb.append(sv("rect", { class: "sb-pilltag", x: 196, y: 576, width: 360, height: 28, rx: 14 }), sv("text", { id: "sb-t-bot", class: "sb-t2", x: 376, y: 595, "text-anchor": "middle" }, "OpenShell proxy · Landlock · process"));
    wire(`M${ENTRY.x},${ENTRY.y} H${AGL}`);
    wire(`M${AGL},${AGENT.y} H${LBUS} M${LBUS},115 V499`);
    wire(`M${AGR},${AGENT.y} H${RBUS} M${RBUS},112 V${LANE} H${ILANE} V305`);
    // entry + chip
    const entry = sv("g", { transform: "translate(6,242)" });
    entry.append(sv("rect", { class: "sb-box", width: 106, height: 130, rx: 16 }), sv("text", { class: "sb-big", x: 53, y: 56, "text-anchor": "middle" }, "💬"), sv("text", { class: "sb-t1", x: 53, y: 88, "text-anchor": "middle" }, "Attacker /"), sv("text", { class: "sb-t1", x: 53, y: 106, "text-anchor": "middle" }, "user turn"));
    const chipG = sv("g", { class: "sb-attackchip", transform: "translate(2,386)" });
    chipG.append(sv("rect", { width: 114, height: 26, rx: 13 }), sv("text", { id: "sb-chip", x: 57, y: 18, "text-anchor": "middle" }, "—"));
    // agent
    const agent = sv("g", { id: "sb-agent", class: "sb-agentg", transform: `translate(${AGL},247)` });
    agent.append(sv("rect", { class: "sb-box agent", width: 152, height: 120, rx: 16 }), sv("text", { class: "sb-big", x: 76, y: 48, "text-anchor": "middle" }, "🤖"), sv("text", { class: "sb-t1", x: 76, y: 78, "text-anchor": "middle" }, "Klarna support"), sv("text", { class: "sb-t1", x: 76, y: 97, "text-anchor": "middle" }, "agent (LLM)"), sv("text", { id: "sb-agent-cap", class: "sb-agentcap", x: 76, y: 140, "text-anchor": "middle" }, ""), badge("sb-lock", 136, 16, true, 15, 0.88));
    root.append(wires, sb, entry, chipG, agent);

    const mk = (n, x, y, w, h, title, sub) => {
      const three = h > 60 && !!sub;
      const g = sv("g", { class: "sb-node", transform: `translate(${x},${y})`, "data-node": n.id, "data-g": n.group });
      g.append(sv("title", {}, `${n.label}${n.detail ? " — " + n.detail : ""}`), sv("rect", { width: w, height: h, rx: 10 }), sv("text", { class: "ic", x: 11, y: h / 2 + 7 }, ICON[n.group] || "•"));
      g.append(sv("text", { class: "t1", x: 38, y: three ? 24 : h / 2 - 2 }, title));
      if (three) g.append(sv("text", { class: "sub", x: 38, y: 42 }, sub));
      g.append(sv("text", { class: "sub nd", x: 38, y: three ? 61 : h / 2 + 14 }, ""));
      const by0 = three ? h - 20 : h / 2;
      g.append(badge("sb-lock", w - 24, by0, true, 15, 0.88), badge("sb-open", w - 24, by0, false, 15, 0.88));
      POS[n.id] = { x, y, w, h, cy: y + h / 2, g };
      return g;
    };
    const inS = [["workspace", "Workspace files", "/sandbox/workdir"], ["outside_workspace", "Files outside workspace", "/etc, /home, …"], ["shell", "Shell (run_command)", "local commands"], ["tool_token", "Tool token (secret)", "injected credential"]];
    const inY = { workspace: 78, outside_workspace: 164, shell: 376, tool_token: 462 };
    inS.forEach(([id, t, sub]) => { const n = Data.node(id); if (n) { root.append(mk(n, RES_X, inY[id], RES_W, 74, t, sub)); wire(`M${LBUS},${inY[id] + 37} H${RES_X + RES_W}`); } });
    // tool server
    const tg = sv("g", {});
    tg.append(sv("rect", { class: "sb-zone out", x: TOOL_X - 12, y: 52, width: TOOL_W + 24, height: 328, rx: 14 }), sv("text", { class: "sb-t2", x: TOOL_X + TOOL_W / 2, y: 74, "text-anchor": "middle" }, "Klarna tool server · MCP :8765"));
    root.append(tg);
    TOOL_ORDER.forEach((name, i) => { const n = Data.node("mcp:" + name); if (n) { root.append(mk(n, TOOL_X, 88 + i * 48, TOOL_W, 40, n.label, "")); wire(`M${RBUS},${88 + i * 48 + 20} H${TOOL_X}`); } });
    [["merchant_api", 408, "Merchant API :9101"], ["attacker_sink", 468, "Attacker sink :9102"]].forEach(([id, y, t]) => { const n = Data.node(id); if (n) { root.append(mk(n, TOOL_X, y, TOOL_W, 48, t, "")); wire(`M${RBUS},${y + 24} H${TOOL_X}`); } });
    // internet: one node
    const inet = Data.node("internet"), iw = 158, ih = 130, ix = 954, iy = 240;
    const ig = sv("g", { class: "sb-node", transform: `translate(${ix},${iy})`, "data-node": "internet", "data-g": "internet" });
    ig.append(sv("title", {}, `${inet.label}: ${inet.detail}`), sv("rect", { width: iw, height: ih, rx: 12 }), sv("text", { class: "ic big", x: iw / 2, y: 46, "text-anchor": "middle" }, "🌐"),
      sv("text", { class: "t1", x: iw / 2, y: 72, "text-anchor": "middle" }, "Internet —"), sv("text", { class: "t1", x: iw / 2, y: 90, "text-anchor": "middle" }, `unknown hosts (${inet.hosts.length})`),
      sv("text", { class: "sub nd", x: iw / 2, y: 113, "text-anchor": "middle" }, ""), badge("sb-lock", iw - 20, 20, true, 15, 0.88), badge("sb-open", iw - 20, 20, false, 15, 0.88));
    POS.internet = { x: ix, y: iy, w: iw, h: ih, cy: iy + ih / 2, g: ig };
    root.append(ig);
    root.append(sv("text", { class: "sb-sub", x: 640, y: LANE - 9 }, "egress via proxy (DNS / CONNECT)"));
    root.append(sv("g", { id: "sb-fx" }));
    root.append(sv("g", { id: "sb-dot", class: "sb-dot", transform: "translate(-50,-50)", style: "display:none" }));
    root.querySelector("#sb-dot").append(sv("circle", { r: 12 }), sv("circle", { r: 6.5 }));
    return root;
  }

  function ndText(id) { return state.cond === NOSB || state.cond === DEF ? "" : Data.nodeState(state.variant, state.cond, id).declared ? "✓ declared" : "not declared"; }
  function resetFb() {
    Object.keys(POS).forEach((id) => { const g = POS[id].g; delete g.dataset.fb; g.classList.remove("reach", "benign-ok", "broken"); g.querySelector(".nd").textContent = ndText(id); });
  }
  function nodeFb(id, kind, text) { const g = POS[id].g; g.dataset.fb = kind; g.querySelector(".nd").textContent = text; }
  function sbRefreshNodes() {
    const v = state.variant, c = state.cond, svg = document.querySelector(".sb-svg");
    if (!svg) return;
    svg.setAttribute("class", `sb-svg cond-${c}`);
    Object.keys(POS).forEach((id) => {
      const st = Data.nodeState(v, c, id), g = POS[id].g;
      g.classList.toggle("locked", st.locked || c === DEF);
      g.classList.toggle("exposed", st.exposed && !st.locked);
      g.classList.toggle("untested", !st.tested);
      g.classList.toggle("declared", st.declared);
      g.classList.toggle("undeclared", !st.declared);
    });
    resetFb();
    const ag = $("sb-agent");
    if (ag) { ag.classList.toggle("locked", c === DEF); $("sb-agent-cap").textContent = c === DEF ? "agent can't start under the default policy" : ""; }
    const cl = $("sb-capline");
    if (cl) cl.textContent = c === DEF ? `Policy: ${CLABEL[c]} (no policy supplied) · every node locked, including the agent · 🔒 = denied by OpenShell` : c === NOSB ? `${CLABEL[c]}: plain container, no OpenShell · open lock = reached, nothing stopped it` : `Policy: ${CLABEL[c]} · 🔒 = denied by OpenShell · ✓ = declared capability`;
    const t1 = $("sb-t-top"), t2 = $("sb-t-bot");
    if (t1) t1.textContent = c === NOSB ? "no sandbox" : "OpenShell sandbox";
    if (t2) t2.textContent = c === NOSB ? "plain container · OS file permissions only" : c === DEF ? "OpenShell default policy · nothing allowed" : "OpenShell proxy · Landlock · process";
    const b = $("sb-boundary");
    if (b) b.setAttribute("class", `sb-boundary ${c}`);
  }

  /* counters */
  function zeroCnt() { return { exposed: 0, blocked: 0, gap: 0, bok: 0, bbroken: 0, n: 0 }; }
  function sbRefreshCounters() {
    const el = $("sb-counters");
    if (!el) return;
    const v = state.variant, c = state.cond, s = Data.sum(v, c);
    const t = state.live && state.cnt ? state.cnt : { exposed: s.attacks_exposed, blocked: s.attacks_blocked, gap: s.gap, bok: s.benign_ok, bbroken: s.benign_broken };
    const total = Data.sbAttacks().length;
    el.innerHTML = `<div class="ctr exposed"><strong>${t.exposed}</strong><span>attacks exposed</span></div><div class="ctr blocked"><strong>${t.blocked}</strong><span>blocked</span></div><div class="ctr gap"><strong>${t.gap}</strong><span>content only → guardrails</span></div><div class="ctr ben"><strong>${t.bok}</strong><span>good actions allowed</span></div><div class="ctr ${t.bbroken ? "exposed" : "blocked"}"><strong>${t.bbroken}</strong><span>good actions broken</span></div>` +
      `<p class="legend">${state.live ? `Live: ${CLABEL[c]} · ${esc(String(state.counted.size))} of ${total} attacks played` : `Totals for ${esc(CLABEL[c])} (${total} attacks, ${Data.benign(v, c).length} good actions). Play to count live.`}</p>`;
  }
  function countAttack(sid) {
    if (state.counted.has(sid)) return;
    state.counted.add(sid);
    const o = Data.outcome(state.variant, state.cond, sid);
    if (o === "exposed") state.cnt.exposed++; else if (o === "blocked") state.cnt.blocked++; else if (o === "gap") state.cnt.gap++;
    sbRefreshCounters();
  }
  function startLive(fresh) {
    if (!state.live || fresh) { state.live = true; state.cnt = zeroCnt(); state.counted = new Set(); }
  }

  /* attack list */
  function filteredAttacks() { return Data.sbAttacks().filter((a) => !state.sbThreat || a.threat_id === state.sbThreat); }
  function sbRenderList() {
    const el = $("sb-list");
    if (!el) return;
    const v = state.variant, sym = { exposed: "!", blocked: "✓", gap: "~", none: "–" };
    el.innerHTML = filteredAttacks().map((a, i) => {
      const ps = Data.byScen(v, NOSB)[a.scenario_id] || [];
      const lbl = a.check ? SNAP.check.title : ps.length ? ps[0].action.label : "no probe";
      const extra = ps.length > 1 ? ` +${ps.length - 1}` : "";
      return `<button type="button" role="option" class="acard ${a.scenario_id === state.attack ? "on" : ""}" data-act="pick" data-sid="${esc(a.scenario_id)}" aria-selected="${a.scenario_id === state.attack}" title="${esc(a.standin ? SNAP.standin.title + " (" + SNAP.standin.label + ")" : a.check ? SNAP.check.title + " (" + SNAP.check.label + ")" : (Data.scenario(a.scenario_id) || {}).title)}"><span class="ac-top"><span class="chip ess">${esc(a.threat_id)}</span>${a.standin ? '<span class="chip bad">stand-in</span>' : a.check ? '<span class="chip chk">check</span>' : ""}<span class="ac-n">#${i + 1}</span><span class="ac-pills">${CONDS.map((c) => { const o = Data.outcome(v, c, a.scenario_id); return `<span class="spill ${o}" title="${esc(CLABEL[c])}: ${o === "exposed" ? "exposed" : o === "blocked" ? "blocked" : "content only"}" aria-label="${esc(CLABEL[c])}: ${o}">${CSHORT[c]} ${sym[o]}</span>`; }).join("")}</span></span><span class="ac-label">${esc(clip(lbl, 100))}${esc(extra)}</span></button>`;
    }).join("") || '<p class="muted">No attacks for this threat.</p>';
  }
  function sbMarkSelected() {
    document.querySelectorAll(".sb-list .acard").forEach((b) => { const on = b.dataset.sid === state.attack; b.classList.toggle("on", on); b.setAttribute("aria-selected", on); });
    const a = curSbAttack(), ps = Data.byScen(state.variant, state.cond)[a.scenario_id] || [];
    const sbs = $("sb-standin");
    if (sbs) { sbs.hidden = !(a.standin || a.check); sbs.textContent = a.standin ? `${SNAP.standin.title}: ${SNAP.standin.label} (issue #12 acceptance scenario: file read).` : a.check ? `${SNAP.check.title} (${SNAP.check.label}): a check of the deployment, not a recorded attack.` : ""; }
    const chipEl = $("sb-chip");
    if (chipEl) chipEl.textContent = `${a.threat_id} · ${TYPE_SHORT[ps[0] ? ps[0].action.type : ""] || "attack"}`;
    $("sb-explain") && ($("sb-explain").textContent = explain(a.scenario_id));
  }

  /* explanation */
  function explainProbe(p) {
    const c = state.cond, tgt = nodeLabel(p.action.resource), eff = p.effect;
    const tool = p.action.resource && p.action.resource.startsWith("mcp:") ? p.action.resource.slice(4) : null;
    if (p.status === "unenforced") return `No sandbox: nothing stops ${p.action.label}. The host doesn't exist, so the request failed, but no control stopped it: a real host would be reached.`;
    if (p.status === "gap" || !p.action.resource) return "Content only: no sandbox action to stop. The harm is in the reply text, so this is routed to guardrails.";
    const ef = Data.effect(eff), rl = ruleFor(eff);
    if (p.status === "denied") {
      if (c === DEF) return p.kind === "benign" ? "OpenShell default: this good action is broken too. The default doesn't allow the agent's program folder (/opt/app-root), so the agent can't start." : "OpenShell default: denied, but only because the agent can't start under the default policy. It blocks everything, including the agent.";
      if (c === NOSB) return "No sandbox: stopped only by the container user's file permissions, not by a sandbox policy.";
      if (eff === "secret_disclosure") return "Recommended: the agent sees only a placeholder; OpenShell injects the real token, so it can't be read or leaked.";
      if (tool && rl && rl.id === "mcp") return `Recommended: rule "${rl.name}" lists only the declared tools; ${tool} is not declared → denied.`;
      if (rl) return `Recommended: rule "${rl.name}" (${rl.sees.toLowerCase()}) → denied (${p.denial_kind || "policy"}).`;
      return `Recommended: ${tgt} is not in the policy → denied (${p.denial_kind || "policy"}).`;
    }
    if (p.kind === "benign") return `Good action on a declared capability (${tgt}): allowed, as it should be.`;
    if (eff === "secret_disclosure") return `${CLABEL[c]}: the real tool token is readable by the agent process, so injected text could make it leak.`;
    if (c === NOSB) return `${CLABEL[c].replace(" (before)", "")}: nothing stops ${p.action.label}; it reaches ${tgt}.`;
    if (ef && ef.gap) return `Allowed: ${ef.desc.toLowerCase()}. The sandbox cannot tell this from legitimate use of a declared capability → needs guardrails.`;
    return `Allowed: ${tgt} is part of the use case, so the policy allows it and the sandbox lets ${p.action.label} through. Harmful use of an allowed capability → guardrails.`;
  }
  function explain(sid) {
    const ps = Data.byScen(state.variant, state.cond)[sid] || [];
    if (!ps.length) return "No probe for this attack.";
    const seen = [];
    ps.forEach((p) => { const t = explainProbe(p); if (!seen.includes(t)) seen.push(t); });
    return seen.slice(0, 2).join(" ");
  }

  /* log */
  function logLine(text, cls) {
    state.log.push({ t: text, c: cls || "" });
    if (state.log.length > 40) state.log.shift();
    sbRenderLog();
  }
  function sbRenderLog() {
    const el = $("sb-log");
    if (!el) return;
    el.innerHTML = state.log.length ? state.log.map((l) => `<span class="${esc(l.c)}">${esc(l.t)}</span>`).join("\n") : '<span class="muted">Play an attack to see what the sandbox logs.</span>';
    el.scrollTop = el.scrollHeight;
  }

  /* geometry */
  const tgtId = (r) => (r && r.startsWith("net:") ? "internet" : r);
  function routeFor(p) {
    const r = tgtId(p.action && p.action.resource), n = r && POS[r];
    const pts = [[ENTRY.x, ENTRY.y], [AGENT.x, AGENT.y]];
    if (!n) return { pts, b: -1, target: null };
    const nd = Data.node(r);
    if (nd.group === "fs" || nd.group === "shell" || nd.group === "secret") {
      pts.push([AGL, AGENT.y], [LBUS, AGENT.y], [LBUS, n.cy], [n.x + n.w, n.cy]);
      return { pts, b: -1, target: r };
    }
    if (nd.group === "internet") {
      pts.push([RBUS, AGENT.y], [RBUS, LANE], [BX, LANE], [ILANE, LANE], [ILANE, n.cy], [n.x, n.cy]);
      return { pts, b: 4, target: r };
    }
    pts.push([RBUS, AGENT.y], [RBUS, n.cy], [BX, n.cy], [n.x, n.cy]);
    return { pts, b: 4, target: r };
  }
  function follow(dot, pts, i0, i1, ms, id) {
    const seg = []; let len = 0;
    for (let i = i0; i < i1; i++) { const d = Math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]); seg.push(d); len += d; }
    const at = (k) => { let d = k * len; for (let i = 0; i < seg.length; i++) { if (d <= seg[i] || i === seg.length - 1) { const f = seg[i] ? Math.min(1, d / seg[i]) : 1, a = pts[i0 + i], b = pts[i0 + i + 1]; return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f]; } d -= seg[i]; } return pts[i1]; };
    const put = (xy) => dot.setAttribute("transform", `translate(${xy[0].toFixed(1)},${xy[1].toFixed(1)})`);
    return new Promise((res) => {
      if (instant() || !len) { put(pts[i1]); return res(id === runId); }
      const t0 = performance.now(), dur = ms / state.speed;
      (function tick() {
        if (id !== runId) return res(false);
        const k = Math.min(1, (performance.now() - t0) / dur);
        put(at(k));
        if (k >= 1) res(true); else setTimeout(tick, 16);
      })();
    });
  }
  const pause = (ms, id) => (instant() ? Promise.resolve(id === runId) : sleep(ms / state.speed).then(() => id === runId));
  function fxTag(x, y, text, cls, w) {
    const fx = $("sb-fx"), g = sv("g", { class: `sb-tag ${cls}`, transform: `translate(${x},${y})` });
    const inner = sv("g", { class: "pop" });
    inner.append(sv("rect", { x: -w / 2, y: -11, width: w, height: 22, rx: 11 }), sv("text", { y: 4.5, "text-anchor": "middle" }, text));
    g.append(inner); fx.append(g); return g;
  }
  function fxLock(x, y) {
    const fx = $("sb-fx"), g = sv("g", { class: "sb-flock", transform: `translate(${x},${y})` });
    const inner = sv("g", { class: "pop" });
    inner.append(sv("circle", { class: "halo", r: 23 }), padlock(true, 1.3));
    g.append(inner); fx.append(g);
  }
  function clearFx() { const fx = $("sb-fx"); if (fx) fx.textContent = ""; resetFb(); }
  const hostOf = (p) => String(p.action.resource || "").replace(/^net:/, "");

  async function animProbe(p, id) {
    const dot = $("sb-dot");
    if (!dot) return;
    clearFx();
    const harmful = p.kind === "harmful", col = harmful ? "attack" : "benign";
    dot.setAttribute("class", `sb-dot ${col}`); dot.style.display = "";
    dot.setAttribute("transform", `translate(${ENTRY.x},${ENTRY.y})`);
    const { pts, b, target } = routeFor(p);
    logLine(`> ${p.kind}: ${p.action.label}`, "dim");
    if (!(await follow(dot, pts, 0, 1, 520, id))) return;
    if (state.cond === DEF && p.status === "denied") {
      dot.style.display = "none";
      $("sb-agent").classList.add("locked");
      fxLock(AGENT.x, 226); fxTag(AGENT.x, 414, harmful ? "AGENT CAN'T START" : "BROKEN", harmful ? "deny" : "broken", 150);
      if (!harmful && target && POS[target]) { POS[target].g.classList.add("broken"); nodeFb(target, "broken", "BROKEN"); }
      logLine(p.denial_line || `DENIED ${p.action.label}`, harmful ? "deny" : "broken");
      await pause(300, id);
      return;
    }
    if (!target || p.status === "gap") {
      dot.setAttribute("class", "sb-dot gap");
      fxTag(AGENT.x, 208, "content only → guardrails", "gap", 206);
      if (!(await pause(500, id))) return;
      await follow(dot, [[AGENT.x, AGENT.y], [ENTRY.x, ENTRY.y]], 0, 1, 500, id);
      dot.style.display = "none";
      logLine("NO SANDBOX ACTION (content only) → guardrails", "gap");
      return;
    }
    const last = pts.length - 1, n = POS[target], net = target === "internet";
    const hostTag = (cls) => { if (net) fxTag(n.x + n.w / 2, n.y + n.h + 18, clip(hostOf(p), 22), `host ${cls}`, n.w - 4); };
    if (p.status === "denied") {
      const stop = b >= 0 ? b : last;
      if (!(await follow(dot, pts, 1, stop, 1100, id))) return;
      dot.style.display = "none";
      const [x, y] = pts[stop];
      if (b >= 0) { fxLock(x, y); fxTag(x + 61, y, "DENIED", "deny", 62); } else nodeFb(target, "deny", "DENIED");
      n.g.classList.add("locked");
      hostTag("deny");
      logLine(p.denial_line || `DENIED ${p.action.label}`, "deny");
      await pause(300, id);
      return;
    }
    if (!(await follow(dot, pts, 1, last, 1300, id))) return;
    dot.style.display = "none";
    hostTag(harmful ? "reach" : "ok");
    if (harmful && p.status === "unenforced") { n.g.classList.add("reach"); nodeFb(target, "reach", "NOT STOPPED"); logLine(`UNENFORCED ${p.action.label}: ${UNENF}`, "reach"); }
    else if (harmful) { n.g.classList.add("reach"); nodeFb(target, "reach", "REACHED"); logLine(`ALLOWED ${p.action.label}`, "reach"); }
    else { n.g.classList.add("benign-ok"); nodeFb(target, "ok", "✓ OK"); logLine(`ALLOWED ${p.action.label}`, "ok"); }
    await pause(300, id);
  }

  async function playAttack(sid, id, single) {
    state.attack = sid;
    sbMarkSelected();
    const el = document.querySelector(`.sb-list .acard[data-sid="${CSS.escape(sid)}"]`), lst = $("sb-list");
    if (el && lst) lst.scrollTop = Math.max(0, el.offsetTop - lst.offsetTop - 60);
    for (const p of Data.byScen(state.variant, state.cond)[sid] || []) { if (id !== runId) return; await animProbe(p, id); }
    if (id !== runId) return;
    countAttack(sid);
  }
  function busy(on) { state.busy = on; const r = $("sb-root"); if (r) r.dataset.busy = on ? "1" : "0"; document.querySelectorAll("#sb-play,#sb-all,#sb-ben,#sb-story").forEach((b) => (b.disabled = on)); }
  async function doPlay(kind) {
    if (state.busy) return;
    const id = ++runId; busy(true);
    try {
      if (kind === "attack") { startLive(false); sbRefreshCounters(); await playAttack(curSbAttack().scenario_id, id, true); }
      else if (kind === "all") {
        startLive(true); sbRefreshCounters();
        for (const a of filteredAttacks()) { if (id !== runId) break; await playAttack(a.scenario_id, id, false); }
      } else {
        startLive(false); sbRefreshCounters();
        $("sb-explain").textContent = "Good actions are the use case's declared capabilities. The recommended policy must allow all of them.";
        for (const p of Data.benign(state.variant, state.cond)) {
          if (id !== runId) break;
          await animProbe(p, id);
          if (id !== runId) break;
          if (p.status === "allowed") state.cnt.bok++; else state.cnt.bbroken++;
          sbRefreshCounters();
        }
      }
    } finally { if (id === runId) busy(false); }
  }
  function sandboxMount(scroll) {
    const wrap = $("sb-svgwrap");
    if (!wrap) return;
    wrap.append(buildSvg());
    if (!state.attack || !Data.sbAttacks().some((a) => a.scenario_id === state.attack)) state.attack = (filteredAttacks()[0] || Data.sbAttacks()[0]).scenario_id;
    sbRenderList(); sbRefreshNodes(); sbRefreshCounters(); sbMarkSelected(); sbRenderLog();
    const l = $("sb-list"); if (l && scroll) l.scrollTop = scroll;
    const sel = document.querySelector(".sb-list .acard.on");
    if (sel && l && !scroll) l.scrollTop = Math.max(0, sel.offsetTop - l.offsetTop - 60);
  }
  function sbSwitchCond(c, keep) {
    if (!keep) { stopAnim(); busy(false); }
    state.cond = c; state.live = false; state.log = [];
    document.querySelectorAll('[data-act="cond"]').forEach((b) => b.setAttribute("aria-pressed", b.dataset.v === c));
    clearFx(); const d = $("sb-dot"); if (d) d.style.display = "none";
    sbRefreshNodes(); sbRefreshCounters(); sbMarkSelected(); sbRenderLog();
  }
  /* guided demo */
  function storySteps() {
    const A = Data.sbAttacks(), V = "refunds", L = "least-privilege";
    const bs = (c, a) => Data.byScen(V, c)[a.scenario_id] || [];
    const find = (pred) => A.find(pred);
    const exf = find((a) => a.threat_id === "T2" && Data.outcome(V, L, a.scenario_id) === "blocked" && bs(NOSB, a).some((p) => p.action.resource === "attacker_sink" && p.status === "allowed"))
      || find((a) => !a.standin && !a.check && Data.outcome(V, NOSB, a.scenario_id) === "exposed" && Data.outcome(V, L, a.scenario_id) === "blocked");
    const standin = find((a) => a.standin), chk = find((a) => a.check);
    const ref = find((a) => a.threat_id === "T3" && !a.check && bs(L, a).some((p) => p.action.resource === "mcp:process_refund" && p.status === "allowed"));
    const ben = Data.benign(V, L), ok = ben.filter((b) => b.status === "allowed").length;
    const bd = Data.benign(V, DEF), okd = bd.filter((b) => b.status === "allowed").length;
    return [
      { cs: [NOSB, L], a: exf, caps: ["Injected exfiltration to the attacker's server: nothing stops it.", "Same attack: denied at the boundary (only declared hosts, only the declared program)."] },
      { cs: [DEF], a: exf, benign: true, caps: [`OpenShell default: everything denied, including the agent's own good actions (${okd}/${bd.length}).`] },
      { cs: [NOSB, L], a: standin, caps: ["Injected request to read an unrelated customer file (hand-written stand-in, not from ASAGO run 4): the file is read.", "Same request: denied by the filesystem policy."] },
      { cs: [NOSB, L], a: chk, caps: ["Deployment check: the real tool token is readable by the agent.", "Same check: the agent sees only a placeholder."] },
      { cs: [L], a: ref, caps: ["A harmful refund uses a declared tool, so it stays allowed: this goes to guardrails."] },
      { cs: [L], benign: true, caps: [`Good actions still work: ${ok}/${ben.length} allowed.`] },
    ];
  }
  async function doStory(upto) {
    if (state.busy) return;
    if (!state.speedUser) { state.speed = 1; const sp = $("sb-speed"); if (sp) sp.value = "1"; }
    const id = ++runId; busy(true);
    const bar = $("sb-story-bar"), steps = storySteps().slice(0, upto == null ? undefined : upto + 1);
    const hold = () => (instant() ? Promise.resolve(id === runId) : sleep(Math.max(1500, 3000 / state.speed)).then(() => id === runId));
    state.sbThreat = ""; const ts = $("sb-threat"); if (ts) ts.value = "";
    try {
      for (let k = 0; k < steps.length; k++) {
        const st = steps[k];
        for (let m = 0; m < st.cs.length; m++) {
          if (id !== runId) return;
          if (st.cs[m] !== state.cond) sbSwitchCond(st.cs[m], true);
          else { clearFx(); state.live = false; }
          bar.hidden = false; bar.textContent = `Step ${k + 1} of ${steps.length} · ${CLABEL[state.cond]} · ${st.caps[m]}`;
          startLive(true); sbRenderList(); sbRefreshCounters();
          if (st.a) await playAttack(st.a.scenario_id, id, true);
          if (id !== runId) return;
          if (st.benign) {
            $("sb-explain").textContent = "Good actions are the use case's declared capabilities. The recommended policy must allow all of them.";
            for (const p of Data.benign(state.variant, state.cond)) { if (id !== runId) return; await animProbe(p, id); if (id !== runId) return; if (p.status === "allowed") state.cnt.bok++; else state.cnt.bbroken++; sbRefreshCounters(); }
          }
          if (id !== runId) return;
          if (k < steps.length - 1 || m < st.cs.length - 1 || upto == null) if (!(await hold())) return;
        }
      }
      if (upto == null) bar.textContent = "Guided demo complete. Pick any attack and condition to explore.";
    } finally { if (id === runId) busy(false); }
  }

  /* ---------- theme, init ---------- */
  function setTheme(dark) {
    document.documentElement.classList.toggle("pf-v6-theme-dark", dark);
    $("theme").textContent = dark ? "Light mode" : "Dark mode";
    try { localStorage.setItem("asago-os-theme", dark ? "dark" : "light"); } catch (e) { /* ignore */ }
  }
  function loadContext() {
    const m = SNAP.meta || {}, md = m.models || {};
    $("uc-name").textContent = (SNAP.use_case || {}).name || "";
    $("replay-label").textContent = `FS-ISAC GenAI policy · replaying a recorded run (${String(m.generated || "").slice(0, 10)}) · no live inference`;
    $("footer-replay").textContent = `Run ${m.run || ""} · local demo, not published`;
    $("ctx-policy").textContent = PM().policy_name || "n/a";
    $("ctx-models").textContent = [md.generators, md.tagger && "tagger: " + String(md.tagger).split(" (")[0]].filter(Boolean).join(" · ") || "n/a";
    $("ctx-os").textContent = `OpenShell ${m.openshell || "n/a"}`;
    $("ctx-run").textContent = m.run || "n/a";
    $("desc-title").textContent = (SNAP.use_case || {}).name || "";
    $("desc-body").textContent = (SNAP.use_case || {}).text || "";
    $("ctx-agent").textContent = Data.vlabel(state.variant);
  }

  function init() {
    let saved = null; try { saved = localStorage.getItem("asago-os-theme"); } catch (e) { /* ignore */ }
    const q = new URLSearchParams((location.hash || "").replace(/^#/, ""));
    setTheme(q.get("theme") ? q.get("theme") !== "light" : saved !== "light");
    if (CONDS.includes(normC(q.get("cond")))) state.cond = normC(q.get("cond"));
    if (q.get("speed") && [1, 2, 4, 16].includes(+q.get("speed"))) { state.speed = +q.get("speed"); state.speedUser = true; }
    state.instant = q.get("instant") === "1";
    loadContext();

    document.addEventListener("click", (e) => {
      const t = e.target.closest("[data-act]");
      if (!t || t.disabled) return;
      const act = t.dataset.act, i = +t.dataset.i;
      if (act === "sel") { state.sel = i; render(); }
      else if (act === "run") startStage(i);
      else if (act === "sfilter") { state.sfilter = state.sfilter === t.dataset.v ? "" : t.dataset.v; state.sshown = 20; render(); }
      else if (act === "more") { state.sshown += 40; render(); }
      else if (act === "risk") { state.risk = state.risk === t.dataset.id ? null : t.dataset.id; render(); }
      else if (act === "scn") { state.scn = state.scn === t.dataset.id ? null : t.dataset.id; render(); }
      else if (act === "tab") { state.tab[state.sel] = i; render(); }
      else if (act === "attack") { state.attack = t.dataset.sid; render(); }
      else if (act === "pfilter") { state.pf = t.dataset.v; render(); }
      else if (act === "gfilter") { state.gf = t.dataset.v; render(); }
      else if (act === "cond") sbSwitchCond(t.dataset.v);
      else if (act === "pick") { if (!state.busy) { state.attack = t.dataset.sid; sbMarkSelected(); } }
      else if (act === "play-attack") doPlay("attack");
      else if (act === "play-all") doPlay("all");
      else if (act === "play-benign") doPlay("benign");
      else if (act === "play-story") doStory();
      else if (act === "stop") { stopAnim(); busy(false); const d = $("sb-dot"); if (d) d.style.display = "none"; }
    });
    document.addEventListener("change", (e) => {
      const id = e.target.id;
      if (id === "sf") { state.sfilter = e.target.value; state.sshown = 20; render(); }
      else if (id === "sb-threat") { state.sbThreat = e.target.value; const f = filteredAttacks(); if (f.length && !f.some((a) => a.scenario_id === state.attack)) state.attack = f[0].scenario_id; sbRenderList(); sbMarkSelected(); }
      else if (id === "sb-speed") { state.speed = +e.target.value; state.speedUser = true; }
    });
    document.addEventListener("toggle", (e) => {
      const d = e.target;
      if (d && d.matches && d.matches("details.deep[data-k]")) { d.open ? state.open.add(d.dataset.k) : state.open.delete(d.dataset.k); }
    }, true);
    $("run-next").addEventListener("click", () => startStage(state.done));
    document.addEventListener("keydown", (e) => { if ((e.key === "Enter" || e.key === " ") && e.target.matches && e.target.matches("tr[data-act]")) { e.preventDefault(); e.target.click(); } });
    $("run-all").addEventListener("click", () => { if (state.running === -1 && state.done < STAGES.length) { state.runAll = true; startStage(state.done); } });
    $("reset").addEventListener("click", reset);
    $("theme").addEventListener("click", () => setTheme(!document.documentElement.classList.contains("pf-v6-theme-dark")));
    $("desc-open").addEventListener("click", () => { const d = $("desc-dialog"); if (d.showModal) d.showModal(); else d.setAttribute("open", ""); });
    $("desc-close").addEventListener("click", () => $("desc-dialog").close());

    // deep link: #stage=4&tab=probe ...
    const num = (k) => (q.get(k) != null && !isNaN(+q.get(k)) ? +q.get(k) : null);
    if (num("stage") != null) {
      const si = Math.max(1, Math.min(4, num("stage"))) - 1;
      state.done = STAGES.length; state.sel = si;
      const ti = STAGES[si].subs.findIndex((x) => x.key === q.get("tab"));
      state.tab[si] = ti >= 0 ? ti : 0;
    }
    if (q.get("threat")) state.sbThreat = q.get("threat");
    if (num("attack") != null) { const a = filteredAttacks()[num("attack")]; if (a) state.attack = a.scenario_id; }
    if (q.get("standin") === "1") { const a = Data.sbAttacks().find((x) => x.standin); if (a) state.attack = a.scenario_id; }
    if (q.get("check") === "1") { const a = Data.sbAttacks().find((x) => x.check); if (a) state.attack = a.scenario_id; }
    render();
    const play = q.get("play");
    if (play && state.sel === 3) setTimeout(() => (play === "story" ? doStory(num("upto")) : doPlay(play === "all" || play === "benign" ? play : "attack")), 50);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
