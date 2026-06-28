"""Phase D failure taxonomy and artifact-based diagnostics."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from earnbench.adapters.swebench_pi_env_diagnosis import _LOG_PATTERNS
from earnbench.provenance import utc_timestamp
from earnbench.registry.pi_env_v1 import PI_ENV_V1_ID
from earnbench.registry.pi_verif_v1 import PI_VERIF_V1_ID
from earnbench.registry.pi_vtest_v1 import PI_VTEST_V1_ID

FAILURE_EMPTY_PATCH = "empty_patch"
FAILURE_MALFORMED_PATCH = "malformed_patch"
FAILURE_PATCH_APPLY_FAILED = "patch_apply_failed"
FAILURE_BUILD_FAILED = "build_failed"
FAILURE_NOMINAL_FAILED = "nominal_failed"
FAILURE_PERTURBATION_FAILED = "perturbation_failed"
FAILURE_TIMEOUT = "timeout"
FAILURE_HARNESS_ERROR = "harness_error"

FAILURE_REASONS = (
    FAILURE_EMPTY_PATCH,
    FAILURE_MALFORMED_PATCH,
    FAILURE_PATCH_APPLY_FAILED,
    FAILURE_BUILD_FAILED,
    FAILURE_NOMINAL_FAILED,
    FAILURE_PERTURBATION_FAILED,
    FAILURE_TIMEOUT,
    FAILURE_HARNESS_ERROR,
)

GRADE_STATUS_OK = "ok"
GRADE_STATUS_FAILED = "failed"
GRADE_STATUS_PARTIAL = "partial"

_TIMEOUT_PATTERNS = (
    re.compile(r"timeout", re.IGNORECASE),
    re.compile(r"timed out", re.IGNORECASE),
    re.compile(r"deadline exceeded", re.IGNORECASE),
)

_PATCH_APPLY_PATTERN = _LOG_PATTERNS["patch_apply_failure"]


@dataclass
class StageDiagnostic:
    stage: str
    status: str
    failure_reason: str = ""
    detail: str = ""
    timestamp_utc: str = field(default_factory=utc_timestamp)


@dataclass
class CellDiagnostics:
    grade_status: str = GRADE_STATUS_FAILED
    failure_reason: str = ""
    failure_stage: str = ""
    failure_detail: str = ""
    stages: list[StageDiagnostic] = field(default_factory=list)

    @property
    def pipeline_failed(self) -> bool:
        return self.grade_status != GRADE_STATUS_OK

    def record(
        self,
        stage: str,
        *,
        status: str,
        failure_reason: str = "",
        detail: str = "",
    ) -> None:
        self.stages.append(
            StageDiagnostic(
                stage=stage,
                status=status,
                failure_reason=failure_reason,
                detail=detail,
            )
        )
        if failure_reason and not self.failure_reason:
            self.failure_reason = failure_reason
            self.failure_stage = stage
            self.failure_detail = detail

    def to_dict(self) -> dict[str, Any]:
        return {
            "grade_status": self.grade_status,
            "failure_reason": self.failure_reason,
            "failure_stage": self.failure_stage,
            "failure_detail": self.failure_detail,
            "stages": [
                {
                    "stage": item.stage,
                    "status": item.status,
                    "failure_reason": item.failure_reason,
                    "detail": item.detail,
                    "timestamp_utc": item.timestamp_utc,
                }
                for item in self.stages
            ],
        }


def classify_prepare_error(exc: BaseException) -> tuple[str, str]:
    message = str(exc).lower()
    if "empty" in message:
        return FAILURE_EMPTY_PATCH, str(exc)
    return FAILURE_MALFORMED_PATCH, str(exc)


def classify_validate_patch(
    *,
    patch_path: Path,
    patch_content: str | None = None,
) -> tuple[str, str] | None:
    if not patch_path.is_file():
        return FAILURE_MALFORMED_PATCH, f"patch file not found: {patch_path}"
    content = patch_content if patch_content is not None else patch_path.read_text(
        encoding="utf-8",
    )
    if not content.strip():
        return FAILURE_EMPTY_PATCH, f"patch file is empty: {patch_path}"
    return None


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return None
    return payload


def _read_log_excerpt(path: Path, *, max_chars: int = 500) -> str:
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) <= max_chars:
        return text.strip()
    return text[-max_chars:].strip()


def _log_indicates_timeout(log_text: str) -> bool:
    return any(pattern.search(log_text) for pattern in _TIMEOUT_PATTERNS)


def _log_indicates_patch_apply_failure(log_text: str) -> bool:
    return bool(_PATCH_APPLY_PATTERN.search(log_text))


def classify_preflight_artifact(instance_dir: Path) -> tuple[str, str] | None:
    payload = _read_json(instance_dir / "preflight.json")
    if payload is None:
        return FAILURE_HARNESS_ERROR, "preflight.json missing after preflight stage"
    status = str(payload.get("status", "")).strip().lower()
    if status == "ok":
        return None
    if status == "build_failed":
        detail = str(payload.get("message") or payload.get("error") or status)
        return FAILURE_BUILD_FAILED, detail
    if status == "missing_images":
        missing = payload.get("missing_images") or []
        detail = f"missing_images: {missing}" if missing else status
        return FAILURE_BUILD_FAILED, detail
    return FAILURE_HARNESS_ERROR, f"preflight status={status}"


def classify_grade_artifact(
    *,
    stage: str,
    instance_dir: Path,
    artifact_subdir: str,
) -> tuple[str, str] | None:
    artifact_dir = instance_dir / artifact_subdir
    grade = _read_json(artifact_dir / "grade.json")
    if grade is None:
        return FAILURE_HARNESS_ERROR, f"{artifact_subdir}/grade.json missing"

    status = str(grade.get("status", "")).strip().lower()
    message = str(grade.get("message") or "")
    log_path = artifact_dir / "harness.log"
    log_text = _read_log_excerpt(log_path)

    if status == "error":
        if _log_indicates_timeout(log_text) or _log_indicates_timeout(message):
            return FAILURE_TIMEOUT, message or _read_log_excerpt(log_path, max_chars=200)
        if _log_indicates_patch_apply_failure(log_text) or _log_indicates_patch_apply_failure(
            message,
        ):
            return FAILURE_PATCH_APPLY_FAILED, message or "patch apply failed in harness log"
        return FAILURE_HARNESS_ERROR, message or _read_log_excerpt(log_path, max_chars=200)

    if status == "ok":
        success = grade.get("success")
        if success is False and stage == "nominal":
            detail = message or "nominal harness completed with success=false"
            return FAILURE_NOMINAL_FAILED, detail
        return None

    if status == "invalid":
        detail = str(grade.get("failure_category") or message or status)
        return FAILURE_HARNESS_ERROR, detail

    if status == "missing":
        return FAILURE_HARNESS_ERROR, message or f"{artifact_subdir} grade status=missing"

    return FAILURE_HARNESS_ERROR, message or f"{artifact_subdir} grade status={status}"


def classify_stage_exception(
    stage: str,
    exc: BaseException,
) -> tuple[str, str]:
    if stage == "prepare":
        return classify_prepare_error(exc)
    if stage == "preflight" and isinstance(exc, RuntimeError):
        message = str(exc)
        if "preflight failed" in message.lower() and "build_failed" in message.lower():
            return FAILURE_BUILD_FAILED, message
    if stage in {PI_VTEST_V1_ID, PI_VERIF_V1_ID, PI_ENV_V1_ID}:
        return FAILURE_PERTURBATION_FAILED, str(exc)
    if stage == "nominal":
        message = str(exc).lower()
        if "timeout" in message or "timed out" in message:
            return FAILURE_TIMEOUT, str(exc)
        if "patch" in message and ("apply" in message or "empty" in message):
            return FAILURE_PATCH_APPLY_FAILED, str(exc)
        return FAILURE_HARNESS_ERROR, str(exc)
    return FAILURE_HARNESS_ERROR, str(exc)


def classify_post_stage(
    *,
    stage: str,
    instance_dir: Path,
) -> tuple[str, str] | None:
    if stage == "preflight":
        return classify_preflight_artifact(instance_dir)
    if stage == "nominal":
        return classify_grade_artifact(
            stage=stage,
            instance_dir=instance_dir,
            artifact_subdir="nominal",
        )
    if stage in {PI_VTEST_V1_ID, PI_VERIF_V1_ID, PI_ENV_V1_ID}:
        classified = classify_grade_artifact(
            stage=stage,
            instance_dir=instance_dir,
            artifact_subdir=stage,
        )
        if classified is None:
            return None
        reason, detail = classified
        if reason == FAILURE_NOMINAL_FAILED:
            return FAILURE_PERTURBATION_FAILED, detail
        if reason in {FAILURE_HARNESS_ERROR, FAILURE_TIMEOUT, FAILURE_PATCH_APPLY_FAILED}:
            return FAILURE_PERTURBATION_FAILED, detail
        return classified
    return None


def finalize_cell_diagnostics(
    diagnostics: CellDiagnostics,
    *,
    aggregated: bool,
    y0: bool | None,
) -> None:
    if not aggregated:
        diagnostics.grade_status = GRADE_STATUS_FAILED
        return

    pipeline_errors = [
        item
        for item in diagnostics.stages
        if item.failure_reason
        and item.failure_reason != FAILURE_NOMINAL_FAILED
    ]
    if pipeline_errors:
        diagnostics.grade_status = GRADE_STATUS_PARTIAL
        return

    if y0 is False and not diagnostics.failure_reason:
        diagnostics.failure_reason = FAILURE_NOMINAL_FAILED
        diagnostics.failure_stage = "nominal"
        if not diagnostics.failure_detail:
            diagnostics.failure_detail = "nominal harness completed with success=false"

    diagnostics.grade_status = GRADE_STATUS_OK


def failure_record(
    *,
    agent: str,
    instance_id: str,
    replicate: int,
    stage: str,
    failure_reason: str,
    error: str,
    failure_detail: str = "",
) -> dict[str, str]:
    detail = failure_detail or error
    return {
        "agent": agent,
        "instance_id": instance_id,
        "replicate": str(replicate),
        "stage": stage,
        "failure_reason": failure_reason,
        "error": error,
        "failure_detail": detail,
        "timestamp_utc": utc_timestamp(),
    }


def write_cell_diagnosis(instance_dir: Path, diagnostics: CellDiagnostics) -> Path:
    path = instance_dir / "phase_d_diagnosis.json"
    path.write_text(
        json.dumps(diagnostics.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def summarize_failure_reasons(rows: dict[str, dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {reason: 0 for reason in FAILURE_REASONS}
    counts[""] = 0
    for row in rows.values():
        reason = str(row.get("failure_reason", "") or "").strip()
        if reason not in counts:
            counts[reason] = 0
        counts[reason] += 1
    return {key: value for key, value in sorted(counts.items()) if value > 0}


__all__ = [
    "CellDiagnostics",
    "FAILURE_REASONS",
    "GRADE_STATUS_FAILED",
    "GRADE_STATUS_OK",
    "GRADE_STATUS_PARTIAL",
    "StageDiagnostic",
    "classify_grade_artifact",
    "classify_post_stage",
    "classify_preflight_artifact",
    "classify_prepare_error",
    "classify_stage_exception",
    "classify_validate_patch",
    "failure_record",
    "finalize_cell_diagnostics",
    "summarize_failure_reasons",
    "write_cell_diagnosis",
]
