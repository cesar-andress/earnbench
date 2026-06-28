# External Label Agreement

Post-hoc scaffold for crossing **frozen EarnBench Phase A/B `summary.csv` rows** with **externally sourced SWE-bench validity audit labels** (PatchDiff, SWE-bench+, SWE-ABS, SWE-Shield, or similar).

This analysis is **read-only**: it does not modify EF@Π, Π membership, INVALID semantics, or any frozen experimental outputs.

## CLI

```bash
earnbench validation external-label-agreement \
  --summary path/to/summary.csv \
  --labels path/to/external_labels.csv \
  --output out/external_label_agreement
```

Validate labels schema only (labels file may be a placeholder until audits publish instance tables):

```bash
earnbench validation external-label-agreement \
  --labels path/to/external_labels.csv \
  --validate-only
```

## External labels CSV schema

| Column | Required | Description |
| --- | --- | --- |
| `instance_id` | yes | SWE-bench instance id (must match `summary.csv`) |
| `source` | yes | Audit provenance (`patchdiff`, `swe_bench_plus`, `swe_abs`, `swe_shield`, …) |
| `label_name` | yes | Label type (see categories below) |
| `label_value` | yes | Polarity / severity token (`true`, `false`, `yes`, `no`, …) |
| `label_confidence` | no | Optional numeric confidence in `[0, 1]` |
| `notes` | no | Free-text provenance or citation to audit row |

Template: `paper/experiments/external_label_agreement.template.csv`

### Supported label categories

| `label_name` (examples) | Category | Typical external polarity when `label_value` is positive |
| --- | --- | --- |
| `weak_test`, `harness_false_pass` | `weak_test` | concern |
| `overfit`, `abs_reject` | `overfit` | concern |
| `design_violation`, `constraint_violation` | `design_violation` | concern |
| `patch_incorrect`, `behavioral_divergence`, `solution_leakage` | `other_concern` | concern |
| `clean`, `oracle_match`, `passes_dev_tests` | `clean` | clean |

Positive `label_value` tokens include: `1`, `true`, `yes`, `flag`, `concern`, `fail`, `rejected`.

Negative / clean tokens include: `0`, `false`, `no`, `clean`, `pass`, `accepted`.

## Analyses

1. **Overlap** — count of instances present in both summary and labels.
2. **EF mean by external label** — grouped by `(source, label_name)`.
3. **Low-EF / false-unearned rates by external label** — uses τ = 0.95 (`FALSE_EARNED_THRESHOLD`) for low-EF; also reports `false_unearned` column when present.
4. **Agreement table** — cross-tab of EF band (`low` / `high` / `undefined`) × external polarity (`concern` / `clean`) by label category.
5. **Disagreement cases**
   - `external_flag_ef_high` — external concern label but EF ≥ τ on Y₀=1
   - `ef_low_external_clean` — EF < τ but external label reads clean

Partial label coverage is expected; always report overlap counts and treat external audits as **convergent contextual evidence**, not validation of EarnBench itself.

## Output artifacts

| File | Description |
| --- | --- |
| `external_label_agreement.json` | Summary metrics, overlap, concordance rate |
| `external_label_agreement.csv` | Case-level join with `agreement_cell` |
| `external_label_by_label.csv` | EF mean and rates by source/label |
| `external_label_agreement_table.csv` | EF band × external polarity counts |
| `external_label_disagreements.csv` | Disagreement rows only |
| `external_label_agreement.md` | Human-readable report |

Schema version: `earnbench.external_label_agreement.v1`

## Interpretation guardrails

- Do **not** treat PatchDiff / SWE-ABS concordance as proof that EF@Π is calibrated; these audits measure different constructs.
- Solution leakage in issue text is **out of MVP Π**; low agreement on leakage-labeled instances does not imply instrument failure.
- Strengthened test suites (SWE-ABS) can reject golden patches; `ef_low_external_clean` may reflect earned-but-fragile instrument disagreement.
- Agent scaffold effects change which instances enter the nominal-success set; stratify by scaffold before ERS-style cross-audit comparisons.

## Related docs

- `paper/experiments/external_label_agreement_protocol.md`
- `docs/validation_ladder.md`
- Paper §Related Work (SWE-bench validity) and §Threats (upstream validity)
