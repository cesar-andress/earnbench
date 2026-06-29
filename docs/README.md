# EarnBench documentation

User-facing guides for the public measurement instrument (release **`v1.0.0`**, Zenodo
DOI [10.5281/zenodo.21019033](https://doi.org/10.5281/zenodo.21019033)).

## Start here

- [Reproducibility guide](REPRODUCIBILITY.md) — monorepo layout, frozen runs, smoke/batch replay
- [Docker setup](docker_setup.md) — SWE-bench harness prerequisites and troubleshooting
- [Release and versioning policy](release_policy.md)
- [Zenodo readiness checklist](zenodo_checklist.md)

## Environment variables

EarnBench reads credentials from the shell environment; CLI flags are overrides only.

| Variable | Used by |
|----------|---------|
| `EARNBENCH_GIT_COMMIT` | Provenance block override in reports |
| `GITHUB_TOKEN` | `controls generate-manifest` (GitHub REST enrichment) |
| `OPENAI_API_KEY` | External agent CLIs configured in Phase C `arms.yaml` |
| `ANTHROPIC_API_KEY` | External agent CLIs configured in Phase C `arms.yaml` |
| `GEMINI_API_KEY` | External agent CLIs configured in Phase C `arms.yaml` |

Phase C `external_cli` subprocesses inherit the parent environment, so provider
CLIs pick up these keys without EarnBench passing them on the command line.

## Measurement and protocols

- [Perturbation outcome classification](outcome_classification.md)
- [Validation ladder CLI](validation_ladder.md)
- [External unearned anchor](external_unearned_anchor.md)
- [Policy-level earned credit (stochastic agents)](policy_earned_credit.md)
- [Policy variance decomposition (Phase C′)](policy_variance.md)
- [Phase C′ pilot manifest validation](phase_c_prime.md)
- [Phase D agent re-grade (ERS glue)](phase_d_ers.md)
- [Registry geometry report](registry_geometry.md)
- [Registry Structure Validation (Validation 11)](registry_structure_validation.md)
- [Policy-level EF reference (legacy alias)](policy_ef.md)
- [Maintainer-certified correctness anchor](maintainer_certified_correctness.md)
- [Certified correct control study (legacy)](certified_correct_controls.md)
- [Docker container cleanup (Phase A/B idempotency)](docker_container_cleanup.md)

## Release and publication

- [Publication readiness audit](publication_readiness_audit.md)
- [Release notes](../RELEASE_NOTES.md)
- [Changelog](../CHANGELOG.md)

Paper supplement (monorepo `paper/` tree): protocols under `paper/experiments/`,
frozen runs under `paper/experiments/runs/`, Zenodo bundle policy in
`paper/experiments/zenodo_output_policy.md`.
