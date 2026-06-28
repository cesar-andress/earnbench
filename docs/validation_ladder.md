# Validation ladder CLI

Analysis scaffolds and schema validators for the EarnBench validation ladder (`paper/experiments/validation_ladder.md`).

## Commands

### Bootstrap uncertainty (layer 7)

```bash
earnbench validation bootstrap path/to/phase_a_or_b --output out/bootstrap \
  --bootstrap-draws 10000 --bootstrap-seed 0
```

Writes `bootstrap_metrics.csv` and `bootstrap_uncertainty.json`.

### Π ablation (layer 6)

```bash
earnbench validation ablation path/to/phase_a_or_b --output out/ablation
```

Writes `pi_ablation.csv` and `pi_ablation.json`. Post-hoc sensitivity only; does not change EF semantics.

### Monte Carlo EF simulation (layer 8)

```bash
earnbench validation monte-carlo --output out/mc \
  --instances 100 --pi-count 3 --survival-prob 0.8 --invalid-prob 0.0 \
  --simulation-draws 10000 --seed 0
```

Writes `monte_carlo_summary.json` and `monte_carlo_metrics.csv`.

### Cross-oracle agreement (layer 3C)

```bash
earnbench validation cross-oracle cross_oracle_comparison.csv --output out/xoracle
earnbench validation cross-oracle cross_oracle_comparison.csv --validate-only
```

Template: `paper/experiments/cross_oracle_comparison.template.csv`.

### External label agreement (SWE-bench validity audits)

```bash
earnbench validation external-label-agreement \
  --summary path/to/summary.csv \
  --labels path/to/external_labels.csv \
  --output out/external_labels
earnbench validation external-label-agreement \
  --labels path/to/external_labels.csv \
  --validate-only
```

Template: `paper/experiments/external_label_agreement.template.csv`.  
Protocol: `paper/experiments/external_label_agreement_protocol.md`.

Writes `external_label_agreement_summary.json`, `external_label_agreement_by_source.csv`, `external_label_agreement_by_label.csv`, `external_label_agreement_confusion.csv`, `external_label_agreement_disagreements.csv`, and `external_label_agreement_report.md`.

Optional: `--ef-threshold 0.95` (default) for low-EF band on Y₀=1 defined rows.

Post-hoc agreement only; does not modify EF@Π, Π, INVALID, or frozen results.

### Stress-test catalog (layer 4)

```bash
earnbench validation stress-test validate-catalog stress_test_catalog.template.csv
```

### Registry evolution scenario (layer 9)

```bash
earnbench validation registry-evolution validate-scenario registry_evolution_scenario.template.json
```

### Registry agreement table (layer 10)

```bash
earnbench validation registry-agreement validate-table registry_agreement.template.csv
```

### External exploit catalog (layer 3A)

See [external_exploit_validation.md](external_exploit_validation.md).

## Frozen boundaries

These tools analyze or validate schemas only. They do **not** modify EF semantics, MVP Π membership, invalid semantics, or Phase A frozen results.
