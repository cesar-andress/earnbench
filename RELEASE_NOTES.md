# Release Notes — EarnBench v0.1.0-rc1

**Release candidate (pre-release).** Do not cite a Zenodo DOI until `v0.1.0` is tagged
and archived. Until then, cite git tag `v0.1.0-rc1` or commit SHA (see [CITATION.cff](CITATION.cff)).

**Tag:** `v0.1.0-rc1`  
**GitHub release title:** `v0.1.0-rc1 — First public release candidate`  
**Repository:** https://github.com/cesar-andress/earnbench

---

## Summary

First public release candidate of the EarnBench measurement instrument: judge-free
Earned Fraction (EF@Π) under a frozen three-channel perturbation registry
(`pi_vtest.v1`, `pi_verif.v1`, `pi_env.v1`) for SWE-bench-class patch artifacts.

This RC packages the **instrument and CLI** used in the TOSEM manuscript validation
ladder. Frozen experiment CSVs and Docker batch outputs live in the companion paper
supplement layout (`paper/experiments/runs/` in the monorepo); they are **not** all
redistributed inside this software wheel.

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

## Known limitations (RC1)

1. **Monorepo dependency:** exploit patches, metadata parquet, and frozen run trees
   default to paths under a sibling `paper/` directory.
2. **`frozen_instrument_manifest.json`** remains `pending_signoff` in the paper repo.
3. **No Zenodo DOI** yet; add `doi` to `CITATION.cff` after deposit.
4. **Phase D agent re-grade** and empirical ERS tables are manuscript-pending, not
   part of this RC claim set.
5. **Author metadata** in `.zenodo.json` uses a placeholder creator list until final
   names and ORCIDs are supplied.

---

## Upgrade / migration

N/A (first RC).

---

## Checksums

Record git commit SHA at tag time:

```bash
git rev-parse HEAD
```

Smoke artifact hashes are pinned in `paper/experiments/frozen_instrument_manifest.json`
(monorepo supplement).

---

## Full changelog

See [CHANGELOG.md](CHANGELOG.md).
