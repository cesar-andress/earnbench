# EarnBench RC1 — Release Checklist

**Target:** `v0.1.0-rc1` (pre-release) → `v0.1.0` (first Zenodo DOI)  
**Last updated:** 2026-06-28  
**Full audit:** [release_readiness_report.md](release_readiness_report.md)

Legend: ✓ done · ◐ partial · ✗ missing · ☐ action item

---

## Repository structure

| Item | Status | Notes |
|------|:------:|-------|
| ✓ | Public package in `earnbench/` | `src/earnbench/` layout |
| ✓ | Paper supplement in `paper/` | Monorepo workspace |
| ✓ | Workspace pointer README | `/README.md` |
| ◐ | Single public git remote | `earnbench/` only; URL alignment pending |
| ✓ | `.gitignore` | Present in package repo |

---

## Publication files

| Item | Status | Path |
|------|:------:|------|
| ✓ | README | `earnbench/README.md` (RC1 status updated) |
| ✓ | LICENSE (MIT) | `earnbench/LICENSE` |
| ✓ | CITATION.cff | `earnbench/CITATION.cff` — authors TBC |
| ✓ | CONTRIBUTING | `earnbench/CONTRIBUTING.md` |
| ✗ | CODE_OF_CONDUCT | Optional — not created |
| ✓ | CHANGELOG | `earnbench/CHANGELOG.md` — 0.1.0 date TBD |
| ✓ | RELEASE_NOTES | `earnbench/RELEASE_NOTES.md` |
| ✓ | SECURITY | `earnbench/SECURITY.md` |
| ✓ | VERSION | `earnbench/VERSION` → `0.1.0-rc1` |
| ✓ | Zenodo metadata draft | `earnbench/.zenodo.json` |
| ✓ | GitHub release draft | `earnbench/GITHUB_RELEASE_RC1.md` |

---

## Version alignment (required at tag)

| Field | Must match tag | Current |
|-------|----------------|---------|
| Git tag | `v0.1.0-rc1` | ✗ not cut |
| `VERSION` | `0.1.0-rc1` | ✓ |
| `pyproject.toml` | `0.1.0` | ◐ — bump to `-rc1` or keep 0.1.0 until final |
| `CITATION.cff` | `0.1.0` | ◐ align at tag |
| `CHANGELOG.md` | entry dated | ✗ TBD |

---

## Reproducibility

| Item | Status | Notes |
|------|:------:|-------|
| ✓ | Synthetic example (no Docker) | `examples/synthetic_visible_test_overfitting.py` |
| ✓ | Metric fixtures | `tests/fixtures/compute_input.json` |
| ✓ | Install docs | `[dev]` and `[swebench]` documented |
| ◐ | Standalone clone path | Requires monorepo for batch replay |
| ✗ | `vendor/swe_verified_test.parquet` | Empty `paper/vendor/` |
| ✓ | Smoke protocol documented | `frozen_instrument_manifest.json` |
| ◐ | Manifest sign-off | `pending_signoff` |
| ✓ | Frozen runs (paper) | `paper/experiments/runs/` — not modified |

---

## CLI & installation

| Item | Status | Notes |
|------|:------:|-------|
| ✓ | `earnbench --help` | Works when installed |
| ✓ | `compute`, `validate-audit` | Core metric path |
| ✓ | `registry validate` | Registry v1 |
| ✓ | `swebench *`, `phase-a run`, `phase-b run` | Docker paths |
| ◐ | `phase-d run` | Implemented; manuscript pending |
| ✓ | `report *` generators | Phase A/B + validation reports |
| ◐ | `earnbench run` | Stub — documented |

---

## Tests & CI

| Item | Status | Notes |
|------|:------:|-------|
| ◐ | pytest | 463 pass, **2 fail**, 10 skip |
| ✓ | CI workflow file | `.github/workflows/ci.yml` |
| ✗ | CI green on GitHub | Not run until push |
| ✗ | Coverage target | Not defined |

---

## Experiments & frozen evidence

| Item | Status | Notes |
|------|:------:|-------|
| ✓ | Phase A full Verified | 489/421 — frozen on disk |
| ✓ | Phase B all15 | Frozen |
| ✓ | Blind injection | Frozen |
| ✓ | Phase C patch collection | Complete per manuscript |
| ◐ | Phase D / ERS | In progress — not headline |
| ◐ | GA memo verified_full | Unsigned |
| ✓ | No rerun in RC1 pass | Constraint honored |

---

## Paper & supplement references

| Item | Status | Notes |
|------|:------:|-------|
| ✓ | LaTeX bind-copy clean | No TODO markers in body |
| ◐ | Bib `earnbench2026artifact` | DOI pending — honest |
| ◐ | §10 artifact section | "Zenodo pending" — update post-DOI |
| ✓ | Phase naming consistency | verified_full headline; pilot300 calibration |
| ✓ | ACM artifact checklist | `paper/artifact/acm_artifact_checklist.md` |
| ✓ | Dataset card | `paper/artifact/dataset_card.md` |

---

## GitHub release (not published)

| Item | Status |
|------|:------:|
| ☐ | Canonical repo URL decided |
| ☐ | Topics / About section set |
| ☐ | Pre-release tag `v0.1.0-rc1` pushed |
| ☐ | Release notes pasted from `RELEASE_NOTES.md` |
| ☐ | Issue/PR templates reviewed |

---

## Zenodo (not deposited)

| Item | Status |
|------|:------:|
| ☐ | Authors + ORCIDs finalized in `.zenodo.json` |
| ☐ | GitHub-Zenodo integration enabled |
| ☐ | Tag-triggered deposit tested |
| ☐ | `CITATION.cff` `doi` field updated |
| ☐ | Paper bib + §10 updated with real DOI |
| ☐ | Supplement bundle (second deposit) after GA sign-off |

---

## Post-release paper updates (prepare only)

| File | Ready to update? |
|------|------------------|
| `paper/references.bib` | ☐ After DOI |
| `paper/sections/10_artifact.tex` | ☐ After DOI |
| `paper/sections/01_introduction.tex` | ☐ After DOI |
| `paper/artifact/acm_artifact_checklist.md` | ☐ After DOI |

---

## RC1 go / no-go

| Gate | Go? |
|------|:---:|
| All BLOCKERS in audit report resolved | ✗ |
| pytest green or failures documented + waived | ◐ |
| Manifest signed | ✗ |
| URLs aligned | ✗ |
| **Public RC1 tag** | **NO-GO until B1–B3** |
| **Internal reviewer handoff** | **GO** (with monorepo instructions) |

---

*Checklist companion to [release_readiness_report.md](release_readiness_report.md).*
