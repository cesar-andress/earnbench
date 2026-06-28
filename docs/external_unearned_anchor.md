# External unearned anchor

External **positive anchor** for the unearned construct: cases where other benchmarks or papers already label nominal success as shortcut / hacked / reward hacking.

**Protocol:** `paper/experiments/external_unearned_anchor_protocol.md`  
**Catalog template:** `paper/experiments/external_unearned_anchor.template.csv`

## Validate catalog

```bash
earnbench external-unearned validate-catalog \
  ../paper/experiments/external_unearned_anchor.template.csv
```

## Execution pipeline

Import external patches and bind them to SWE-bench Verified instances:

```bash
earnbench external-unearned validate-manifest execution_manifest.csv \
  --catalog external_unearned_anchor.csv \
  --require-patches

earnbench external-unearned import-patches \
  --manifest execution_manifest.csv \
  --catalog external_unearned_anchor.csv \
  --patches-dir ./patches \
  --output out/external_unearned_bundle
```

Run nominal + π grading for catalog rows with `inclusion_decision=include`:

```bash
earnbench external-unearned run \
  --catalog external_unearned_anchor.csv \
  --bundle out/external_unearned_bundle \
  --output out/external_unearned_run \
  --metadata-parquet ../paper/vendor/swe_verified_test.parquet
```

Writes `external_unearned_results.csv` with columns required by the report layer.

Execution manifest CSV columns: `external_id`, `instance_id`, `patch_ref`, optional `y0_policy` (`prod_only` default).

## Results CSV schema

Required columns for harness outcomes (one row per catalog `external_id`):

| Column | Description |
|--------|-------------|
| `external_id` | Join key to catalog |
| `y0` | Nominal pass |
| `ef_pi` | Earned fraction |
| `ef_status` | `defined` or `undefined` |
| `failed_mechanisms` | Semicolon-separated channel names |

## Generate report

```bash
earnbench report external-unearned \
  --catalog external_unearned_anchor.csv \
  --results external_unearned_results.csv \
  --output out/external_unearned
```

Computes:

- included external anchors
- IN_REGISTRY detection rate
- OUT_OF_REGISTRY expected miss rate (false-negative floor)
- EF distribution and channel attribution

## Outputs

| File | Description |
|------|-------------|
| `external_unearned_report.md` | Tables 1–5 |
| `external_unearned_summary.json` | Metrics payload |
| `external_unearned_join.csv` | Catalog × results join |
| `external_unearned_channel_attribution.csv` | Detected unearned by channel |

## EF agreement analysis

Case-level agreement between external labels and EF outcomes (reviewer-facing disagreement taxonomy):

```bash
earnbench report external-unearned-agreement \
  --catalog external_unearned_anchor.csv \
  --results external_unearned_results.csv \
  --output out/external_unearned_agreement
```

| File | Description |
|------|-------------|
| `external_unearned_agreement.md` | Agreement summary, disagreements, OOR expected misses |
| `external_unearned_agreement.json` | Metrics payload |
| `external_unearned_agreement.csv` | Per-case `agreement_class` and explanation |

Agreement classes: `ef_detects`, `ef_misses_expected`, `ef_misses_unexpected`, `ef_undefined`, `ef_disagrees_with_label`.

## Relation to other anchors

| Anchor | Construct pole |
|--------|----------------|
| [Maintainer-certified correctness](maintainer_certified_correctness.md) | Earned / low false-unearned |
| External unearned anchor | Unearned / detection of shortcuts |

Does not modify EF semantics, Π, invalid semantics, or validators.
