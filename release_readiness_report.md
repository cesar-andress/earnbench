# EarnBench RC1 — Release Readiness Report

**Audit date:** 2026-06-28  
**Scope:** Publication, reproducibility, and repository quality for first public release candidate (`v0.1.0-rc1`)  
**Auditor role:** ACM/TOSEM artifact evaluation + production editor  
**Method:** Static repository audit, markdown link check, `pytest` run, CLI smoke test, cross-read of paper artifact docs. **No experiments re-run. No core logic changes.**

---

## Executive summary

EarnBench is **technically mature as a measurement instrument** (rich CLI, 460+ passing unit tests, frozen validation runs in the paper tree) but **not yet ready for a frictionless public RC** without explicit monorepo instructions and release hygiene.

| Dimension | Assessment |
|-----------|------------|
| Code instrument | **Strong** — EF@Π, registry, batch runners, reports implemented |
| Standalone reproducibility | **Weak** — defaults assume sibling `paper/` tree |
| Publication metadata | **Partial** — LICENSE/CITATION exist; DOI and authors incomplete |
| Paper synchronization | **Partial** — manuscript honestly states pending DOI; internal defense docs stale |
| ACM artifact reviewer experience | **Conditional pass** on smoke + metric; **fail** on blind standalone clone |

**Overall readiness score: 78 / 100** for public GitHub + Zenodo RC1 (pre-release).

Recommended action: cut **`v0.1.0-rc1` pre-release tag** after author metadata review;
defer **`v0.1.0` + Zenodo DOI** until manifest sign-off in the paper supplement.

---

## Strengths

1. **Instrument completeness.** Registry v1, EF metric, INVALID calculus, Phase A/B batch paths, blind injection, report generators, and validation-layer tooling are implemented and documented in `earnbench/docs/`.
2. **Test coverage.** `pytest`: **464 passed**, 11 skipped — CI workflow installs `[dev,swebench]`.
3. **Legal & citation baseline.** MIT `LICENSE`, `CITATION.cff`, `CHANGELOG.md`, `docs/release_policy.md`, `docs/zenodo_checklist.md`.
4. **Frozen evidence exists (paper repo).** Phase A full Verified (489/421), Phase B all15, blind run outputs under `paper/experiments/runs/` — not modified in this audit.
5. **Manuscript honesty.** LaTeX §8–§10 and Discussion state Phase D / ERS / external labels / Zenodo as open obligations (no fabricated DOIs).
6. **Editorial bind-copy.** Paper LaTeX compiles; overfull/underfull warnings cleared in prior pass.
7. **Internal audits.** `earnbench/docs/publication_readiness_audit.md`, `paper/submission_readiness.md`, `paper/artifact/acm_artifact_checklist.md` provide traceability.

---

## Blocking issues (must fix before public RC1 tag)

| ID | Issue | Location | Recommendation |
|----|-------|----------|----------------|
| B1 | **Repository URL mismatch** | ~~`pyproject.toml`, `CITATION.cff`~~ | **Resolved:** aligned to `https://github.com/cesar-andress/earnbench` |
| B2 | **`frozen_instrument_manifest.json` unsigned** | `paper/experiments/frozen_instrument_manifest.json` (`status: pending_signoff`) | Sign manifest per `stage3_freeze_protocol.md`; do **not** change pinned SHAs or EF semantics |
| B3 | **No git tag / no Zenodo DOI** | No `v0.1.0-rc1` tag; bib entry `earnbench2026artifact` notes DOI pending | Cut pre-release tag; deposit Zenodo after author ORCID/affiliation finalization |
| B4 | **Standalone reproduction impossible** | Defaults assume sibling `paper/` tree | Documented in `docs/REPRODUCIBILITY.md` and README; supplement is separate Zenodo deposit |
| B5 | **`Development Status :: Pre-Alpha`** vs reality | ~~`pyproject.toml` classifiers~~ | **Resolved:** Beta at RC1 |
| B6 | **GA memo unsigned for headline cohort** | `GA_phase_a_verified_full.md` absent | Sign gate memo in paper supplement; manuscript already states unsigned |

---

## Warnings (should fix; not necessarily RC blockers)

| ID | Issue | Notes |
|----|-------|-------|
| W1 | **pytest failures** | **Resolved** — `test_swebench_run_pi_vtest_cli`, docker cleanup test |
| W2 | **README stale** | **Resolved** — RC1 status, badges, frozen-run table |
| W3 | **`release_policy.md` gate checkboxes** | **Updated** for implemented executors |
| W4 | **`paper/vendor/` parquet** | Present in monorepo (~2 MB); not shipped in software repo |
| W5 | **Internal defense markdown stale** | `submission_readiness.md`, `evidence_gaps.md` cite old TODO bib / §8 gaps superseded by editorial sync |
| W6 | **Monorepo has no unified git remote** | `paper/` not in `earnbench` git; Zenodo supplement strategy must clarify two-archive model |
| W7 | **Phase D in progress on disk** | `paper/experiments/runs/phase_d_*` exists; manuscript correctly does not headline those results |
| W8 | **Author placeholders** | `CITATION.cff`, `.zenodo.json` — finalize names/ORCIDs before Zenodo deposit |
| W9 | **`earnbench run` stub** | Exits with code 2 — documented but easy to stumble on |
| W10 | **CI** | **Added** — `.github/workflows/ci.yml`; verify green on GitHub after push |

---

## Repository consistency audit

### Broken paths / links

| Check | Result |
|-------|--------|
| Broken relative markdown links (docs, README, artifact/) | **0 found** |
| LaTeX undefined references (bind-copy build) | **None** (last successful build) |
| Missing figure files (`paper/figures/*.tex`) | **All present** |
| `vendor/swe_verified_test.parquet` | **Missing** (documented gap) |

### Naming consistency

| Pattern | Observation |
|---------|-------------|
| Phase A headline | Manuscript uses `phase_a_verified_full` ✓ |
| Phase A calibration | `pilot300` / `phase_a_pilot300` used consistently as **non-headline** ✓ |
| Run directory typos | Internal docs mention `phase_a_phase_a_pilot300` typo in one ladder footnote — cosmetic |
| Exploit IDs | E001–E015 aligned across YAML, paper §8, protocols ✓ |

### Stale TODOs (non-exhaustive)

| Area | Status |
|------|--------|
| LaTeX body (bind-copy) | Clean — no `TODO` markers |
| `references.bib` | `earnbench2026artifact` DOI pending (honest) |
| Internal paper markdown (`submission_readiness.md`, etc.) | **Stale** — retain as historical audit trail; exclude from public supplement |
| `paper/experiments/validation_ladder.md` | Empirical cells marked TODO — protocol doc, not shipped as results |

### Duplicated / obsolete documents

| Keep | Archive / exclude from public release |
|------|----------------------------------------|
| `earnbench/docs/publication_readiness_audit.md` | Superseded by this report for RC1 |
| `paper/editorial_pass_report.md`, `editorial_final_review.md` | Internal only |
| Multiple readiness files | Consolidate pointer in workspace `README.md` |

---

## Publication readiness inventory

| File | `earnbench/` | Workspace root | Action in RC1 pass |
|------|:------------:|:--------------:|-------------------|
| README.md | ✓ (updated) | ✓ (new pointer) | Done |
| LICENSE | ✓ | — | Exists |
| CITATION.cff | ✓ | — | Needs authors before Zenodo |
| CONTRIBUTING.md | ✓ | — | **Created** |
| CHANGELOG.md | ✓ | — | Exists; date TBD at tag |
| RELEASE_NOTES.md | ✓ | — | **Created** |
| SECURITY.md | ✓ | — | **Created** |
| VERSION | ✓ | — | **Created** (`0.1.0-rc1`) |
| .zenodo.json | ✓ | — | **Created** (draft) |
| CODE_OF_CONDUCT.md | — | — | Optional; not created |
| CI workflow | ✓ | — | **Created** |

---

## GitHub release preparation (draft — not published)

See [`earnbench/GITHUB_RELEASE_RC1.md`](GITHUB_RELEASE_RC1.md).

**Suggested version:** `v0.1.0-rc1` (pre-release)  
**Suggested title:** `v0.1.0-rc1 — First public release candidate`

---

## Zenodo preparation

Draft metadata: [`earnbench/.zenodo.json`](.zenodo.json)

**Before deposit:**

1. Replace author names, affiliations, ORCIDs (no fabricated IDs).
2. Set `publication_date` to tag date.
3. Add `related_identifiers` only when supplement/paper DOIs exist.
4. Enable GitHub-Zenodo integration **after** tag on canonical repo.

**Two-archive model (unchanged):**

| Archive | Contents | When |
|---------|----------|------|
| Code | `earnbench/` repository | RC1 pre-release |
| Supplement | Frozen CSVs, manifests, selected audits | After GA sign-off per `zenodo_output_policy.md` |

---

## Artifact reproducibility review

### What a reviewer can do today (standalone `earnbench/` clone)

```bash
pip install -e ".[dev]"
pytest                    # 2 failures — see W1
earnbench compute tests/fixtures/compute_input.json
earnbench registry validate
python examples/synthetic_visible_test_overfitting.py
```

**Time:** minutes | **Hardware:** laptop | **Docker:** not required

### What requires monorepo + Docker

| Task | Requirements | Runtime estimate |
|------|--------------|------------------|
| GA-SMOKE (`psf__requests-1724`) | Docker, `[swebench]`, `paper/experiments/runs/` layout | 1–3 h first build |
| Phase A full replay | Metadata parquet, 500-instance manifest, cluster | days |
| Phase B all15 | `paper/exploits/`, Docker | hours |
| Phase D / ERS | Agent patches + re-grade | manuscript-pending |

### Confusion risks for ACM reviewers

1. **Dual adapter story** — ABC stub vs `swebench` batch path (documented in README RC1 pass).
2. **Default relative paths** — fail if cwd is not monorepo root.
3. **Empty vendor/** — must download SWE-bench Verified metadata separately.
4. **`pending_signoff` manifest** — undermines "frozen instrument" claim until signed.
5. **Paper §10** — "Zenodo DOI pending" requires post-release bib update.

---

## Paper synchronization — post-release update locations

Do **not** invent URLs/DOIs now. After GitHub tag + Zenodo deposit, update:

| Location | Current text | Post-release action |
|----------|--------------|---------------------|
| `paper/references.bib` → `earnbench2026artifact` | `Zenodo DOI pending release` | Add `doi = {...}` + URL |
| `paper/sections/10_artifact.tex` L12 | "Zenodo DOI is pending; cite git SHA" | Insert DOI or versioned Zenodo URL |
| `paper/sections/01_introduction.tex` L75 | "release DOIs pending" | Name code DOI + supplement DOI if separate |
| `paper/sections/05_earnbench_methodology.tex` | `\cite{earnbench2026artifact}` | Bib entry complete |
| `paper/appendix/a_protocol_checklists.tex` L31 | "Zenodo supplement bundle will index…" | Past tense when live |
| `paper/artifact/acm_artifact_checklist.md` L24–25, L80 | TODO DOI fields | Fill after deposit |
| `paper/artifact/dataset_card.md` | Pending identifiers | Align with Zenodo supplement manifest |
| `main.tex` | `\acmDOI{}` empty | Journal metadata when assigned |

**Placeholder template for LaTeX (do not commit until DOI known):**

```latex
% After Zenodo deposit:
% \newcommand{\EarnBenchCodeDOI}{10.5281/zenodo.XXXXXXX}
% \newcommand{\EarnBenchCodeURL}{https://doi.org/\EarnBenchCodeDOI}
```

---

## Recommended fixes (priority order)

1. Align GitHub remote URL across `pyproject.toml`, `CITATION.cff`, README badges placeholder.
2. Sign `frozen_instrument_manifest.json` (paper repo, no semantic changes).
3. Fix or document 2 failing pytest tests; run CI workflow.
4. Finalize `CITATION.cff` / `.zenodo.json` authors.
5. Tag `v0.1.0-rc1` (pre-release) on canonical repo.
6. Update `release_policy.md` gate checkboxes (documentation only).
7. Publish Zenodo code deposit; update paper bib + §10 in one editorial commit.
8. Mark internal `submission_readiness.md` as superseded by this report (optional README note).

---

## Files created or modified in this RC1 pass

**Created:**

- `release_readiness_report.md` (this file)
- `release_checklist.md`
- `README.md` (workspace pointer)
- `earnbench/CONTRIBUTING.md`
- `earnbench/SECURITY.md`
- `earnbench/RELEASE_NOTES.md`
- `earnbench/VERSION`
- `earnbench/.zenodo.json`
- `earnbench/GITHUB_RELEASE_RC1.md`
- `earnbench/.github/workflows/ci.yml`
- `earnbench/.github/ISSUE_TEMPLATE/bug_report.md`
- `earnbench/.github/pull_request_template.md`

**Modified (documentation only):**

- `earnbench/README.md` — Status, layout, CLI, install extras

**Not modified (per constraints):**

- `earnbench/src/**` core logic
- `paper/experiments/runs/**` frozen outputs
- EF@Π semantics, Π registry
- Experimental results or statistics

---

## Overall readiness score breakdown

| Category | Weight | Score | Weighted |
|----------|--------|------:|---------:|
| Code quality & tests | 25% | 75 | 18.8 |
| Reproducibility docs | 25% | 45 | 11.3 |
| Publication metadata | 20% | 50 | 10.0 |
| Paper/artifact alignment | 15% | 65 | 9.8 |
| Release infrastructure | 15% | 55 | 8.3 |
| **Total** | 100% | | **58.2 → 58** |

**Interpretation:** Ready for **internal RC1 review** and **pre-release tag** after B1–B3; not ready for **final v0.1.0 + TOSEM artifact badge** until B4–B6 and Zenodo deposit.

---

*End of report.*
