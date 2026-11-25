# run4-only recommender outputs

- **Date:** 2026-11-20
- **Input:** ASAGO run 4 only (`asago-inputs/results/run4-fsisac-allthreats-nocap/`). No other run folders are read.
  - Artifacts: `artifacts/` (71 scenario dirs, each with `*-garak.json`)
  - Scenario YAMLs: `scenarios/scenarios/` (71 files, one per artifact; all matched by scenario_id)
- **Mode:** rules only (no `--llm`). Lab env: `recommender/lab.yaml`.
- **Variants:** `readonly/`, `refunds/` (manifests in `manifests/`).

## Commands (run from `recommender/`, `uv run python`)

```
R=asago-inputs/results/run4-fsisac-allthreats-nocap
A="$R/artifacts"; S="$R/scenarios/scenarios"
for V in readonly refunds; do
  O="$R/recommender/run4-only/$V"; mkdir -p "$O"
  uv run python tagger.py  --manifest manifests/sandbox-manifest-$V.yaml --artifacts "$A" --scenarios-dir "$S" --lab lab.yaml --out "$O/tags.json" > "$O/tagger.log" 2>&1
  # by-threat.csv and by-threat-gap-effects.csv derived from tags.json
  python3 -I /tmp/run4_summary/summarize.py "$O"
done
```

(Paths above are relative to the repo root for `$R`; the tagger paths were absolute in the actual run.)

## Outputs per variant

- `tags.json`: 71 records (71 applicable)
- `selection.json`, `fragments.txt`, `policy.yaml`, `rationale.md`, `runtime.json`: selector outputs
- `by-threat.csv`: columns `threat_id,attacks,blockable,gap`
  - attacks = applicable records for the threat
  - blockable = records with no gap effect (all effects covered by selected fragments)
  - gap = records with at least one gap effect
- `by-threat-gap-effects.csv`: gap effect entries per threat (`threat_id,effect,gap_count`)
- `tagger.log`, `selector.log`: stdout/stderr of the runs

## Notes

- The summary helper is a one-off script kept outside the repo (`/tmp/run4_summary/summarize.py`).
- Run 4 `README.md` lists scenario 1b1135653dd5... as a failed artifact (validation ok=false). Its artifact dir still contains a `*-garak.json`, so the tagger included it (T15, tool_return, egress_unknown_host). Left in, as the tagger is directory-based.
