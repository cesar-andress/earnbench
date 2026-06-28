# Phase D — Agent patch re-grade (ERS glue)

Phase D re-grades **Phase C agent patches** with the same frozen EF@Π pipeline used in
Phase A (nominal → `pi_vtest.v1` → `pi_verif.v1` → `pi_env.v1` → EF aggregation).
Phase C collects patches only; Phase D computes EF post hoc.

This module does **not** change EF@Π semantics, Π membership, or INVALID handling.

## Commands

```bash
earnbench phase-d run \
  --phase-c-run experiments/runs/phase_c_ers_minimal \
  --metadata-parquet ../paper/vendor/swe_verified_test.parquet \
  --output experiments/runs/phase_d_ers_minimal \
  --workers 1 \
  --max-parallel-containers 1 \
  --max-parallel-builds 1 \
  --resume

earnbench phase-d summarize \
  --run experiments/runs/phase_d_ers_minimal
```

After Phase D completes, run Earned Rank Stability (ERS):

```bash
earnbench report rank-stability \
  --agent-results experiments/runs/phase_d_ers_minimal/agent_results.csv \
  --output experiments/runs/phase_d_ers_minimal/ers \
  --bootstrap 10000
```

## Inputs

| Flag | Command | Required | Description |
|------|---------|----------|-------------|
| `--phase-c-run` | run | yes | Completed Phase C directory (`attempts.csv`, patch files) |
| `--metadata-parquet` | run | yes | SWE-bench Verified metadata (`.parquet` or `.json`) |
| `--output` | run | yes | Phase D output root |
| `--workers` | run | no | Concurrent cell workers (default: 1) |
| `--max-parallel-containers` | run | no | Docker container parallelism cap |
| `--max-parallel-builds` | run | no | Docker build parallelism cap |
| `--resume` | run | no | Skip completed cells with `report.json` on disk |
| `--run-id` | run | no | Run identifier in manifests and CSV rows |
| `--dataset-revision` | run | no | Dataset revision label for config digest |
| `--build-missing-images` | run | no | Build missing SWE-bench harness images during preflight |
| `--run` | summarize | yes | Completed Phase D output directory |

### Eligible Phase C attempts

Only rows in `PHASE_C_RUN/attempts.csv` with:

- `status=ok`
- non-empty `patch_path`

are re-graded. Failed, skipped, or empty-patch attempts are counted in
`phase_d_summary.json` but not graded.

## Outputs

```text
OUTPUT/
├── run_manifest.json
├── phase_d_summary.json
├── agent_results.csv          # input to report rank-stability
├── failures.csv
└── cells/
    └── <agent>/
        └── <instance_id>/     # nominal + π artifacts + report.json
            ├── meta.json
            ├── nominal/
            ├── pi_vtest.v1/
            ├── pi_verif.v1/
            ├── pi_env.v1/
            └── report.json
```

When `replicate > 0`, cell workspaces live under `cells/<agent>/r<replicate>/`.

### `agent_results.csv`

Long-format table with one row per graded `(agent, instance_id, replicate)`.
Required columns for ERS include `agent`, `instance_id`, `y0`,
`ef_exclude_invalid`, `ef_invalid_as_fail`, `failed_mechanisms`, and
`invalid_pi_count`.

For primary ERS with one patch per `(agent, instance)`, use `replicates: 1` in
Phase C arms so each agent×instance appears once. Policy-level analyses with
multiple replicates should use `report policy-ef` instead of `rank-stability`.

## Pipeline stages (per cell)

1. **prepare** — `prepare_exploit` with agent patch (`patch_class=agent_patch`, `y0_policy=prod_only`)
2. **preflight** — SWE-bench Docker image preflight
3. **nominal** — nominal harness grading on `prod_only.patch`
4. **π** — `pi_vtest.v1` (if supported), `pi_verif.v1`, `pi_env.v1`
5. **aggregate** — frozen `compute_earned_fraction` via `aggregate_instance`

EF variants and INVALID accounting match Phase A `summary.csv` semantics.

## Related

- [Phase C agent collection](phase_c.md)
- `paper/experiments/phase_d_ers_execution.md` — operator note for the minimal ERS run
- `paper/experiments/phase_c_d_minimal_ers_plan.md` — instance set and arms pin
