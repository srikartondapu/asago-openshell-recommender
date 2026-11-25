# Replay policy conditions (run 4, all threats, no cap)

Policy inputs for the run 4 conditions. Layout: `<variant>/<condition>/`
with `policy.yaml`, `runtime.json`, and `provider-profile.yaml` where the condition uses secrets.

| Variant | Condition | Source | Fragments |
|---|---|---|---|
| readonly | baseline | `recommender/out/readonly/baseline/` (copied) | none (permissive) |
| readonly | least-privilege | `recommender/out/readonly/least-privilege/` (copied) | all 10 |
| readonly | recommended | `recommender/run4-only/readonly/` (copied) | 8 selected by the run 4 selector |
| refunds | baseline | permissive policy (not reproducible with the current compiler) | none (permissive) |
| refunds | least-privilege | compiled with `compile.py` | all 10 |
| refunds | recommended | `recommender/run4-only/refunds/` (copied) | 7 selected by the run 4 selector |

Recommended sources are under `asago-inputs/results/run4-fsisac-allthreats-nocap/recommender/run4-only/<variant>/`.
The run 4 selector output has no `provider-profile.yaml` for either variant. That is consistent with
its fragments (no `secrets.provider_injection`) and `token_mode: env`.

## Commands

Run from `recommender/` (the compiler reads `lab.yaml` from there by default).
`$C` is `asago-inputs/results/run4-fsisac-allthreats-nocap/replay/conditions`.

```
# readonly baseline and least-privilege: copied from the existing compiled outputs
#   (regenerating with the same commands reproduces them byte-for-byte apart from the header line)
cp out/readonly/baseline/{policy.yaml,runtime.json} $C/readonly/baseline/
cp out/readonly/least-privilege/{policy.yaml,runtime.json,provider-profile.yaml} $C/readonly/least-privilege/

# recommended: copied from the run 4 selector output
cp .../recommender/run4-only/readonly/{policy.yaml,runtime.json} $C/readonly/recommended/
cp .../recommender/run4-only/refunds/{policy.yaml,runtime.json}  $C/refunds/recommended/

# refunds least-privilege: compiled from the refunds manifest
python3 compile.py --manifest ../manifests/sandbox-manifest-refunds.yaml --out $C/refunds/least-privilege
```

## Checks

- `openshell profile lint -f <least-privilege>/provider-profile.yaml`: refunds passes. Readonly reports
  "custom provider profile 'klarna-support-readonly-tools-token' already exists", because the gateway already
  holds that profile from an earlier deploy. deploy.sh deletes and re-imports it, so this is a gateway-state
  conflict, not a file defect.
- `openshell policy` has no lint subcommand. All six `policy.yaml` files were parsed with PyYAML.
