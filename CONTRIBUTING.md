# Contributing to EarnBench

Thank you for your interest in EarnBench. This repository ships a **frozen measurement
instrument** (Earned Fraction under a declared perturbation registry). Contributions
must preserve reproducibility of published validation evidence.

## Scope of acceptable changes

| Area | Welcome | Requires instrument version bump |
|------|---------|----------------------------------|
| Documentation, examples, tests | Yes | No |
| Bug fixes that do not change EF on reference fixtures | Yes | PATCH |
| New perturbation IDs (`pi_*.vN+1`) | Yes | MINOR |
| Changes to EF formula, INVALID semantics, or existing `pi_*.vN` behavior | No without protocol | MAJOR |
| Rewriting frozen experiment outputs under `paper/experiments/runs/` | **No** | N/A |

See [docs/release_policy.md](docs/release_policy.md) for semver and perturbation
versioning rules.

## Development setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

For SWE-bench Docker grading (optional):

```bash
pip install -e ".[swebench]"
```

Requires Docker, sufficient disk (~50 GB+ for image caches), and network access for
first-time image builds.

## Monorepo layout

Full paper reproduction assumes a sibling `paper/` tree (exploit patches, instance
metadata parquet, frozen run artifacts). Cloning **only**
https://github.com/cesar-andress/earnbench supports:

- `earnbench compute` / `validate-audit` on JSON fixtures
- `pytest` unit tests
- `examples/synthetic_visible_test_overfitting.py`

See [docs/REPRODUCIBILITY.md](docs/REPRODUCIBILITY.md) for
standalone vs monorepo requirements.

## Pull request checklist

1. `pytest` passes on Python 3.10–3.12 (when CI is enabled).
2. `ruff check src tests` passes (if you touched Python).
3. No secrets, API keys, or large binary blobs.
4. Documentation updated for CLI or path changes.
5. If behavior affects EF on reference inputs, update
   `docs/release_policy.md` gate and bump version fields consistently.

## Commit messages

Use imperative mood and reference issue numbers when applicable:

```
Fix pi_env audit path when cache_dir is relative
docs: clarify swebench extra install for Phase A batch
```

## Code of conduct

Be respectful and precise. Disagreements about measurement semantics should cite
protocol documents (`docs/outcome_classification.md`, paper supplement) rather than
informal benchmark lore.

## Questions

Open a GitHub issue with the **question** label for clarification on instrument scope,
not for requests to reinterpret frozen Phase A/B validation numbers.
