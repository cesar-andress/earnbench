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
then exits with code **2** and a clear *not implemented* message. Real Docker /
SWE-bench execution will replace this in a later release.

Equivalent module invocation:

```bash
python -m earnbench compute tests/fixtures/compute_input.json
```

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
