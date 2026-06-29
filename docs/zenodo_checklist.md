# Zenodo Release Record — EarnBench v1.0.0

This checklist documents the **archived** software release on Zenodo. It replaces the
pre-release RC planning checklist. Companion frozen validation outputs from the TOSEM paper
live in the monorepo `paper/` supplement and are **not** part of this software deposit unless
archived separately as a dataset (see `paper/experiments/zenodo_output_policy.md` in the
manuscript monorepo).

**Software repository:** https://github.com/cesar-andress/earnbench

---

## Published release (v1.0.0)

| Field | Value |
|-------|-------|
| **Git tag** | `v1.0.0` |
| **GitHub release title** | EarnBench v1.0.0 — Initial Reproducibility Release |
| **Zenodo record type** | Software |
| **Zenodo artifact title** | EarnBench: Executable Counterfactual Measurement of Earned Success for Software Engineering Agents |
| **Zenodo DOI** | [10.5281/zenodo.21019033](https://doi.org/10.5281/zenodo.21019033) |
| **Package version** | `1.0.0` (aligned with tag) |
| **Publication date** | 2026-06-28 |
| **License** | MIT |

The DOI was minted at deposit time and has been copied back into [CITATION.cff](../CITATION.cff),
[README.md](../README.md), [.zenodo.json](../.zenodo.json), and this checklist.

The uploaded source archive corresponds to git tag **`v1.0.0`**.

---

## What this deposit contains

- [x] Python package (`src/earnbench/`), registry, CLI, tests, synthetic example
- [x] Reproducibility and Docker documentation (`docs/REPRODUCIBILITY.md`, `docs/docker_setup.md`)
- [x] Batch runner scaffolding for SWE-bench-class perturbation grading
- [x] MIT `LICENSE`, `CITATION.cff`, `CHANGELOG.md`, `RELEASE_NOTES.md`

## What this deposit excludes

Large generated experiment outputs are **not** bundled in the software archive:

- Frozen run trees under `paper/experiments/runs/` (monorepo supplement layout)
- Docker image caches, harness logs, and multi-GB batch artifacts
- Paper result bundles and aggregate CSVs used in manuscript tables

Scripts, protocols, and regeneration instructions **are** included so independent replicators
can rebuild outputs locally or from a separate dataset deposit if one is published.

---

## Pre-release checklist (completed)

### Repository structure

- [x] Top-level layout: `src/earnbench/`, `tests/`, `docs/`, `examples/`, `scripts/`
- [x] Importable Python package under `src/earnbench/`
- [x] `CHANGELOG.md` with release notes per version
- [x] `.gitignore` covers build artifacts, caches, harness logs, generated reports

### License and citation

- [x] `LICENSE` (MIT) matches `pyproject.toml` and `CITATION.cff`
- [x] `CITATION.cff` includes DOI `10.5281/zenodo.21019033`
- [x] Author metadata: César Andrés (ORCID 0009-0001-8968-3404)

### Version alignment at `v1.0.0`

- [x] Git tag `v1.0.0`
- [x] `VERSION`, `pyproject.toml`, `__version__`, `CITATION.cff`, `.zenodo.json` → `1.0.0`
- [x] GitHub release from tag
- [x] Release notes from [RELEASE_NOTES.md](../RELEASE_NOTES.md)

### Zenodo archive

- [x] Record type: Software
- [x] Source tarball matches tag `v1.0.0`
- [x] Metadata: title, creators, description, license, keywords
- [x] DOI registered and propagated to README / CITATION / `.zenodo.json`

### Validation before tag (historical)

```bash
pip install -e ".[dev]"
pytest
ruff check .
pip install -e .
python examples/synthetic_visible_test_overfitting.py
git status   # clean at tag time
```

---

## Follow-ups (optional, not blocking v1.0.0)

- [ ] Separate Zenodo **dataset** deposit for frozen paper supplement CSVs / run aggregates
- [ ] Related identifier in `.zenodo.json` for paper DOI when available
- [ ] Runtime dependency lockfile for long-horizon batch reproduction

---

## Release sign-off

| Role | Name | Date | Tag | DOI |
|------|------|------|-----|-----|
| Maintainer | César Andrés | 2026-06-28 | `v1.0.0` | 10.5281/zenodo.21019033 |

**Checklist version:** 2026-06-28 (post-release)
