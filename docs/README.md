# EarnBench documentation

Documentation is in early draft. Design notes live in the private research repository during initial development.

## Environment variables

EarnBench reads credentials from the shell environment; CLI flags are overrides only.

| Variable | Used by |
|----------|---------|
| `GITHUB_TOKEN` | `controls generate-manifest` (GitHub REST enrichment) |
| `OPENAI_API_KEY` | External agent CLIs configured in Phase C `arms.yaml` |
| `ANTHROPIC_API_KEY` | External agent CLIs configured in Phase C `arms.yaml` |
| `GEMINI_API_KEY` | External agent CLIs configured in Phase C `arms.yaml` |

Phase C `external_cli` subprocesses inherit the parent environment, so provider
CLIs pick up these keys without EarnBench passing them on the command line.

## Checklists and policy

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
- [Perturbation outcome classification](outcome_classification.md)
- [Zenodo readiness checklist](zenodo_checklist.md)
- [Release and versioning policy](release_policy.md)

## Planned topics

- Earned Fraction definitions
- Counterfactual perturbation specifications
- Harness integration (for example SWE-bench-class evaluators)
- Audit log schema — see ``earnbench.audit.AuditRecord`` (``audit.json`` fields)
