# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
and the [release policy](docs/release_policy.md).

## [Unreleased]

## [1.0.0] - 2026-06-28

Initial public reproducibility release archived on Zenodo
([10.5281/zenodo.21019033](https://doi.org/10.5281/zenodo.21019033)).

### Added

- Zenodo DOI propagated to `CITATION.cff`, README, and `.zenodo.json`
- Post-release Zenodo checklist documenting software vs supplement archive split

### Changed

- Package version aligned to `1.0.0` across `VERSION`, `pyproject.toml`, and `__version__`
- Release notes and reproducibility docs cite tag `v1.0.0` and minted DOI
- PyPI classifier: Production/Stable

### Archive scope

- **Software deposit:** instrument, registry, CLI, tests, synthetic example, docs
- **Excluded:** large frozen run trees and Docker batch outputs under `paper/experiments/runs/`
  (companion dataset deposit if needed)

## [0.1.0-rc1] - 2026-06-28

First public **release candidate** (superseded by `v1.0.0`). Packages the measurement
instrument and CLI used in the TOSEM manuscript validation ladder.

See git tag `v0.1.0-rc1` for the RC snapshot. Do not cite RC1 when the Zenodo DOI is available.

[Unreleased]: https://github.com/cesar-andress/earnbench/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/cesar-andress/earnbench/releases/tag/v1.0.0
[0.1.0-rc1]: https://github.com/cesar-andress/earnbench/releases/tag/v0.1.0-rc1
