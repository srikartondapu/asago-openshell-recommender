# Direct probe report

Generated 2026-11-21T01:09:07+00:00. Status meanings: allowed = the action reached its target (tool-level errors count as allowed); denied = sandbox policy blocked it (log line, or OS/proxy result without a log line); error = inconclusive; gap = no action (content only).

## Runs

| variant | condition | harmful | benign | allowed | denied | error | gap | wall s |
|---|---|---|---|---|---|---|---|---|
| readonly | baseline | 77 | 7 | 61 | 17 | 0 | 6 | 119.7 |
| readonly | least-privilege | 77 | 7 | 20 | 58 | 0 | 6 | 119.4 |
| readonly | recommended | 77 | 7 | 20 | 58 | 0 | 6 | 119.2 |
| refunds | baseline | 75 | 9 | 61 | 17 | 0 | 6 | 119.4 |
| refunds | least-privilege | 75 | 9 | 59 | 19 | 0 | 6 | 127.1 |
| refunds | recommended | 75 | 9 | 59 | 19 | 0 | 6 | 126.8 |

## Attacks by threat (attack-level outcome per condition)

blocked = all action probes denied; exposed = some/all allowed (partial = both); inconclusive = errors only; gap_only = content effects only.

### readonly / baseline

| threat | blocked | partial | exposed | inconclusive | gap_only |
|---|---|---|---|---|---|
| T10 | 0 | 0 | 4 | 0 | 6 |
| T15 | 1 | 0 | 0 | 0 | 0 |
| T2 | 12 | 2 | 28 | 0 | 0 |
| T3 | 0 | 0 | 8 | 0 | 0 |
| T9 | 0 | 0 | 10 | 0 | 0 |

### readonly / least-privilege

| threat | blocked | partial | exposed | inconclusive | gap_only |
|---|---|---|---|---|---|
| T10 | 0 | 0 | 4 | 0 | 6 |
| T15 | 1 | 0 | 0 | 0 | 0 |
| T2 | 33 | 1 | 8 | 0 | 0 |
| T3 | 8 | 0 | 0 | 0 | 0 |
| T9 | 10 | 0 | 0 | 0 | 0 |

### readonly / recommended

| threat | blocked | partial | exposed | inconclusive | gap_only |
|---|---|---|---|---|---|
| T10 | 0 | 0 | 4 | 0 | 6 |
| T15 | 1 | 0 | 0 | 0 | 0 |
| T2 | 33 | 1 | 8 | 0 | 0 |
| T3 | 8 | 0 | 0 | 0 | 0 |
| T9 | 10 | 0 | 0 | 0 | 0 |

### refunds / baseline

| threat | blocked | partial | exposed | inconclusive | gap_only |
|---|---|---|---|---|---|
| T10 | 0 | 0 | 4 | 0 | 6 |
| T15 | 1 | 0 | 0 | 0 | 0 |
| T2 | 13 | 1 | 28 | 0 | 0 |
| T3 | 0 | 0 | 8 | 0 | 0 |
| T9 | 0 | 0 | 10 | 0 | 0 |

### refunds / least-privilege

| threat | blocked | partial | exposed | inconclusive | gap_only |
|---|---|---|---|---|---|
| T10 | 0 | 0 | 4 | 0 | 6 |
| T15 | 1 | 0 | 0 | 0 | 0 |
| T2 | 14 | 1 | 27 | 0 | 0 |
| T3 | 0 | 0 | 8 | 0 | 0 |
| T9 | 0 | 0 | 10 | 0 | 0 |

### refunds / recommended

| threat | blocked | partial | exposed | inconclusive | gap_only |
|---|---|---|---|---|---|
| T10 | 0 | 0 | 4 | 0 | 6 |
| T15 | 1 | 0 | 0 | 0 | 0 |
| T2 | 14 | 1 | 27 | 0 | 0 |
| T3 | 0 | 0 | 8 | 0 | 0 |
| T9 | 0 | 0 | 10 | 0 | 0 |

## Benign probes (denied benign = broken capability)

- readonly / baseline: allowed 7, denied 0 , error 0 
- readonly / least-privilege: allowed 7, denied 0 , error 0 
- readonly / recommended: allowed 7, denied 0 , error 0 
- refunds / baseline: allowed 9, denied 0 , error 0 
- refunds / least-privilege: allowed 9, denied 0 , error 0 
- refunds / recommended: allowed 9, denied 0 , error 0 

## Compared with baseline (exposed attacks per threat)

- readonly least-privilege vs baseline (exposed+partial): T10: 4->4, T15: 0->0, T2: 30->9, T3: 8->0, T9: 10->0
- readonly recommended vs baseline (exposed+partial): T10: 4->4, T15: 0->0, T2: 30->9, T3: 8->0, T9: 10->0
- refunds least-privilege vs baseline (exposed+partial): T10: 4->4, T15: 0->0, T2: 29->28, T3: 8->8, T9: 10->10
- refunds recommended vs baseline (exposed+partial): T10: 4->4, T15: 0->0, T2: 29->28, T3: 8->8, T9: 10->10
