# Policy-level Earned Fraction

See **[Policy-level earned credit analysis](policy_earned_credit.md)** for the canonical CLI reference, outputs, and metrics.

This page is a short alias retained for backward compatibility.

## Outcome vs policy estimands

| Estimand | Unit | Definition |
|----------|------|------------|
| **Outcome-level EF@Π** | Fixed patch attempt | Existing `ef_pi` on one harness row |
| **Policy-level EF** | Agent policy | E[EF@Π \| Y₀=1] over replicates |
| **Earned pass rate** | Agent × evaluation grid | mean(Y₀ · EF@Π) over instances and replicates |

## Input CSV (long format)

Required columns:

| Column | Description |
|--------|-------------|
| `agent` | Agent arm label |
| `model` | Model identifier (provenance) |
| `provider` | Provider label (provenance) |
| `instance_id` | Benchmark instance |
| `replicate` | Stochastic attempt index |
| `y0` | Nominal pass |
| `ef_pi` | Outcome-level EF@Π |
| `ef_status` | `defined` or `undefined` |
| `failed_mechanisms` | Channel list for the attempt |
| `invalid_pi_count` | Invalid π count |
| `status` | Harness row status |

Each `(agent, instance_id, replicate)` row must be unique.

## Generate report

```bash
earnbench report policy-ef \
  --agent-results agent_results.csv \
  --output out/policy_ef \
  --bootstrap 10000
```

## Outputs

| File | Description |
|------|-------------|
| `policy_ef_by_agent.csv` | Nominal/earned pass rates, policy EF, variance, ranks |
| `policy_ef_variance.csv` | Global and per-agent variance metrics |
| `policy_ef_bootstrap.json` | Bootstrap CIs and pairwise flip probabilities |
| `policy_ef_report.md` | Human-readable summary |

## Metrics

- Nominal pass rate and earned pass rate by agent
- Mean EF conditional on pass (policy-level EF)
- Inter-replicate variance (replicate-level earned pass rate variance)
- Instance-level variance (instance mean contribution variance)
- Within-agent and between-agent outcome variance decomposition
- ERS using **expected earned pass rate** (Spearman/Kendall, rank shifts)
- Pairwise rank flip rate and bootstrap flip probability per pair

Bootstrap resamples **instances with replacement** and keeps all replicates for each sampled instance (cluster bootstrap).

Does **not** modify EF formulas, Π registry, INVALID semantics, or validators.
