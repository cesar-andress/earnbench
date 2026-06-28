# Release and Versioning Policy

This document defines how EarnBench versions are numbered, released, archived on Zenodo, and deprecated. It complements [zenodo_checklist.md](zenodo_checklist.md).

**Current status:** release candidate **`v0.1.0-rc1`** on `main`; final **`v0.1.0`**
requires manifest sign-off and Zenodo DOI per gate below.

---

## Semantic versioning policy

EarnBench follows [Semantic Versioning 2.0.0](https://semver.org/) for git tags and the Python package version (`MAJOR.MINOR.PATCH`).

| Component | Bump when |
|-----------|-----------|
| **MAJOR** | Breaking public API changes; changed Earned Fraction definition; perturbation ID semantics that alter scores on fixed inputs without a migration path |
| **MINOR** | Backward-compatible features: new perturbations, adapters, report fields, optional dependencies |
| **PATCH** | Backward-compatible bug fixes, documentation, tests; no change to EF on reference fixtures |

### Version alignment (required at tag time)

These must match for every release tag `vX.Y.Z` (or pre-release tag `vX.Y.Z-rcN`):

- Git tag
- `VERSION` file
- `pyproject.toml` `project.version`
- `src/earnbench/__version__`
- `CITATION.cff` `version`
- `.zenodo.json` `version` (when depositing)
- Top entry in [CHANGELOG.md](../CHANGELOG.md)

### Pre-release labels

- Commits on `main` ahead of a tag are **development** builds; cite by git commit hash, not DOI.
- Pre-release tags (`v0.2.0-rc1`) may be used for paper review artifacts; they receive a Zenodo deposit only if explicitly needed for reviewers.

### Perturbation versioning (separate from package semver)

Counterfactual perturbations use stable IDs: `pi_<name>.vN` (e.g. `pi_vtest.v1`).

- **Never mutate** the behavior of an existing `vN` in place.
- Behavior changes require incrementing to `vN+1` and a **MINOR** or **MAJOR** package bump depending on score impact.
- Deprecated perturbations remain documented for at least one **MINOR** release.

---

## What qualifies for v0.1.0

Tag **`v0.1.0`** and the **first Zenodo DOI** require all of the following.

### Measurement core (done on current `main`)

- [x] `compute_earned_fraction()` with undefined handling
- [x] `EarnedFractionReport` with mechanism attribution and warnings
- [x] Unit tests for metric edge cases
- [x] Synthetic end-to-end example

### Perturbation executors (implemented; validation evidence in paper supplement)

- [x] `pi_vtest.v1` — holdout re-grade with published partition rule
- [x] `pi_verif.v1` — pristine trusted verifier + tamper detection
- [x] `pi_env.v1` — clean hardened re-execution
- [x] Golden-patch validation on reference instances (Phase A frozen runs)
- [x] `audit.json` per perturbation run

### Integration

- [x] SWE-bench-class adapter (patch + instance → outcomes via Docker harness)
- [x] End-to-end example on real benchmark instance (smoke path documented)
- [x] CI running `pytest` on supported Python versions (`.github/workflows/ci.yml`)

### Release hygiene

- [x] [CHANGELOG.md](../CHANGELOG.md) entry for `0.1.0-rc1`
- [ ] Dependency lockfile for reproducible installs (optional for RC1)
- [x] README updated for RC1 (instrument + monorepo supplement model)
- [x] [zenodo_checklist.md](zenodo_checklist.md) maintained for deposit

### Explicitly not required for v0.1.0

- Full five-mechanism perturbation set
- Paper experiment tables
- Public leaderboard

Until the gate passes, **`v0.1.0` must not be tagged** and **no Zenodo DOI** should be cited as the official artifact.

---

## What qualifies for paper artifact release

The version cited in the paper must satisfy **v0.1.0** plus paper-specific requirements:

| Requirement | Description |
|-------------|-------------|
| **Frozen tag** | Immutable git tag (≥ `v0.1.0`); paper states exact tag and Zenodo DOI |
| **Perturbation parity** | Every `pi_*` used in experiments is in the public registry with configs |
| **Formula parity** | Paper EF definition matches `compute_earned_fraction()` (or divergence is stated) |
| **Reproduction script** | Documented commands reproduce ≥1 main table from public inputs |
| **Inputs archived** | Prediction patches, instance list, and holdout partition rule are public or linked |
| **No LLM judges** | Reported EF pipeline is judge-free |
| **Separate reporting** | Paper tables report nominal pass rate and EF \| pass separately |

### Paper vs preview

- **Preview / draft:** cite GitHub commit hash only; label as pre-release in the paper.
- **Camera-ready:** cite Zenodo DOI of the paper artifact tag; upload matching source to Zenodo.

If the paper ships before v0.1.0 gate completion, the paper must **not** claim a general-purpose benchmark release—only a preview implementation.

---

## Zenodo DOI procedure

1. **Complete** [zenodo_checklist.md](zenodo_checklist.md).
2. **Verify** pre-flight commands pass on a clean tree (see checklist).
3. **Bump** version fields if not already aligned.
4. **Finalize** [CHANGELOG.md](../CHANGELOG.md) release section with date.
5. **Commit** release prep; working tree clean.
6. **Tag:** `git tag -a vX.Y.Z -m "Release vX.Y.Z"` and push tag.
7. **GitHub release:** create release from tag; attach notes from CHANGELOG.
8. **Zenodo:** create deposit from GitHub release (or upload tag tarball).
   - Resource type: Software
   - License: MIT
   - Metadata: title, creators, description, keywords from `CITATION.cff`
9. **Publish** Zenodo record; obtain DOI.
10. **Post-release commit** (PATCH bump or docs-only on `main`):
    - Add `doi` to `CITATION.cff`
    - Add DOI badge/link to README
    - Record DOI in CHANGELOG release entry

### When to mint a new DOI

| Change | New DOI? |
|--------|----------|
| PATCH fix, no score change on fixtures | Same tag policy: prefer new PATCH tag + new DOI for Zenodo “version of record” |
| Score-affecting fix | **Yes** — MINOR or PATCH + prominent CHANGELOG |
| New perturbations used in paper | **Yes** — MINOR tag minimum |
| Documentation only | Optional new DOI; may update GitHub release notes only |

Zenodo versions are immutable; metadata updates use new uploads or new versions, not silent edits.

---

## Changelog expectations

Maintain [CHANGELOG.md](../CHANGELOG.md) following [Keep a Changelog](https://keepachangelog.com/).

### Structure

- **`[Unreleased]`** — changes on `main` not yet tagged
- **`[X.Y.Z] - YYYY-MM-DD`** — one section per release, newest first

### Categories

Use: `Added`, `Changed`, `Deprecated`, `Removed`, `Fixed`, `Security`.

### Entry requirements per release

Each tagged version must document:

- User-visible API changes
- New or changed perturbation IDs
- Breaking changes and migration notes
- Dependency / Python version changes
- Known limitations introduced or resolved

### Link to semver

Breaking changes must say **BREAKING** and justify a MAJOR bump.

---

## Reproducibility expectations

Every tagged release must document how to reproduce shipped behavior.

### Minimum (all releases)

- Python version bounds in `pyproject.toml`
- Instructions in README: install, test, run synthetic example
- Git tag identifies exact source

### v0.1.0 and paper artifact releases

- Lockfile or pinned constraints for runtime and experiment dependencies
- Docker image digest(s) for benchmark perturbations
- Perturbation config SHA-256 hashes in docs or manifest
- Reference fixture or instance list with expected `EarnedFractionReport` outputs
- Audit JSON schema version

### Reproduction unit

- **v0.1.0:** fixed patch re-grade under declared perturbations (not stochastic agent re-sampling).
- Seeds: metric core is deterministic; any stochastic agent examples must document seed policy.

### Failure to reproduce

If independent reproduction fails on reference fixtures, publish a **PATCH** with fix or retract score claims in CHANGELOG; do not silently change perturbation behavior under the same `pi_*.vN` ID.

---

## Deprecation policy

### Public Python API

1. Mark deprecated in docstring and CHANGELOG under `Deprecated`.
2. Emit `DeprecationWarning` for at least one **MINOR** release when feasible.
3. Remove in next **MAJOR** release.

### Perturbation IDs

- Retired IDs remain listed in docs with replacement (`pi_foo.v1` → `pi_foo.v2`).
- Scores computed under retired IDs are not comparable across releases without a migration table.

### Adapters and optional extras

- Optional dependency groups (e.g. `[swebench]`) may be deprecated independently; document in CHANGELOG.

### Support window

- **Latest MINOR** series receives bug fixes.
- No commitment to backport PATCHes to older MAJOR versions unless a security issue affects cited paper artifacts (case-by-case).

---

## Release roles

| Step | Owner |
|------|--------|
| Version bump + CHANGELOG | Maintainer |
| Tag + GitHub release | Maintainer |
| Zenodo deposit | Maintainer |
| Post-release DOI in repo | Maintainer |
| Paper citation update | Paper authors |

---

## Related documents

- [Zenodo readiness checklist](zenodo_checklist.md)
- [CHANGELOG.md](../CHANGELOG.md)
- [CITATION.cff](../CITATION.cff)

**Policy version:** 2026-06-27
