"""EarnBench command-line interface."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from earnbench.audit import AuditRecord
from earnbench.metrics import compute_earned_fraction
from earnbench.outcomes import NominalOutcome, OutcomeStatus, PerturbationResult


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


def cmd_compute(args: argparse.Namespace) -> None:
    """Compute Earned Fraction from a JSON outcomes file."""
    data = _load_json_file(Path(args.input))
    if not isinstance(data, dict):
        raise CLIError("compute input must be a JSON object")

    nominal, perturbations = parse_compute_input(data)
    report = compute_earned_fraction(nominal, perturbations)
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
