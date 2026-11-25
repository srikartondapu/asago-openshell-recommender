# Validation: klarna-support (refunds)

Three checks on the recommended (least-privilege) policy. **Before** = no sandbox (same agent image in a plain container, measured with the same actions). Middle = **OpenShell default**: the sandbox created with no policy supplied, so OpenShell applies its own restrictive default (no network rules: every connection denied; read-only system paths; writes only to /tmp). It is safe but breaks the agent. After = the recommended policy.

## 1. Valid OpenShell configuration
- OpenShell accepted `policy.yaml` and `provider-profile.yaml` (sandbox created by `deploy.sh`) and the agent ran.
- Good actions (the use case's declared capabilities) under the recommended policy: **9/9 allowed**.

## 2. Policy Prover (static, `prove.py`)
- Each action encoded as a tiny policy and proven against the recommended policy: good pass 3, bad pass 16, fail 0, not provable 17 (MCP tool calls, local commands and file paths not in the policy; covered by check 3).
- Details: `prove.md`.

## 3. Sandbox probes (dynamic: the agent's own programs perform each action inside the real sandbox, no LLM)
This is a sandbox test, not an agent test: it assumes the agent is already fooled and checks what the sandbox still allows.

| | Before: no sandbox | OpenShell default (no policy supplied) | **After: recommended** |
|---|---|---|---|
| Good actions allowed | 9/9 | 0/9 | 9/9 |
| Bad actions denied | 2/78 | 72/78 | **22/78** |
| Attacks fully blocked | 1/73 | 67/73 | **17/73** |
| Attacks with an action still allowed | 66 | 0 | 50 |
| Text-only attacks (nothing to block) | 6 | 6 | 6 |

Before, `unenforced` actions (attack hosts that don't exist on the internet) count as not protected: nothing would stop a real host.

## Protection gained: before (no sandbox) -> after (recommended)

| What the action needs | Rule that now stops it | Actions denied before -> after |
|---|---|---|
| egress_unknown_host | network: connections only to declared hosts:ports | 0/14 -> **14/14** |
| exec_spawned_network | network: each endpoint open only to the program that needs it (no curl/wget) | 0/3 -> **3/3** |
| fs_write_outside_workspace | filesystem: writes only in the declared workspace | 2/2 -> **2/2** |
| fs_read_undeclared | filesystem: reads only system paths + declared paths (image workdir not exposed) | 0/2 -> **2/2** |
| secret_disclosure | secrets: tool token injected by OpenShell; the sandbox only sees a placeholder | 0/1 -> **1/1** |

## Still allowed under the recommended policy (-> guardrails)

- **41 attacks**: The tool is part of the use case, so the policy must allow it. OpenShell sees the tool name, not whether the arguments are legitimate (whose order, what amount, true summary) -> guardrails / business rule.
- **9 attacks**: A command that stays inside the sandbox. The agent runtime exposes run_command; OpenShell confines its network and files but can't stop the command itself -> remove the tool from the agent, or guardrails.
- **6 attacks**: Text-only harm: no action for a sandbox to block -> guardrails.
