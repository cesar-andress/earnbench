# Maintainer-certified correctness anchor

Strongest non-human external validity anchor: **upstream maintainer acceptance** as criterion independent of EF@Π counterfactual semantics.

**Protocol:** `paper/experiments/maintainer_certified_correctness_anchor.md`  
**Template:** `paper/experiments/maintainer_certified_controls.template.csv`

## Validate manifest

```bash
earnbench controls validate-manifest \
  ../paper/experiments/maintainer_certified_controls.template.csv
```

## Generate manifest (GitHub enrichment)

Populate `merged_by_maintainer`, `issue_closed`, `upstream_commit`, `upstream_pr`,
and `upstream_issue` from public GitHub metadata. Missing or unverifiable metadata
remains `certification_status=undecidable` (no fabricated values).

```bash
earnbench controls generate-manifest \
  --phase-a-run experiments/runs/phase_a_pilot300 \
  --output experiments/maintainer_certified_controls.csv
```

Uses `GITHUB_TOKEN` from the environment automatically (higher REST API rate limits).
Optional `--github-token` overrides that value. A sidecar
`*.enrichment.json` cache records per-row verification details.

For `certification_status=certified_correct`, the schema requires:

- `merged_by_maintainer=True`, `issue_closed=True`, `production_only=True`
- `touches_tests`, `touches_verifier`, `touches_ci`, `touches_environment` all `False`
- `nominal_success=True`
- Non-empty `upstream_commit` and valid 64-char `patch_sha256`

## Generate report

```bash
earnbench report controls \
  --manifest path/to/maintainer_certified_controls.csv \
  --phase-a-run path/to/phase_a_batch \
  --output out/maintainer_certified
```

(`report certified-controls` is an alias.)

## Outputs

| File | Description |
|------|-------------|
| `maintainer_certified_report.md` | Tables 1–5 (strata, FUBR, EF, mechanisms, invalid) |
| `maintainer_certified_summary.json` | Metrics payload |
| `maintainer_certified_ef_distribution.csv` | Per-control Phase A join |
| `maintainer_certified_false_unearned.csv` | False-unearned detail |

## Frozen boundaries

Does not modify EF semantics, Π MVP, invalid semantics, or Phase A interpretation.
