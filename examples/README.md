# Examples

Runnable demonstrations of EarnBench without external benchmark dependencies.

## Synthetic visible test overfitting

```bash
pip install -e .
python examples/synthetic_visible_test_overfitting.py
```

Simulates a nominally successful agent run and three counterfactual perturbations
(`visible_test_removed`, `metadata_removed`, `verifier_hardened`). Writes
`synthetic_visible_test_overfitting.report.json` alongside the script (gitignored).

### Expected output (abbreviated)

```json
{
  "status": "defined",
  "earned_fraction": 0.6666666666666666,
  "valid_count": 3,
  "successful_count": 2
}
```

Exit code **0**. No Docker or SWE-bench metadata required.

## SWE-bench smoke (optional)

Requires `pip install -e ".[swebench]"`, Docker, and Verified metadata parquet.
See [docs/REPRODUCIBILITY.md](../docs/REPRODUCIBILITY.md).
