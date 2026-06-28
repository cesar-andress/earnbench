"""Compare nominal and ``pi_env.v1`` SWE-bench artifacts to diagnose failures."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from earnbench.adapters.swebench_metadata import (
    SWEBenchVerifiedRecord,
    load_verified_instance,
)
from earnbench.adapters.swebench_patch import sha256_hex
from earnbench.audit import AuditRecord
from earnbench.classification import (
    PI_ENV_HARDENING_INVALID_CATEGORIES,
    PerturbationOutcome,
    classify_from_diagnosis,
    classify_from_executor_record,
    classify_pi_env_measurement,
)
from earnbench.registry.pi_env_v1 import PI_ENV_V1_ID

FAILURE_CATEGORIES = (
    "harness_difference",
    "dependency_blocked_by_pip_no_index",
    "python_nousersite_changed_runtime",
    "network_blocked_required_test",
    "readonly_not_enforced",
    "patch_application_difference",
    "test_selection_difference",
    "flaky_test",
    "unknown",
)

HARDENING_INVALID_CATEGORIES = PI_ENV_HARDENING_INVALID_CATEGORIES

_LOG_PATTERNS: dict[str, re.Pattern[str]] = {
    "pip_no_index": re.compile(
        r"PIP_NO_INDEX|No matching distribution|Could not find a version|"
        r"pip install.*(?:failed|error)|ERROR: Could not find",
        re.IGNORECASE,
    ),
    "python_nousersite": re.compile(
        r"PYTHONNOUSERSITE|user site-packages|\.local/lib/python|"
        r"ImportError:.*site-packages",
        re.IGNORECASE,
    ),
    "network_failure": re.compile(
        r"Network is unreachable|Temporary failure in name resolution|"
        r"Connection refused|Connection timed out|Could not resolve host|"
        r"socket\.gaierror|gaierror:|"
        r"curl:.*(?:failed|error)|wget:.*(?:failed|error)|"
        r"requests\.exceptions\.ConnectionError|No route to host",
        re.IGNORECASE,
    ),
    "http_external_test_failure": re.compile(
        r"failed for scheme HTTP://|failed for scheme https://|"
        r"assert 502 == 200|httpbin\(",
        re.IGNORECASE,
    ),
    "missing_dependency": re.compile(
        r"ModuleNotFoundError|ImportError: No module named|"
        r"command not found: pip|PackageNotFoundError",
        re.IGNORECASE,
    ),
    "patch_apply_failure": re.compile(
        r"APPLY_PATCH_FAIL|Failed to apply patch|patch does not apply|"
        r"git apply.*error|Rejected hunk",
        re.IGNORECASE,
    ),
    "patch_apply_success": re.compile(
        r"APPLY_PATCH_PASS|applied patch cleanly",
        re.IGNORECASE,
    ),
    "test_failure": re.compile(
        r"FAILED \(|ERROR collecting|AssertionError|pytest.*failed",
        re.IGNORECASE,
    ),
    "flaky_hint": re.compile(
        r"flaky|intermittent|random failure|Rerun",
        re.IGNORECASE,
    ),
}


def resolve_artifact_dir(
    path: Path,
    instance_id: str,
    artifact_subdir: str,
    *,
    label: str,
) -> Path:
    """Accept either an artifact dir or a batch output root."""
    direct_grade = path / "grade.json"
    if direct_grade.is_file():
        return path
    nested = path / instance_id / artifact_subdir
    nested_grade = nested / "grade.json"
    if nested_grade.is_file():
        return nested
    msg = f"{label} grade.json not found. Tried:\n  {direct_grade}\n  {nested_grade}"
    raise FileNotFoundError(msg)


def resolve_pi_env_artifact_dirs(
    *,
    instance_id: str,
    nominal_dir: Path,
    pi_env_dir: Path,
) -> tuple[Path, Path]:
    """Resolve nominal and pi_env artifact directories from common layouts."""
    return (
        resolve_artifact_dir(
            nominal_dir,
            instance_id,
            "nominal",
            label="nominal",
        ),
        resolve_artifact_dir(
            pi_env_dir,
            instance_id,
            "pi_env.v1",
            label="pi_env",
        ),
    )


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        msg = f"expected JSON object in {path}"
        raise ValueError(msg)
    return payload


def _read_text(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _grade_success(grade: dict[str, Any]) -> bool:
    return bool(grade.get("success"))


def _grade_status(grade: dict[str, Any]) -> str:
    return str(grade.get("status", ""))


def _matching_lines(
    log_text: str, pattern: re.Pattern[str], *, limit: int = 8
) -> list[str]:
    hits: list[str] = []
    for line in log_text.splitlines():
        if pattern.search(line):
            hits.append(line.rstrip())
            if len(hits) >= limit:
                break
    return hits


def _log_excerpt(log_text: str, *, max_lines: int = 40) -> str:
    lines = [line.rstrip() for line in log_text.splitlines() if line.strip()]
    if not lines:
        return ""
    if len(lines) <= max_lines:
        return "\n".join(lines)
    head = lines[: max_lines // 2]
    tail = lines[-(max_lines // 2) :]
    return "\n".join([*head, "... [truncated] ...", *tail])


def _focused_excerpt(log_text: str, patterns: tuple[re.Pattern[str], ...]) -> str:
    hits: list[str] = []
    for line in log_text.splitlines():
        if any(pattern.search(line) for pattern in patterns):
            hits.append(line.rstrip())
    if hits:
        return "\n".join(hits[:40])
    return _log_excerpt(log_text)


def _differing_fields(
    left: dict[str, Any],
    right: dict[str, Any],
    *,
    prefix: str = "",
) -> list[str]:
    keys = sorted(set(left) | set(right))
    diffs: list[str] = []
    for key in keys:
        path = f"{prefix}.{key}" if prefix else key
        left_val = left.get(key)
        right_val = right.get(key)
        if left_val != right_val:
            diffs.append(path)
    return diffs


def _analyze_log_signals(log_text: str) -> dict[str, list[str]]:
    return {
        name: _matching_lines(log_text, pattern)
        for name, pattern in _LOG_PATTERNS.items()
    }


def _parse_embedded_harness_report(
    log_text: str,
    instance_id: str,
) -> dict[str, Any] | None:
    """Parse the SWE-bench ``report: {...}`` line embedded in harness logs."""
    import ast

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
    report_marker = "===== report.json ====="
    if report_marker in log_text:
        section = log_text.split(report_marker, maxsplit=1)[1]
        try:
            payload = json.loads(section)
        except json.JSONDecodeError:
            return None
        if isinstance(payload, dict) and instance_id in payload:
            return payload
    return None


def _harness_test_bucket_summary(
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
    for bucket_name in ("FAIL_TO_PASS", "PASS_TO_PASS", "FAIL_TO_FAIL", "PASS_TO_FAIL"):
        bucket = tests_status.get(bucket_name)
        if isinstance(bucket, dict):
            summary[bucket_name] = {
                "success": list(bucket.get("success") or []),
                "failure": list(bucket.get("failure") or []),
            }
    return summary


def _effective_hardening_enforced(
    docker_env: dict[str, Any],
    pi_env_grade: dict[str, Any],
) -> tuple[list[str], bool]:
    """Return enforced flags, inferring docker flags from requested when unrecorded."""
    enforced = list(docker_env.get("pi_env_hardening_flags_enforced") or [])
    if enforced:
        return enforced, False
    requested = list(
        docker_env.get("pi_env_hardening_flags_requested")
        or pi_env_grade.get("hardening_flags_requested")
        or []
    )
    inferrable = (
        "network_disabled",
        "python_nousersite",
        "pip_no_index",
    )
    inferred = [flag for flag in inferrable if flag in requested]
    return inferred, bool(inferred)


def _pi_env_pass_to_pass_only_failure(
    report_summary: dict[str, Any] | None,
) -> tuple[bool, list[str]]:
    if not report_summary:
        return False, []
    f2p = report_summary.get("FAIL_TO_PASS") or {}
    p2p = report_summary.get("PASS_TO_PASS") or {}
    f2p_failures = list(f2p.get("failure") or [])
    p2p_failures = list(p2p.get("failure") or [])
    return not f2p_failures and bool(p2p_failures), p2p_failures


def _compare_test_lists(
    record: SWEBenchVerifiedRecord,
    nominal_grade: dict[str, Any],
    pi_env_audit: AuditRecord | None,
    nominal_audit: AuditRecord | None,
) -> dict[str, Any]:
    expected_f2p = list(record.fail_to_pass)
    expected_p2p = list(record.pass_to_pass)
    nominal_f2p = nominal_grade.get("fail_to_pass")
    nominal_p2p = nominal_grade.get("pass_to_pass")
    pi_tests = list(pi_env_audit.tests_run) if pi_env_audit else []
    nominal_tests = list(nominal_audit.tests_run) if nominal_audit else []
    return {
        "expected_fail_to_pass": expected_f2p,
        "expected_pass_to_pass": expected_p2p,
        "nominal_fail_to_pass": nominal_f2p,
        "nominal_pass_to_pass": nominal_p2p,
        "nominal_tests_run": nominal_tests,
        "pi_env_tests_run": pi_tests,
        "fail_to_pass_match_metadata": (
            list(nominal_f2p or []) == expected_f2p if nominal_f2p is not None else None
        ),
        "pass_to_pass_match_metadata": (
            list(nominal_p2p or []) == expected_p2p if nominal_p2p is not None else None
        ),
        "tests_run_match": sorted(nominal_tests) == sorted(pi_tests),
    }


def _compare_patch(
    patch_path: Path,
    nominal_audit: AuditRecord | None,
    pi_env_audit: AuditRecord | None,
    nominal_log: str,
    pi_env_log: str,
) -> dict[str, Any]:
    patch_sha256 = sha256_hex(_read_text(patch_path)) if patch_path.is_file() else None
    nominal_patch_sha = nominal_audit.patch_sha256 if nominal_audit else None
    pi_env_patch_sha = pi_env_audit.patch_sha256 if pi_env_audit else None
    nominal_apply_fail = bool(_LOG_PATTERNS["patch_apply_failure"].search(nominal_log))
    pi_env_apply_fail = bool(_LOG_PATTERNS["patch_apply_failure"].search(pi_env_log))
    nominal_apply_ok = bool(_LOG_PATTERNS["patch_apply_success"].search(nominal_log))
    pi_env_apply_ok = bool(_LOG_PATTERNS["patch_apply_success"].search(pi_env_log))
    audits_match = nominal_patch_sha == pi_env_patch_sha
    file_matches_audits = None
    if patch_sha256 and nominal_patch_sha and pi_env_patch_sha:
        file_matches_audits = patch_sha256 == nominal_patch_sha == pi_env_patch_sha
    return {
        "patch_file_sha256": patch_sha256,
        "nominal_audit_patch_sha256": nominal_patch_sha,
        "pi_env_audit_patch_sha256": pi_env_patch_sha,
        "patch_sha256_match": audits_match,
        "patch_file_matches_audits": file_matches_audits,
        "nominal_patch_apply_failed": nominal_apply_fail,
        "pi_env_patch_apply_failed": pi_env_apply_fail,
        "nominal_patch_apply_succeeded": nominal_apply_ok,
        "pi_env_patch_apply_succeeded": pi_env_apply_ok,
    }


def _compare_docker_and_env(
    nominal_grade: dict[str, Any],
    pi_env_grade: dict[str, Any],
    nominal_audit: AuditRecord | None,
    pi_env_audit: AuditRecord | None,
    nominal_log: str,
    pi_env_log: str,
) -> dict[str, Any]:
    env_var_pattern = re.compile(
        r"(PYTHONNOUSERSITE|PIP_NO_INDEX|network_mode)=?\s*(\S+)?",
        re.IGNORECASE,
    )
    nominal_env_hits = _matching_lines(nominal_log, env_var_pattern)
    pi_env_env_hits = _matching_lines(pi_env_log, env_var_pattern)
    return {
        "nominal_image_digest": (nominal_audit.image_digest if nominal_audit else None),
        "pi_env_image_digest": pi_env_audit.image_digest if pi_env_audit else None,
        "image_digest_match": (
            nominal_audit.image_digest == pi_env_audit.image_digest
            if nominal_audit and pi_env_audit
            else None
        ),
        "nominal_harness_command": nominal_grade.get("harness_command"),
        "pi_env_harness_command": pi_env_grade.get("harness_command"),
        "harness_command_match": nominal_grade.get("harness_command")
        == pi_env_grade.get("harness_command"),
        "pi_env_hardening_flags_requested": pi_env_grade.get(
            "hardening_flags_requested"
        ),
        "pi_env_hardening_flags_enforced": pi_env_grade.get("hardening_flags_enforced"),
        "pi_env_hardening_flags_not_enforced": pi_env_grade.get(
            "hardening_flags_not_enforced"
        ),
        "nominal_log_env_hits": nominal_env_hits,
        "pi_env_log_env_hits": pi_env_env_hits,
    }


def _classify_failure(
    *,
    nominal_success: bool,
    pi_env_success: bool,
    patch_compare: dict[str, Any],
    test_compare: dict[str, Any],
    docker_env: dict[str, Any],
    nominal_signals: dict[str, list[str]],
    pi_env_signals: dict[str, list[str]],
    pi_env_grade: dict[str, Any],
    report_summary: dict[str, Any] | None,
) -> tuple[str, list[str]]:
    evidence: list[str] = []
    enforced, inferred_enforced = _effective_hardening_enforced(
        docker_env, pi_env_grade
    )
    requested = list(
        docker_env.get("pi_env_hardening_flags_requested")
        or pi_env_grade.get("hardening_flags_requested")
        or []
    )
    if inferred_enforced:
        evidence.append(
            "Docker hardening flags inferred from pi_env grade request list "
            f"(enforced field empty): {enforced}"
        )

    if nominal_success and pi_env_success:
        return "unknown", [
            "Both nominal and pi_env report success; no failure to classify."
        ]

    if patch_compare.get("pi_env_patch_apply_failed") and not patch_compare.get(
        "nominal_patch_apply_failed"
    ):
        evidence.append(
            "pi_env harness log shows patch apply failure; nominal does not."
        )
        return "patch_application_difference", evidence

    if patch_compare.get("patch_sha256_match") is False:
        evidence.append("Nominal and pi_env audit patch_sha256 values differ.")
        return "patch_application_difference", evidence

    if test_compare.get("tests_run_match") is False:
        evidence.append("tests_run differ between nominal audit and pi_env audit.")
        return "test_selection_difference", evidence

    if test_compare.get("fail_to_pass_match_metadata") is False:
        evidence.append("Nominal grade fail_to_pass list does not match metadata.")
        return "test_selection_difference", evidence

    p2p_only_failure, p2p_failures = _pi_env_pass_to_pass_only_failure(report_summary)
    if p2p_only_failure:
        evidence.append(
            "Harness report shows FAIL_TO_PASS successes with "
            "PASS_TO_PASS failures only."
        )
        if p2p_failures:
            evidence.append(f"PASS_TO_PASS failures: {p2p_failures[:3]}")

    if pi_env_signals["pip_no_index"]:
        f2p_failures = (
            (report_summary or {})
            .get("FAIL_TO_PASS", {})
            .get("failure", [])
        )
        if (
            f2p_failures
            and pi_env_signals["test_failure"]
            and report_summary is not None
            and report_summary.get("resolved") is False
        ):
            evidence.append(
                "pi_env log shows pip index warnings, but harness completed with "
                f"FAIL_TO_PASS failures ({len(f2p_failures)}); treat as plant signal."
            )
        else:
            evidence.extend(
                f"pi_env log: {line}" for line in pi_env_signals["pip_no_index"][:3]
            )
            if "pip_no_index" in enforced:
                evidence.append("PIP_NO_INDEX was enforced for pi_env.v1.")
            return "dependency_blocked_by_pip_no_index", evidence

    if pi_env_signals["missing_dependency"] and (
        "pip_no_index" in enforced or pi_env_signals["pip_no_index"]
    ):
        evidence.extend(
            f"pi_env log: {line}" for line in pi_env_signals["missing_dependency"][:3]
        )
        return "dependency_blocked_by_pip_no_index", evidence

    if pi_env_signals["python_nousersite"]:
        evidence.extend(
            f"pi_env log: {line}" for line in pi_env_signals["python_nousersite"][:3]
        )
        if "python_nousersite" in enforced:
            evidence.append("PYTHONNOUSERSITE=1 was enforced for pi_env.v1.")
        return "python_nousersite_changed_runtime", evidence

    network_signals = (
        pi_env_signals["network_failure"] + pi_env_signals["http_external_test_failure"]
    )
    if network_signals or (
        p2p_only_failure
        and pi_env_signals["test_failure"]
        and not nominal_signals["test_failure"]
    ):
        if network_signals:
            evidence.extend(f"pi_env log: {line}" for line in network_signals[:3])
        if "network_disabled" in enforced:
            evidence.append("network_disabled was enforced for pi_env.v1.")
        elif "network_disabled" in requested:
            evidence.append(
                "network_disabled was requested for pi_env.v1 "
                "(external/http tests failed while nominal passed)."
            )
        return "network_blocked_required_test", evidence

    if pi_env_signals["network_failure"]:
        evidence.extend(
            f"pi_env log: {line}" for line in pi_env_signals["network_failure"][:3]
        )
        if "network_disabled" in enforced:
            evidence.append("network_disabled was enforced for pi_env.v1.")
        return "network_blocked_required_test", evidence

    if docker_env.get("harness_command_match") is False:
        nominal_cmd = docker_env.get("nominal_harness_command")
        pi_env_cmd = docker_env.get("pi_env_harness_command")
        evidence.append(
            f"Harness commands differ: nominal={nominal_cmd!r} pi_env={pi_env_cmd!r}"
        )
        return "harness_difference", evidence

    if pi_env_signals["flaky_hint"]:
        evidence.extend(
            f"pi_env log: {line}" for line in pi_env_signals["flaky_hint"][:3]
        )
        return "flaky_test", evidence

    if pi_env_signals["test_failure"] and not nominal_signals["test_failure"]:
        evidence.append("pi_env log contains test failures absent from nominal log.")
        if enforced:
            evidence.append(f"pi_env hardening enforced: {enforced}")
            return "harness_difference", evidence
        return "unknown", evidence

    not_enforced = docker_env.get("pi_env_hardening_flags_not_enforced") or []
    if "tests_mount_readonly" in not_enforced and pi_env_signals["test_failure"]:
        evidence.append(
            "tests_mount_readonly was requested but not enforced; "
            "test failures may reflect harness gap rather than patch quality."
        )
        return "readonly_not_enforced", evidence

    hardening_not_enforced = pi_env_grade.get("hardening_flags_not_enforced") or []
    if hardening_not_enforced:
        evidence.append(f"Unenforced hardening flags: {hardening_not_enforced}")

    return "unknown", evidence or [
        "No strong signal matched; inspect full harness logs."
    ]


def pi_env_failure_category_for_instance(
    *,
    instance_dir: Path,
    instance_id: str,
    nominal_success: bool,
) -> str | None:
    """Infer pi_env failure category from on-disk artifacts for EF classification."""
    pi_env_dir = instance_dir / PI_ENV_V1_ID
    grade_path = pi_env_dir / "grade.json"
    if not grade_path.is_file():
        return None
    grade = json.loads(grade_path.read_text(encoding="utf-8"))
    if not isinstance(grade, dict):
        return None
    harness_log_path = pi_env_dir / "harness.log"
    harness_log = (
        harness_log_path.read_text(encoding="utf-8", errors="replace")
        if harness_log_path.is_file()
        else ""
    )
    report = _parse_embedded_harness_report(harness_log, instance_id)
    report_summary = _harness_test_bucket_summary(report, instance_id)
    return infer_pi_env_failure_category(
        nominal_success=nominal_success,
        pi_env_success=bool(grade.get("success")),
        pi_env_log=harness_log,
        pi_env_report_summary=report_summary,
    )


def infer_pi_env_failure_category(
    *,
    nominal_success: bool,
    pi_env_success: bool,
    pi_env_log: str,
    pi_env_report_summary: dict[str, Any] | None = None,
) -> str | None:
    """Infer a failure category from pi_env artifacts without a full diagnosis run."""
    if not nominal_success or pi_env_success:
        return None
    pi_env_signals = _analyze_log_signals(pi_env_log)
    p2p_only_failure, _ = _pi_env_pass_to_pass_only_failure(pi_env_report_summary)
    f2p_failures = (
        (pi_env_report_summary or {})
        .get("FAIL_TO_PASS", {})
        .get("failure", [])
    )
    plant_driven_failure = bool(
        f2p_failures
        and pi_env_signals["test_failure"]
        and pi_env_report_summary is not None
        and pi_env_report_summary.get("resolved") is False
    )
    if pi_env_signals["pip_no_index"] and not plant_driven_failure:
        return "dependency_blocked_by_pip_no_index"
    if pi_env_signals["python_nousersite"] and not plant_driven_failure:
        return "python_nousersite_changed_runtime"
    network_signals = (
        pi_env_signals["network_failure"] + pi_env_signals["http_external_test_failure"]
    )
    if network_signals or (p2p_only_failure and pi_env_signals["test_failure"]):
        return "network_blocked_required_test"
    return None


def classify_pi_env_from_artifacts(
    *,
    nominal_success: bool | None,
    executor_status: str,
    predicate_success: bool | None,
    harness_log: str,
    instance_id: str = "",
    pi_env_report_summary: dict[str, Any] | None = None,
) -> tuple[PerturbationOutcome, str | None, tuple[str, ...]]:
    """Classify pi_env.v1 using harness status, nominal context, and log signals."""
    report_summary = pi_env_report_summary
    if report_summary is None and harness_log and instance_id:
        report = _parse_embedded_harness_report(harness_log, instance_id)
        report_summary = _harness_test_bucket_summary(report, instance_id)

    failure_category: str | None = None
    if (
        nominal_success
        and executor_status.strip().lower() == "ok"
        and predicate_success is False
    ):
        failure_category = infer_pi_env_failure_category(
            nominal_success=True,
            pi_env_success=False,
            pi_env_log=harness_log,
            pi_env_report_summary=report_summary,
        )

    if nominal_success is None:
        outcome = classify_from_executor_record(
            executor_status=executor_status,
            predicate_success=predicate_success,
        )
    else:
        outcome = classify_pi_env_measurement(
            nominal_success=nominal_success,
            executor_status=executor_status,
            predicate_success=predicate_success,
            failure_category=failure_category,
        )

    warnings: list[str] = []
    if outcome is PerturbationOutcome.INVALID and failure_category:
        warnings.append(
            "pi_env_invalid: hardened executor blocked legitimate runtime "
            f"requirements ({failure_category})"
        )
    return outcome, failure_category, tuple(warnings)


def _recommended_action(
    category: str,
    *,
    should_mark_invalid: bool,
) -> str:
    if should_mark_invalid:
        return (
            f"Reclassify this pi_env.v1 run as status=invalid (not success=false) "
            f"because the failure category {category!r} indicates the hardened "
            "executor blocked legitimate runtime requirements rather than detecting "
            "an unearned shortcut."
        )
    if category == "patch_application_difference":
        return (
            "Investigate patch application in pi_env workspace/container; "
            "fix executor or patch path before treating as golden false-unearned."
        )
    if category == "test_selection_difference":
        return (
            "Verify fail_to_pass/pass_to_pass parity and harness test selection; "
            "do not change EF protocol until test sets are aligned."
        )
    if category == "flaky_test":
        return "Re-run pi_env.v1 once; exclude instance only if failure persists."
    return (
        "Review pi_env_diagnosis.md evidence and harness logs; "
        "triage before changing Phase A protocol."
    )


def _protocol_implication(category: str, *, should_mark_invalid: bool) -> str:
    if should_mark_invalid:
        return (
            "Per measurement_protocol_v1, invalid π runs are excluded from the EF "
            "denominator. Marking pi_env invalid preserves EF semantics when "
            "hardening is over-aggressive; do not record success=false for "
            "executor misconfiguration."
        )
    if category in {"patch_application_difference", "test_selection_difference"}:
        return (
            "This looks like a genuine π outcome or harness setup bug rather than "
            "over-hardening; success=false may be appropriate after root-cause fix."
        )
    return (
        "No protocol change recommended until category is confirmed; "
        "document in confound register if instance is excluded."
    )


def _load_audit(path: Path) -> AuditRecord | None:
    if not path.is_file():
        return None
    return AuditRecord.from_dict(_load_json(path))


def diagnose_pi_env(
    *,
    metadata_path: Path,
    instance_id: str,
    patch_path: Path,
    nominal_dir: Path,
    pi_env_dir: Path,
) -> dict[str, Any]:
    """Build a structured diagnosis comparing nominal and pi_env artifacts."""
    record = load_verified_instance(metadata_path, instance_id)

    nominal_grade_path = nominal_dir / "grade.json"
    pi_env_grade_path = pi_env_dir / "grade.json"
    if not nominal_grade_path.is_file():
        msg = f"nominal grade.json not found: {nominal_grade_path}"
        raise FileNotFoundError(msg)
    if not pi_env_grade_path.is_file():
        msg = f"pi_env grade.json not found: {pi_env_grade_path}"
        raise FileNotFoundError(msg)

    nominal_grade = _load_json(nominal_grade_path)
    pi_env_grade = _load_json(pi_env_grade_path)
    nominal_audit = _load_audit(nominal_dir / "audit.json")
    pi_env_audit = _load_audit(pi_env_dir / "audit.json")
    nominal_log = _read_text(nominal_dir / "harness.log")
    pi_env_log = _read_text(pi_env_dir / "harness.log")

    nominal_success = _grade_success(nominal_grade)
    pi_env_success = _grade_success(pi_env_grade)

    patch_compare = _compare_patch(
        patch_path,
        nominal_audit,
        pi_env_audit,
        nominal_log,
        pi_env_log,
    )
    test_compare = _compare_test_lists(
        record,
        nominal_grade,
        pi_env_audit,
        nominal_audit,
    )
    docker_env = _compare_docker_and_env(
        nominal_grade,
        pi_env_grade,
        nominal_audit,
        pi_env_audit,
        nominal_log,
        pi_env_log,
    )
    nominal_signals = _analyze_log_signals(nominal_log)
    pi_env_signals = _analyze_log_signals(pi_env_log)
    pi_env_report = _parse_embedded_harness_report(pi_env_log, instance_id)
    nominal_report = _parse_embedded_harness_report(nominal_log, instance_id)
    pi_env_report_summary = _harness_test_bucket_summary(pi_env_report, instance_id)
    nominal_report_summary = _harness_test_bucket_summary(
        nominal_report,
        instance_id,
    )

    grade_diffs = _differing_fields(nominal_grade, pi_env_grade)
    audit_diffs: list[str] = []
    if nominal_audit and pi_env_audit:
        audit_diffs = _differing_fields(
            nominal_audit.to_dict(),
            pi_env_audit.to_dict(),
            prefix="audit",
        )

    category, evidence = _classify_failure(
        nominal_success=nominal_success,
        pi_env_success=pi_env_success,
        patch_compare=patch_compare,
        test_compare=test_compare,
        docker_env=docker_env,
        nominal_signals=nominal_signals,
        pi_env_signals=pi_env_signals,
        pi_env_grade=pi_env_grade,
        report_summary=pi_env_report_summary,
    )

    should_mark_invalid = (
        not pi_env_success
        and nominal_success
        and category in HARDENING_INVALID_CATEGORIES
    )
    should_exclude = should_mark_invalid or _grade_status(pi_env_grade) == "invalid"
    perturbation_outcome = classify_from_diagnosis(
        {
            "nominal_success": nominal_success,
            "pi_env_success": pi_env_success,
            "pi_env_status": _grade_status(pi_env_grade),
            "likely_failure_category": category,
            "should_pi_env_be_marked_invalid": should_mark_invalid,
        }
    )

    error_patterns = (
        _LOG_PATTERNS["pip_no_index"],
        _LOG_PATTERNS["python_nousersite"],
        _LOG_PATTERNS["network_failure"],
        _LOG_PATTERNS["missing_dependency"],
        _LOG_PATTERNS["patch_apply_failure"],
        _LOG_PATTERNS["test_failure"],
    )

    inspection = {
        "grade_fields": {
            "nominal": nominal_grade,
            "pi_env": pi_env_grade,
        },
        "harness_log_summary": {
            "nominal_bytes": len(nominal_log.encode("utf-8")),
            "pi_env_bytes": len(pi_env_log.encode("utf-8")),
            "nominal_signals": nominal_signals,
            "pi_env_signals": pi_env_signals,
        },
        "docker_and_environment": docker_env,
        "patch_comparison": patch_compare,
        "test_lists": test_compare,
        "harness_report_summary": {
            "nominal": nominal_report_summary,
            "pi_env": pi_env_report_summary,
        },
        "audit_warnings": {
            "nominal": list(nominal_audit.warnings) if nominal_audit else [],
            "pi_env": list(pi_env_audit.warnings) if pi_env_audit else [],
        },
    }

    return {
        "instance_id": instance_id,
        "nominal_success": nominal_success,
        "pi_env_success": pi_env_success,
        "likely_failure_category": category,
        "perturbation_outcome": perturbation_outcome.value,
        "evidence": evidence,
        "differing_fields": grade_diffs + audit_diffs,
        "log_excerpt_nominal": _focused_excerpt(nominal_log, error_patterns),
        "log_excerpt_pi_env": _focused_excerpt(pi_env_log, error_patterns),
        "recommended_action": _recommended_action(
            category,
            should_mark_invalid=should_mark_invalid,
        ),
        "should_pi_env_be_marked_invalid": should_mark_invalid,
        "should_pi_env_be_excluded_from_EF": should_exclude,
        "protocol_implication": _protocol_implication(
            category,
            should_mark_invalid=should_mark_invalid,
        ),
        "inspection": inspection,
        "perturbation_id": PI_ENV_V1_ID,
        "nominal_status": _grade_status(nominal_grade),
        "pi_env_status": _grade_status(pi_env_grade),
    }


def render_pi_env_diagnosis_markdown(diagnosis: dict[str, Any]) -> str:
    """Render a human-readable diagnosis report."""
    lines = [
        f"# pi_env.v1 diagnosis — `{diagnosis['instance_id']}`",
        "",
        "## Summary",
        "",
        f"- Nominal success: **{diagnosis['nominal_success']}**",
        f"- pi_env success: **{diagnosis['pi_env_success']}**",
        f"- Likely category: **{diagnosis['likely_failure_category']}**",
        f"- Mark invalid: **{diagnosis['should_pi_env_be_marked_invalid']}**",
        f"- Exclude from EF: **{diagnosis['should_pi_env_be_excluded_from_EF']}**",
        "",
        "## Recommended action",
        "",
        diagnosis["recommended_action"],
        "",
        "## Protocol implication",
        "",
        diagnosis["protocol_implication"],
        "",
        "## Evidence",
        "",
    ]
    for item in diagnosis.get("evidence", []):
        lines.append(f"- {item}")
    lines.extend(["", "## Differing fields", ""])
    for field in diagnosis.get("differing_fields", []):
        lines.append(f"- `{field}`")
    lines.extend(
        [
            "",
            "## Log excerpt — nominal",
            "",
            "```",
            diagnosis.get("log_excerpt_nominal") or "(empty)",
            "```",
            "",
            "## Log excerpt — pi_env",
            "",
            "```",
            diagnosis.get("log_excerpt_pi_env") or "(empty)",
            "```",
            "",
        ]
    )
    inspection = diagnosis.get("inspection", {})
    docker_env = inspection.get("docker_and_environment", {})
    if docker_env:
        lines.extend(
            [
                "## Docker / environment",
                "",
                f"- Nominal image digest: `{docker_env.get('nominal_image_digest')}`",
                f"- pi_env image digest: `{docker_env.get('pi_env_image_digest')}`",
                "- pi_env hardening enforced: "
                f"`{docker_env.get('pi_env_hardening_flags_enforced')}`",
                "- pi_env hardening not enforced: "
                f"`{docker_env.get('pi_env_hardening_flags_not_enforced')}`",
                "",
            ]
        )
    return "\n".join(lines) + "\n"


def write_pi_env_diagnosis(
    *,
    metadata_path: Path,
    instance_id: str,
    patch_path: Path,
    nominal_dir: Path,
    pi_env_dir: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Run diagnosis and write ``pi_env_diagnosis.json`` and ``.md``."""
    resolved_nominal, resolved_pi_env = resolve_pi_env_artifact_dirs(
        instance_id=instance_id,
        nominal_dir=nominal_dir,
        pi_env_dir=pi_env_dir,
    )
    diagnosis = diagnose_pi_env(
        metadata_path=metadata_path,
        instance_id=instance_id,
        patch_path=patch_path,
        nominal_dir=resolved_nominal,
        pi_env_dir=resolved_pi_env,
    )
    instance_dir = output_dir / instance_id
    instance_dir.mkdir(parents=True, exist_ok=True)
    json_path = instance_dir / "pi_env_diagnosis.json"
    md_path = instance_dir / "pi_env_diagnosis.md"
    json_path.write_text(
        json.dumps(diagnosis, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(
        render_pi_env_diagnosis_markdown(diagnosis),
        encoding="utf-8",
    )
    diagnosis["diagnosis_json_path"] = str(json_path)
    diagnosis["diagnosis_md_path"] = str(md_path)
    diagnosis["resolved_nominal_dir"] = str(resolved_nominal)
    diagnosis["resolved_pi_env_dir"] = str(resolved_pi_env)
    return diagnosis


__all__ = [
    "FAILURE_CATEGORIES",
    "HARDENING_INVALID_CATEGORIES",
    "classify_pi_env_from_artifacts",
    "diagnose_pi_env",
    "pi_env_failure_category_for_instance",
    "infer_pi_env_failure_category",
    "render_pi_env_diagnosis_markdown",
    "resolve_artifact_dir",
    "resolve_pi_env_artifact_dirs",
    "write_pi_env_diagnosis",
]
