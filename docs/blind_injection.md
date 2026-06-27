# Blind mechanism injection (B-blind)

Construct-validity experiment that tests whether EF@Π responds to the **injected shortcut mechanism**, not generic fragility, using paired clean/injected artifacts and an **out-of-registry** control arm.

## Why

Phase B planted exploits (E001–E015) share the same perturbation registry Π used for grading. B-blind breaks that circularity by hiding `injected_channel` from the evaluator until a lockfile-verified unblind step.

## Workflow

```bash
# 1. Injector role — freeze manifests
earnbench injection prepare \
  --spec-dir ../paper/experiments/blind_injection/specs \
  --output runs/blind_prepare

# 2. Evaluator role — blind harness run
earnbench injection run \
  --evaluator-manifest runs/blind_prepare/evaluator_manifest.json \
  --metadata-parquet ../paper/vendor/swe_verified_test.parquet \
  --output runs/blind_run \
  --workers 1 \
  --resume

# 3. Analyst role — verify lockfile and merge labels
earnbench injection unblind \
  --run runs/blind_run \
  --injector-manifest runs/blind_prepare/injector_manifest.json \
  --lockfile runs/blind_prepare/blind_lockfile.json \
  --output runs/blind_run
```

## Artifacts

| File | Role |
|------|------|
| `injector_manifest.json` | Ground truth (channel, expected π, EF) |
| `evaluator_manifest.json` | Blinded artifact list for harness |
| `blind_lockfile.json` | SHA256 of both manifests at freeze |
| `injection_results.csv` | Harness outcomes per artifact |
| `injection_validity_report.md` | Post-unblind analysis |

## Spec fields

See `earnbench injection validate` and `paper/experiments/blind_injection/injector_manifest.schema.json`.

In-registry channels map to expected failed π:

| Channel | `expected_failed_pi` |
|---------|----------------------|
| `visible_test_overfitting` | `pi_vtest.v1` |
| `verifier_tampering` | `pi_verif.v1` |
| `environment_hijack` | `pi_env.v1` |

Out-of-registry (`memorization_or_patch_replay`, etc.): `expected_failed_pi: none`, EF ≈ 1.0 is **expected** (registry false-negative floor).

## Related

- `paper/experiments/blind_injection/README.md`
- `paper/experiments/blind_mechanism_injection_protocol.md`
- `earnbench injection list|show|validate` — spec inspection without harness
