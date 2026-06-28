# Release Notes — EarnBench v0.1.0-rc1

**Release candidate (not final).** Do not cite a Zenodo DOI until `v0.1.0` is tagged
and archived. Until then, cite the git commit SHA.

**Suggested tag:** `v0.1.0-rc1`  
**Suggested GitHub release title:** `v0.1.0-rc1 — First public release candidate`  
**Target date:** 2026-06-28 (draft; not published)

---

## Summary

First public release candidate of the EarnBench measurement instrument: judge-free
Earned Fraction (EF@Π) under a frozen three-channel perturbation registry
(`pi_vtest.v1`, `pi_verif.v1`, `pi_env.v1`) for SWE-bench-class patch artifacts.

This RC packages the **instrument and CLI** used in the TOSEM manuscript validation
ladder. Frozen experiment CSVs and Docker batch outputs live in the companion paper
supplement repository layout (`paper/experiments/runs/`); they are **not** all
redistributed inside this wheel.

---

## Highlights

- EF@Π metric with INVALID handling and dual sensitivity bands
- Perturbation registry v1 with CLI introspection
- SWE-bench Docker batch paths: Phase A/B, blind injection, Phase C/D scaffolding
- Report generators: Phase A/B summaries, registry geometry/structure, rank-stability protocol
- Synthetic example requiring no Docker
- 460+ unit tests (2 known failures in swebench CLI mocks — see audit report)

---

## Installation

```bash
git clone <REPOSITORY_URL>
cd earnbench
pip install -e ".[dev]"        # metric + tests
pip install -e ".[swebench]"   # Docker SWE-bench grading
earnbench --help
python examples/synthetic_visible_test_overfitting.py
```

Replace `<REPOSITORY_URL>` with the canonical public URL at release time.

---

## Known limitations (RC1)

1. **Monorepo dependency:** exploit patches, metadata parquet, and frozen run trees
   default to paths under a sibling `paper/` directory.
2. **`frozen_instrument_manifest.json`** remains `pending_signoff` in the paper repo.
3. **No Zenodo DOI** yet; `CITATION.cff` and `pyproject.toml` URLs may differ from
   the actual GitHub remote until release hygiene is complete.
4. **README** sections still describe an "early skeleton" in places — being updated
   in the RC1 documentation pass.
5. **Phase D agent re-grade** and empirical ERS tables are manuscript-pending, not
   part of this RC claim set.
6. **`vendor/swe_verified_test.parquet`** not shipped; obtain per dataset card or
   set `--metadata-path` explicitly.

---

## Upgrade / migration

N/A (first RC).

---

## Checksums

Record git commit SHA at tag time:

```bash
git rev-parse HEAD
```

Smoke artifact hashes are pinned in `paper/experiments/frozen_instrument_manifest.json`
(monorepo).

---

## Full changelog

See [CHANGELOG.md](CHANGELOG.md).
