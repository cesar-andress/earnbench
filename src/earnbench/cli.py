"""EarnBench command-line interface."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from earnbench.adapters.swebench import prepare_smoke
from earnbench.adapters.swebench_config import (
    add_swebench_performance_arguments,
    print_swebench_execution_summary,
    resolve_swebench_run_config_from_args,
)
from earnbench.adapters.swebench_metadata import MetadataLoadError
from earnbench.adapters.swebench_nominal import (
    HarnessNotInstalledError,
    run_nominal_grading,
)
from earnbench.adapters.swebench_pi_env import run_pi_env_grading
from earnbench.adapters.swebench_pi_env_diagnosis import write_pi_env_diagnosis
from earnbench.adapters.swebench_pi_verif import run_pi_verif_grading
from earnbench.adapters.swebench_preflight import (
    MissingDockerImagesError,
    run_swebench_preflight,
)
from earnbench.audit import AuditRecord
from earnbench.metrics import compute_earned_fraction
from earnbench.outcomes import NominalOutcome, OutcomeStatus, PerturbationResult
from earnbench.provenance import Provenance, build_provenance
from earnbench.registry import RegistryError
from earnbench.registry import get as get_perturbation
from earnbench.registry import list as list_perturbations
from earnbench.registry import validate as validate_registry
from earnbench.scheduler import (
    PhaseASchedulerConfig,
    configure_structured_logging,
    run_phase_a_scheduler,
)


class CLIError(Exception):
    """User-facing CLI error with optional exit code."""

    def __init__(self, message: str, *, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def _load_json_file(path: Path) -> Any:
    if not path.is_file():
        raise CLIError(f"file not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CLIError(f"invalid JSON in {path}: {exc}") from exc


def parse_compute_input(
    data: dict[str, Any],
) -> tuple[NominalOutcome, list[PerturbationResult]]:
    """Parse a compute input JSON object into grading outcomes."""
    if "nominal" not in data:
        raise CLIError("input JSON must contain a 'nominal' object")
    perturbations_raw = data.get("perturbations")
    if perturbations_raw is None:
        perturbations_raw = data.get("counterfactuals")
    if perturbations_raw is None:
        raise CLIError("input JSON must contain a 'perturbations' array")
    if not isinstance(perturbations_raw, list):
        raise CLIError("'perturbations' must be a JSON array")

    nominal_raw = data["nominal"]
    if not isinstance(nominal_raw, dict):
        raise CLIError("'nominal' must be a JSON object")

    try:
        nominal = NominalOutcome(
            run_id=str(nominal_raw["run_id"]),
            task_id=str(nominal_raw["task_id"]),
            success=bool(nominal_raw["success"]),
        )
    except KeyError as exc:
        raise CLIError(f"nominal object missing field: {exc.args[0]}") from exc
    except ValueError as exc:
        raise CLIError(f"invalid nominal outcome: {exc}") from exc

    perturbations: list[PerturbationResult] = []
    for index, item in enumerate(perturbations_raw):
        if not isinstance(item, dict):
            raise CLIError(f"perturbations[{index}] must be a JSON object")
        try:
            perturbation_id = str(item["perturbation_id"])
            status = OutcomeStatus(str(item["status"]))
        except KeyError as exc:
            raise CLIError(
                f"perturbations[{index}] missing field: {exc.args[0]}"
            ) from exc
        except ValueError as exc:
            raise CLIError(f"perturbations[{index}] has invalid status: {exc}") from exc

        success_raw = item.get("success")
        success = None if success_raw is None else bool(success_raw)
        try:
            perturbations.append(
                PerturbationResult(
                    perturbation_id=perturbation_id,
                    status=status,
                    success=success,
                    channel=str(item.get("channel", "")),
                    message=str(item.get("message", "")),
                )
            )
        except ValueError as exc:
            raise CLIError(f"perturbations[{index}] is invalid: {exc}") from exc

    return nominal, perturbations


def parse_provenance_from_input(data: dict[str, Any]) -> Provenance | None:
    """Parse optional provenance overrides from a compute input document."""
    provenance_raw = data.get("provenance")
    if isinstance(provenance_raw, dict):
        return Provenance.from_dict(provenance_raw)

    config_digest = data.get("config_digest")
    random_seed = data.get("random_seed")
    docker_image_digest = data.get("docker_image_digest")
    if (
        config_digest is not None
        or random_seed is not None
        or docker_image_digest is not None
    ):
        seed_value: int | None
        if random_seed is None:
            seed_value = None
        else:
            seed_value = int(random_seed)
        return build_provenance(
            config_digest=str(config_digest or ""),
            docker_image_digest=(
                str(docker_image_digest) if docker_image_digest is not None else None
            ),
            random_seed=seed_value,
        )
    return None


def cmd_compute(args: argparse.Namespace) -> None:
    """Compute Earned Fraction from a JSON outcomes file."""
    data = _load_json_file(Path(args.input))
    if not isinstance(data, dict):
        raise CLIError("compute input must be a JSON object")

    nominal, perturbations = parse_compute_input(data)
    provenance = parse_provenance_from_input(data)
    report = compute_earned_fraction(
        nominal,
        perturbations,
        provenance=provenance,
    )
    json.dump(report.to_dict(), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def cmd_validate_audit(args: argparse.Namespace) -> None:
    """Validate an audit.json file against AuditRecord."""
    data = _load_json_file(Path(args.audit))
    if not isinstance(data, dict):
        raise CLIError("audit file must contain a JSON object")

    try:
        record = AuditRecord.from_dict(data)
    except (KeyError, TypeError, ValueError) as exc:
        raise CLIError(f"invalid audit record: {exc}") from exc

    if args.quiet:
        return

    json.dump(record.to_dict(), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def validate_run_arguments(
    *,
    instance: str,
    patch: Path,
    perturbation: str,
    config: Path,
) -> None:
    """Validate ``earnbench run`` arguments before execution."""
    if not instance.strip():
        raise CLIError("--instance must be non-empty")
    if not perturbation.strip():
        raise CLIError("--perturbation must be non-empty")
    if not patch.is_file():
        raise CLIError(f"--patch file not found: {patch}")
    if not config.is_file():
        raise CLIError(f"--config file not found: {config}")


def run_grading(
    *,
    instance: str,
    patch: Path,
    perturbation: str,
    config: Path,
) -> None:
    """Validate inputs and dispatch grading (execution not implemented)."""
    validate_run_arguments(
        instance=instance,
        patch=patch,
        perturbation=perturbation,
        config=config,
    )
    print(
        "EarnBench run: arguments validated. "
        f"instance={instance!r}, perturbation={perturbation!r}, "
        f"patch={patch}, config={config}",
        file=sys.stderr,
    )
    raise NotImplementedError(
        "SWE-bench harness execution is not implemented yet. "
        "Use 'earnbench compute' with recorded outcomes until adapters ship."
    )


def cmd_run(args: argparse.Namespace) -> None:
    """Validate run arguments; real harness execution is not implemented."""
    run_grading(
        instance=args.instance,
        patch=Path(args.patch),
        perturbation=args.perturbation,
        config=Path(args.config),
    )


def cmd_registry_list(args: argparse.Namespace) -> None:
    """Print registered perturbation ids and names."""
    del args
    payload = {
        "perturbations": [spec.to_dict() for spec in list_perturbations()],
    }
    json.dump(payload, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def cmd_registry_show(args: argparse.Namespace) -> None:
    """Print one perturbation spec as JSON."""
    try:
        spec = get_perturbation(args.perturbation_id)
    except RegistryError as exc:
        raise CLIError(str(exc)) from exc
    json.dump(spec.to_dict(), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def cmd_swebench_prepare_smoke(args: argparse.Namespace) -> None:
    """Prepare Phase A smoke artifacts without Docker execution."""
    metadata_path = Path(args.metadata_parquet)
    output_dir = Path(args.output)
    suffix = metadata_path.suffix.lower()
    if suffix not in {".parquet", ".json"}:
        raise CLIError(
            f"--metadata-parquet must be a .parquet or .json file, got: {metadata_path}"
        )

    try:
        plan = prepare_smoke(
            metadata_path=metadata_path,
            instance_id=args.instance_id,
            output_dir=output_dir,
            run_id=args.run_id,
            dataset_revision=args.dataset_revision,
        )
    except MetadataLoadError as exc:
        raise CLIError(str(exc)) from exc
    except ValueError as exc:
        raise CLIError(str(exc)) from exc

    if args.quiet:
        return

    json.dump(plan, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def cmd_swebench_preflight(args: argparse.Namespace) -> None:
    """Check and optionally build SWE-bench Docker images for one instance."""
    metadata_path = Path(args.metadata_parquet)
    output_dir = Path(args.output)
    suffix = metadata_path.suffix.lower()
    if suffix not in {".parquet", ".json"}:
        raise CLIError(
            f"--metadata-parquet must be a .parquet or .json file, got: {metadata_path}"
        )

    try:
        run_config = resolve_swebench_run_config_from_args(args)
    except (FileNotFoundError, ValueError) as exc:
        raise CLIError(str(exc)) from exc

    print_swebench_execution_summary(
        command="preflight",
        config=run_config,
        output_dir=output_dir,
        instance_count=1,
    )

    try:
        payload = run_swebench_preflight(
            metadata_path=metadata_path,
            instance_id=args.instance_id,
            output_dir=output_dir,
            build_missing_images=args.build_missing_images,
            config=run_config,
        )
    except MetadataLoadError as exc:
        raise CLIError(str(exc)) from exc

    if payload["status"] != "ok":
        raise CLIError(
            f"preflight status: {payload['status']} "
            f"(see {output_dir / args.instance_id / 'preflight.json'})",
            exit_code=1,
        )

    if args.quiet:
        return

    json.dump(payload, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def cmd_swebench_run_nominal(args: argparse.Namespace) -> None:
    """Run nominal SWE-bench grading for one instance."""
    metadata_path = Path(args.metadata_parquet)
    patch_path = Path(args.patch)
    output_dir = Path(args.output)
    suffix = metadata_path.suffix.lower()
    if suffix not in {".parquet", ".json"}:
        raise CLIError(
            f"--metadata-parquet must be a .parquet or .json file, got: {metadata_path}"
        )
    if not patch_path.is_file():
        raise CLIError(f"--patch file not found: {patch_path}")

    try:
        run_config = resolve_swebench_run_config_from_args(args)
    except (FileNotFoundError, ValueError) as exc:
        raise CLIError(str(exc)) from exc

    print_swebench_execution_summary(
        command="run-nominal",
        config=run_config,
        output_dir=output_dir,
        instance_count=1,
    )

    try:
        grade = run_nominal_grading(
            metadata_path=metadata_path,
            instance_id=args.instance_id,
            patch_path=patch_path,
            output_dir=output_dir,
            timeout_seconds=run_config.timeout_seconds,
            run_id=args.run_id or None,
            config=run_config,
        )
    except MetadataLoadError as exc:
        raise CLIError(str(exc)) from exc
    except HarnessNotInstalledError as exc:
        raise CLIError(str(exc)) from exc
    except MissingDockerImagesError as exc:
        raise CLIError(str(exc)) from exc
    except FileNotFoundError as exc:
        raise CLIError(str(exc)) from exc
    except ValueError as exc:
        raise CLIError(str(exc)) from exc

    if args.quiet:
        return

    json.dump(grade, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def cmd_swebench_run_pi_verif(args: argparse.Namespace) -> None:
    """Run pi_verif.v1 SWE-bench grading for one instance."""
    metadata_path = Path(args.metadata_parquet)
    patch_path = Path(args.patch)
    output_dir = Path(args.output)
    suffix = metadata_path.suffix.lower()
    if suffix not in {".parquet", ".json"}:
        raise CLIError(
            f"--metadata-parquet must be a .parquet or .json file, got: {metadata_path}"
        )
    if not patch_path.is_file():
        raise CLIError(f"--patch file not found: {patch_path}")

    try:
        run_config = resolve_swebench_run_config_from_args(args)
    except (FileNotFoundError, ValueError) as exc:
        raise CLIError(str(exc)) from exc

    print_swebench_execution_summary(
        command="run-pi-verif",
        config=run_config,
        output_dir=output_dir,
        instance_count=1,
    )

    try:
        grade = run_pi_verif_grading(
            metadata_path=metadata_path,
            instance_id=args.instance_id,
            patch_path=patch_path,
            output_dir=output_dir,
            timeout_seconds=run_config.timeout_seconds,
            run_id=args.run_id or None,
            config=run_config,
        )
    except MetadataLoadError as exc:
        raise CLIError(str(exc)) from exc
    except HarnessNotInstalledError as exc:
        raise CLIError(str(exc)) from exc
    except MissingDockerImagesError as exc:
        raise CLIError(str(exc)) from exc
    except FileNotFoundError as exc:
        raise CLIError(str(exc)) from exc
    except ValueError as exc:
        raise CLIError(str(exc)) from exc

    if args.quiet:
        return

    json.dump(grade, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def cmd_swebench_run_pi_env(args: argparse.Namespace) -> None:
    """Run pi_env.v1 SWE-bench grading for one instance."""
    metadata_path = Path(args.metadata_parquet)
    patch_path = Path(args.patch)
    output_dir = Path(args.output)
    suffix = metadata_path.suffix.lower()
    if suffix not in {".parquet", ".json"}:
        raise CLIError(
            f"--metadata-parquet must be a .parquet or .json file, got: {metadata_path}"
        )
    if not patch_path.is_file():
        raise CLIError(f"--patch file not found: {patch_path}")

    try:
        run_config = resolve_swebench_run_config_from_args(args)
    except (FileNotFoundError, ValueError) as exc:
        raise CLIError(str(exc)) from exc

    print_swebench_execution_summary(
        command="run-pi-env",
        config=run_config,
        output_dir=output_dir,
        instance_count=1,
    )

    try:
        grade = run_pi_env_grading(
            metadata_path=metadata_path,
            instance_id=args.instance_id,
            patch_path=patch_path,
            output_dir=output_dir,
            timeout_seconds=run_config.timeout_seconds,
            run_id=args.run_id or None,
            config=run_config,
        )
    except MetadataLoadError as exc:
        raise CLIError(str(exc)) from exc
    except HarnessNotInstalledError as exc:
        raise CLIError(str(exc)) from exc
    except MissingDockerImagesError as exc:
        raise CLIError(str(exc)) from exc
    except FileNotFoundError as exc:
        raise CLIError(str(exc)) from exc
    except ValueError as exc:
        raise CLIError(str(exc)) from exc

    if args.quiet:
        return

    json.dump(grade, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def cmd_swebench_diagnose_pi_env(args: argparse.Namespace) -> None:
    """Compare nominal and pi_env artifacts to diagnose pi_env.v1 failures."""
    metadata_path = Path(args.metadata_parquet)
    patch_path = Path(args.patch)
    nominal_dir = Path(args.nominal_dir)
    pi_env_dir = Path(args.pi_env_dir)
    output_dir = Path(args.output)
    suffix = metadata_path.suffix.lower()
    if suffix not in {".parquet", ".json"}:
        raise CLIError(
            f"--metadata-parquet must be a .parquet or .json file, got: {metadata_path}"
        )
    if not patch_path.is_file():
        raise CLIError(f"--patch file not found: {patch_path}")
    if not nominal_dir.is_dir():
        raise CLIError(f"--nominal-dir not found: {nominal_dir}")
    if not pi_env_dir.is_dir():
        raise CLIError(f"--pi-env-dir not found: {pi_env_dir}")

    try:
        diagnosis = write_pi_env_diagnosis(
            metadata_path=metadata_path,
            instance_id=args.instance_id,
            patch_path=patch_path,
            nominal_dir=nominal_dir,
            pi_env_dir=pi_env_dir,
            output_dir=output_dir,
        )
    except MetadataLoadError as exc:
        raise CLIError(str(exc)) from exc
    except FileNotFoundError as exc:
        raise CLIError(str(exc)) from exc
    except ValueError as exc:
        raise CLIError(str(exc)) from exc

    if args.quiet:
        return

    json.dump(diagnosis, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def cmd_phase_a(args: argparse.Namespace) -> None:
    """Run Phase A golden validation with parallel instance and π scheduling."""
    metadata_path = Path(args.metadata_parquet)
    output_dir = Path(args.output)
    suffix = metadata_path.suffix.lower()
    if suffix not in {".parquet", ".json"}:
        raise CLIError(
            f"--metadata-parquet must be a .parquet or .json file, got: {metadata_path}"
        )

    instance_ids: tuple[str, ...] | None = None
    if args.instances:
        instance_ids = tuple(
            sorted({item.strip() for item in args.instances.split(",") if item.strip()})
        )
        if not instance_ids:
            raise CLIError("--instances must list at least one instance id")

    try:
        run_config = resolve_swebench_run_config_from_args(args)
    except (FileNotFoundError, ValueError) as exc:
        raise CLIError(str(exc)) from exc

    configure_structured_logging(verbose=not args.quiet)
    print_swebench_execution_summary(
        command="phase-a",
        config=run_config,
        output_dir=output_dir,
        instance_count=len(instance_ids) if instance_ids else 0,
    )

    config = PhaseASchedulerConfig(
        metadata_path=metadata_path,
        output_dir=output_dir,
        instance_ids=instance_ids or (),
        workers=args.workers if args.workers is not None else run_config.workers,
        parallel_perturbations=args.parallel_perturbations,
        resume=args.resume,
        retry_failed=args.retry_failed,
        run_config=run_config,
        run_id=args.run_id or f"phase_a_{output_dir.name}",
        dataset_revision=args.dataset_revision,
        build_missing_images=args.build_missing_images,
    )

    try:
        summary = run_phase_a_scheduler(config)
    except ValueError as exc:
        raise CLIError(str(exc)) from exc

    if args.quiet:
        return

    json.dump(summary, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def cmd_registry_validate(args: argparse.Namespace) -> None:
    """Validate registry manifest against built-in specs."""
    del args
    errors = validate_registry()
    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        raise CLIError("registry validation failed", exit_code=1)
    json.dump({"status": "ok"}, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="earnbench",
        description="EarnBench — judge-free Earned Fraction measurement",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    compute_parser = subparsers.add_parser(
        "compute",
        help="Compute Earned Fraction from nominal and perturbation outcomes",
    )
    compute_parser.add_argument(
        "input",
        help="JSON file with 'nominal' and 'perturbations' fields",
    )

    validate_parser = subparsers.add_parser(
        "validate-audit",
        help="Validate an audit.json file against the AuditRecord schema",
    )
    validate_parser.add_argument("audit", help="Path to audit.json")
    validate_parser.add_argument(
        "--quiet",
        action="store_true",
        help="Exit 0 on success without printing the normalized record",
    )

    run_parser = subparsers.add_parser(
        "run",
        help="Run grading for one instance (execution not yet implemented)",
    )
    run_parser.add_argument(
        "--instance",
        required=True,
        help="SWE-bench instance id (e.g. django__django-13279)",
    )
    run_parser.add_argument(
        "--patch",
        required=True,
        help="Path to a unified-diff patch file",
    )
    run_parser.add_argument(
        "--perturbation",
        required=True,
        help="Perturbation id (e.g. pi_vtest.v1, nominal for Y0)",
    )
    run_parser.add_argument(
        "--config",
        required=True,
        help="Path to run_config.yaml",
    )

    registry_parser = subparsers.add_parser(
        "registry",
        help="Inspect the versioned perturbation registry",
    )
    registry_subparsers = registry_parser.add_subparsers(
        dest="registry_command",
        required=True,
    )
    registry_subparsers.add_parser(
        "list",
        help="List registered perturbations",
    )
    registry_show_parser = registry_subparsers.add_parser(
        "show",
        help="Show one perturbation spec",
    )
    registry_show_parser.add_argument(
        "perturbation_id",
        help="Perturbation id (e.g. pi_vtest.v1)",
    )
    registry_subparsers.add_parser(
        "validate",
        help="Validate manifest against built-in registry specs",
    )

    swebench_parser = subparsers.add_parser(
        "swebench",
        help="SWE-bench Verified adapter commands",
    )
    swebench_subparsers = swebench_parser.add_subparsers(
        dest="swebench_command",
        required=True,
    )
    prepare_smoke_parser = swebench_subparsers.add_parser(
        "prepare-smoke",
        help="Dry-run Phase A smoke preparation (no Docker)",
    )
    prepare_smoke_parser.add_argument(
        "--metadata-parquet",
        required=True,
        help="Path to SWE-bench Verified metadata (.parquet or test .json fixture)",
    )
    prepare_smoke_parser.add_argument(
        "--instance-id",
        required=True,
        help="SWE-bench instance id (e.g. psf__requests-1724)",
    )
    prepare_smoke_parser.add_argument(
        "--output",
        required=True,
        help="Output directory for smoke artifacts",
    )
    prepare_smoke_parser.add_argument(
        "--run-id",
        default="",
        help="Optional run id recorded in meta.json and plan.json",
    )
    prepare_smoke_parser.add_argument(
        "--dataset-revision",
        default="unpinned",
        help="Dataset revision label stored in adapter config digest inputs",
    )
    prepare_smoke_parser.add_argument(
        "--quiet",
        action="store_true",
        help="Write artifacts only; do not print plan.json to stdout",
    )
    preflight_parser = swebench_subparsers.add_parser(
        "preflight",
        help="Check or build SWE-bench Docker images for one instance",
    )
    preflight_parser.add_argument(
        "--metadata-parquet",
        required=True,
        help="Path to SWE-bench Verified metadata (.parquet or test .json fixture)",
    )
    preflight_parser.add_argument(
        "--instance-id",
        required=True,
        help="SWE-bench instance id (e.g. psf__requests-1724)",
    )
    preflight_parser.add_argument(
        "--output",
        required=True,
        help="Output directory for preflight artifacts",
    )
    preflight_parser.add_argument(
        "--build-missing-images",
        action="store_true",
        help="Build missing SWE-bench harness images when possible",
    )
    add_swebench_performance_arguments(preflight_parser)
    preflight_parser.add_argument(
        "--quiet",
        action="store_true",
        help="Write artifacts only; do not print preflight.json to stdout",
    )
    run_nominal_parser = swebench_subparsers.add_parser(
        "run-nominal",
        help="Run nominal SWE-bench grading (Docker harness)",
    )
    run_nominal_parser.add_argument(
        "--metadata-parquet",
        required=True,
        help="Path to SWE-bench Verified metadata (.parquet or test .json fixture)",
    )
    run_nominal_parser.add_argument(
        "--instance-id",
        required=True,
        help="SWE-bench instance id (e.g. psf__requests-1724)",
    )
    run_nominal_parser.add_argument(
        "--patch",
        required=True,
        help="Path to unified-diff patch file (prod-only recommended)",
    )
    run_nominal_parser.add_argument(
        "--output",
        required=True,
        help="Output directory for nominal grading artifacts",
    )
    add_swebench_performance_arguments(run_nominal_parser)
    run_nominal_parser.add_argument(
        "--run-id",
        default="",
        help="Optional SWE-bench harness run id (default: nominal_<instance_id>)",
    )
    run_nominal_parser.add_argument(
        "--quiet",
        action="store_true",
        help="Write artifacts only; do not print grade.json to stdout",
    )
    run_pi_verif_parser = swebench_subparsers.add_parser(
        "run-pi-verif",
        help="Run pi_verif.v1 SWE-bench grading (pristine trusted verifier)",
    )
    run_pi_verif_parser.add_argument(
        "--metadata-parquet",
        required=True,
        help="Path to SWE-bench Verified metadata (.parquet or test .json fixture)",
    )
    run_pi_verif_parser.add_argument(
        "--instance-id",
        required=True,
        help="SWE-bench instance id (e.g. psf__requests-1724)",
    )
    run_pi_verif_parser.add_argument(
        "--patch",
        required=True,
        help="Path to prod-only unified-diff patch file",
    )
    run_pi_verif_parser.add_argument(
        "--output",
        required=True,
        help="Output directory for pi_verif.v1 grading artifacts",
    )
    add_swebench_performance_arguments(run_pi_verif_parser)
    run_pi_verif_parser.add_argument(
        "--run-id",
        default="",
        help="Optional SWE-bench harness run id (default: pi_verif_<instance_id>)",
    )
    run_pi_verif_parser.add_argument(
        "--quiet",
        action="store_true",
        help="Write artifacts only; do not print grade.json to stdout",
    )
    run_pi_env_parser = swebench_subparsers.add_parser(
        "run-pi-env",
        help="Run pi_env.v1 SWE-bench grading (clean-slate hardened execution)",
    )
    run_pi_env_parser.add_argument(
        "--metadata-parquet",
        required=True,
        help="Path to SWE-bench Verified metadata (.parquet or test .json fixture)",
    )
    run_pi_env_parser.add_argument(
        "--instance-id",
        required=True,
        help="SWE-bench instance id (e.g. psf__requests-1724)",
    )
    run_pi_env_parser.add_argument(
        "--patch",
        required=True,
        help="Path to prod-only unified-diff patch file",
    )
    run_pi_env_parser.add_argument(
        "--output",
        required=True,
        help="Output directory for pi_env.v1 grading artifacts",
    )
    add_swebench_performance_arguments(run_pi_env_parser)
    run_pi_env_parser.add_argument(
        "--run-id",
        default="",
        help="Optional SWE-bench harness run id (default: pi_env_<instance_id>)",
    )
    run_pi_env_parser.add_argument(
        "--quiet",
        action="store_true",
        help="Write artifacts only; do not print grade.json to stdout",
    )
    diagnose_pi_env_parser = swebench_subparsers.add_parser(
        "diagnose-pi-env",
        help="Compare nominal vs pi_env.v1 artifacts and diagnose failures",
    )
    diagnose_pi_env_parser.add_argument(
        "--metadata-parquet",
        required=True,
        help="Path to SWE-bench Verified metadata (.parquet or test .json fixture)",
    )
    diagnose_pi_env_parser.add_argument(
        "--instance-id",
        required=True,
        help="SWE-bench instance id (e.g. psf__requests-1724)",
    )
    diagnose_pi_env_parser.add_argument(
        "--patch",
        required=True,
        help="Path to prod-only unified-diff patch file used for both runs",
    )
    diagnose_pi_env_parser.add_argument(
        "--nominal-dir",
        required=True,
        help="Path to nominal artifact directory (contains grade.json, harness.log)",
    )
    diagnose_pi_env_parser.add_argument(
        "--pi-env-dir",
        required=True,
        help="Path to pi_env.v1 artifact directory (contains grade.json, harness.log)",
    )
    diagnose_pi_env_parser.add_argument(
        "--output",
        required=True,
        help="Output directory for pi_env_diagnosis.json and pi_env_diagnosis.md",
    )
    diagnose_pi_env_parser.add_argument(
        "--quiet",
        action="store_true",
        help="Write diagnosis files only; do not print JSON to stdout",
    )

    phase_a_parser = subparsers.add_parser(
        "phase-a",
        help="Run Phase A golden validation batch (parallel scheduler)",
    )
    phase_a_parser.add_argument(
        "--metadata-parquet",
        required=True,
        help="Path to SWE-bench Verified metadata (.parquet or test .json fixture)",
    )
    phase_a_parser.add_argument(
        "--output",
        required=True,
        help="Batch output directory for Phase A artifacts and golden_validation.csv",
    )
    phase_a_parser.add_argument(
        "--instances",
        default="",
        help=(
            "Comma-separated instance ids "
            "(required for parquet; optional for json fixtures)"
        ),
    )
    phase_a_parser.add_argument(
        "--parallel-perturbations",
        type=int,
        default=3,
        help="Max concurrent π perturbation workers per instance (default: 3)",
    )
    phase_a_parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip completed jobs recorded in phase_a_scheduler_state.json",
    )
    phase_a_parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="With --resume, re-run jobs previously marked failed",
    )
    phase_a_parser.add_argument(
        "--run-id",
        default="",
        help="Batch run identifier stored in meta.json and golden_validation.csv",
    )
    phase_a_parser.add_argument(
        "--dataset-revision",
        default="unpinned",
        help="Dataset revision label stored in adapter config digest inputs",
    )
    phase_a_parser.add_argument(
        "--build-missing-images",
        action="store_true",
        help="Build missing SWE-bench harness images during preflight",
    )
    add_swebench_performance_arguments(phase_a_parser)
    phase_a_parser.add_argument(
        "--quiet",
        action="store_true",
        help="Structured logs only; do not print scheduler summary JSON to stdout",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "compute":
            cmd_compute(args)
        elif args.command == "validate-audit":
            cmd_validate_audit(args)
        elif args.command == "run":
            cmd_run(args)
        elif args.command == "registry":
            if args.registry_command == "list":
                cmd_registry_list(args)
            elif args.registry_command == "show":
                cmd_registry_show(args)
            elif args.registry_command == "validate":
                cmd_registry_validate(args)
            else:
                parser.error(f"unknown registry command: {args.registry_command}")
        elif args.command == "swebench":
            if args.swebench_command == "prepare-smoke":
                cmd_swebench_prepare_smoke(args)
            elif args.swebench_command == "preflight":
                cmd_swebench_preflight(args)
            elif args.swebench_command == "run-nominal":
                cmd_swebench_run_nominal(args)
            elif args.swebench_command == "run-pi-verif":
                cmd_swebench_run_pi_verif(args)
            elif args.swebench_command == "run-pi-env":
                cmd_swebench_run_pi_env(args)
            elif args.swebench_command == "diagnose-pi-env":
                cmd_swebench_diagnose_pi_env(args)
            else:
                parser.error(f"unknown swebench command: {args.swebench_command}")
        elif args.command == "phase-a":
            cmd_phase_a(args)
        else:
            parser.error(f"unknown command: {args.command}")
    except CLIError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.exit_code
    except NotImplementedError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
