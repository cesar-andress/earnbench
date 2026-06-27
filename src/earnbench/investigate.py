"""Investigate Phase A batch outcomes for a single instance."""

from __future__ import annotations

import ast
import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from earnbench.adapters.swebench_pi_env_diagnosis import (
    diagnose_pi_env,
    render_pi_env_diagnosis_markdown,
    resolve_pi_env_artifact_dirs,
)
from earnbench.phase_a_batch import RUN_MANIFEST_JSON, SUMMARY_CSV
from earnbench.registry.pi_env_v1 import PI_ENV_V1_ID
from earnbench.registry.pi_verif_v1 import PI_VERIF_V1_ID
from earnbench.registry.pi_vtest_v1 import PI_VTEST_V1_ID

INVESTIGATION_JSON = "investigation.json"
INVESTIGATION_MD = "investigation.md"

STAGE_SPECS: tuple[tuple[str, str | None], ...] = (
    ("nominal", None),
    (PI_VERIF_V1_ID, PI_VERIF_V1_ID),
    (PI_VTEST_V1_ID, PI_VTEST_V1_ID),
    (PI_ENV_V1_ID, PI_ENV_V1_ID),
)


@dataclass(frozen=True, slots=True)
class InvestigationResult:
    phase_a_run: Path
    instance_id: str
    instance_dir: Path
    investigation_json: Path
    investigation_md: Path


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        msg = f"{path} must contain a JSON object"
        raise ValueError(msg)
    return payload


def _load_summary_row(phase_a_run: Path, instance_id: str) -> dict[str, str]:
    summary_path = phase_a_run / SUMMARY_CSV
    if not summary_path.is_file():
        msg = f"missing required artifact: {summary_path}"
        raise FileNotFoundError(msg)
    with summary_path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("instance_id") == instance_id:
                return dict(row)
    msg = f"instance_id not found in {summary_path}: {instance_id}"
    raise ValueError(msg)


def _resolve_metadata_path(
    phase_a_run: Path,
    instance_dir: Path,
    metadata_path: Path | None,
) -> Path | None:
    if metadata_path is not None:
        resolved = metadata_path.resolve()
        if not resolved.is_file():
            msg = f"metadata file not found: {resolved}"
            raise FileNotFoundError(msg)
        return resolved
    meta_path = instance_dir / "meta.json"
    if meta_path.is_file():
        meta = _load_json(meta_path)
        source = str(meta.get("metadata_source") or "").strip()
        if source:
            candidate = Path(source)
            if candidate.is_file():
                return candidate.resolve()
    manifest_path = phase_a_run / RUN_MANIFEST_JSON
    if manifest_path.is_file():
        manifest = _load_json(manifest_path)
        source = str(manifest.get("metadata_path") or "").strip()
        if source:
            candidate = Path(source)
            if candidate.is_file():
                return candidate.resolve()
    return None


def _parse_embedded_harness_report(
    log_text: str,
    instance_id: str,
) -> dict[str, Any] | None:
    prefix = "report:"
    for line in log_text.splitlines():
        stripped = line.strip()
        if not stripped.startswith(prefix):
            continue
        payload_text = stripped[len(prefix) :].strip()
        try:
            payload = ast.literal_eval(payload_text)
        except (SyntaxError, ValueError):
            continue
        if isinstance(payload, dict) and instance_id in payload:
            return payload
    marker = "===== report.json ====="
    if marker in log_text:
        section = log_text.split(marker, maxsplit=1)[1]
        try:
            payload = json.loads(section)
        except json.JSONDecodeError:
            return None
        if isinstance(payload, dict) and instance_id in payload:
            return payload
    return None


def _harness_bucket_summary(
    report: dict[str, Any] | None,
    instance_id: str,
) -> dict[str, Any] | None:
    if not report or instance_id not in report:
        return None
    entry = report[instance_id]
    tests_status = entry.get("tests_status")
    if not isinstance(tests_status, dict):
        return None
    summary: dict[str, Any] = {
        "resolved": entry.get("resolved"),
        "patch_successfully_applied": entry.get("patch_successfully_applied"),
    }
    for bucket in ("FAIL_TO_PASS", "PASS_TO_PASS", "FAIL_TO_FAIL", "PASS_TO_FAIL"):
        bucket_payload = tests_status.get(bucket)
        if isinstance(bucket_payload, dict):
            summary[bucket] = {
                "success": list(bucket_payload.get("success") or []),
                "failure": list(bucket_payload.get("failure") or []),
            }
    return summary


def _log_excerpt(log_path: Path, *, max_lines: int = 12) -> str | None:
    if not log_path.is_file():
        return None
    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    if not lines:
        return None
    tail = lines[-max_lines:]
    return "\n".join(tail)


def _infer_failure_mode(
    *,
    stage: str,
    grade: dict[str, Any] | None,
    harness_summary: dict[str, Any] | None,
) -> str | None:
    if grade is None:
        return "missing_grade"
    status = str(grade.get("status") or "")
    outcome = str(grade.get("outcome") or status)
    success = grade.get("success")

    if status == "invalid" or outcome == "invalid":
        category = grade.get("failure_category")
        if category:
            return str(category)
        return "pi_invalid"

    if status == "error" or outcome == "error":
        return "harness_error"

    if success is True:
        return None

    if harness_summary:
        f2p = harness_summary.get("FAIL_TO_PASS") or {}
        p2p = harness_summary.get("PASS_TO_PASS") or {}
        p2f = harness_summary.get("PASS_TO_FAIL") or {}
        f2p_failures = list(f2p.get("failure") or [])
        p2p_failures = list(p2p.get("failure") or [])
        p2f_failures = list(p2f.get("failure") or [])
        if p2f_failures:
            return "pass_to_fail_regression"
        if p2p_failures and not f2p_failures:
            return "pass_to_pass_regression"
        if f2p_failures and stage == PI_VTEST_V1_ID:
            holdout = set(grade.get("holdout_f2p") or [])
            if holdout.intersection(f2p_failures):
                return "holdout_false_unearned"
            return "visible_or_f2p_failure"
        if f2p_failures:
            return "fail_to_pass_failure"
        if p2p_failures:
            return "pass_to_pass_regression"

    if stage == PI_VTEST_V1_ID:
        return "holdout_false_unearned"
    if stage == PI_VERIF_V1_ID:
        return "verif_false_unearned"
    if stage == PI_ENV_V1_ID:
        return "env_false_unearned"
    if stage == "nominal":
        return "nominal_failed"
    return "unknown_failure"


def _suggest_exclude_reason(
    summary_row: dict[str, str],
    stage_diagnoses: list[dict[str, Any]],
) -> dict[str, Any]:
    exclude_reason = str(summary_row.get("exclude_reason") or "").strip()
    retained = str(summary_row.get("retained", "")).strip().lower() in {
        "1",
        "true",
        "yes",
    }
    failure_layers: list[str] = []
    for stage in stage_diagnoses:
        if stage.get("failure_mode"):
            failure_layers.append(str(stage["stage"]))

    suggested_code = exclude_reason or None
    if not retained and not suggested_code:
        for stage in stage_diagnoses:
            mode = str(stage.get("failure_mode") or "")
            if mode == "nominal_failed":
                suggested_code = "nominal_failed"
                break
            if mode == "holdout_false_unearned":
                suggested_code = "holdout_false_unearned"
                break
            if mode == "verif_false_unearned":
                suggested_code = "verif_false_unearned"
                break
            if mode == "env_false_unearned":
                suggested_code = "env_false_unearned"
                break
            if mode == "pi_invalid" or mode.endswith("_blocked"):
                suggested_code = "pi_invalid"
                break

    return {
        "retained": retained,
        "exclude_reason_observed": exclude_reason or None,
        "exclude_reason_suggested": suggested_code,
        "failure_layers": failure_layers,
        "recoverable": "unknown",
    }


def _recommended_actions(
    summary_row: dict[str, str],
    stage_diagnoses: list[dict[str, Any]],
    pi_env_diagnosis: dict[str, Any] | None,
) -> list[str]:
    actions: list[str] = []
    if str(summary_row.get("retained", "")).strip().lower() in {"1", "true", "yes"}:
        actions.append(
            "Instance retained for frozen pilot set (EF@Π = 1 under primary estimand)."
        )
        return actions

    for stage in stage_diagnoses:
        mode = str(stage.get("failure_mode") or "")
        stage_name = str(stage["stage"])
        if mode == "holdout_false_unearned":
            actions.append(
                f"Review holdout partition and golden patch on {stage_name}; "
                "record as holdout_false_unearned in confound_register.csv."
            )
        elif mode == "pass_to_pass_regression":
            p2p = (
                (stage.get("harness_summary") or {})
                .get("PASS_TO_PASS", {})
                .get("failure", [])
            )
            tests = ", ".join(p2p[:3]) if p2p else "unknown P2P tests"
            actions.append(
                f"{stage_name} shows PASS_TO_PASS regression ({tests}); "
                "inspect harness flakiness or prod-only strip before excluding."
            )
        elif mode == "pi_invalid" or "blocked" in mode:
            actions.append(
                f"{stage_name} is INVALID (non-measurement); exclude from EF denominator "
                "and report dual EF sensitivity if rate is non-trivial."
            )
        elif mode == "verif_false_unearned":
            actions.append(
                f"Golden patch failed {stage_name}; inspect tamper flags and pristine reset."
            )
        elif mode == "nominal_failed":
            actions.append(
                "Nominal Y₀ failed; EF@Π undefined. Verify patch apply and dataset revision."
            )

    if pi_env_diagnosis:
        actions.append(str(pi_env_diagnosis.get("recommended_action") or "").strip())

    if not actions:
        actions.append(
            "Review stage tables and harness logs; update confound_register.csv manually."
        )
    return [item for item in actions if item]


def _diagnose_stage(
    instance_dir: Path,
    instance_id: str,
    stage_dir_name: str,
    perturbation_id: str | None,
) -> dict[str, Any]:
    stage_dir = instance_dir / stage_dir_name
    grade_path = stage_dir / "grade.json"
    audit_path = stage_dir / "audit.json"
    harness_path = stage_dir / "harness.log"

    grade = _load_json(grade_path) if grade_path.is_file() else None
    audit = _load_json(audit_path) if audit_path.is_file() else None
    harness_summary = None
    if harness_path.is_file():
        report = _parse_embedded_harness_report(
            harness_path.read_text(encoding="utf-8", errors="replace"),
            instance_id,
        )
        harness_summary = _harness_bucket_summary(report, instance_id)

    failure_mode = _infer_failure_mode(
        stage=stage_dir_name if stage_dir_name != "nominal" else "nominal",
        grade=grade,
        harness_summary=harness_summary,
    )
    return {
        "stage": stage_dir_name,
        "perturbation_id": perturbation_id or "nominal.v1",
        "artifact_dir": str(stage_dir),
        "grade_path": str(grade_path) if grade_path.is_file() else None,
        "audit_path": str(audit_path) if audit_path.is_file() else None,
        "harness_log_path": str(harness_path) if harness_path.is_file() else None,
        "status": (grade or audit or {}).get("status"),
        "outcome": (grade or audit or {}).get("outcome"),
        "success": (grade or {}).get("success"),
        "failure_mode": failure_mode,
        "harness_summary": harness_summary,
        "holdout_f2p": list((grade or {}).get("holdout_f2p") or []),
        "visible_f2p": list((grade or {}).get("visible_f2p") or []),
        "failure_category": (grade or {}).get("failure_category"),
        "tamper_detected": (grade or {}).get("tamper_detected"),
        "log_excerpt": _log_excerpt(harness_path),
    }


def build_phase_a_investigation(
    *,
    phase_a_run: Path,
    instance_id: str,
    metadata_path: Path | None = None,
) -> dict[str, Any]:
    """Assemble a structured investigation payload for one Phase A instance."""
    phase_a_run = phase_a_run.resolve()
    instance_dir = phase_a_run / instance_id
    if not instance_dir.is_dir():
        msg = f"instance directory not found: {instance_dir}"
        raise FileNotFoundError(msg)

    summary_row = _load_summary_row(phase_a_run, instance_id)
    report_path = instance_dir / "report.json"
    meta_path = instance_dir / "meta.json"
    report = _load_json(report_path) if report_path.is_file() else {}
    meta = _load_json(meta_path) if meta_path.is_file() else {}

    stage_diagnoses = [
        _diagnose_stage(instance_dir, instance_id, stage_name, perturbation_id)
        for stage_name, perturbation_id in STAGE_SPECS
    ]

    resolved_metadata = _resolve_metadata_path(
        phase_a_run,
        instance_dir,
        metadata_path,
    )
    pi_env_diagnosis: dict[str, Any] | None = None
    patch_path = instance_dir / "patch" / "prod_only.patch"
    nominal_dir = instance_dir / "nominal"
    pi_env_dir = instance_dir / PI_ENV_V1_ID
    if (
        resolved_metadata is not None
        and patch_path.is_file()
        and nominal_dir.is_dir()
        and pi_env_dir.is_dir()
    ):
        try:
            resolved_nominal, resolved_pi_env = resolve_pi_env_artifact_dirs(
                instance_id=instance_id,
                nominal_dir=nominal_dir,
                pi_env_dir=pi_env_dir,
            )
            pi_env_diagnosis = diagnose_pi_env(
                metadata_path=resolved_metadata,
                instance_id=instance_id,
                patch_path=patch_path,
                nominal_dir=resolved_nominal,
                pi_env_dir=resolved_pi_env,
            )
        except (FileNotFoundError, ValueError):
            pi_env_diagnosis = None

    confound = _suggest_exclude_reason(summary_row, stage_diagnoses)
    actions = _recommended_actions(summary_row, stage_diagnoses, pi_env_diagnosis)

    return {
        "instance_id": instance_id,
        "repo": summary_row.get("repo") or meta.get("repo"),
        "phase_a_run": str(phase_a_run),
        "run_id": summary_row.get("run_id") or meta.get("run_id"),
        "config_digest": summary_row.get("config_digest") or meta.get("config_digest"),
        "summary": summary_row,
        "earned_fraction_report": report,
        "meta": {
            "base_commit": meta.get("base_commit"),
            "prod_patch_sha256": meta.get("prod_patch_sha256"),
            "stripped_paths": meta.get("stripped_paths"),
            "prod_paths": meta.get("prod_paths"),
        },
        "stages": stage_diagnoses,
        "confound_register_suggestion": confound,
        "pi_env_diagnosis": pi_env_diagnosis,
        "recommended_actions": actions,
        "artifact_paths": {
            "instance_dir": str(instance_dir),
            "report_json": str(report_path) if report_path.is_file() else None,
            "batch_log": str(instance_dir / "batch.log")
            if (instance_dir / "batch.log").is_file()
            else None,
            "prod_patch": str(patch_path) if patch_path.is_file() else None,
        },
    }


def _markdown_table(headers: tuple[str, ...], rows: list[tuple[str, ...]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(lines)


def render_phase_a_investigation(payload: dict[str, Any]) -> str:
    """Render human-readable markdown for an investigation payload."""
    summary = payload.get("summary") or {}
    confound = payload.get("confound_register_suggestion") or {}
    stages = payload.get("stages") or []

    stage_rows: list[tuple[str, ...]] = []
    for stage in stages:
        harness = stage.get("harness_summary") or {}
        f2p_fail = ", ".join((harness.get("FAIL_TO_PASS") or {}).get("failure") or [])
        p2p_fail = ", ".join((harness.get("PASS_TO_PASS") or {}).get("failure") or [])
        stage_rows.append(
            (
                str(stage.get("stage")),
                str(stage.get("status") or "—"),
                str(stage.get("outcome") or "—"),
                str(stage.get("success")),
                str(stage.get("failure_mode") or "—"),
                f2p_fail or "—",
                p2p_fail or "—",
            )
        )

    lines = [
        f"# Phase A investigation — `{payload.get('instance_id')}`",
        "",
        "## Summary",
        "",
        f"- **Repo:** `{payload.get('repo')}`",
        f"- **Run ID:** `{payload.get('run_id')}`",
        f"- **Retained:** `{confound.get('retained')}`",
        f"- **Exclude reason (observed):** `{confound.get('exclude_reason_observed')}`",
        f"- **Exclude reason (suggested):** `{confound.get('exclude_reason_suggested')}`",
        f"- **EF@Π:** `{summary.get('ef_pi')}` (`{summary.get('ef_status')}`)",
        f"- **False unearned:** `{summary.get('false_unearned')}`",
        "",
        "## Recommended actions",
        "",
    ]
    for action in payload.get("recommended_actions") or []:
        lines.append(f"- {action}")
    lines.extend(
        [
            "",
            "## Stage diagnosis",
            "",
            _markdown_table(
                (
                    "Stage",
                    "Status",
                    "Outcome",
                    "Success",
                    "Failure mode",
                    "F2P failures",
                    "P2P failures",
                ),
                stage_rows,
            ),
            "",
        ]
    )

    for stage in stages:
        if not stage.get("failure_mode"):
            continue
        excerpt = stage.get("log_excerpt")
        if not excerpt:
            continue
        lines.extend(
            [
                f"### Log excerpt — `{stage.get('stage')}`",
                "",
                "```",
                excerpt,
                "```",
                "",
            ]
        )

    pi_env = payload.get("pi_env_diagnosis")
    if pi_env:
        lines.extend(
            [
                "## pi_env.v1 diagnosis",
                "",
                render_pi_env_diagnosis_markdown(pi_env),
            ]
        )

    lines.extend(
        [
            "## Confound register suggestion",
            "",
            _markdown_table(
                ("Field", "Value"),
                [
                    ("instance_id", str(payload.get("instance_id"))),
                    ("repo", str(payload.get("repo") or "")),
                    ("exclude_reason", str(confound.get("exclude_reason_suggested") or "")),
                    (
                        "failure_layer",
                        ", ".join(confound.get("failure_layers") or []) or "—",
                    ),
                    ("recoverable", str(confound.get("recoverable") or "unknown")),
                ],
            ),
            "",
        ]
    )
    return "\n".join(lines)


def write_phase_a_investigation(
    *,
    phase_a_run: Path,
    instance_id: str,
    metadata_path: Path | None = None,
    output_dir: Path | None = None,
) -> InvestigationResult:
    """Investigate one Phase A instance and write JSON + markdown reports."""
    payload = build_phase_a_investigation(
        phase_a_run=phase_a_run,
        instance_id=instance_id,
        metadata_path=metadata_path,
    )
    target_dir = (output_dir or (phase_a_run / instance_id)).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    json_path = target_dir / INVESTIGATION_JSON
    md_path = target_dir / INVESTIGATION_MD
    json_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(render_phase_a_investigation(payload), encoding="utf-8")
    return InvestigationResult(
        phase_a_run=phase_a_run.resolve(),
        instance_id=instance_id,
        instance_dir=target_dir,
        investigation_json=json_path,
        investigation_md=md_path,
    )


__all__ = [
    "INVESTIGATION_JSON",
    "INVESTIGATION_MD",
    "InvestigationResult",
    "build_phase_a_investigation",
    "render_phase_a_investigation",
    "write_phase_a_investigation",
]
