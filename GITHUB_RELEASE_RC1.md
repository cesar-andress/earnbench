# GitHub Release Preparation — v0.1.0-rc1

## Repository settings

| Field | Value |
|-------|-------|
| **URL** | https://github.com/cesar-andress/earnbench |
| **Description** | Judge-free EF@Π measurement instrument for SWE-bench-class patch artifacts |
| **Website** | https://github.com/cesar-andress/earnbench/tree/main/docs |
| **Topics** | `benchmark-integrity`, `software-engineering`, `llm-agents`, `swe-bench`, `reward-hacking`, `counterfactual-evaluation`, `measurement-instrument`, `reproducibility` |

## Release

| Field | Value |
|-------|-------|
| **Tag** | `v0.1.0-rc1` |
| **Target** | `main` at RC commit |
| **Title** | `v0.1.0-rc1 — First public release candidate` |
| **Pre-release** | Yes |

## Release notes body

Copy [RELEASE_NOTES.md](RELEASE_NOTES.md). Add at top:

```markdown
> **Citation:** Until Zenodo DOI is live, cite tag `v0.1.0-rc1` and commit `<SHA>`.
> See [CITATION.cff](CITATION.cff).
```

## README badges (after Zenodo DOI)

Add DOI badge **only after deposit** — do not invent DOIs:

```markdown
[![DOI](https://zenodo.org/badge/DOI/<ZENODO-DOI>.svg)](https://doi.org/<ZENODO-DOI>)
```

## Pre-release checklist

- [x] Align `pyproject.toml`, `CITATION.cff`, `VERSION`, `__version__` with `0.1.0-rc1`
- [x] Repository URL → `cesar-andress/earnbench`
- [x] `pytest` green locally (464 passed)
- [x] CI workflow (`.github/workflows/ci.yml`)
- [x] README Status + reproducibility docs
- [ ] Cut git tag `v0.1.0-rc1`
- [ ] GitHub pre-release from this tag
- [ ] `frozen_instrument_manifest.json` signed in paper supplement (separate repo path)
- [ ] Finalize `.zenodo.json` creators (names, ORCIDs, affiliations)
- [ ] Enable GitHub–Zenodo integration and deposit after tag
- [ ] Update `CITATION.cff` `doi` field post-deposit

## Issue templates

See `.github/ISSUE_TEMPLATE/` and `.github/pull_request_template.md`.
