# ASAGO OpenShell Recommender

Prototype recommender that turns an agent's use case and the attacks measured against it (ASAGO run 4) into a minimal OpenShell sandbox policy, then proves the policy blocks the attacks while benign tasks still pass.

## Layout

```
src/openshell_recommender/   recommender modules and the `asago-openshell-recommender` CLI (cli.py); see Code guide below
tests/                       regression and unit tests, tests/fixtures
lab/                         local OpenShell lab (environment for `test`): gateway/turn scripts, lab-image, lab/agent (vendored mini-agents)
examples/klarna-support/     inputs/, asago-run4/ (pipeline results), asago-inputs/, standin-fs-read/, policy-mapper/
demo/                        static demo site, demo/generator builds demo/data
docs/                        design notes, overview, before/after comparison
scripts/                     build-demo.sh, check-demo.sh (+ check.html), used by `demo build` / `demo check`
output/                      one directory per run: output/<run-id>/ (created by recommend and test; gitignored)
out/                         logs from `demo build` and `demo check` (gitignored)
```

## Quickstart

Run every command from the repository root. Relative `--run-dir` and `--output-dir` paths are resolved from the current directory, so from the root `output/e2e` is `<repo>/output/e2e`. The default output directory is always `<repo>/output/`.

The first `uv run` creates `.venv` in the repository. If you see `warning: VIRTUAL_ENV=... does not match the project environment path`, your shell has a `VIRTUAL_ENV` left over from another project. Run `unset VIRTUAL_ENV` and try again.

```sh
# 0. Prerequisites (once)
brew install podman                              # container engine
podman machine init && podman machine start      # macOS only, once per machine
brew install nvidia/openshell/openshell          # provides openshell and openshell-prover
brew install uv                                  # Python runner (or: curl -LsSf https://astral.sh/uv/install.sh | sh)
#   Google Chrome: needed only for `demo check`

uv run asago-openshell-recommender doctor        # 1. ✓/✗ table of prerequisites and lab state; exit 1 if one is missing
uv run asago-openshell-recommender lab setup     # 2. builds 2 images (~7 GB; first build is download-bound, ~1 min when cached); creates lab/run/tool-token (chmod 600)
uv run asago-openshell-recommender lab up        # 3. starts the gateway, mock services and tool server in the background
uv run asago-openshell-recommender recommend --run-id e2e                    # 4. writes output/e2e/policy.yaml and prove.md
uv run asago-openshell-recommender test --run-dir output/e2e                 # 5. ~5 min: sandbox-backed conditions ~2 min each, OpenShell default ~10 s (the agent can't start under it)
uv run asago-openshell-recommender demo build && uv run asago-openshell-recommender demo serve   # 6. serve runs until Ctrl-C
uv run asago-openshell-recommender lab down      # 7. stops only the services that `lab up` started
```

### Expected results

A fresh run's `validation.md` should be identical to the reference at `examples/klarna-support/asago-run4/recommendation/klarna-support/validation.md`. The numbers are:

| | No sandbox | OpenShell default | Recommended |
|---|---|---|---|
| Good actions allowed | 9/9 | 0/9 | 9/9 |
| Bad actions denied | 2/78 | 72/78 | 22/78 |
| Attacks fully blocked | 1/73 | 67/73 | 17/73 |
| Still exposed → guardrails | 66 | 0 | 50 |
| Text-only | 6 | 6 | 6 |

The OpenShell default denies almost everything: it blocks 67 of 73 attacks but also all 9 good actions. The recommended policy keeps all good actions working and blocks more attacks than no sandbox. The 50 attacks that remain exposed are what guardrails must cover (`gaps.json`).

## Details

### recommend: `recommend` → `policy.yaml`

This compiles the Klarna support refunds manifest into `output/<run-id>/policy.yaml` and proves it against the attack-derived test actions from ASAGO run 4. Defaults: the Klarna manifest, `examples/klarna-support/asago-run4` as the pipeline results, and a UTC timestamp as the run ID. `recommend` needs only uv and the OpenShell CLI (for `openshell-prover`).

```text
$ uv run asago-openshell-recommender recommend --run-id verify
-> compile policy
   ok (0s), log: .../output/verify/logs/compile.log
-> derive attack probe plan from pipeline results
   ok (0s), log: .../output/verify/logs/plan.log
-> prove policy (openshell-prover)
   ok (1s), log: .../output/verify/logs/prove.log
run:    .../output/verify
policy: .../output/verify/policy.yaml
prove:  .../output/verify/prove.md
next:   asago-openshell-recommender test --run-dir .../output/verify
```

`prove.md` lists each attack and benign action with its prover result. `not_provable` means the action is outside the prover's model (for example MCP calls), so only the probes can judge it.

### test: `test`

`test` deploys the run's own `policy.yaml` as the `recommended` condition, next to `openshell-default` and a no-sandbox baseline, probes each one with the attacks and benign tasks, and writes the evidence into the same run directory. It needs the lab (steps 2 and 3 of the Quickstart).

Results are written to output/<run>/probe/<variant>/{no-sandbox,recommended,openshell-default}/

The run directory then holds `validation.md`, `tests.json`, `gaps.json`, `policy-diff.md`, `README.md` and the probe results in `probe/`.

### The lab: environment for `test`

```sh
uv run asago-openshell-recommender doctor  # ✓/✗ table of prerequisites and lab state; exit 1 if a prerequisite is missing
```

- `lab setup [--dry-run]` creates `lab/run/tool-token` (random, chmod 600) and builds the two images (slow the first time).
- `lab up` starts the gateway on :17670, the mock services on :9101/:9102 and the tool server on :8765, in the background. It refuses to start if a port is taken and names the process holding it.
- `lab status` shows each service and its port. `lab down` stops only the services that `lab up` started.

Images: `localhost/mini-agents-sandbox:asago` (base sandbox, built from `lab/agent/Dockerfile.sandbox`) and `localhost/mini-agents-sandbox:lab` (lab layer from `lab/lab-image/Dockerfile`, the image the sandboxes and no-sandbox probes run).

### Demo

```sh
uv run asago-openshell-recommender demo build    # regenerates demo/data from the run4 outputs; log in out/logs/demo-build.log
uv run asago-openshell-recommender demo check    # clicks through the demo in headless Chrome; log in out/logs/demo-check.log
uv run asago-openshell-recommender demo serve    # serves the demo at http://127.0.0.1:8080/ (Ctrl-C to stop)
                                                 # port busy? add --port 8081
```

`demo build` and `demo check` write their logs under `out/` (gitignored). `demo check` uses headless Chrome, so it needs Google Chrome installed (or `CHROME=/path/to/chrome`).

## Commands

| Command | What it does |
|---|---|
| `recommend [--manifest M] [--pipeline-results R] [--run-id X] [--output-dir output]` | Compile `policy.yaml` from a manifest, derive the test actions from `R`, and prove the policy. Writes `output/<run-id>/` |
| `test --run-dir output/X [--pipeline-results R] [--conditions {no-sandbox,recommended,openshell-default} ...] [--no-cleanup]` | Deploy and probe the run's policy and the conditions (needs `lab up`), then write the validation evidence into the run directory |
| `lab setup [--dry-run] \| up \| down \| status` | Create the tool token and build the images, start or stop the lab services, or show their state |
| `doctor` | Check podman, the podman machine, openshell, openshell-prover, uv, the gateway binary, lab state, images, token and ports |
| `demo build \| check \| serve [--port 8080]` | Regenerate `demo/data`, click through the demo in headless Chrome, or serve it on 127.0.0.1 |

Defaults: `--manifest` and `--pipeline-results` point at the Klarna support example. `--run-id` defaults to a UTC timestamp (`YYYYMMDD-HHMMSS`). The default output directory is `output/` in the repository root. A relative `--output-dir` or `--run-dir` is resolved from the current directory.

## Tests

```sh
uv run --with pytest pytest                # unit tests (38)
uv run python tests/run_regression.py      # tagger regression checks (102)
```

## Code guide

The recommender is `src/openshell_recommender/`: nine Python files, about 3,300 lines. Everything else in the repo is example data (`examples/`), the copied mock agent (`lab/agent/`), the demo and tests. Suggested reading order: `compile.py` → `prove.py` → `tagger.py` → `probe.py` → `bundle.py`, with `cli.py` tying them together.

| File | What it does | Main functions |
|---|---|---|
| `compile.py` (~120 lines) | Compiles the agent's sandbox manifest into a least-privilege OpenShell policy. Each policy section takes its values from the manifest (what the agent needs) or from `lab.yaml` (what every sandbox needs). Writes `policy.yaml`, `provider-profile.yaml` (token injection for the MCP server) and `runtime.json` (how to create the sandbox). | `compile_policy`, `mcp_rules`, `channels` |
| `prove.py` (~210) | Static check of the policy with NVIDIA's openshell-prover. Each good or bad test action is encoded as a tiny policy that allows only that action and checked against the recommended policy: inside the boundary means allowed, outside means blocked. MCP tool rules are outside the prover's model and are reported as "not provable" (the sandbox probes cover them). | `tiny_policy`, `test_from_probe`, `run_prover`, `verdict` |
| `tagger.py` (~650) | Reads each ASAGO attack artifact and maps it to **effects** (contact an unknown host, call an undeclared tool, read a file outside the workspace, and so on) using the rules in `rules.yaml`. It resolves tool names against the manifest. An optional LLM fallback (`--llm`, unused in the runs) only handles attacks the rules couldn't tag, and each LLM effect must quote the attack text verbatim. | `tag_artifact`, `rule_effects`, `resolve_tools`, `llm_check` |
| `probe.py` (~930) | Turns each tagged attack into concrete actions (fetch this URL, call this MCP tool with these arguments, read this path, run this command), plus the good actions from the manifest. Then deploys a real sandbox, runs the actions with the agent's own programs, and decides allowed or denied from the OpenShell log, HTTP 403s or OS permission errors. `plan`, `run` and `report` subcommands. | `build_scenario_probes`, `benign_probes`, `cmd_run`, `decide`, `attack_outcome` |
| `probe_runner.py` (~160) | Runs **inside** the sandbox under the same binary the agent uses (`python-ogx` for MCP calls, `python-mcp` for workstation tools), so the policy that applies to the probe is the policy that applies to the agent. Prints one JSON line per action and scrubs the token from all output. | `execute`, `run_mcp`, `run_ws` |
| `nosandbox.py` (~110) | The "no sandbox" condition: runs the same actions in a plain Podman container (same image, same mock services) with no OpenShell, so there's a before number. | `run_batch` |
| `bundle.py` (~280) | Writes the evidence: `validation.md` (config + prover + probe results across the three conditions), `gaps.json` (per attack: blocked by which rule, or a residual risk for guardrails), `tests.json` and `policy-diff.md`. | `main`, `attack_outcome` |
| `cli.py` (~680) | The `asago-openshell-recommender` command: `doctor`, `lab setup/up/down/status`, `recommend` (compile + prove), `test` (probe the three conditions + bundle), `demo build/check/serve`. Calls the modules above. | `cmd_recommend`, `cmd_test`, `cmd_lab_setup`, `cmd_lab_up` |
| `common.py` (~170) | Shared helpers for the probe runners: run commands, read the token (for redaction only), parse sandbox log lines and classify denials. | `classify_denial`, `load_secret` |

Config next to the code: `rules.yaml` (the effect vocabulary and which policy rule blocks which effect), `lab.yaml` (paths and endpoints every sandbox needs), `deploy.sh` (creates a sandbox from a compiled policy). `tools/extract_asago_ids.py` is a one-off that extracted the IDs used in the mock agent's seed data.

## Notes

Quick proof of concept shared for review. `lab/agent/` is a copy of the mock agent from [hjrnunes/mini-agents](https://github.com/hjrnunes/mini-agents) (see `lab/agent/VENDORED.md`).
