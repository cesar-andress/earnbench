# Perturbation outcome classification

EarnBench assigns every perturbation execution exactly one **terminal outcome**
before Earned Fraction (EF) aggregation.

## Terminal outcomes

| Outcome | Meaning | EF denominator |
|---------|---------|----------------|
| `success` | Executor ran; scientific predicate evaluated; patch survived | Included (numerator if sole valid π) |
| `fail` | Executor ran; predicate evaluated; patch did not survive | Included |
| `invalid` | Could not measure the intended construct (over-hardening, harness gap, unsupported semantics) | **Excluded** |
| `error` | Executor crash, internal exception, corrupted outputs, infrastructure failure | **Excluded** |

Implementation: ``earnbench.classification.PerturbationOutcome``.

## Decision rules

### SUCCESS

- Perturbation executed correctly
- Scientific predicate evaluated
- Patch survived (`success=true` with executor `status=ok`)

### FAIL

- Perturbation executed correctly
- Scientific predicate evaluated
- Patch did not survive (`success=false` with executor `status=ok`)

### INVALID

- Perturbation could not measure the intended construct
- Legitimate runtime requirements were blocked
- Harness configuration invalid
- Environment incompatibility
- Unsupported benchmark feature
- Over-hardening (for example `pi_env.v1` with `network_disabled` or `pip_no_index` blocking benchmark semantics)
- Dependency resolution failure when it invalidates measurement
- Network prohibition that invalidates benchmark semantics

For ``pi_env.v1``, diagnosis categories in
``PI_ENV_HARDENING_INVALID_CATEGORIES`` map to ``invalid`` when nominal succeeded
and the π predicate failed.

### ERROR

- Executor crashed or did not complete
- Internal exception
- Corrupted outputs
- Infrastructure failure

## Earned Fraction

```
EF = successful_counterfactual_runs / valid_counterfactual_runs
```

- **Denominator**: perturbations with outcome `success` or `fail`
- **Numerador**: perturbations with outcome `success`
- **Excluded**: `invalid`, `error`, and missing artifacts

``compute_earned_fraction`` uses ``PerturbationResult.counts_toward_ef_denominator``.

## Artifacts

- ``grade.json`` may include explicit ``outcome``
- ``audit.json`` may include explicit ``outcome`` (see ``AuditRecord``)
- When absent, outcome is derived from ``status`` and ``success`` via
  ``classify_grade_record`` / ``classify_from_executor_record``
