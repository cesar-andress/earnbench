# EarnBench

An executable counterfactual measurement framework for estimating how much of an AI software-engineering agent's success is earned rather than supported by exploitable evaluation channels.

## Status

This repository is an **early artifact skeleton**. APIs, perturbation specs, and benchmark integrations are under active design. **No finished benchmark results are reported here.**

## Concept

EarnBench assigns an **Earned Fraction (EF)** in \([0, 1]\) to nominally successful agent outcomes. EF is computed by re-running executable grading under counterfactual perturbations that close known shortcut surfaces (for example holdout tests, trusted verifiers, and hardened execution environments). Measurement is **judge-free**: outcomes come from tests and harness logs, not LLM evaluators.

## Repository layout

```
src/earnbench/   Python package (core types and API stubs)
tests/           Unit tests
docs/            Documentation (in progress)
examples/        Usage examples (in progress)
scripts/         Helper scripts (in progress)
```

## Installation

Requires Python 3.10+.

```bash
pip install -e ".[dev]"
```

## Development

```bash
pytest
ruff check .
ruff format .
```

## License

See [LICENSE](LICENSE).

## Citation

See [CITATION.cff](CITATION.cff).
