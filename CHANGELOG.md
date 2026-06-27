# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
and the [release policy](docs/release_policy.md).

## [Unreleased]

### Added

- Release and versioning policy (`docs/release_policy.md`)
- Zenodo readiness checklist (`docs/zenodo_checklist.md`)
- Synthetic visible-test overfitting example (`examples/synthetic_visible_test_overfitting.py`)
- MVP Earned Fraction metric (`compute_earned_fraction`, `EarnedFractionReport`)
- Core modules: `tasks`, `perturbations`, `runs`, `outcomes`, `metrics`, `reports`
- Unit tests for architecture, metric edge cases, and example execution
- Initial repository skeleton: README, LICENSE (MIT), CITATION.cff, pyproject.toml

### Changed

- README with installation, development, synthetic example, and policy links

## [0.1.0] - TBD

_Not released. See [release policy](docs/release_policy.md) for the v0.1.0 gate._

When released, this section will record the first benchmark-integrated artifact with
perturbation executors `pi_vtest.v1`, `pi_verif.v1`, and `pi_env.v1`.

[Unreleased]: https://github.com/earnbench/earnbench/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/earnbench/earnbench/releases/tag/v0.1.0
