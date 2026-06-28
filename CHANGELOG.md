# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
and the [release policy](docs/release_policy.md).

## [Unreleased]

## [0.1.0-rc1] - 2026-06-28

First public **release candidate** (pre-release). Packages the measurement instrument
and CLI used in the TOSEM manuscript validation ladder. Frozen experiment CSVs and
Docker batch outputs remain in the companion `paper/experiments/runs/` supplement layout.

### Added

- Release candidate metadata: `VERSION`, `.zenodo.json`, `RELEASE_NOTES.md`, CI workflow
- Release and versioning policy (`docs/release_policy.md`)
- Zenodo readiness checklist (`docs/zenodo_checklist.md`)
- Reproducibility and Docker setup guides (`docs/REPRODUCIBILITY.md`, `docs/docker_setup.md`)
- Synthetic visible-test overfitting example (`examples/synthetic_visible_test_overfitting.py`)
- MVP Earned Fraction metric (`compute_earned_fraction`, `EarnedFractionReport`)
- Perturbation registry v1 (`pi_vtest.v1`, `pi_verif.v1`, `pi_env.v1`) with SWE-bench executors
- Phase A/B batch runners, blind injection CLI, report generators, validation-layer tooling
- Unit tests (460+) and GitHub Actions CI on Python 3.10–3.12

### Changed

- README, CONTRIBUTING, and docs index updated for RC1 publication layout
- Package classifier: Beta (release candidate)
- Repository URLs aligned to `https://github.com/cesar-andress/earnbench`

### Known limitations

- `frozen_instrument_manifest.json` in the paper repo remains `pending_signoff`
- Standalone clone cannot replay full Docker batches without sibling `paper/` paths
- Zenodo DOI pending; cite tag `v0.1.0-rc1` or git SHA

## [0.1.0] - TBD

_Not released._ Final release after manifest sign-off, author metadata finalization,
and Zenodo deposit. See [release policy](docs/release_policy.md).

[Unreleased]: https://github.com/cesar-andress/earnbench/compare/v0.1.0-rc1...HEAD
[0.1.0-rc1]: https://github.com/cesar-andress/earnbench/releases/tag/v0.1.0-rc1
[0.1.0]: https://github.com/cesar-andress/earnbench/releases/tag/v0.1.0
