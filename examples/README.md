# Examples

Runnable demonstrations of EarnBench without external benchmark dependencies.

## Synthetic visible test overfitting

```bash
pip install -e .
python examples/synthetic_visible_test_overfitting.py
```

Simulates a nominally successful agent run and three counterfactual perturbations
(`visible_test_removed`, `metadata_removed`, `verifier_hardened`). Writes
`synthetic_visible_test_overfitting.report.json` alongside the script.
