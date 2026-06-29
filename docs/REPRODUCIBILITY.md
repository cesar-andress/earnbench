# Reproducibility guide

EarnBench separates a **public software instrument** (this repository) from a
**paper supplement** (frozen CSVs, exploit patches, Docker batch outputs, gate memos).
Both are required to reproduce headline validation numbers in the TOSEM manuscript.

## Archive model (Zenodo)

| Deposit | Contents | Location |
|---------|----------|----------|
| **Software** | Python package, registry, CLI, tests, synthetic example | This git repository → Zenodo software upload |
| **Supplement** | Frozen run trees, aggregate tables, protocols, manifest | `paper/experiments/` in monorepo → separate Zenodo dataset (see `paper/experiments/zenodo_output_policy.md`) |

Do **not** modify files under `paper/experiments/runs/` when reproducing published
results; treat them as read-only evidence.

## Monorepo layout

Recommended checkout for full reproduction:

```text
workspace/
├── earnbench/          ← this repository (instrument)
└── paper/              ← TOSEM supplement (manuscript + frozen runs)
    ├── experiments/
    │   ├── frozen_instrument_manifest.json
    │   ├── runs/
    │   │   ├── phase_a_verified_full/
    │   │   ├── phase_b_all15/
    │   │   └── blind_run/
    │   └── protocols (*.md)
    └── vendor/
        └── swe_verified_test.parquet
```

Relative paths in CLI defaults and the frozen manifest assume `../paper/` from the
instrument root.

## Instrument pin

Before batch replay, verify the instrument matches the frozen manifest:

```bash
cd earnbench
git rev-parse HEAD
# Compare to paper/experiments/frozen_instrument_manifest.json → software.git_commit
```

Manifest status `pending_signoff` means gate memos are not yet signed; frozen run
outputs are cited as-is without changing pinned SHAs.

## Reproducing from the archived release

Check out tag **`v1.0.0`** (or download the Zenodo software tarball) before batch replay:

```bash
git clone https://github.com/cesar-andress/earnbench.git
cd earnbench
git checkout v1.0.0
pip install -e ".[dev,swebench]"
```

The version of record for citation is Zenodo DOI
**[10.5281/zenodo.21019033](https://doi.org/10.5281/zenodo.21019033)**.

## What you can reproduce from this repo alone

| Task | Requires Docker | Requires `paper/` |
|------|-----------------|-------------------|
| `pytest` | No | No |
| `earnbench compute` on JSON fixtures | No | No |
| `examples/synthetic_visible_test_overfitting.py` | No | No |
| `earnbench registry validate` | No | No |
| GA smoke on `psf__requests-1724` | Yes | Yes (metadata parquet) |
| Phase A full Verified batch | Yes | Yes |
| Phase B exploit batch | Yes | Yes (exploit patches) |
| Blind injection batch | Yes | Yes (injection specs + patches) |

## Environment setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e ".[dev,swebench]"
pytest -q
```

Python **3.10–3.12** is supported (see CI matrix).

Optional environment variables (never commit secrets):

| Variable | Purpose |
|----------|---------|
| `EARNBENCH_GIT_COMMIT` | Override provenance git pin in reports |
| `GITHUB_TOKEN` | Maintainer controls manifest enrichment |
| Provider API keys | Phase C external agent CLIs only |

## Smoke reproduction (single instance)

After [Docker setup](docker_setup.md) and obtaining Verified metadata:

```bash
export PARQUET=../paper/vendor/swe_verified_test.parquet
export OUT=/tmp/earnbench_smoke

earnbench swebench prepare-smoke \
  --metadata-parquet "$PARQUET" \
  --instance-id psf__requests-1724 \
  --output "$OUT"

earnbench swebench preflight \
  --metadata-parquet "$PARQUET" \
  --instance-id psf__requests-1724 \
  --output "$OUT" \
  --build-missing-images

earnbench swebench run-nominal \
  --metadata-parquet "$PARQUET" \
  --instance-id psf__requests-1724 \
  --patch "$OUT/psf__requests-1724/patch/prod_only.patch" \
  --output "$OUT"
```

Compare digests and layout to values recorded in `frozen_instrument_manifest.json`.

## Batch reproduction (headline cohorts)

Pre-registered commands and instance manifests live under `paper/experiments/`:

| Phase | Run directory | Selection manifest |
|-------|---------------|-------------------|
| Phase A full Verified | `runs/phase_a_verified_full/` | `phase_a_instance_selection_verified_full.json` |
| Phase A pilot300 (calibration) | `runs/phase_a_pilot300/` | `phase_a_instance_selection_300.json` |
| Phase B all15 | `runs/phase_b_all15/` | exploit specs under `paper/exploits/` |
| Blind injection | `runs/blind_run/` | `blind_mechanism_injection_protocol.md` |

Use `earnbench phase-a run`, `earnbench phase-b run`, and blind batch tooling documented
in the main [README](../README.md). Resume flags (`--resume`) support long Docker batches.

## Verifying frozen aggregates without re-run

Headline tables in the manuscript copy from:

- `paper/experiments/runs/phase_a_verified_full/statistics.json`
- `paper/experiments/runs/phase_b_all15/statistics.json`
- `paper/experiments/runs/blind_run/blind_injection_summary.json`

Deterministic report regeneration:

```bash
earnbench report phase-a ../paper/experiments/runs/phase_a_verified_full
earnbench report phase-b ../paper/experiments/runs/phase_b_all15
```

## Citation

Cite Zenodo DOI **[10.5281/zenodo.21019033](https://doi.org/10.5281/zenodo.21019033)** or tag
**`v1.0.0`** per [CITATION.cff](../CITATION.cff). See [zenodo_checklist.md](zenodo_checklist.md)
for archive scope (software vs companion supplement).

## Related documents

- [docker_setup.md](docker_setup.md)
- [release_policy.md](release_policy.md)
- [publication_readiness_audit.md](publication_readiness_audit.md)
- Paper supplement: `paper/experiments/zenodo_output_policy.md`
- Paper artifact checklist: `paper/artifact/acm_artifact_checklist.md`
