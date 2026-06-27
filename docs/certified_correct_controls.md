# Certified correct control study

Documentary certification anchor for construct validity — estimates false-unearned base rate on patches certified correct **without** human adjudication panels.

**Protocol:** `paper/experiments/certified_correct_controls_protocol.md`  
**Rubric:** `paper/experiments/certified_correct_controls_rubric.md`  
**Template:** `paper/experiments/certified_correct_controls_manifest.template.csv`

## Validate manifest

```bash
earnbench controls validate-manifest \
  ../paper/experiments/certified_correct_controls_manifest.template.csv
```

Validation rules for `certified_correct` rows:

- `production_only=yes`
- `touches_tests`, `touches_verifier`, `touches_environment` must all be `no`
- `certification_status` ∈ `{certified_correct, rejected, undecidable}`

## Generate report

```bash
earnbench report certified-controls \
  --manifest path/to/manifest.csv \
  --phase-a-run path/to/phase_a_batch \
  --output out/certified_controls
```

Joins manifest rows with frozen Phase A `summary.csv` by `instance_id` and computes:

- stratum counts (certified / undecidable / rejected)
- false-unearned count and rate on certified_correct matched rows
- EF distribution, failed mechanisms, invalid sensitivity

## Outputs

| File | Description |
|------|-------------|
| `certified_controls_report.md` | Markdown summary |
| `certified_controls_summary.json` | Metrics payload |
| `certified_controls_ef_distribution.csv` | Per-control join with Phase A |
| `certified_controls_false_unearned.csv` | False-unearned detail |

## Frozen boundaries

Does not modify EF semantics, Π, invalid semantics, validators, or Phase A gate interpretation.
