# EarnBench codebase — publication readiness audit

**Generated:** 2026-06-28  
**Scope:** `earnbench/` implementation only (not paper prose).  
**Method:** Static inspection of CLI, batch runners, adapters, reports, docs, manifests, and `py_compile` on critical modules. No web lookup.

---

## Executive summary

The package contains **working Docker batch paths** for Phase A/B, blind injection, and post-hoc reports when invoked via `earnbench swebench *`, `phase-a run`, `phase-b run`, and `injection run`. However, **the installed CLI currently fails to import** due to unresolved git merge conflicts in Phase D modules, and **standalone reproduction from the `earnbench` repo alone is not possible** without the sibling `paper/` tree (metadata parquet, exploit patches, frozen manifest).

| Severity | Count | Theme |
|----------|------:|-------|
| **BLOCKER** | 5 | Broken CLI import; monorepo-only inputs; dual adapter API |
| **SHOULD FIX** | 9 | Scaffolding gaps, stale docs, π order inconsistency, misleading metrics |
| **NICE TO HAVE** | 4 | Future CLI slots, packaging polish |

---

## 1. Experimental scaffolding not fully implemented

### BLOCKER

| Finding | Location | Impact |
|---------|----------|--------|
| **Unresolved merge conflicts** make Phase D modules syntactically invalid | `src/earnbench/phase_d_regrade.py` (multiple `<<<<<<< HEAD` markers), `src/earnbench/phase_d_diagnostics.py` | `import earnbench.cli` fails on Python 3.11+; **`earnbench phase-d run` / entire CLI broken** at startup |
| **Exploit and injection inputs live outside the package** | Phase B docs default `--exploit-dir ../paper/exploits`; metadata defaults `../paper/vendor/swe_verified_test.parquet` in `phase_b_batch.resolve_metadata_path()` | Cloning only `earnbench/earnbench` cannot rerun Phase B or resolve metadata without manual paths |
| **Frozen instrument manifest not in code repo** | `paper/experiments/frozen_instrument_manifest.json` (`status: pending_signoff`); not shipped in wheel | Reproducers cannot pin instrument from package alone; manifest git SHA may drift from HEAD |

### SHOULD FIX

| Finding | Location | Impact |
|---------|----------|--------|
| **`SWEBenchAdapter.evaluate_nominal` / `evaluate_perturbation` raise `NotImplementedError`** | `src/earnbench/adapters/swebench.py` | Unified adapter API is a stub; real execution only via `swebench_*` modules and batch runners — easy to call wrong entry point |
| **Registry `executor_stub` on all three MVP π specs** | `registry/pi_{vtest,verif,env}_v1.py` | Direct registry executor calls fail; batch path bypasses stubs (OK if documented) |
| **`earnbench run` validates then exits NotImplemented** | `cli.py:cmd_run` | Documented stub, but still advertised in README; users may expect end-to-end grading |
| **`external-exploit` validate-only** | `external_exploits/catalog.py`; CLI subcommand `validate-catalog` only | No harness execution path for external exploit catalog (docs acknowledge validation scaffold) |
| **`phase-c-prime` validate-only** | `phase_c_prime/`; CLI `validate-manifest` only | No batch runner for Phase C′ replicates; reports consume user-supplied CSV |
| **Maintainer-certified pipeline produces mostly `undecidable` without GitHub enrichment** | `certified_controls/generate_manifest.py` | Expected behavior, but M-cert reports must not be read as empirical FUBR evidence until enrichment succeeds (documented in `docs/maintainer_certified_correctness.md`) |
| **External-unearned execution depends on user-built patch bundle** | `external_unearned/execute.py` (implemented) but catalog rows deferred in paper gap memos | CLI exists; full anchor cohort not runnable from shipped fixtures alone |

### NICE TO HAVE

| Finding | Location |
|---------|----------|
| **`earnbench report discriminant-validity`** referenced as future in paper validation ladder; **not in CLI** | Protocol doc only |
| **Validation layers 9–10** (`registry-evolution`, `registry-agreement`) are **schema validators only** | `validation_ladder.md` marks as future/appendix |
| **`Development Status :: 2 - Pre-Alpha`** in `pyproject.toml` | Accurate but signals immaturity |

---

## 2. Documented commands vs CLI support

Audited against `earnbench/docs/*.md`, `README.md`, and `cli.py` subparsers.

### Commands documented and **implemented**

| Command group | Status |
|---------------|--------|
| `compute`, `validate-audit` | OK |
| `registry list|show|validate` | OK |
| `exploit list|show|validate|validate-runtime` | OK |
| `injection list|show|validate|prepare|run|unblind` | OK (harness via batch, not registry stub) |
| `swebench prepare-smoke|preflight|run-nominal|run-pi-*|diagnose-pi-env` | OK (requires `[swebench]` extra + Docker) |
| `phase-a schedule`, `phase-a run` | OK **if CLI imports** |
| `phase-b run` | OK |
| `phase-c prepare|run|summarize` | OK (agent arms user-configured) |
| `phase-d run|summarize` | **CLI wired but module broken** (merge conflicts) |
| `report phase-a|phase-b|rank-stability|policy-ef|policy-variance|registry-geometry|registry-structure|injection-validity|controls|certified-controls|external-unearned|external-unearned-agreement` | OK |
| `controls validate-manifest|generate-manifest` | OK |
| `external-unearned validate-catalog|validate-manifest|import-patches|run` | OK |
| `external-exploit validate-catalog` | OK (validate only) |
| `phase-c-prime validate-manifest` | OK (validate only) |
| `validation bootstrap|ablation|monte-carlo|cross-oracle|stress-test validate-catalog|registry-evolution validate-scenario|registry-agreement validate-table` | OK |
| `investigate` | OK |

### BLOCKER / SHOULD FIX — documentation vs reality

| Documented expectation | Actual | Severity |
|------------------------|--------|----------|
| `earnbench phase-d run` in `docs/phase_d_ers.md` | Module does not import | **BLOCKER** |
| README: Phase A scheduler — *"Only pi_vtest.v1 still records status=missing until its executor ships"* | `run_pi_vtest_grading` is implemented; scheduler calls it | **SHOULD FIX** (stale README misleads reproducers) |
| Default metadata path `../paper/vendor/...` in docs | Not in package; fails if cwd is not monorepo layout | **BLOCKER** for standalone clone |
| Phase B `--exploit-dir ../paper/exploits` | Exploits not in `earnbench` wheel | **BLOCKER** for standalone clone |
| README presents `earnbench run` without prominent "use swebench/phase-* instead" in quickstart | Stub exits code 2 | **SHOULD FIX** |

### NICE TO HAVE

| Gap | Notes |
|-----|-------|
| `controls report` as top-level command | Docs correctly use `earnbench report controls`; no issue |
| Templates live under `paper/experiments/*.template.csv` | Cross-repo path assumed in `validation_ladder.md` |

---

## 3. Missing report generators

All report functions wired in `cli.py` under `earnbench report …` **except**:

| Expected (protocol / ladder) | In CLI? | Severity |
|----------------------------|---------|----------|
| `report discriminant-validity` | No | **NICE TO HAVE** (explicitly future in `paper/experiments/discriminant_validity_study.md`) |
| Layer 9–10 execution reports | Validate-only inputs | **NICE TO HAVE** |

**Implemented generators (verified in CLI):**  
`phase_a_report`, `phase_b_report`, `policy_ef`, `policy_variance`, `rank_stability`, `registry_geometry`, `registry_structure_validation`, `injection_validity`, `certified_controls/report`, `external_unearned/report`, `external_unearned/agreement`, plus validation-layer outputs (`bootstrap_uncertainty`, `pi_ablation`, `monte_carlo_ef`, `cross_oracle_agreement`).

---

## 4. Placeholder adapters

| Component | Type | Severity |
|-----------|------|----------|
| `SWEBenchAdapter.evaluate_*` | Abstract adapter surface; NotImplemented | **BLOCKER** if users/integration tests call ABC instead of batch path |
| `registry/pi_*.v1` `executor_stub` | NotImplemented by design | **SHOULD FIX** to document; batch uses `swebench_pi_*` directly |
| `agents/external_cli.py` | Placeholder until `command_template` set; rejects `"TODO"` | **SHOULD FIX** — Phase C reproducibility requires user-supplied shell commands (`shell=True`) |
| `agents/ollama.py`, other agent drivers | Real but environment-dependent | **SHOULD FIX** — document model/API pins |

---

## 5. TODOs affecting reproducibility

| Marker | Location | Severity |
|--------|----------|----------|
| Git conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`) | `phase_d_regrade.py`, `phase_d_diagnostics.py` | **BLOCKER** |
| `command_template == "TODO"` | `agents/external_cli.py:26` | **SHOULD FIX** (runtime error if arms.yaml not filled) |
| `dataset_revision: str = "unpinned"` default | Batch configs (`phase_a_batch`, scheduler, etc.) | **SHOULD FIX** — document requirement to pass pinned revision for publication runs |
| `frozen_instrument_manifest.json` → `"status": "pending_signoff"` | Paper repo | **SHOULD FIX** before artifact claims |
| No `# TODO` / `# FIXME` in core measurement modules (`metrics.py`, `classification.py`, `scheduler.run_perturbation_stage`) | — | Clean |

---

## 6. Silent assumptions to document before publication

| Assumption | Where it hides | Severity |
|------------|----------------|----------|
| **`pip install earnbench` without `[swebench]`** installs zero runtime deps; Docker harness fails at first batch command | `pyproject.toml` `dependencies = []` | **BLOCKER** — install docs must require `pip install -e ".[swebench]"` |
| **Two Phase A entry points with different π order**: `phase-a run` uses `BATCH_PI_ORDER = (verif, vtest, env)`; `phase-a schedule` uses `PI_IDS = (vtest, verif, env)` | `phase_a_batch.py`, `scheduler.py` | **SHOULD FIX** — EF is order-invariant but artifacts/resume paths differ; protocol should mandate one command |
| **`registry/manifest.json` `default_pi_order`** is `(vtest, verif, env)` but batch uses verif-first | `registry/manifest.json` vs `BATCH_PI_ORDER` | **SHOULD FIX** |
| **`supported_perturbations()` may omit `pi_vtest.v1`** when instance has no holdout-eligible tests | `adapters/swebench.py` | **SHOULD FIX** — summary CSV shows high `pi_vtest` missing rate; not a harness bug but affects cohort geometry |
| **Nominal `prod_only` vs `raw_full`** per exploit/golden row | Phase B specs | **SHOULD FIX** — stratification required for cross-row comparison |
| **`earned_pass_rate_undefined_as_zero`** in policy-variance reports | `policy_variance.py` | **SHOULD FIX** — secondary column; report text warns but easy to misread as headline EF |
| **Grade `status=missing`** mapped to `PerturbationOutcome.ERROR` in `classify_from_executor_record` when not ok/invalid/error | `classification.py` | **SHOULD FIX** — excluded from EF (OK) but `pi_*_status` column says `missing`, not `invalid` per paper ontology |
| **Blind injection / Phase B require monorepo layout** for defaults | `resolve_metadata_path`, docs | **BLOCKER** for third-party repro without explicit flags |
| **GitHub token for M-cert enrichment** optional but rate-limited | `certified_controls/github_metadata.py` | **SHOULD FIX** — document env var / token policy |

---

## 7. Code paths that could produce misleading experimental outputs

| Risk | Mechanism | Severity |
|------|-----------|----------|
| **Calling `earnbench run` or `SWEBenchAdapter.evaluate_*`** | Immediate NotImplemented or empty expectation | **BLOCKER** (fails loud — OK if CLI fixed) |
| **Using `phase-a schedule` vs `phase-a run` interchangeably** | Different schedulers / π ordering / output filenames (`golden_validation.csv` vs `summary.csv`) | **SHOULD FIX** |
| **Reading policy-variance `earned_pass_rate_undefined_as_zero` as primary EF** | Explicit zero-imputation variant | **SHOULD FIX** |
| **M-cert / external-unearned reports on empty or all-`undecidable` manifests** | Reports emit scaffolding zeros / join failures with disclaimers | **SHOULD FIX** — verify report headers warn prominently (partially done in `external_unearned/report.py`) |
| **Resume skips failed stages without `--retry-failed`** | `scheduler.skip_if_done` retains prior failures silently | **SHOULD FIX** — document in batch runbooks |
| **`classify_pi_env_measurement` reclassifies failures as INVALID** | Can inflate INVALID vs FAIL; dual EF band mitigates | **SHOULD FIX** — already reported in paper pilot; instrument behavior must stay pinned |
| **Stale README claim that pi_vtest executor is missing** | Operators may skip vtest stage or misinterpret CSV | **SHOULD FIX** |

No evidence that **`compute_earned_fraction`** imputes undefined EF as zero in the primary estimand (`metrics.py` returns undefined reports correctly).

---

## 8. Missing manifests and schemas

| Artifact | Shipped? | Validation | Severity |
|----------|----------|------------|----------|
| `registry/manifest.json` | Yes (wheel `force-include`) | `registry validate` | OK |
| `audit.json` / `AuditRecord` | Schema in Python (`audit.py`) | `validate-audit` | **SHOULD FIX** — no checked-in JSON Schema file for external tools |
| `grade.json` / `report.json` | Written by harness; no standalone JSON Schema in repo | Ad hoc | **SHOULD FIX** |
| `run_manifest.json`, `batch_state.json`, Phase D `agent_results.csv` | Written by batches; column contracts in code | Tests partial | **SHOULD FIX** — publish column spec in docs |
| `frozen_instrument_manifest.json` | Paper repo only; pending signoff | Manual | **BLOCKER** for artifact gate |
| SWE-bench Verified metadata parquet | Paper `vendor/` only | — | **BLOCKER** for standalone repro |
| Exploit YAML + patches (E001–E015) | Paper `exploits/` | `exploit validate` | **BLOCKER** for standalone repro |
| Injection blind specs | Paper repo + tests/fixtures | `injection validate` | Partial in package fixtures |
| External-unearned / M-cert templates | Paper `experiments/*.template.csv` | validate-* CLI | Cross-repo |
| JSON Schema files (`*.schema.json`) | **None in repo** | — | **NICE TO HAVE** for Zenodo supplement tooling |

---

## Recommended fix order (reproducibility only)

1. **Resolve merge conflicts** in `phase_d_regrade.py` and `phase_d_diagnostics.py`; verify `python -m py_compile` and `earnbench --help`.
2. **Document monorepo layout** (or vend minimal metadata + exploit fixture subset into package/data).
3. **Align π execution order** across scheduler, batch, and `registry/manifest.json`; state canonical command (`phase-a run` vs `schedule`).
4. **Update README** — remove stale pi_vtest stub claim; elevate `[swebench]` install requirement.
5. **Finalize and ship** `frozen_instrument_manifest.json` with signed-off git SHAs.
6. **Clarify adapter surfaces** — deprecate or redirect `earnbench run` to `phase-a run` / `swebench run-nominal`.
7. **Publish artifact schemas** (audit, summary.csv, agent_results.csv) before Zenodo cut.

---

## Verification notes

- `py_compile` on `phase_d_regrade.py` / `phase_d_diagnostics.py`: **SyntaxError** (merge markers).
- `import earnbench.cli` on Python 3.11/3.12: **fails** (same root cause).
- Core batch modules (`scheduler.run_perturbation_stage`) call **real** `run_pi_*_grading` functions — prior audit concern about global stubs applies to registry/ABC paths only, not frozen pilot300/all15 batches already on disk.
- Test suite not fully executed here (environment `pytest` segfault on default Python 3.6); use Python ≥3.10 per `pyproject.toml`.
