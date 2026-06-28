# Docker setup for SWE-bench grading

EarnBench does **not** ship a project Dockerfile. Batch grading uses the official
**SWE-bench harness** to build and run per-instance evaluation containers on your
host Docker daemon.

## Prerequisites

| Requirement | Notes |
|-------------|-------|
| Docker Engine | Linux recommended; rootless Docker supported with same socket access |
| Disk | **50 GB+** free for base/env/instance image layers across Verified instances |
| RAM | Plan **~8 GB per concurrent container** (`--max-parallel-containers`) |
| Network | Required for first-time image builds and some instance tests |
| Python extra | `pip install -e ".[swebench]"` installs `docker`, `swebench`, `pyarrow` |

Verify Docker:

```bash
docker info
python -c "import docker; docker.from_env().ping(); print('docker ok')"
```

## Install harness extra

From the repository root:

```bash
pip install -e ".[swebench]"
earnbench swebench --help
```

If `swebench` import fails, the CLI exits with install instructions.

## Metadata parquet

SWE-bench Verified instance metadata is **not** included in this software repository.
In the monorepo layout, place or symlink:

```text
../paper/vendor/swe_verified_test.parquet
```

Obtain Verified metadata per the paper dataset card
(`paper/artifact/dataset_card.md` in the monorepo; Hugging Face upstream).

## Image lifecycle

1. **`preflight`** — inspect required images; optionally `--build-missing-images`
2. **`run-nominal` / `run-pi-*`** — grade patches inside containers
3. **Caches** — default `<output>/<instance_id>/.swebench_cache` (gitignored)

EarnBench wraps container creation with cleanup hooks to reduce orphaned containers
after failed batches ([docker_container_cleanup.md](docker_container_cleanup.md)).

## Recommended smoke workflow

See [REPRODUCIBILITY.md](REPRODUCIBILITY.md#smoke-reproduction-single-instance) for
the `psf__requests-1724` sequence (prepare → preflight → run-nominal → π runs).

## Parallelism defaults

When flags are omitted, workers and container caps default to `min(cpu_count(), 12)`.
Override for constrained hosts:

```bash
earnbench phase-a run ... \
  --workers 4 \
  --max-parallel-containers 2 \
  --max-parallel-builds 2
```

## pi_env hardening flags

`pi_env.v1` may set `network_disabled`, `PYTHONNOUSERSITE`, and `PIP_NO_INDEX`.
When legitimate runtime requirements fail under hardening, the instrument records
`status=invalid` (excluded from EF denominator), not a false harness failure.
Use `earnbench swebench diagnose-pi-env` to inspect mismatches.

## Security notes

- Grading executes upstream SWE-bench shell scripts inside containers.
- Restrict Docker socket access on shared machines.
- Do not mount sensitive host paths into harness containers.

## Troubleshooting

| Symptom | Action |
|---------|--------|
| `Image not found` | Run `preflight --build-missing-images` |
| Stuck containers | See [docker_container_cleanup.md](docker_container_cleanup.md) |
| OOM during batch | Lower `--max-parallel-containers` |
| `ModuleNotFoundError: swebench` | `pip install -e ".[swebench]"` |

## What is excluded from Zenodo software archive

Per `.gitignore`, local harness logs, `.swebench_cache/`, `experiments/runs/`, and
`logs/` are **not** published with the code deposit. Frozen batch outputs belong in
the paper supplement bundle.
