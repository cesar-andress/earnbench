# Phase B — Planted exploit batch

Phase B runs the same SWE-bench grading pipeline as Phase A on **pre-registered
planted exploit patches** (`exploit_planted`), then compares observed outcomes to
frozen YAML expectations.

## Command

```bash
earnbench phase-b run \
  --exploit-dir ../paper/exploits \
  --metadata-parquet ../paper/vendor/swe_verified_test.parquet \
  --output experiments/runs/phase_b_exploit \
  --workers 8 \
  --max-parallel-containers 8 \
  --resume
```

## Inputs

| Flag | Required | Description |
|------|----------|-------------|
| `--exploit-dir` | yes | Directory with `E*.yaml` specs and `patches/E*.patch` unified diffs |
| `--metadata-parquet` | no* | SWE-bench Verified metadata (`.parquet` or `.json`) |
| `--output` | no | Batch output root (default: `./phase_b`) |
| `--workers` | no | Concurrent exploit workers (same semantics as Phase A) |
| `--max-parallel-containers` | no | Docker concurrency cap |
| `--resume` | no | Skip completed exploits / stages |

\*Default: `$EARNBENCH_METADATA_PARQUET` or `../paper/vendor/swe_verified_test.parquet`.

## Per-exploit pipeline

For each spec in `--exploit-dir` (sorted by `exploit_id`):

1. **prepare** — load `patches/<exploit_id>.patch`, write `meta.json` / patch files  
2. **preflight** — SWE-bench Docker image checks  
3. **nominal** — grade with `prod_only` or `raw_full` per `y0_policy`  
4. **π** — sequential `pi_verif.v1` → `pi_vtest.v1` → `pi_env.v1`  
5. **aggregate** — compute EF@Π and criterion columns  

Failures on one exploit **do not** stop the batch.

## Output layout

```text
<output>/
├── summary.csv
├── failures.csv
├── statistics.json
├── confusion_matrix.csv
├── registry_coverage.csv
├── run_manifest.json
├── phase_b_report.md          # from `earnbench report phase-b`
├── batch_state.json
├── reports/<exploit_id>.json
├── audits/<exploit_id>/
└── <exploit_id>/
    └── <instance_id>/
        ├── nominal/
        ├── pi_vtest.v1/
        ├── pi_verif.v1/
        ├── pi_env.v1/
        └── report.json
```

## Resume

With `--resume`, an exploit is complete when
`<output>/<exploit_id>/<instance_id>/report.json` exists. Individual stages
(`prepare`, `preflight`, `nominal`, each π) resume independently when their
artifact files are present.

## Report generation

After a batch completes, generate a deterministic markdown report:

```bash
earnbench report phase-b experiments/runs/phase_b_exploit
```

Writes `phase_b_report.md` in the batch directory with completed/failed counts,
family-level tables, expected-vs-observed outcomes, confusion matrix, targeted-π
failure rates, EF and invalid distributions, sensitivity-gap analysis, registry
coverage, kill-condition checklist, and publication-ready summary text.

## Related

- Phase A batch: `docs/` / `earnbench phase-a run`
- Exploit specs: `earnbench exploit list --directory …`
- Paper protocol: `paper/experiments/phase_b_adversarial_exploit_protocol.md`
