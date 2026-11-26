# run4-fsisac-allthreats-nocap

- **Timestamp dir:** 20261120T161601_b91bf3c8f4e5a24cee37af7fcc179b4f
- **Date:** Nov 20 2026
- **Generation mode:** exhaustive (`--generation-mode exhaustive`), no per-pattern cap

## Generator flags
- No `--max-scenarios-per-pattern` cap (recorded as null in run-manifest.yaml)
- No threats filter: full threat catalog (`--threats-path` = bundled owasp-agentic-threats taxonomy, T1-T17)
- `--risk-extraction <home>/Documents/TrustyAI/risk-selected_20261110_171524/fs-isac/risk-extraction.json`
- `--sssom <home>/Documents/TrustyAI/asago-examples-pr35/asago-scenario-generator/inputs/mappings/risk_to_category.sssom.tsv` (as recorded in run-manifest.yaml)
- `--profile manifests/klarna-agent-profile.yaml`
- `--qualification-facts manifests/klarna-agent-qualification-facts.yaml`
- `--max-scenario-techniques 1`
- Model: gemma-4-26b

## Results
- **Funnel:** qualified 115, admitted 71, quarantined 44 (22 projection_infeasible, 20 semantic_draft_invalid, 2 admission_exception)
- **Scenarios by threat_id:** T2: 42, T3: 8, T9: 10, T10: 10, T15: 1
- **injection_surface:** user_turn: 70, tool_return: 1
- **Artifacts ok/failed:** 70 ok / 1 failed
- **Failed scenario ids:**
  - scenario:v2:1b1135653dd5a2e99c2265ea44a35f921e111610bde7c62021edc7194b5d17e6 ("injection_surface must be 'user_turn', got 'tool_return'"; "user_turn artifact must end with role=user")

## Folder contents
- `scenarios/`: copy of asago-inputs/scenarios/<timestamp>/ (includes run-manifest.yaml, mode 600 preserved)
- `artifacts/`: copy of asago-inputs/artifacts/<timestamp>/
- `logs/`: 71 per-scenario artifact logs, plus step4.out, scenarios.log and artifacts.log. Those three were overwritten by each run, so they reflect run 4.
- `recommender/run4-only/`: tagger output for run 4 (tags.json per variant), read by `test` to build the bundle.
- `probe/full/`: measured probe results per variant and condition (the reference run), read by the demo build.
- `replay/conditions/`: policy inputs per variant and condition; `test` copies the OpenShell default condition from here.
- `recommendation/`: the reference bundle (validation.md, gaps.json, tests.json, policy-diff.md) that a fresh `test` must match.
