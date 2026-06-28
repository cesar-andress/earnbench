# Zenodo Readiness Checklist

Use this checklist before cutting **`v0.1.0-rc1`** (pre-release) and the final **`v0.1.0`**
Zenodo deposit. Companion frozen validation outputs live in the monorepo `paper/`
supplement (separate dataset deposit per `paper/experiments/zenodo_output_policy.md`).

**Software repository:** https://github.com/cesar-andress/earnbench

---

## 1. Repository structure

- [x] Top-level layout: `src/earnbench/`, `tests/`, `docs/`, `examples/`, `scripts/`
- [x] Importable Python package under `src/earnbench/`
- [x] `CHANGELOG.md` with release notes per version (see [release policy](release_policy.md))
- [x] `docs/README.md` index linking user-facing guides
- [x] `.gitignore` covers build artifacts, caches, harness logs, generated reports
- [ ] No secrets in git history (maintainer audit before public launch)

---

## 2. License

- [x] `LICENSE` file present (MIT)
- [x] `pyproject.toml` `license` field matches `LICENSE`
- [x] `CITATION.cff` `license` field matches `LICENSE`
- [ ] Third-party dependency licenses documented if runtime deps added
- [ ] SWE-bench / Docker image license compatibility noted when integrated

---

## 3. Citation file

- [x] `CITATION.cff` present (CFF 1.2.0)
- [x] `title`, `version`, `abstract`, `keywords`, `repository-code` populated
- [x] Author metadata present (César Andrés; ORCID 0009-0001-8968-3404)
- [x] `date-released` set for RC (`2026-06-28`)
- [ ] `doi` field added after Zenodo deposit (post-release update or second deposit)
- [ ] Related identifiers: paper DOI, benchmark DOI when available

---

## 4. README clarity

- [x] One-sentence project description
- [x] RC1 status and monorepo supplement model documented
- [x] Link to [REPRODUCIBILITY.md](REPRODUCIBILITY.md) and [docker_setup.md](docker_setup.md)
- [x] License and citation pointers
- [ ] Zenodo DOI badge after first release

---

## 5. Installation instructions

- [x] Minimum Python version stated (`>=3.10`)
- [x] Editable install: `pip install -e ".[dev]"`
- [x] Runtime extra documented: `pip install -e ".[swebench]"`
- [x] Install steps in [RELEASE_NOTES.md](../RELEASE_NOTES.md)

---

## 6. Example execution

- [x] Synthetic example script: `examples/synthetic_visible_test_overfitting.py`
- [x] README commands: `pip install -e .` + run example
- [x] Example tested in CI / `tests/test_examples.py`
- [x] SWE-bench smoke path documented in README and [docker_setup.md](docker_setup.md)
- [x] Expected sample output in [examples/README.md](../examples/README.md)

---

## 7. Test suite

- [x] `pytest` configuration in `pyproject.toml`
- [x] Unit tests for package, metric, and example execution
- [x] CI workflow (`.github/workflows/ci.yml`) on push/PR to `main`
- [ ] Coverage target documented (optional)
- [x] Integration tests for swebench CLI (mocked; optional `[swebench]` extra in CI)

---

## 8. Version tag

- [ ] Git tag `v0.1.0-rc1` matches all version fields
- [x] `VERSION`, `pyproject.toml`, `__version__`, `CITATION.cff`, `.zenodo.json` → `0.1.0-rc1`
- [ ] Tag created from clean working tree
- [ ] GitHub pre-release notes from [RELEASE_NOTES.md](../RELEASE_NOTES.md)

**Current package version:** `0.1.0-rc1` (pre-release; final Zenodo DOI typically on `v0.1.0` after manifest sign-off)

---

## 9. Archived release

- [ ] GitHub release created from tag
- [ ] Zenodo deposit linked to GitHub release (automated or manual upload)
- [ ] Zenodo record type: Software
- [ ] Upload includes source tarball matching tag commit
- [ ] Zenodo metadata: title, creators, description, license, keywords
- [ ] DOI registered and copied back to `CITATION.cff` and README

---

## 10. Reproducibility metadata

- [ ] `CITATION.cff` version and date match archived release
- [ ] Container image digest(s) recorded for perturbation runs (when applicable)
- [ ] Perturbation config hashes (`pi_*.vN`) documented per release
- [ ] Audit log JSON schema version pinned in docs
- [ ] Reproduction script or Makefile target for main example
- [ ] Independent re-run instructions for paper experiments (when published)

---

## 11. Dependency pinning

- [x] Build system pinned in `pyproject.toml` (`hatchling`)
- [ ] Runtime dependencies pinned or lockfile (`requirements-lock.txt`, `uv.lock`)
- [ ] Dev dependencies pinned for CI (`pytest`, `ruff` versions recorded)
- [ ] Optional benchmark extras pinned separately
- [ ] Release notes list dependency versions used for validation

**Current state:** zero runtime dependencies; dev deps use minimum versions only.

---

## 12. Expected runtime

- [ ] Document wall-clock for synthetic example (order of seconds)
- [ ] Document wall-clock per instance for SWE-bench perturbations (when added)
- [ ] Document total compute for paper-scale experiments
- [x] Synthetic example: sub-second metric computation; no GPU required

---

## 13. Hardware assumptions

- [x] Synthetic example: CPU only; no GPU required
- [ ] SWE-bench integration: RAM / disk for Docker images documented
- [ ] Minimum disk space for datasets and containers
- [ ] Network requirements (offline install vs pull images)
- [ ] GPU noted as **not required** for core EF library (agent eval may use API/GPU separately)

---

## 14. Random seeds

- [x] Core EF metric: deterministic given inputs (no RNG in library)
- [ ] Agent/scaffold runs: seed policy documented when examples invoke LLMs
- [ ] Holdout partition rule documented as deterministic hash (no RNG) when implemented
- [ ] Any stochastic components list seed fixing procedure in docs

---

## 15. Data availability

- [x] Synthetic example: no external dataset
- [ ] SWE-bench Verified: download instructions + version pin (public dataset)
- [ ] Holdout bundles: public release location and license
- [ ] Precomputed prediction files for reproduction (if used in paper)
- [ ] No private or embargoed data required to run shipped examples

---

## 16. Known limitations

- [x] README states early skeleton; no finished benchmark results
- [ ] `docs/limitations.md` or README section listing:
  - EF defined only on nominal passes
  - Perturbation coverage (EF@Π) not exhaustive
  - Post-hoc re-grade vs process counterfactuals
  - SWE-bench harness coupling risks
- [ ] Link to issue tracker for bug reports
- [ ] Version compatibility matrix (Python, SWE-bench package)

---

## Pre-flight command block

Run before tagging:

```bash
pip install -e ".[dev]"
pytest
ruff check .
pip install -e .
python examples/synthetic_visible_test_overfitting.py
git status   # must be clean
```

---

## Release sign-off

| Role | Name | Date | Tag |
|------|------|------|-----|
| Maintainer | | | `v` |

**Zenodo DOI:** _(fill after deposit)_

**Checklist version:** 2026-06-27
