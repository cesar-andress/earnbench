"""Unblind blind injection runs and produce validity analysis."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from earnbench.injection_batch import INJECTION_RESULTS_CSV
from earnbench.injection_validity import (
    INJECTION_VALIDITY_REPORT_MD,
    generate_injection_validity_report,
    load_injection_results,
)
from earnbench.injections.manifests import (
    injector_specs_from_manifest,
    load_injector_manifest,
    verify_lockfile_integrity,
)


class BlindUnblindError(Exception):
    """Raised when unblind preconditions fail."""


@dataclass(frozen=True, slots=True)
class UnblindInjectionResult:
    output_dir: Path
    report_md: Path
    summary_csv: Path
    channel_attribution_matrix_csv: Path


def unblind_injection_run(
    *,
    run_dir: Path,
    injector_manifest_path: Path,
    lockfile_path: Path,
    output_dir: Path | None = None,
) -> UnblindInjectionResult:
    """Verify lockfile, merge labels, and write injection validity artifacts."""
    verify_lockfile_integrity(
        lockfile_path,
        injector_manifest_path=injector_manifest_path,
    )

    injector_manifest = load_injector_manifest(injector_manifest_path)
    specs = injector_specs_from_manifest(injector_manifest)

    results_path = run_dir / INJECTION_RESULTS_CSV
    if not results_path.is_file():
        msg = f"injection results not found: {results_path}"
        raise BlindUnblindError(msg)

    results = load_injection_results(results_path)
    missing_injected = sorted(spec_id for spec_id in specs if spec_id not in results)
    if missing_injected:
        msg = "injection results missing injected rows for: " + ", ".join(
            missing_injected
        )
        raise BlindUnblindError(msg)

    resolved_output = (output_dir or run_dir).resolve()
    report_result = generate_injection_validity_report(
        results_path=results_path,
        specs_dir=None,
        output_dir=resolved_output,
        specs=specs,
    )

    return UnblindInjectionResult(
        output_dir=resolved_output,
        report_md=resolved_output / INJECTION_VALIDITY_REPORT_MD,
        summary_csv=report_result.summary_csv,
        channel_attribution_matrix_csv=report_result.channel_attribution_matrix_csv,
    )
