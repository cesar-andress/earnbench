# Registry geometry report

Post-hoc **internal-validity** analysis of multichannel survival structure on Phase A/B `summary.csv` rows. Does **not** modify EF@Π, Π membership, INVALID semantics, or existing report generators.

**Paper notes:** `paper/notes/registry_geometry.md`

## Command

```bash
earnbench report registry-geometry \
  --summary SUMMARY_CSV \
  --output OUT
```

`--summary` accepts a path to `summary.csv` or a batch output directory containing one.

## Input

Phase A or Phase B style `summary.csv` with (at minimum):

| Column | Role |
|--------|------|
| `instance_id` | Row identifier |
| `y0` | Nominal pass |
| `y_vtest`, `y_verif`, `y_env` | Per-π survival (0/1) |
| `pi_vtest_status`, `pi_verif_status`, `pi_env_status` | Terminal outcomes (`success` / `fail` / `invalid` / `error`) |
| `ef_pi` | Outcome-level EF (optional if all π outcomes present) |
| `invalid_pi_count` | Exclusion accounting |
| `retained`, `false_unearned` | Phase A columns (ignored for primary cohort unless present) |

Optional: `agent` — when present, profile histograms are also computed per agent in `registry_geometry_summary.json`.

## Primary cohort

Primary analysis uses rows with:

- `y0 = true`
- all three π statuses valid for EF denominator (`success` or `fail`)
- all three `y_*` outcomes defined

Excluded rows are counted separately in the summary JSON (`excluded_from_primary`).

## Outputs

| File | Content |
|------|---------|
| `registry_geometry_summary.json` | Full payload: cohort counts, profiles, redundancy, same-EF examples, optional by-agent |
| `registry_geometry_profiles.csv` | Profile label, count, fraction, mean/median EF |
| `registry_geometry_cofailure_matrix.csv` | Pairwise co-failure counts, Jaccard, phi |
| `registry_geometry_channel_correlations.csv` | Pairwise redundancy estimates and unique-detection counts |
| `registry_geometry_marginal_contribution.csv` | Per-channel unique failures and EF ablation sensitivity |
| `registry_geometry_report.md` | Human-readable summary |

## Profile labels

`survive_all`, `fail_vtest`, `fail_verif`, `fail_env`, `fail_vtest_verif`, `fail_vtest_env`, `fail_verif_env`, `fail_all`

## Metrics

1. **Profiles** — histogram over primary cohort with EF moments per profile  
2. **Co-failure matrix** — both-fail / either-fail counts, Jaccard overlap, phi correlation  
3. **Marginal contribution** — single-channel-only failures, EF change under channel ablation, mean ablation delta  
4. **Redundancy** — high co-failure pairs (Jaccard ≥ 0.70) and channels with unique detections  
5. **Same EF, different profile** — instances sharing `ef_pi` but distinct failure patterns  
6. **By agent** (optional) — profile breakdown when `agent` column exists  

Channel ablation reuses the same post-hoc EF recomputation as `earnbench validation ablation` (exclude-invalid primary accounting).

## Related

- `earnbench validation ablation` — scalar EF sensitivity to removing one π from the denominator  
- `paper/experiments/validation_ladder.md` §4.3
