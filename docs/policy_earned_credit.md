# Policy-level earned credit analysis

**EF@Π is the outcome-level estimand; policy-level earned credit is the distribution of EF@Π induced by a stochastic agent policy over repeated attempts.**

This command aggregates **outcome-level EF@Π** over stochastic agent replicates without changing fixed-outcome EF semantics, Π membership, or INVALID handling. For single-realization leaderboards, use `earnbench report rank-stability` instead.

**Paper notes:** `paper/notes/policy_level_earned_credit.md`, `paper/notes/policy_level_ef.md`

## Command

```bash
earnbench report policy-ef \
  --agent-results agent_results.csv \
  --output OUT \
  --bootstrap 10000
```

## Input CSV (long format)

### Required columns

| Column | Description |
|--------|-------------|
| `agent` | Agent arm label |
| `model` | Model identifier (provenance) |
| `provider` | Provider label (provenance) |
| `instance_id` | Benchmark instance |
| `replicate` | Stochastic attempt index (unique per agent×instance) |
| `y0` | Nominal pass (`true`/`false`) |
| `ef_pi` | Outcome-level EF@Π |
| `ef_status` | `defined` or `undefined` |
| `failed_mechanisms` | Channel list for the attempt |
| `invalid_pi_count` | Invalid π count |
| `status` | Harness row status |

Each `(agent, instance_id, replicate)` row must be unique. All agents must share the same `instance_id` set.

### Optional columns

| Column | Description |
|--------|-------------|
| `difficulty` | Difficulty bin label; when present, enables exploitation-frontier tables |

## Outputs

| File | Description |
|------|-------------|
| `policy_ef_by_agent.csv` | Nominal/earned pass rates, policy EF, variance, ranks |
| `policy_ef_variance.csv` | Global and per-agent variance decomposition |
| `policy_ef_pairwise_flips.csv` | Pairwise rank flips and bootstrap flip probability |
| `policy_ef_bootstrap.json` | Bootstrap CIs, ERS, pairwise flip probabilities |
| `policy_ef_report.md` | Human-readable summary |
| `policy_ef_exploitation_frontier.csv` | *(optional)* By agent and `difficulty` bin when column present |

## Metrics

| Metric | Definition |
|--------|------------|
| **Nominal pass rate** | mean(\(Y_0\)) over attempts |
| **Earned pass rate** | mean(\(Y_0 \cdot \text{EF@Π}\)) over attempts |
| **Mean EF \| pass** | E[EF@Π \| \(Y_0=1\)] on defined successes |
| **Policy EF** | Alias of mean EF \| pass at agent level |
| **Inter-replicate variance** | Variance of replicate-level earned pass rates |
| **Within-agent variance** | Population variance of earned contributions within agent |
| **Between-agent variance** | ANOVA-style decomposition of earned contributions |
| **Pairwise rank flip** | Nominal ordering disagrees with earned ordering for a pair |
| **Bootstrap flip probability** | Fraction of bootstrap draws where a pair flips |
| **ERS** | Spearman/Kendall between nominal-rank and earned-rank vectors (expected earned pass rate) |
| **Exploitation frontier** | Earned vs nominal pass by agent and difficulty bin |

Bootstrap resamples **instances with replacement** (cluster bootstrap) and keeps all replicates for each sampled instance.

## Example workflow

1. Run agents with \(K \geq 3\) replicates per instance (Phase C protocol).
2. Export long-format `agent_results.csv` with one row per attempt.
3. Run `report policy-ef` with `--bootstrap 10000`.
4. Read `policy_ef_by_agent.csv` for primary aggregates; use `policy_ef_pairwise_flips.csv` and bootstrap JSON for rank-stability uncertainty.

Does **not** modify `rank_stability.py` semantics or outcome-level EF formulas.
