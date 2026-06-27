"""EarnBench command-line interface."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from earnbench.adapters.swebench import (
    DEFAULT_HOLDOUT_K,
    DEFAULT_HOLDOUT_SALT,
    prepare_smoke,
)
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
from earnbench.adapters.swebench_pi_vtest import run_pi_vtest_grading
from earnbench.adapters.swebench_preflight import (
    MissingDockerImagesError,
    run_swebench_preflight,
)
from earnbench.audit import AuditRecord
from earnbench.classification import PerturbationOutcome
from earnbench.exploit_validator import validate_runtime_run
from earnbench.exploits.catalog import ExploitCatalogError, get_exploit, list_exploits
from earnbench.exploits.validate import validate_path
from earnbench.external_exploits import validate_external_exploit_catalog
from earnbench.injection_batch import InjectionBatchConfig, run_injection_batch
from earnbench.injection_unblind import BlindUnblindError, unblind_injection_run
from earnbench.injection_validity import generate_injection_validity_report
from earnbench.injections import (
    InjectionCatalogError,
    get_injection,
    list_injections,
)
from earnbench.injections import (
    validate_path as validate_injection_path,
)
from earnbench.injections.manifests import (
    BlindInjectionError,
    prepare_injection_manifests,
)
from earnbench.investigate import write_phase_a_investigation
from earnbench.metrics import compute_earned_fraction
from earnbench.outcomes import NominalOutcome, OutcomeStatus, PerturbationResult
from earnbench.phase_a_batch import (
    PhaseABatchConfig,
    resolve_batch_paths,
    run_phase_a_batch,
)
from earnbench.phase_a_report import generate_phase_a_report
from earnbench.phase_b_batch import (
    PhaseBBatchConfig,
    resolve_metadata_path,
    run_phase_b_batch,
)
from earnbench.phase_b_report import generate_phase_b_report
from earnbench.phase_c_agents import (
    PhaseCError,
    prepare_phase_c,
    run_phase_c,
    summarize_phase_c,
)
from earnbench.provenance import Provenance, build_provenance
from earnbench.rank_stability import generate_rank_stability_report
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
        outcome_raw = item.get("outcome")
        outcome = (
            PerturbationOutcome(str(outcome_raw)) if outcome_raw is not None else None
        )
        try:
            perturbations.append(
                PerturbationResult(
                    perturbation_id=perturbation_id,
                    status=status,
                    success=success,
                    outcome=outcome,
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


def cmd_swebench_run_pi_vtest(args: argparse.Namespace) -> None:
    """Run pi_vtest.v1 SWE-bench grading for one instance."""
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
        command="run-pi-vtest",
        config=run_config,
        output_dir=output_dir,
        instance_count=1,
    )

    try:
        grade = run_pi_vtest_grading(
            metadata_path=metadata_path,
            instance_id=args.instance_id,
            patch_path=patch_path,
            output_dir=output_dir,
            timeout_seconds=run_config.timeout_seconds,
            run_id=args.run_id or None,
            config=run_config,
            holdout_salt=args.holdout_salt,
            holdout_k=args.holdout_k,
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


def cmd_phase_a_schedule(args: argparse.Namespace) -> None:
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
        command="phase-a schedule",
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


def cmd_phase_a_run(args: argparse.Namespace) -> None:
    """Run Phase A golden validation batch experiment (sequential π per instance)."""
    manifest_path = Path(args.manifest)

    if not manifest_path.is_file():
        raise CLIError(f"--manifest file not found: {manifest_path}")

    try:
        metadata_path, output_dir = resolve_batch_paths(
            manifest_path,
            metadata_parquet=Path(args.metadata_parquet)
            if args.metadata_parquet
            else None,
            output_dir=Path(args.output) if args.output else None,
        )
    except FileNotFoundError as exc:
        raise CLIError(str(exc)) from exc

    suffix = metadata_path.suffix.lower()
    if suffix not in {".parquet", ".json"}:
        raise CLIError(
            f"--metadata-parquet must be a .parquet or .json file, got: {metadata_path}"
        )

    try:
        run_config = resolve_swebench_run_config_from_args(args)
    except (FileNotFoundError, ValueError) as exc:
        raise CLIError(str(exc)) from exc

    configure_structured_logging(verbose=not args.quiet)
    print_swebench_execution_summary(
        command="phase-a run",
        config=run_config,
        output_dir=output_dir,
        instance_count=0,
    )

    config = PhaseABatchConfig(
        manifest_path=manifest_path,
        metadata_path=metadata_path,
        output_dir=output_dir,
        workers=args.workers if args.workers is not None else run_config.workers,
        resume=args.resume,
        run_config=run_config,
        run_id=args.run_id or f"phase_a_{output_dir.name}",
        dataset_revision=args.dataset_revision,
        build_missing_images=args.build_missing_images,
    )

    try:
        summary = run_phase_a_batch(config)
    except FileNotFoundError as exc:
        raise CLIError(str(exc)) from exc
    except ValueError as exc:
        raise CLIError(str(exc)) from exc

    if args.quiet:
        return

    json.dump(summary, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def cmd_report_phase_a(args: argparse.Namespace) -> None:
    """Generate deterministic Phase A markdown report."""
    output_dir = Path(args.output_dir).resolve()
    try:
        result = generate_phase_a_report(output_dir)
    except FileNotFoundError as exc:
        raise CLIError(str(exc)) from exc
    except ValueError as exc:
        raise CLIError(str(exc)) from exc

    if args.quiet:
        return

    json.dump(
        {
            "report_path": str(result.report_path),
            "output_dir": str(result.output_dir),
        },
        sys.stdout,
        indent=2,
        sort_keys=True,
    )
    sys.stdout.write("\n")


def cmd_report_phase_b(args: argparse.Namespace) -> None:
    """Generate deterministic Phase B markdown report."""
    output_dir = Path(args.output_dir).resolve()
    try:
        result = generate_phase_b_report(output_dir)
    except FileNotFoundError as exc:
        raise CLIError(str(exc)) from exc
    except ValueError as exc:
        raise CLIError(str(exc)) from exc

    if args.quiet:
        return

    json.dump(
        {
            "report_path": str(result.report_path),
            "output_dir": str(result.output_dir),
        },
        sys.stdout,
        indent=2,
        sort_keys=True,
    )
    sys.stdout.write("\n")


def cmd_report_rank_stability(args: argparse.Namespace) -> None:
    """Generate Earned Rank Stability artifacts from agent × instance CSV."""
    agent_results = Path(args.agent_results).resolve()
    output_dir = Path(args.output).resolve()
    try:
        result = generate_rank_stability_report(
            agent_results,
            output_dir,
            bootstrap_draws=args.bootstrap,
        )
    except FileNotFoundError as exc:
        raise CLIError(str(exc)) from exc
    except ValueError as exc:
        raise CLIError(str(exc)) from exc

    if args.quiet:
        return

    json.dump(
        {
            "output_dir": str(result.output_dir),
            "summary_csv": str(result.summary_csv),
            "pairwise_flips_csv": str(result.pairwise_flips_csv),
            "channel_rank_contributions_csv": str(
                result.channel_rank_contributions_csv,
            ),
            "report_md": str(result.report_md),
            "report_json": str(result.report_json),
        },
        sys.stdout,
        indent=2,
        sort_keys=True,
    )
    sys.stdout.write("\n")


def cmd_report_injection_validity(args: argparse.Namespace) -> None:
    """Generate blinded injection validity report artifacts."""
    results_path = Path(args.results).resolve()
    specs_dir = Path(args.specs).resolve()
    output_dir = Path(args.output).resolve()
    try:
        result = generate_injection_validity_report(
            results_path,
            specs_dir,
            output_dir,
        )
    except FileNotFoundError as exc:
        raise CLIError(str(exc)) from exc
    except ValueError as exc:
        raise CLIError(str(exc)) from exc

    if args.quiet:
        return

    json.dump(
        {
            "output_dir": str(result.output_dir),
            "summary_csv": str(result.summary_csv),
            "channel_attribution_matrix_csv": str(
                result.channel_attribution_matrix_csv,
            ),
            "false_earned_false_unearned_csv": str(
                result.false_earned_false_unearned_csv,
            ),
            "invalid_asymmetry_csv": str(result.invalid_asymmetry_csv),
            "report_md": str(result.report_md),
        },
        sys.stdout,
        indent=2,
        sort_keys=True,
    )
    sys.stdout.write("\n")


def cmd_investigate(args: argparse.Namespace) -> None:
    """Investigate one Phase A batch instance."""
    phase_a_run = Path(args.phase_a_run).resolve()
    metadata_path = (
        Path(args.metadata_parquet).resolve() if args.metadata_parquet else None
    )
    output_dir = Path(args.output).resolve() if args.output else None
    try:
        result = write_phase_a_investigation(
            phase_a_run=phase_a_run,
            instance_id=args.instance_id,
            metadata_path=metadata_path,
            output_dir=output_dir,
        )
    except FileNotFoundError as exc:
        raise CLIError(str(exc)) from exc
    except ValueError as exc:
        raise CLIError(str(exc)) from exc

    if args.quiet:
        return

    json.dump(
        {
            "instance_id": result.instance_id,
            "phase_a_run": str(result.phase_a_run),
            "investigation_json": str(result.investigation_json),
            "investigation_md": str(result.investigation_md),
        },
        sys.stdout,
        indent=2,
        sort_keys=True,
    )
    sys.stdout.write("\n")


def cmd_phase_b_run(args: argparse.Namespace) -> None:
    """Run Phase B planted exploit batch experiment."""
    exploit_dir = Path(args.exploit_dir)
    if not exploit_dir.is_dir():
        raise CLIError(f"--exploit-dir not found: {exploit_dir}")

    try:
        metadata_path = resolve_metadata_path(
            Path(args.metadata_parquet) if args.metadata_parquet else None,
        )
    except FileNotFoundError as exc:
        raise CLIError(str(exc)) from exc

    output_dir = (
        Path(args.output).resolve()
        if args.output
        else (Path.cwd() / "phase_b").resolve()
    )

    suffix = metadata_path.suffix.lower()
    if suffix not in {".parquet", ".json"}:
        raise CLIError(
            f"--metadata-parquet must be a .parquet or .json file, got: {metadata_path}"
        )

    exploit_ids: tuple[str, ...] | None = None
    if args.exploit_ids:
        exploit_ids = tuple(
            item.strip()
            for item in args.exploit_ids.split(",")
            if item.strip()
        )
        if not exploit_ids:
            raise CLIError("--exploit-ids must list at least one exploit id")

    try:
        run_config = resolve_swebench_run_config_from_args(args)
    except (FileNotFoundError, ValueError) as exc:
        raise CLIError(str(exc)) from exc

    configure_structured_logging(verbose=not args.quiet)
    print_swebench_execution_summary(
        command="phase-b run",
        config=run_config,
        output_dir=output_dir,
        instance_count=0,
    )

    config = PhaseBBatchConfig(
        exploit_dir=exploit_dir.resolve(),
        metadata_path=metadata_path,
        output_dir=output_dir,
        workers=args.workers if args.workers is not None else run_config.workers,
        resume=args.resume,
        run_config=run_config,
        run_id=args.run_id or f"phase_b_{output_dir.name}",
        dataset_revision=args.dataset_revision,
        build_missing_images=args.build_missing_images,
        exploit_ids=exploit_ids,
    )

    try:
        summary = run_phase_b_batch(config)
    except ValueError as exc:
        raise CLIError(str(exc)) from exc

    if args.quiet:
        return

    json.dump(summary, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def cmd_phase_c_prepare(args: argparse.Namespace) -> None:
    """Prepare Phase C agent collection manifest and directory layout."""
    instances_path = Path(args.instances) if args.instances else None
    try:
        result = prepare_phase_c(
            phase_a_run=Path(args.phase_a_run),
            output_dir=Path(args.output),
            arms_path=Path(args.agent_arms),
            instances_path=instances_path,
        )
    except PhaseCError as exc:
        raise CLIError(str(exc)) from exc

    if args.quiet:
        return

    payload = {
        "manifest_path": str(result.manifest_path),
        "task_count": result.task_count,
        "instance_count": result.instance_count,
        "arm_count": result.arm_count,
    }
    json.dump(payload, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def cmd_phase_c_run(args: argparse.Namespace) -> None:
    """Run Phase C patch collection for all manifest tasks."""
    output_dir = Path(args.output) if args.output else None
    try:
        result = run_phase_c(
            manifest_path=Path(args.manifest),
            output_dir=output_dir,
            workers=args.workers,
            resume=args.resume,
        )
    except PhaseCError as exc:
        raise CLIError(str(exc)) from exc

    if args.quiet:
        return

    payload = {
        "output_dir": str(result.output_dir),
        "attempt_count": result.attempt_count,
        "ok_count": result.ok_count,
        "no_patch_count": result.no_patch_count,
        "error_count": result.error_count,
        "skipped_count": result.skipped_count,
        "attempts_csv": str(result.attempts_csv),
        "failures_path": str(result.failures_path),
    }
    json.dump(payload, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def cmd_phase_c_summarize(args: argparse.Namespace) -> None:
    """Summarize a completed Phase C run directory."""
    try:
        summary = summarize_phase_c(output_dir=Path(args.run))
    except PhaseCError as exc:
        raise CLIError(str(exc)) from exc

    if args.quiet:
        return

    json.dump(summary.to_dict(), sys.stdout, indent=2, sort_keys=True)
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


def cmd_exploit_list(args: argparse.Namespace) -> None:
    """List exploit specifications in a directory."""
    directory = Path(args.directory)
    try:
        specs = list_exploits(directory)
    except ExploitCatalogError as exc:
        raise CLIError(str(exc)) from exc
    payload = {"exploits": [spec.to_dict() for spec in specs]}
    json.dump(payload, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def cmd_exploit_show(args: argparse.Namespace) -> None:
    """Print one exploit specification as JSON."""
    directory = Path(args.directory)
    try:
        spec = get_exploit(directory, args.exploit_id)
    except ExploitCatalogError as exc:
        raise CLIError(str(exc)) from exc
    json.dump(spec.to_dict(), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def cmd_exploit_validate(args: argparse.Namespace) -> None:
    """Validate one exploit spec file or a directory of specs."""
    errors = validate_path(Path(args.path))
    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        raise CLIError("exploit validation failed", exit_code=1)
    if args.quiet:
        return
    json.dump({"status": "ok"}, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def cmd_exploit_validate_runtime(args: argparse.Namespace) -> None:
    """Validate completed Phase B runtime artifacts against scientific invariants."""
    output_dir = Path(args.run).resolve()
    exploit_dir = Path(args.exploit_dir).resolve() if args.exploit_dir else None
    result = validate_runtime_run(
        output_dir,
        exploit_dir=exploit_dir,
        exploit_ids=tuple(args.exploit_ids.split(",")) if args.exploit_ids else None,
    )
    payload = {
        "status": "ok" if result.ok else "failed",
        "output_dir": str(result.output_dir),
        "exploit_dir": str(result.exploit_dir),
        "validated_exploit_ids": list(result.validated_exploit_ids),
        "error_count": len(result.errors),
        "errors": list(result.errors),
    }
    if not args.quiet or not result.ok:
        json.dump(payload, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    if not result.ok:
        for error in result.errors:
            print(f"error: {error}", file=sys.stderr)
        raise CLIError("runtime exploit validation failed", exit_code=1)


def cmd_injection_list(args: argparse.Namespace) -> None:
    """List blinded injection specifications in a directory."""
    directory = Path(args.directory)
    try:
        specs = list_injections(directory)
    except InjectionCatalogError as exc:
        raise CLIError(str(exc)) from exc
    payload = {"injections": [spec.to_dict() for spec in specs]}
    json.dump(payload, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def cmd_injection_show(args: argparse.Namespace) -> None:
    """Print one blinded injection specification as JSON."""
    directory = Path(args.directory)
    try:
        spec = get_injection(directory, args.injection_id)
    except InjectionCatalogError as exc:
        raise CLIError(str(exc)) from exc
    json.dump(spec.to_dict(), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def cmd_injection_validate(args: argparse.Namespace) -> None:
    """Validate one injection spec file or a directory of specs."""
    errors = validate_injection_path(Path(args.path))
    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        raise CLIError("injection validation failed", exit_code=1)
    if args.quiet:
        return
    json.dump({"status": "ok"}, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def cmd_injection_prepare(args: argparse.Namespace) -> None:
    """Build injector/evaluator manifests and blind lockfile from specs."""
    spec_dir = Path(args.spec_dir)
    if not spec_dir.is_dir():
        raise CLIError(f"--spec-dir not found: {spec_dir}")
    output_dir = Path(args.output).resolve()
    errors = validate_injection_path(spec_dir)
    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        raise CLIError("injection spec validation failed", exit_code=1)
    try:
        result = prepare_injection_manifests(spec_dir, output_dir)
    except BlindInjectionError as exc:
        raise CLIError(str(exc)) from exc
    payload = {
        "status": "ok",
        "pair_count": result.pair_count,
        "artifact_count": result.artifact_count,
        "injector_manifest": str(result.injector_manifest),
        "evaluator_manifest": str(result.evaluator_manifest),
        "blind_lockfile": str(result.blind_lockfile),
    }
    if not args.quiet:
        json.dump(payload, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")


def cmd_injection_run(args: argparse.Namespace) -> None:
    """Run blind injection grading from evaluator manifest only."""
    evaluator_manifest = Path(args.evaluator_manifest)
    if not evaluator_manifest.is_file():
        raise CLIError(f"--evaluator-manifest not found: {evaluator_manifest}")
    try:
        metadata_path = resolve_metadata_path(
            Path(args.metadata_parquet) if args.metadata_parquet else None,
        )
    except FileNotFoundError as exc:
        raise CLIError(str(exc)) from exc
    output_dir = (
        Path(args.output).resolve()
        if args.output
        else (Path.cwd() / "blind_injection_run").resolve()
    )
    try:
        run_config = resolve_swebench_run_config_from_args(args)
    except (FileNotFoundError, ValueError) as exc:
        raise CLIError(str(exc)) from exc
    configure_structured_logging(verbose=not args.quiet)
    print_swebench_execution_summary(
        command="injection run",
        config=run_config,
        output_dir=output_dir,
        instance_count=0,
    )
    batch_config = InjectionBatchConfig(
        evaluator_manifest_path=evaluator_manifest.resolve(),
        metadata_path=metadata_path,
        output_dir=output_dir,
        run_config=run_config,
        workers=run_config.workers,
        resume=bool(args.resume),
        run_id=args.run_id or "blind_injection",
        dataset_revision=args.dataset_revision,
        build_missing_images=bool(args.build_missing_images),
    )
    payload = run_injection_batch(batch_config)
    if not args.quiet:
        json.dump(payload, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")


def cmd_injection_unblind(args: argparse.Namespace) -> None:
    """Verify lockfile and merge ground truth for injection validity analysis."""
    run_dir = Path(args.run).resolve()
    injector_manifest = Path(args.injector_manifest).resolve()
    lockfile = Path(args.lockfile).resolve()
    output_dir = Path(args.output).resolve() if args.output else run_dir
    try:
        result = unblind_injection_run(
            run_dir=run_dir,
            injector_manifest_path=injector_manifest,
            lockfile_path=lockfile,
            output_dir=output_dir,
        )
    except (BlindUnblindError, BlindInjectionError) as exc:
        raise CLIError(str(exc)) from exc
    payload = {
        "status": "ok",
        "output_dir": str(result.output_dir),
        "injection_validity_report_md": str(result.report_md),
        "channel_attribution_matrix_csv": str(result.channel_attribution_matrix_csv),
        "injection_validity_summary_csv": str(result.summary_csv),
    }
    if not args.quiet:
        json.dump(payload, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")


def cmd_external_exploit_validate_catalog(args: argparse.Namespace) -> None:
    """Validate a Phase B-ext external exploit catalog CSV."""
    result = validate_external_exploit_catalog(Path(args.catalog))
    if not result.ok:
        for error in result.errors:
            print(f"error: {error}", file=sys.stderr)
        raise CLIError("external exploit catalog validation failed", exit_code=1)
    if args.quiet:
        return
    json.dump(
        {
            "status": "ok",
            "catalog": str(result.path),
            "row_count": result.row_count,
        },
        sys.stdout,
        indent=2,
        sort_keys=True,
    )
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

    exploit_parser = subparsers.add_parser(
        "exploit",
        help="Inspect and validate planted exploit specifications",
    )
    exploit_subparsers = exploit_parser.add_subparsers(
        dest="exploit_command",
        required=True,
    )
    exploit_list_parser = exploit_subparsers.add_parser(
        "list",
        help="List exploit specs in a directory",
    )
    exploit_list_parser.add_argument(
        "directory",
        help="Directory containing exploit spec files (.json, .yaml, .yml)",
    )
    exploit_show_parser = exploit_subparsers.add_parser(
        "show",
        help="Show one exploit spec",
    )
    exploit_show_parser.add_argument(
        "exploit_id",
        help="Exploit id (exploit_id field)",
    )
    exploit_show_parser.add_argument(
        "directory",
        help="Directory containing exploit spec files",
    )
    exploit_validate_parser = exploit_subparsers.add_parser(
        "validate",
        help="Validate an exploit spec file or directory",
    )
    exploit_validate_parser.add_argument(
        "path",
        help="Exploit spec file or directory of specs",
    )
    exploit_validate_parser.add_argument(
        "--quiet",
        action="store_true",
        help="Do not print validation summary JSON on success",
    )
    exploit_validate_runtime_parser = exploit_subparsers.add_parser(
        "validate-runtime",
        help="Validate Phase B runtime results against scientific invariants",
    )
    exploit_validate_runtime_parser.add_argument(
        "--run",
        required=True,
        help="Completed Phase B batch output directory containing summary.csv",
    )
    exploit_validate_runtime_parser.add_argument(
        "--exploit-dir",
        default="",
        help="Exploit spec directory (default: read from run_manifest.json)",
    )
    exploit_validate_runtime_parser.add_argument(
        "--exploit-ids",
        default="",
        help="Comma-separated exploit ids to validate (default: all in run manifest)",
    )
    exploit_validate_runtime_parser.add_argument(
        "--quiet",
        action="store_true",
        help="Do not print validation summary JSON on success",
    )

    injection_parser = subparsers.add_parser(
        "injection",
        help="Inspect and validate blinded injection specifications",
    )
    injection_subparsers = injection_parser.add_subparsers(
        dest="injection_command",
        required=True,
    )
    injection_list_parser = injection_subparsers.add_parser(
        "list",
        help="List injection specs in a directory",
    )
    injection_list_parser.add_argument(
        "directory",
        help="Directory containing injection spec files (.json, .yaml, .yml)",
    )
    injection_show_parser = injection_subparsers.add_parser(
        "show",
        help="Show one injection spec",
    )
    injection_show_parser.add_argument(
        "injection_id",
        help="Injection id (injection_id field)",
    )
    injection_show_parser.add_argument(
        "directory",
        help="Directory containing injection spec files",
    )
    injection_validate_parser = injection_subparsers.add_parser(
        "validate",
        help="Validate an injection spec file or directory",
    )
    injection_validate_parser.add_argument(
        "path",
        help="Injection spec file or directory of specs",
    )
    injection_validate_parser.add_argument(
        "--quiet",
        action="store_true",
        help="Do not print validation summary JSON on success",
    )
    injection_prepare_parser = injection_subparsers.add_parser(
        "prepare",
        help="Build injector/evaluator manifests and blind lockfile",
    )
    injection_prepare_parser.add_argument(
        "--spec-dir",
        required=True,
        help="Directory containing BI*.yaml injection specs and patches/",
    )
    injection_prepare_parser.add_argument(
        "--output",
        required=True,
        help="Output directory for manifests and lockfile",
    )
    injection_prepare_parser.add_argument(
        "--quiet",
        action="store_true",
        help="Do not print prepare summary JSON on success",
    )
    injection_run_parser = injection_subparsers.add_parser(
        "run",
        help="Run blind injection harness grading from evaluator manifest",
    )
    injection_run_parser.add_argument(
        "--evaluator-manifest",
        required=True,
        help="Path to evaluator_manifest.json (blinded artifact list)",
    )
    injection_run_parser.add_argument(
        "--metadata-parquet",
        default="",
        help="SWE-bench Verified metadata (.parquet or .json)",
    )
    injection_run_parser.add_argument(
        "--output",
        default="",
        help="Batch output directory (default: blind_injection_run/)",
    )
    injection_run_parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip completed artifact stages recorded on disk",
    )
    injection_run_parser.add_argument(
        "--run-id",
        default="",
        help="Run identifier stored in run_manifest.json",
    )
    injection_run_parser.add_argument(
        "--dataset-revision",
        default="unpinned",
        help="Dataset revision label for adapter config digest",
    )
    injection_run_parser.add_argument(
        "--build-missing-images",
        action="store_true",
        help="Build missing SWE-bench harness images during preflight",
    )
    injection_run_parser.add_argument(
        "--quiet",
        action="store_true",
        help="Progress on stderr only; omit batch summary JSON on stdout",
    )
    injection_unblind_parser = injection_subparsers.add_parser(
        "unblind",
        help="Verify lockfile and produce injection validity analysis",
    )
    injection_unblind_parser.add_argument(
        "--run",
        required=True,
        help="Blind run output directory from injection run",
    )
    injection_unblind_parser.add_argument(
        "--injector-manifest",
        required=True,
        help="Ground-truth injector_manifest.json from prepare step",
    )
    injection_unblind_parser.add_argument(
        "--lockfile",
        required=True,
        help="blind_lockfile.json from prepare step",
    )
    injection_unblind_parser.add_argument(
        "--output",
        default="",
        help="Analysis output directory (default: same as --run)",
    )
    injection_unblind_parser.add_argument(
        "--quiet",
        action="store_true",
        help="Do not print unblind summary JSON on success",
    )

    external_exploit_parser = subparsers.add_parser(
        "external-exploit",
        help="Phase B-ext external exploit catalog tools",
    )
    external_exploit_subparsers = external_exploit_parser.add_subparsers(
        dest="external_exploit_command",
        required=True,
    )
    external_validate_catalog_parser = external_exploit_subparsers.add_parser(
        "validate-catalog",
        help="Validate external exploit catalog CSV schema",
    )
    external_validate_catalog_parser.add_argument(
        "catalog",
        help="Path to external_exploit_catalog.csv",
    )
    external_validate_catalog_parser.add_argument(
        "--quiet",
        action="store_true",
        help="Do not print validation summary JSON on success",
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
    run_pi_vtest_parser = swebench_subparsers.add_parser(
        "run-pi-vtest",
        help="Run pi_vtest.v1 SWE-bench grading (holdout F2P re-grade)",
    )
    run_pi_vtest_parser.add_argument(
        "--metadata-parquet",
        required=True,
        help="Path to SWE-bench Verified metadata (.parquet or test .json fixture)",
    )
    run_pi_vtest_parser.add_argument(
        "--instance-id",
        required=True,
        help="SWE-bench instance id (e.g. psf__requests-1724)",
    )
    run_pi_vtest_parser.add_argument(
        "--patch",
        required=True,
        help="Path to prod-only unified-diff patch file",
    )
    run_pi_vtest_parser.add_argument(
        "--output",
        required=True,
        help="Output directory for pi_vtest.v1 grading artifacts",
    )
    add_swebench_performance_arguments(run_pi_vtest_parser)
    run_pi_vtest_parser.add_argument(
        "--holdout-salt",
        default=DEFAULT_HOLDOUT_SALT,
        help="Deterministic salt for holdout partition H(x)",
    )
    run_pi_vtest_parser.add_argument(
        "--holdout-k",
        type=int,
        default=DEFAULT_HOLDOUT_K,
        help="Modulus K for hash(instance_id + salt + test) mod K",
    )
    run_pi_vtest_parser.add_argument(
        "--run-id",
        default="",
        help="Optional SWE-bench harness run id (default: pi_vtest_<instance_id>)",
    )
    run_pi_vtest_parser.add_argument(
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
        help=(
            "Nominal artifact dir (grade.json) or batch output root "
            "(<dir>/<instance_id>/nominal resolved automatically)"
        ),
    )
    diagnose_pi_env_parser.add_argument(
        "--pi-env-dir",
        required=True,
        help=(
            "pi_env.v1 artifact dir (grade.json) or batch output root "
            "(<dir>/<instance_id>/pi_env.v1 resolved automatically)"
        ),
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
        help="Phase A golden validation (batch runner or parallel scheduler)",
    )
    phase_a_subparsers = phase_a_parser.add_subparsers(
        dest="phase_a_command",
        required=True,
    )

    phase_a_run_parser = phase_a_subparsers.add_parser(
        "run",
        help="Run Phase A batch experiment (nominal → π sequential → EF)",
    )
    phase_a_run_parser.add_argument(
        "--manifest",
        required=True,
        help="Pilot instance manifest JSON (e.g. pilot_instance_selection.json)",
    )
    phase_a_run_parser.add_argument(
        "--metadata-parquet",
        default="",
        help=(
            "SWE-bench Verified metadata (.parquet or .json). "
            "Default: manifest metadata_parquet, "
            "$EARNBENCH_METADATA_PARQUET, or ../paper/vendor/swe_verified_test.parquet"
        ),
    )
    phase_a_run_parser.add_argument(
        "--output",
        default="",
        help="Batch output directory (default: phase_a/ or manifest output field)",
    )
    add_swebench_performance_arguments(phase_a_run_parser)
    phase_a_run_parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip completed instances and stages recorded on disk",
    )
    phase_a_run_parser.add_argument(
        "--run-id",
        default="",
        help="Batch run identifier stored in run_manifest.json and summary.csv",
    )
    phase_a_run_parser.add_argument(
        "--dataset-revision",
        default="unpinned",
        help="Dataset revision label stored in adapter config digest inputs",
    )
    phase_a_run_parser.add_argument(
        "--build-missing-images",
        action="store_true",
        help="Build missing SWE-bench harness images during preflight",
    )
    phase_a_run_parser.add_argument(
        "--quiet",
        action="store_true",
        help="Progress on stderr only; do not print batch summary JSON to stdout",
    )

    phase_a_schedule_parser = phase_a_subparsers.add_parser(
        "schedule",
        help="Run Phase A parallel scheduler (legacy π-parallel worker)",
    )
    phase_a_schedule_parser.add_argument(
        "--metadata-parquet",
        required=True,
        help="Path to SWE-bench Verified metadata (.parquet or test .json fixture)",
    )
    phase_a_schedule_parser.add_argument(
        "--output",
        required=True,
        help="Batch output directory for Phase A artifacts and golden_validation.csv",
    )
    phase_a_schedule_parser.add_argument(
        "--instances",
        default="",
        help=(
            "Comma-separated instance ids "
            "(required for parquet; optional for json fixtures)"
        ),
    )
    phase_a_schedule_parser.add_argument(
        "--parallel-perturbations",
        type=int,
        default=3,
        help="Max concurrent π perturbation workers per instance (default: 3)",
    )
    phase_a_schedule_parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip completed jobs recorded in phase_a_scheduler_state.json",
    )
    phase_a_schedule_parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="With --resume, re-run jobs previously marked failed",
    )
    phase_a_schedule_parser.add_argument(
        "--run-id",
        default="",
        help="Batch run identifier stored in meta.json and golden_validation.csv",
    )
    phase_a_schedule_parser.add_argument(
        "--dataset-revision",
        default="unpinned",
        help="Dataset revision label stored in adapter config digest inputs",
    )
    phase_a_schedule_parser.add_argument(
        "--build-missing-images",
        action="store_true",
        help="Build missing SWE-bench harness images during preflight",
    )
    add_swebench_performance_arguments(phase_a_schedule_parser)
    phase_a_schedule_parser.add_argument(
        "--quiet",
        action="store_true",
        help="Structured logs only; do not print scheduler summary JSON to stdout",
    )

    phase_b_parser = subparsers.add_parser(
        "phase-b",
        help="Phase B planted exploit criterion battery",
    )
    phase_b_subparsers = phase_b_parser.add_subparsers(
        dest="phase_b_command",
        required=True,
    )

    phase_b_run_parser = phase_b_subparsers.add_parser(
        "run",
        help="Run Phase B exploit batch (nominal → π sequential → EF)",
    )
    phase_b_run_parser.add_argument(
        "--exploit-dir",
        required=True,
        help="Directory containing exploit YAML specs and patches/ subdirectory",
    )
    phase_b_run_parser.add_argument(
        "--exploit-ids",
        default="",
        help=(
            "Comma-separated exploit ids to run (default: all specs in --exploit-dir). "
            "Order follows the catalog, not this list."
        ),
    )
    phase_b_run_parser.add_argument(
        "--metadata-parquet",
        default="",
        help=(
            "SWE-bench Verified metadata (.parquet or .json). "
            "Default: $EARNBENCH_METADATA_PARQUET or ../paper/vendor/swe_verified_test.parquet"
        ),
    )
    phase_b_run_parser.add_argument(
        "--output",
        default="",
        help="Batch output directory (default: phase_b/)",
    )
    add_swebench_performance_arguments(phase_b_run_parser)
    phase_b_run_parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip completed exploits and stages recorded on disk",
    )
    phase_b_run_parser.add_argument(
        "--run-id",
        default="",
        help="Batch run identifier stored in run_manifest.json and summary.csv",
    )
    phase_b_run_parser.add_argument(
        "--dataset-revision",
        default="unpinned",
        help="Dataset revision label stored in adapter config digest inputs",
    )
    phase_b_run_parser.add_argument(
        "--build-missing-images",
        action="store_true",
        help="Build missing SWE-bench harness images during preflight",
    )
    phase_b_run_parser.add_argument(
        "--quiet",
        action="store_true",
        help="Progress on stderr only; do not print batch summary JSON to stdout",
    )

    phase_c_parser = subparsers.add_parser(
        "phase-c",
        help="Phase C agent patch collection (no EF computation)",
    )
    phase_c_subparsers = phase_c_parser.add_subparsers(
        dest="phase_c_command",
        required=True,
    )

    phase_c_prepare_parser = phase_c_subparsers.add_parser(
        "prepare",
        help="Prepare Phase C manifest from a Phase A run and agent arms",
    )
    phase_c_prepare_parser.add_argument(
        "--phase-a-run",
        required=True,
        help="Completed Phase A batch directory (run_manifest.json + summary.csv)",
    )
    phase_c_prepare_parser.add_argument(
        "--output",
        required=True,
        help="Phase C output directory",
    )
    phase_c_prepare_parser.add_argument(
        "--agent-arms",
        required=True,
        help="YAML file listing agent arms (arms.yaml)",
    )
    phase_c_prepare_parser.add_argument(
        "--instances",
        default="",
        help=(
            "Optional CSV or JSON instance-id list; "
            "default: retained instances from Phase A summary.csv"
        ),
    )
    phase_c_prepare_parser.add_argument(
        "--quiet",
        action="store_true",
        help="Do not print prepare summary JSON to stdout",
    )

    phase_c_run_parser = phase_c_subparsers.add_parser(
        "run",
        help="Collect patch attempts for all manifest tasks",
    )
    phase_c_run_parser.add_argument(
        "--manifest",
        required=True,
        help="Prepared run_manifest.json path (typically OUT/run_manifest.json)",
    )
    phase_c_run_parser.add_argument(
        "--output",
        default="",
        help="Override output directory from manifest (default: manifest output_dir)",
    )
    phase_c_run_parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Concurrent collection workers (default: 1)",
    )
    phase_c_run_parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip tasks already recorded in attempts.jsonl",
    )
    phase_c_run_parser.add_argument(
        "--quiet",
        action="store_true",
        help="Do not print run summary JSON to stdout",
    )

    phase_c_summarize_parser = phase_c_subparsers.add_parser(
        "summarize",
        help="Summarize a completed Phase C run directory",
    )
    phase_c_summarize_parser.add_argument(
        "--run",
        required=True,
        help="Phase C output directory",
    )
    phase_c_summarize_parser.add_argument(
        "--quiet",
        action="store_true",
        help="Do not print summary JSON to stdout",
    )

    report_parser = subparsers.add_parser(
        "report",
        help="Generate publication reports from completed batch outputs",
    )
    report_subparsers = report_parser.add_subparsers(
        dest="report_command",
        required=True,
    )
    report_phase_a_parser = report_subparsers.add_parser(
        "phase-a",
        help="Generate phase_a_report.md from a completed Phase A batch directory",
    )
    report_phase_a_parser.add_argument(
        "output_dir",
        help="Path to completed Phase A batch output (contains summary.csv)",
    )
    report_phase_a_parser.add_argument(
        "--quiet",
        action="store_true",
        help="Write report only; do not print JSON summary to stdout",
    )
    report_phase_b_parser = report_subparsers.add_parser(
        "phase-b",
        help="Generate phase_b_report.md from a completed Phase B batch directory",
    )
    report_phase_b_parser.add_argument(
        "output_dir",
        help="Path to completed Phase B batch output (contains summary.csv)",
    )
    report_phase_b_parser.add_argument(
        "--quiet",
        action="store_true",
        help="Write report only; do not print JSON summary to stdout",
    )
    report_rank_stability_parser = report_subparsers.add_parser(
        "rank-stability",
        help="Compute Earned Rank Stability (ERS) from agent × instance CSV",
    )
    report_rank_stability_parser.add_argument(
        "--agent-results",
        required=True,
        help="CSV with agent, instance_id, y0, ef variants, and failure metadata",
    )
    report_rank_stability_parser.add_argument(
        "--output",
        required=True,
        help="Directory for rank_stability_* artifacts",
    )
    report_rank_stability_parser.add_argument(
        "--bootstrap",
        type=int,
        default=10_000,
        help="Bootstrap resamples over instances for rank-shift and ERS CIs",
    )
    report_rank_stability_parser.add_argument(
        "--quiet",
        action="store_true",
        help="Write artifacts only; do not print JSON summary to stdout",
    )
    report_injection_validity_parser = report_subparsers.add_parser(
        "injection-validity",
        help="Analyze blinded mechanism injection construct validity",
    )
    report_injection_validity_parser.add_argument(
        "--results",
        required=True,
        help="CSV with per-injection harness outcomes",
    )
    report_injection_validity_parser.add_argument(
        "--specs",
        required=True,
        help="Directory of injection spec files (.json, .yaml, .yml)",
    )
    report_injection_validity_parser.add_argument(
        "--output",
        required=True,
        help="Directory for injection_validity_* artifacts",
    )
    report_injection_validity_parser.add_argument(
        "--quiet",
        action="store_true",
        help="Write artifacts only; do not print JSON summary to stdout",
    )

    investigate_parser = subparsers.add_parser(
        "investigate",
        help="Investigate a Phase A batch instance outcome",
    )
    investigate_parser.add_argument(
        "--phase-a-run",
        required=True,
        help="Path to completed Phase A batch output directory",
    )
    investigate_parser.add_argument(
        "--instance-id",
        required=True,
        help="SWE-bench instance id to investigate",
    )
    investigate_parser.add_argument(
        "--metadata-parquet",
        default="",
        help="Optional metadata override for pi_env diagnosis",
    )
    investigate_parser.add_argument(
        "--output",
        default="",
        help="Directory for investigation files (default: <phase-a-run>/<instance-id>)",
    )
    investigate_parser.add_argument(
        "--quiet",
        action="store_true",
        help="Write investigation files only; do not print JSON to stdout",
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
        elif args.command == "exploit":
            if args.exploit_command == "list":
                cmd_exploit_list(args)
            elif args.exploit_command == "show":
                cmd_exploit_show(args)
            elif args.exploit_command == "validate":
                cmd_exploit_validate(args)
            elif args.exploit_command == "validate-runtime":
                cmd_exploit_validate_runtime(args)
            else:
                parser.error(f"unknown exploit command: {args.exploit_command}")
        elif args.command == "injection":
            if args.injection_command == "list":
                cmd_injection_list(args)
            elif args.injection_command == "show":
                cmd_injection_show(args)
            elif args.injection_command == "validate":
                cmd_injection_validate(args)
            elif args.injection_command == "prepare":
                cmd_injection_prepare(args)
            elif args.injection_command == "run":
                cmd_injection_run(args)
            elif args.injection_command == "unblind":
                cmd_injection_unblind(args)
            else:
                parser.error(f"unknown injection command: {args.injection_command}")
        elif args.command == "external-exploit":
            if args.external_exploit_command == "validate-catalog":
                cmd_external_exploit_validate_catalog(args)
            else:
                parser.error(
                    f"unknown external-exploit command: {args.external_exploit_command}"
                )
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
            elif args.swebench_command == "run-pi-vtest":
                cmd_swebench_run_pi_vtest(args)
            elif args.swebench_command == "diagnose-pi-env":
                cmd_swebench_diagnose_pi_env(args)
            else:
                parser.error(f"unknown swebench command: {args.swebench_command}")
        elif args.command == "phase-a":
            if args.phase_a_command == "run":
                cmd_phase_a_run(args)
            elif args.phase_a_command == "schedule":
                cmd_phase_a_schedule(args)
            else:
                parser.error(f"unknown phase-a command: {args.phase_a_command}")
        elif args.command == "phase-b":
            if args.phase_b_command == "run":
                cmd_phase_b_run(args)
            else:
                parser.error(f"unknown phase-b command: {args.phase_b_command}")
        elif args.command == "phase-c":
            if args.phase_c_command == "prepare":
                cmd_phase_c_prepare(args)
            elif args.phase_c_command == "run":
                cmd_phase_c_run(args)
            elif args.phase_c_command == "summarize":
                cmd_phase_c_summarize(args)
            else:
                parser.error(f"unknown phase-c command: {args.phase_c_command}")
        elif args.command == "report":
            if args.report_command == "phase-a":
                cmd_report_phase_a(args)
            elif args.report_command == "phase-b":
                cmd_report_phase_b(args)
            elif args.report_command == "rank-stability":
                cmd_report_rank_stability(args)
            elif args.report_command == "injection-validity":
                cmd_report_injection_validity(args)
            else:
                parser.error(f"unknown report command: {args.report_command}")
        elif args.command == "investigate":
            cmd_investigate(args)
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
