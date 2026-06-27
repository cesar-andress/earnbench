# Policy-level earned-credit variance decomposition (Phase C′)

Phase C′ analysis layer for decomposing **earned_score = y0 × ef_pi** across repeated agent attempts. Does **not** modify outcome-level EF@Π, Π, INVALID semantics, or `rank_stability.py` ERS formulas.

**Paper notes:** `paper/notes/policy_level_earned_credit.md`, `paper/experiments/phase_c_prime_policy_variance.md`

## Command

```bash
earnbench report policy-variance \
  --agent-results agent_results.csv \
  --output OUT \
  --bootstrap 10000 \
  --seed 0
```

Related (narrower aggregates): `earnbench report policy-ef`.

## Input CSV

### Required columns

`agent`, `model`, `provider`, `instance_id`, `replicate`, `y0`, `ef_pi`, `ef_status`, `failed_mechanisms` (alias: `failed`), `invalid_pi_count`, `status`

### Optional columns

`difficulty_bin` (alias: `difficulty`), `patch_loc`, `files_touched`, `trajectory_tokens`, `wall_time_seconds`

Unique `(agent, instance_id, replicate)` per row.

## Scoring policies

| Policy | Rule |
|--------|------|
| **Primary earned_score** | `y0 * ef_pi` when EF defined; 0 when fail or undefined |
| **earned_pass_rate** | Mean earned_score; undefined on success counted as missingness (`undefined_rate`) |
| **mean_ef_conditional_on_pass** | Mean `ef_pi` only on defined successes (exclude undefined) |
| **Sensitivity undefined_as_zero** | Same as primary for earned_score; reported as `earned_pass_rate_undefined_as_zero` |

## Outputs

| File | Content |
|------|---------|
| `policy_variance_by_agent.csv` | Marginal rates, EF\|pass, undefined/invalid, per-agent variance, three rank columns |
| `policy_variance_by_agent_instance.csv` | Cell means/variances, dominant failed mechanism |
| `policy_variance_components.csv` | ANOVA-style decomposition (global + per-agent) |
| `policy_variance_bootstrap.json` | Bootstrap CIs, ERS bridge Spearman, pairwise flip probabilities |
| `policy_variance_pairwise_flips.csv` | Nominal vs policy-earned vs single-run flip table |
| `exploitation_frontier.csv` | Only when `difficulty_bin` or `patch_loc` present |
| `policy_variance_report.md` | Human-readable summary |

## Variance decomposition (central estimand)

Transparent population-variance partition on **earned_score**:

1. **within_cell_variance** — same agent×instance, across replicates  
2. **between_instance_variance** — instance means around agent mean  
3. **between_agent_variance** — agent means around grand mean (global scope)  
4. **residual_or_missing_component** — unexplained variance plus undefined/missing mass  

**Limitation:** components may not sum exactly when many cells have \(K=1\).

## ERS bridge

Three rankings (semantics unchanged; new inputs only):

- **nominal_rank** — by nominal pass rate  
- **single_run_earned_rank** — by first replicate per instance  
- **policy_earned_rank** — by expected earned pass rate (all replicates)

Bootstrap reports pairwise flip probabilities between ranking pairs.

## Exploitation frontier

Emitted **only** when `difficulty_bin` or `patch_loc` columns exist. No inference without bin columns.
