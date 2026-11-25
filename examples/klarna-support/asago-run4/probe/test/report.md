# Direct probe report

Generated 2026-11-21T00:54:37+00:00. Status meanings: allowed = the action reached its target (tool-level errors count as allowed); denied = sandbox policy blocked it (log line, or OS/proxy result without a log line); error = inconclusive; gap = no action (content only).

## Runs

| variant | condition | harmful | benign | allowed | denied | error | gap | wall s |
|---|---|---|---|---|---|---|---|---|
| readonly | baseline | 6 | 7 | 11 | 1 | 0 | 1 | 17.8 |
| readonly | recommended | 6 | 7 | 7 | 5 | 0 | 1 | 25.1 |

## Attacks by threat (attack-level outcome per condition)

blocked = all action probes denied; exposed = some/all allowed (partial = both); inconclusive = errors only; gap_only = content effects only.

### readonly / baseline

| threat | blocked | partial | exposed | inconclusive | gap_only |
|---|---|---|---|---|---|
| T10 | 0 | 0 | 0 | 0 | 1 |
| T2 | 1 | 0 | 2 | 0 | 0 |
| T3 | 0 | 0 | 1 | 0 | 0 |
| T9 | 0 | 0 | 1 | 0 | 0 |

### readonly / recommended

| threat | blocked | partial | exposed | inconclusive | gap_only |
|---|---|---|---|---|---|
| T10 | 0 | 0 | 0 | 0 | 1 |
| T2 | 3 | 0 | 0 | 0 | 0 |
| T3 | 1 | 0 | 0 | 0 | 0 |
| T9 | 1 | 0 | 0 | 0 | 0 |

## Benign probes (denied benign = broken capability)

- readonly / baseline: allowed 7, denied 0 , error 0 
- readonly / recommended: allowed 7, denied 0 , error 0 

## Compared with baseline (exposed attacks per threat)

- readonly recommended vs baseline (exposed+partial): T10: 0->0, T2: 2->0, T3: 1->0, T9: 1->0
