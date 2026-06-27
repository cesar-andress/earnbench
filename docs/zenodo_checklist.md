# Zenodo Readiness Checklist

Use this checklist before cutting a **version tag** and creating a **Zenodo archived release** of EarnBench. Items reflect current repository state; unchecked boxes are expected until v0.1.0 gate is met.

Related: [artifact contract in private paper repo](https://github.com/earnbench/earnbench) — design notes are not archived on Zenodo.

---

## 1. Repository structure

- [x] Top-level layout: `src/earnbench/`, `tests/`, `docs/`, `examples/`, `scripts/`
- [x] Importable Python package under `src/earnbench/`
- [ ] `CHANGELOG.md` with release notes per version
- [ ] `docs/` index linking all user-facing guides (beyond this checklist)
- [ ] No secrets, credentials, or large binary blobs in git history
- [ ] `.gitignore` covers build artifacts, caches, generated reports (`examples/*.report.json`)

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
- [ ] Author names and ORCIDs finalized (replace “EarnBench contributors”)
- [ ] `date-released` updated to match tag date
- [ ] `doi` field added after Zenodo deposit (post-release update or second deposit)
- [ ] Related identifiers: paper DOI, benchmark DOI when available

---

## 4. README clarity

- [x] One-sentence project description
- [x] Early-stage status disclaimer (no finished benchmark results)
- [x] Concept: Earned Fraction, judge-free measurement
- [x] Repository layout section
- [ ] Link to full documentation / API reference when available
- [x] License and citation pointers
- [ ] Zenodo DOI badge after first release

---

## 5. Installation instructions

- [x] Minimum Python version stated (`>=3.10`)
- [x] Editable install: `pip install -e ".[dev]"`
- [ ] Runtime extras documented (e.g. `pip install -e ".[swebench]"` when added)
- [ ] Lockfile or constraints file for reproducible installs
- [ ] Verified install steps on clean virtualenv recorded in release notes

---

## 6. Example execution

- [x] Synthetic example script: `examples/synthetic_visible_test_overfitting.py`
- [x] README commands: `pip install -e .` + run example
- [x] Example tested in CI / `tests/test_examples.py`
- [ ] SWE-bench (or other) integration example when harness ships
- [ ] Expected stdout / sample report JSON documented in `examples/README.md`

---

## 7. Test suite

- [x] `pytest` configuration in `pyproject.toml`
- [x] Unit tests for package, metric, and example execution
- [ ] CI workflow (GitHub Actions) running tests on push/tag
- [ ] Coverage report or minimum coverage target documented
- [ ] Integration tests for perturbation executors (when added)

---

## 8. Version tag

- [ ] Git tag `vX.Y.Z` matches `pyproject.toml` version
- [ ] Git tag matches `earnbench.__version__`
- [ ] Git tag matches `CITATION.cff` `version`
- [ ] Tag created from clean working tree (`git status` clean)
- [ ] Tag message or GitHub release notes summarize changes

**Current package version:** `0.1.0` (pre-release; do not tag Zenodo until v0.1.0 gate complete)

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
