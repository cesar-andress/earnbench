# GitHub Release Preparation — v0.1.0-rc1 (draft, not published)

## Repository settings (manual)

| Field | Recommended value |
|-------|-------------------|
| **Description** | Judge-free EF@Π measurement instrument for SWE-bench-class patch artifacts |
| **Website** | Link to `docs/README.md` or project page when available |
| **Topics** | `benchmark-integrity`, `software-engineering`, `llm-agents`, `swe-bench`, `reward-hacking`, `counterfactual-evaluation`, `measurement-instrument`, `reproducibility` |

## Suggested release

| Field | Value |
|-------|-------|
| **Tag** | `v0.1.0-rc1` |
| **Target** | `main` at signed-off commit |
| **Title** | `v0.1.0-rc1 — First public release candidate` |
| **Pre-release** | Yes (check "This is a pre-release") |

## Release notes body (copy/paste)

See [RELEASE_NOTES.md](RELEASE_NOTES.md).

Add at top after publish:

```markdown
> **Citation:** Until Zenodo DOI is live, cite commit `<SHA>` and version `0.1.0-rc1`.
> See CITATION.cff.
```

## Badges for README (after DOI exists)

```markdown
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](pyproject.toml)
[![DOI](https://zenodo.org/badge/DOI/PLACEHOLDER.svg)](https://doi.org/PLACEHOLDER)
```

Replace `PLACEHOLDER` only after Zenodo deposit — do not invent DOIs.

## Issue templates

Optional templates added under `.github/ISSUE_TEMPLATE/`.

## Pre-release checklist

- [ ] Align `pyproject.toml` and `CITATION.cff` `repository-code` URL with actual GitHub remote
- [ ] `frozen_instrument_manifest.json` signed (`status: signed` or equivalent)
- [ ] `pytest` green (or document known failures)
- [ ] README Status section updated
- [ ] Tag matches `VERSION`, `CITATION.cff`, `pyproject.toml`
- [ ] Zenodo `.zenodo.json` authors finalized
- [ ] No secrets in git history
