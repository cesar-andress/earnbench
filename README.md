# EarnBench

An executable counterfactual measurement framework for estimating how much of an AI software-engineering agent's success is earned rather than supported by exploitable evaluation channels.

## Status

This repository is an **early artifact skeleton**. APIs, perturbation specs, and benchmark integrations are under active design. **No finished benchmark results are reported here.**

## Concept

EarnBench assigns an **Earned Fraction (EF)** in \([0, 1]\) to nominally successful agent outcomes. EF is computed by re-running executable grading under counterfactual perturbations that close known shortcut surfaces (for example holdout tests, trusted verifiers, and hardened execution environments). Measurement is **judge-free**: outcomes come from tests and harness logs, not LLM evaluators.

## Repository layout

```
src/earnbench/          Python package (core types and API)
  adapters/             Benchmark adapter interfaces (SWE-bench stub)
  registry/             Versioned perturbation registry (MVP Π)
tests/                  Unit tests
docs/                   Documentation (in progress)
examples/               Usage examples (in progress)
scripts/                Helper scripts (in progress)
```

### Benchmark adapters

`earnbench.adapters` defines typed request/response schemas (`PatchArtifact`,
`BenchmarkInstance`, `AdapterConfig`, `AuditRecord`, …) and a
`SWEBenchAdapter` stub for SWE-bench Verified post-hoc re-grade. **Input
validation is implemented; Docker/harness execution is not.** Calling
`evaluate_nominal` or `evaluate_perturbation` raises `NotImplementedError`
until a later release.

## Installation

Requires Python 3.10+.

```bash
pip install -e ".[dev]"
```

After installation, the **`earnbench`** command is available on your `PATH`.

## CLI

The CLI is a **skeleton**: `compute` and `validate-audit` are fully functional;
`run` validates arguments but does not execute SWE-bench grading yet.

### Compute Earned Fraction from outcomes

Input JSON must contain a `nominal` object and a `perturbations` array (recorded
harness outcomes, not raw logs):

```bash
earnbench compute tests/fixtures/compute_input.json
```

Example input:

```json
{
  "nominal": {
    "run_id": "cli-run-001",
    "task_id": "django__django-13279",
    "success": true
  },
  "perturbations": [
    {
      "perturbation_id": "pi_vtest.v1",
      "status": "ok",
      "success": true,
      "channel": "vtest"
    }
  ]
}
```

Prints an `EarnedFractionReport` as JSON on stdout, including a nested
`provenance` object (see [Provenance](#provenance)).

Optional input fields for provenance overrides:

```json
{
  "config_digest": "sha256:run-config",
  "random_seed": 42,
  "docker_image_digest": "sha256:docker-image"
}
```

Or supply a full `"provenance"` object to pin every field for reproducibility tests.

### Validate an audit record

```bash
earnbench validate-audit path/to/audit.json
earnbench validate-audit --quiet path/to/audit.json
```

Exits with code **0** when the file matches `AuditRecord`; **nonzero** on invalid
JSON or schema violations.

### Run grading (stub)

```bash
earnbench run \
  --instance django__django-13279 \
  --patch path/to/prod_only.patch \
  --perturbation pi_vtest.v1 \
  --config path/to/run_config.yaml
```

Validates that `--patch` and `--config` exist, prints a status line on stderr,
then exits with code **2** and a clear *not implemented* message. Use
`swebench run-nominal` for real SWE-bench Docker grading (see below).

### SWE-bench smoke testing

Install optional dependencies (Verified metadata + official harness):

```bash
pip install -e ".[swebench]"
```

Real grading also requires a running **Docker** daemon.

Prepare smoke artifacts from SWE-bench Verified metadata:

```bash
earnbench swebench prepare-smoke \
  --metadata-parquet path/to/swe_verified_test.parquet \
  --instance-id psf__requests-1724 \
  --output /tmp/earnbench_smoke
```

**Phase A smoke sequence** (recommended order):

```bash
# 1. Dry-run artifact layout (no Docker)
earnbench swebench prepare-smoke \
  --metadata-parquet path/to/swe_verified_test.parquet \
  --instance-id psf__requests-1724 \
  --output /tmp/earnbench_smoke

# 2. Verify or build harness Docker images
earnbench swebench preflight \
  --metadata-parquet path/to/swe_verified_test.parquet \
  --instance-id psf__requests-1724 \
  --output /tmp/earnbench_smoke \
  --build-missing-images

# 3. Nominal golden-patch grading (F2P + P2P)
earnbench swebench run-nominal \
  --metadata-parquet path/to/swe_verified_test.parquet \
  --instance-id psf__requests-1724 \
  --patch /tmp/earnbench_smoke/psf__requests-1724/patch/prod_only.patch \
  --output /tmp/earnbench_smoke

# 4. Pristine trusted verifier (pi_verif.v1) on the same prod-only patch
earnbench swebench run-pi-verif \
  --metadata-parquet path/to/swe_verified_test.parquet \
  --instance-id psf__requests-1724 \
  --patch /tmp/earnbench_smoke/psf__requests-1724/patch/prod_only.patch \
  --output /tmp/earnbench_smoke
```

**Phase A batch scheduler** (parallel instances and π perturbations):

```bash
earnbench phase-a \
  --metadata-parquet path/to/swe_verified_test.parquet \
  --instances django__django-13279,psf__requests-1724 \
  --output /tmp/phase_a_batch \
  --workers 8 \
  --parallel-perturbations 3 \
  --max-parallel-containers 6 \
  --build-missing-images \
  --resume
```

Per instance the protocol order is unchanged: **nominal → (π_verif ∥ π_env ∥ π_vtest) → audit/EF/report**, then the batch writes `golden_validation.csv` and `phase_a_scheduler_state.json` for resume. Use `--retry-failed` with `--resume` to re-run failed jobs. π executors other than `pi_verif.v1` record `status=missing` until implemented.

**Performance settings**

Defaults (when flags are omitted) use **`min(cpu_count(), 12)`** for
`--workers`, `--max-parallel-containers`, and `--max-parallel-builds`.
Override any of them explicitly or via JSON config:

```json
{
  "workers": 12,
  "max_parallel_containers": 8,
  "max_parallel_builds": 6,
  "timeout_seconds": 1800,
  "cache_dir": "/tmp/earnbench_swebench_cache"
}
```

| Flag | Role |
|------|------|
| `--workers` | Top-level orchestration budget (batch instances / future perturbation fan-out) |
| `--max-parallel-containers` | Cap on concurrent SWE-bench grading containers |
| `--max-parallel-builds` | Cap on concurrent harness Docker image builds (`preflight`) |
| `--reuse-images` / `--no-reuse-images` | Reuse local images vs force rebuild |
| `--no-build` | Never build images during preflight |
| `--cache-dir` | Persistent harness build logs (default `<output>/<instance_id>/.swebench_cache`) |
| `--timeout-seconds` | Per-instance harness timeout (default **1800**) |
| `--config` | JSON file with any of the above |

**Sizing guidance**

| Scenario | Suggested settings | Notes |
|----------|-------------------|-------|
| High-CPU workstation (this repo default) | omit flags | Uses `min(cpu_count(), 12)` for all three caps |
| Smoke / low memory | `--workers 1 --max-parallel-containers 1 --max-parallel-builds 1` | Serial Docker for easier debugging |
| Phase A batch | `--workers 12 --max-parallel-containers 8 --max-parallel-builds 6` | Tune to **RAM / 8GB** concurrent containers |
| Build-heavy preflight | raise `--max-parallel-builds` | Parallel base/env/instance image builds |

Single-instance `run-nominal` / `run-pi-verif` still run **one grading container**;
parallel caps apply to image inspection (I/O), image builds, and future batch modes.
Before execution, commands print **effective** parallelism to stderr.

Preflight writes `<output>/<instance_id>/preflight.json` and `preflight.log`
with required Docker image names, local presence checks, and actionable build
commands when images are missing. If `run-nominal` fails because the environment
image is absent, it points you at the preflight command above.

Run **nominal** grading alone (after images exist):

```bash
earnbench swebench run-nominal \
  --metadata-parquet path/to/swe_verified_test.parquet \
  --instance-id psf__requests-1724 \
  --patch /tmp/earnbench_smoke/psf__requests-1724/patch/prod_only.patch \
  --output /tmp/earnbench_smoke
```

Writes under `<output>/<instance_id>/nominal/`:

- `grade.json` — harness outcome summary
- `harness.log` — captured harness output
- `audit.json` — `AuditRecord` with `perturbation_id: nominal.v1`

`run-pi-verif` writes the same layout under `<output>/<instance_id>/pi_verif.v1/`.
If the patch modifies protected verifier paths, grading is skipped: `audit.json`
records `status: ok`, `success: false`, and a tamper warning (expected for raw
golden patches that still contain test hunks). Use the prod-only patch from
`prepare-smoke` for the smoke pass case.

If the SWE-bench harness is not installed, the command exits with an actionable
error pointing to `pip install -e ".[swebench]"`.

Equivalent module invocation:

```bash
python -m earnbench compute tests/fixtures/compute_input.json
```

### Perturbation registry

Shipped MVP registry (`earnbench_perturbation_registry.v1`):

| ID | Closes |
|----|--------|
| `pi_vtest.v1` | Visible-test overfitting (holdout F2P re-grade) |
| `pi_verif.v1` | Verifier tampering (pristine trusted runner) |
| `pi_env.v1` | Environment shortcuts (clean-slate hardened container) |

```bash
earnbench registry list
earnbench registry show pi_vtest.v1
earnbench registry validate
```

Python API:

```python
from earnbench.registry import get, list, load_manifest, validate

spec = get("pi_vtest.v1")
errors = spec.validate_config(
    {"holdout_salt": "earnbench_v0.1_holdout_salt", "holdout_k": 2}
)
assert validate() == []
manifest = load_manifest()
all_specs = list()
```

Each spec exposes metadata (`id`, `version`, `name`, `description`,
`supported_channels`, `config_schema`, `expected_outputs`) plus an
`executor_stub` (raises `NotImplementedError` until harness integration) and a
`validator` for config dicts.

## Provenance

Every `EarnedFractionReport` and serialized `AuditRecord` includes a **`provenance`**
block describing the measurement environment:

| Field | Description |
|-------|-------------|
| `earnbench_version` | Installed package version |
| `git_commit` | Git commit hash (`EARNBENCH_GIT_COMMIT` or repo HEAD) |
| `python_version` | Interpreter version |
| `platform` | OS/platform string |
| `docker_image_digest` | Container image digest when grading runs in Docker |
| `perturbation_registry_version` | Version label for the shipped Π registry |
| `config_digest` | Hash of the run configuration |
| `timestamp_utc` | UTC timestamp (ISO-8601) |
| `random_seed` | Random seed when applicable (`null` otherwise) |
| `hostname` | Optional host identifier |
| `execution_uuid` | Unique id for this measurement execution |

Build provenance in Python:

```python
from earnbench import build_provenance, compute_earned_fraction

provenance = build_provenance(
    config_digest="sha256:abc",
    docker_image_digest="sha256:img",
    random_seed=42,
)
report = compute_earned_fraction(nominal, counterfactuals, provenance=provenance)
print(report.to_dict()["provenance"])
```

## Development

```bash
pytest
ruff check .
ruff format .
```

## Example (synthetic, no SWE-bench)

A minimal end-to-end demo simulates a nominally successful run and three
counterfactual perturbations (`visible_test_removed`, `metadata_removed`,
`verifier_hardened`). One perturbation fails and two survive, yielding
Earned Fraction \(= 2/3\).

```bash
pip install -e .
python examples/synthetic_visible_test_overfitting.py
```

The script prints an `EarnedFractionReport` summary and writes
`examples/synthetic_visible_test_overfitting.report.json`.

## License

See [LICENSE](LICENSE).

## Citation

See [CITATION.cff](CITATION.cff).

## Zenodo release

Before archiving on Zenodo, complete [docs/zenodo_checklist.md](docs/zenodo_checklist.md).

See also the [release and versioning policy](docs/release_policy.md) and [CHANGELOG.md](CHANGELOG.md).
