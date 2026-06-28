# External Label Agreement

Post-hoc scaffold for crossing **frozen EarnBench Phase A/B `summary.csv` rows** with **externally sourced SWE-bench validity audit labels** (PatchDiff, SWE-bench+, SWE-ABS, SWE-Shield, Cursor-style audits, or compatible sources).

This analysis is **read-only**: it does not modify EF@Π, Π membership, INVALID semantics, or any frozen experimental outputs.

## CLI

```bash
earnbench validation external-label-agreement \
  --summary path/to/summary.csv \
  --labels path/to/external_labels.csv \
  --output out/external_label_agreement \
  --ef-threshold 0.95
```

Validate labels schema only (placeholder CSVs OK until audit tables publish):

```bash
earnbench validation external-label-agreement \
  --labels path/to/external_labels.csv \
  --validate-only
```

## Summary input (`summary.csv`)

Uses existing frozen EarnBench batch summaries via `load_phase_summary_rows`. Expected columns include:

| Column | Role |
| --- | --- |
| `instance_id` | Join key |
| `y0` | Nominal pass gate |
| `ef_pi` | Headline EF@Π |
| `ef_exclude_invalid`, `ef_invalid_as_fail` | Dual-band columns (read-only) |
| `ef_status` | `defined` vs undefined/invalid — **undefined EF is excluded from low/high bands, not treated as zero** |
| `false_unearned` | Phase A false-unearned flag |
| `retained` | Retained-at-EF=1 flag |
| `invalid_pi_rate` | Invalid π rate when present |
| `failed_mechanisms` | Optional `;`-separated channel list (Phase D / injection CSVs) |

## External labels CSV schema

| Column | Required | Description |
| --- | --- | --- |
| `instance_id` | yes | SWE-bench instance id |
| `source` | yes | Audit provenance (`patchdiff`, `swe_bench_plus`, `swe_abs`, `swe_shield`, `cursor`, …) |
| `label_name` | yes | Label type (see categories below) |
| `label_value` | yes | Polarity token (`true`, `false`, `yes`, `flagged`, …) |
| `label_confidence` | no | Numeric confidence in `[0, 1]` |
| `notes` | no | Free-text provenance |
| `citation_key` | no | BibTeX key for audit source |
| `url` | no | Source URL |

Template: `paper/experiments/external_label_agreement.template.csv`

**Multiple labels per instance** are supported (one row per label).

### Supported label categories

| `label_name` (examples) | Category | External polarity when positive |
| --- | --- | --- |
| `weak_test`, `harness_false_pass` | `weak_test` | flagged |
| `overfit`, `abs_reject` | `overfit` | flagged |
| `design_violation` | `design_violation` | flagged |
| `patch_incorrect`, `behavioral_divergence`, `solution_leakage` | `other_concern` | flagged |
| `benchmark_inflation`, `retrieval_exploit`, `cursor_audit_flag` | `other_concern` | flagged |
| `clean`, `oracle_match`, `passes_dev_tests` | `clean` | clean |

## Analyses

1. **Overlap by source** — instances in both summary and each audit source
2. **Label coverage by source** — `summary_coverage_rate`, `source_match_rate`
3. **EF mean by external label** — grouped by `(source, label_name)`
4. **Low-EF rate** — EF < τ on Y₀=1 defined rows (default τ=0.95, `--ef-threshold`)
5. **False-unearned rate** — from `false_unearned` column when present
6. **Retained rate** — from `retained` column when present
7. **Invalid-π rate mean** — from `invalid_pi_rate` when present
8. **Failed-mechanism row count** — rows with non-empty `failed_mechanisms`
9. **Confusion table** — four decidable cells:
   - `ef_low_vs_external_flagged`
   - `ef_high_vs_external_clean`
   - `ef_low_vs_external_clean` (disagreement: `ef_low_external_clean`)
   - `ef_high_vs_external_flagged` (disagreement: `external_flagged_ef_high`)

Partial label coverage is expected; always report overlap denominators.

## Output artifacts

| File | Description |
| --- | --- |
| `external_label_agreement_summary.json` | Overlap, concordance, threshold, by-source summary |
| `external_label_agreement_by_source.csv` | Overlap and coverage per audit source |
| `external_label_agreement_by_label.csv` | EF and rate metrics per `(source, label_name)` |
| `external_label_agreement_confusion.csv` | EF band × external polarity counts |
| `external_label_agreement_disagreements.csv` | Disagreement rows only |
| `external_label_agreement_report.md` | Human-readable report |

Schema version: `earnbench.external_label_agreement.v2`

## Interpretation guardrails

- External labels are **convergent contextual evidence**, not validation of EarnBench itself.
- Do **not** claim PatchDiff / SWE-ABS results validate EF@Π operating characteristics.
- Solution leakage in issue text is **out of MVP Π**; low agreement does not imply instrument failure alone.
- Strengthened suites (SWE-ABS) can reject golden patches → `ef_low_external_clean` may reflect earned-but-fragile disagreement.

## Related docs

- `paper/experiments/external_label_agreement_protocol.md`
- `docs/validation_ladder.md`
