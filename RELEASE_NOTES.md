# Release Notes — EarnBench v1.0.0

**Initial Reproducibility Release** — archived on Zenodo.

| Field | Value |
|-------|-------|
| **Tag** | `v1.0.0` |
| **GitHub release title** | EarnBench v1.0.0 — Initial Reproducibility Release |
| **Zenodo title** | EarnBench: Executable Counterfactual Measurement of Earned Success for Software Engineering Agents |
| **Zenodo DOI** | [10.5281/zenodo.21019033](https://doi.org/10.5281/zenodo.21019033) |
| **Repository** | https://github.com/cesar-andress/earnbench |

---

## Summary

First public reproducibility release of the EarnBench measurement instrument: judge-free
Earned Fraction (EF@Π) under a frozen three-channel perturbation registry
(`pi_vtest.v1`, `pi_verif.v1`, `pi_env.v1`) for SWE-bench-class patch artifacts.

This release packages the **executable software instrument and CLI** used in the TOSEM
manuscript validation ladder. Frozen experiment CSVs and Docker batch outputs cited in
the paper live in the companion monorepo supplement layout (`paper/experiments/runs/`);
they are **not** redistributed inside the Zenodo software tarball and may be archived
separately as a dataset if needed.

---

## Highlights

- EF@Π metric with INVALID handling and dual sensitivity bands
- Perturbation registry v1 with CLI introspection
- SWE-bench Docker batch paths: Phase A/B, blind injection, Phase C/D scaffolding
- Report generators: Phase A/B summaries, registry geometry/structure, rank-stability protocol
- Synthetic example requiring no Docker
- 464+ unit tests; CI on Python 3.10–3.12

---

## Installation

```bash
git clone https://github.com/cesar-andress/earnbench.git
cd earnbench
git checkout v1.0.0
pip install -e ".[dev]"        # metric + tests
pip install -e ".[swebench]"   # Docker SWE-bench grading
earnbench --help
python examples/synthetic_visible_test_overfitting.py
```

---

## Reproducing frozen validation evidence

Full headline results (Phase A full Verified, Phase B, blind injection) require:

1. Monorepo layout with sibling `paper/` directory
2. SWE-bench Verified metadata at `paper/vendor/swe_verified_test.parquet`
3. Instrument pin in `paper/experiments/frozen_instrument_manifest.json`
4. Docker daemon, ~50 GB+ disk for image caches

See [docs/REPRODUCIBILITY.md](docs/REPRODUCIBILITY.md) and [docs/docker_setup.md](docs/docker_setup.md).

---

## Known limitations

1. **Monorepo dependency:** exploit patches, metadata parquet, and frozen run trees
   default to paths under a sibling `paper/` directory.
2. **Software vs supplement:** this Zenodo deposit does not include multi-GB batch outputs;
   regenerate locally or use a separate dataset deposit when available.
3. **Phase D agent re-grade** and empirical ERS tables remain manuscript-scoped; see paper
   for reported scope.

---

## Citation

```bibtex
@software{earnbench2026,
  author = {Andr{\'e}s, C{\'e}sar},
  title = {EarnBench: Executable Counterfactual Measurement of Earned Success for Software Engineering Agents},
  year = {2026},
  version = {1.0.0},
  doi = {10.5281/zenodo.21019033},
  url = {https://doi.org/10.5281/zenodo.21019033}
}
```

Or use [CITATION.cff](CITATION.cff).

---

## Full changelog

See [CHANGELOG.md](CHANGELOG.md) (includes historical `[0.1.0-rc1]` entry).
