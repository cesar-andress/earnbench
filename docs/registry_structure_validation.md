# Registry Structure Validation (Validation 11)

Post-hoc **internal-validity** analysis of multichannel perturbation registry Π on Phase A/B/blind/Phase C `summary.csv` rows. Does **not** modify EF@Π, Π membership, INVALID semantics, or existing report generators.

**Paper notes:** `paper/notes/registry_structure_validation.md`  
**Related (narrower):** `earnbench report registry-geometry`

## Command

```bash
earnbench report registry-structure \
  --summary SUMMARY_CSV \
  --output OUT
```

`--summary` accepts a path to `summary.csv` or a batch output directory containing one.

## Primary cohort

Rows with `y0=true` and valid terminal status on all three MVP π operators (`success` or `fail`). Rows with `INVALID`/`ERROR` are excluded from primary channel-structure estimates and reported in `registry_invalid_distribution.csv`.

## Outputs

| File | Content |
|------|---------|
| `registry_structure_report.md` | Human-readable summary with scope disclaimers |
| `registry_structure_summary.json` | Full payload |
| `registry_cofailure_matrix.csv` | Pairwise both-fail, Jaccard, phi, odds ratio |
| `registry_overlap.csv` | Off-diagonal redundancy estimates |
| `registry_unique_detection.csv` | Per-channel unique vs shared failures + examples |
| `registry_information_content.csv` | EF ablation sensitivity and redundancy ratio |
| `registry_same_ef_profiles.csv` | Rows where equal EF hides distinct profiles |
| `registry_dimensionality.json` | Failure-vector covariance eigenvalues + interpretation |
| `registry_invalid_distribution.csv` | Per-channel INVALID/ERROR rates and bias notes |

## Analyses

1. **Co-failure matrix** — Jaccard, phi, odds ratio (descriptive; not causal)  
2. **Unique detection** — single-channel-only failures  
3. **Information content** — EF change under channel ablation, redundancy ratio  
4. **Same EF, different profile** — scalar EF collapse examples  
5. **Registry dimensionality** — eigenvalue decomposition of failure indicators  
6. **INVALID localisation** — measurement missingness by channel  

## Interpretation discipline

- Not a new EF estimand.  
- Does not claim causal channel interaction.  
- Does not claim independence unless supported by measured association.  
- Report redundancy honestly when observed.

## Validation ladder

Validation layer **11** in `paper/experiments/validation_ladder.md`.
