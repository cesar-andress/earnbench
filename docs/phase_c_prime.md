# Phase C′ variance pilot (planning)

Phase C′0 is a **cheap go/no-go pilot** before multi-agent variance collection: **1 agent × 10 instances × \(K=30\)** repeated attempts.

**Paper protocol:** `paper/experiments/phase_c_prime_pilot_c0.md`  
**Manifest template:** `paper/experiments/phase_c_prime_pilot_manifest.template.csv`  
**Full Phase C′ design:** `paper/experiments/phase_c_prime_policy_variance.md`

## Manifest validation (no agent execution)

Validate planning CSV schema before any collection spend:

```bash
earnbench phase-c-prime validate-manifest manifest.csv
```

### Required columns

`agent`, `model`, `provider`, `instance_id`, `replicate_count`, `temperature`, `seed_policy`, `difficulty_bin`, `patch_loc`, `files_touched`, `notes`

### Rules

| Field | Validation |
|-------|------------|
| `replicate_count` | Positive integer |
| `(agent, instance_id)` | Unique rows |
| `temperature` | Empty or parseable float |
| `seed_policy` | Non-empty |

On success, prints JSON summary with `row_count`, `agent_count`, `instance_count`, `total_attempts`.

## After collection + Phase D

Analyze results with variance decomposition:

```bash
earnbench report policy-variance \
  --agent-results agent_results.csv \
  --output out/c_prime_c0 \
  --bootstrap 10000 \
  --seed 0
```

See `docs/policy_variance.md` for output artifacts and go/no-go inputs.

## Status

**Manifest validation only** in this release. Agent execution harness for Phase C′0 is not yet wired to the CLI.
