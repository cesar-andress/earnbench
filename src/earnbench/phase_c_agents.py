"""Phase C agent patch collection (prepare, run, summarize)."""

from __future__ import annotations

import csv
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from earnbench.adapters.swebench_metadata import load_verified_instance_row
from earnbench.agents.base import (
    AgentRunContext,
    BaseAgentAdapter,
    build_repair_prompt,
    replicate_seed,
)
from earnbench.agents.external_cli import build_external_cli_adapter
from earnbench.agents.ollama import build_ollama_adapter
from earnbench.agents.schemas import (
    ATTEMPT_CSV_COLUMNS,
    FAILURE_CSV_COLUMNS,
    PHASE_C_SCHEMA_VERSION,
    PHASE_C_SCAFFOLD_ID,
    AgentArmSpec,
    AttemptRecord,
    CollectionTask,
    PhaseCRunManifest,
    PhaseCSummary,
)
from earnbench.provenance import utc_timestamp

RUN_MANIFEST_JSON = "run_manifest.json"
ATTEMPTS_JSONL = "attempts.jsonl"
ATTEMPTS_CSV = "attempts.csv"
FAILURES_CSV = "failures.csv"
SUMMARY_JSON = "summary.json"
PHASE_A_RUN_MANIFEST = "run_manifest.json"
PHASE_A_SUMMARY = "summary.csv"


class PhaseCError(Exception):
    """Raised when Phase C preparation or execution fails."""


@dataclass(frozen=True, slots=True)
class PhaseCPrepareResult:
    manifest_path: Path
    task_count: int
    instance_count: int
    arm_count: int


@dataclass(frozen=True, slots=True)
class PhaseCRunResult:
    output_dir: Path
    attempt_count: int
    ok_count: int
    no_patch_count: int
    invalid_patch_count: int
    error_count: int
    skipped_count: int
    failures_path: Path
    attempts_csv: Path


def load_arms_yaml(path: Path) -> tuple[AgentArmSpec, ...]:
    """Load and validate agent arm specs from ``arms.yaml``."""
    try:
        import yaml
    except ImportError as exc:
        msg = "PyYAML is required for Phase C: pip install pyyaml"
        raise PhaseCError(msg) from exc

    if not path.is_file():
        msg = f"arms file not found: {path}"
        raise PhaseCError(msg)

    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or "arms" not in payload:
        msg = f"arms file must contain top-level 'arms' list: {path}"
        raise PhaseCError(msg)
    raw_arms = payload["arms"]
    if not isinstance(raw_arms, list):
        msg = f"'arms' must be a list in {path}"
        raise PhaseCError(msg)

    arms: list[AgentArmSpec] = []
    errors: list[str] = []
    seen: set[str] = set()
    for item in raw_arms:
        if not isinstance(item, dict):
            errors.append(f"invalid arm entry (expected mapping): {item!r}")
            continue
        arm = AgentArmSpec.from_dict(item)
        if arm.id in seen:
            errors.append(f"duplicate arm id: {arm.id!r}")
        seen.add(arm.id)
        errors.extend(arm.validate())
        arms.append(arm)
    if errors:
        raise PhaseCError("\n".join(errors))
    if not arms:
        msg = f"no arms defined in {path}"
        raise PhaseCError(msg)
    return tuple(arms)


def load_instance_ids(path: Path) -> tuple[str, ...]:
    """Load instance ids from CSV or JSON."""
    if not path.is_file():
        msg = f"instances file not found: {path}"
        raise PhaseCError(msg)

    suffix = path.suffix.lower()
    if suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            return tuple(str(item) for item in payload)
        if isinstance(payload, dict):
            if "instance_ids" in payload:
                return tuple(str(item) for item in payload["instance_ids"])
            if "instances" in payload:
                return tuple(str(item) for item in payload["instances"])
        msg = f"unsupported JSON instances shape: {path}"
        raise PhaseCError(msg)

    if suffix == ".csv":
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames and "instance_id" in reader.fieldnames:
                return tuple(
                    str(row["instance_id"]).strip()
                    for row in reader
                    if str(row.get("instance_id", "")).strip()
                )
            handle.seek(0)
            plain = csv.reader(handle)
            return tuple(
                str(row[0]).strip()
                for row in plain
                if row and str(row[0]).strip() and row[0] != "instance_id"
            )

    msg = f"unsupported instances format (expected .csv or .json): {path}"
    raise PhaseCError(msg)


def resolve_phase_a_metadata(phase_a_run: Path) -> Path:
    """Resolve metadata parquet path from a Phase A run directory."""
    manifest_path = phase_a_run / PHASE_A_RUN_MANIFEST
    if not manifest_path.is_file():
        msg = f"Phase A run_manifest.json not found: {manifest_path}"
        raise PhaseCError(msg)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    metadata_path = Path(str(payload["metadata_path"]))
    if not metadata_path.is_file():
        msg = f"metadata path from Phase A run not found: {metadata_path}"
        raise PhaseCError(msg)
    return metadata_path.resolve()


def instance_ids_from_phase_a(phase_a_run: Path) -> tuple[str, ...]:
    """Return retained instance ids from Phase A ``summary.csv`` when available."""
    summary_path = phase_a_run / PHASE_A_SUMMARY
    if not summary_path.is_file():
        msg = f"Phase A summary.csv not found: {summary_path}"
        raise PhaseCError(msg)
    ids: list[str] = []
    with summary_path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            instance_id = str(row.get("instance_id", "")).strip()
            if not instance_id:
                continue
            retained = str(row.get("retained", "true")).strip().lower()
            if retained in {"", "true", "1", "yes"}:
                ids.append(instance_id)
    if not ids:
        msg = f"no retained instances found in {summary_path}"
        raise PhaseCError(msg)
    return tuple(dict.fromkeys(ids))


def prepare_phase_c(
    *,
    phase_a_run: Path,
    output_dir: Path,
    arms_path: Path,
    instances_path: Path | None = None,
    run_id: str | None = None,
    base_seed: int = 20260627,
) -> PhaseCPrepareResult:
    """Prepare Phase C collection manifest and directory layout."""
    phase_a_run = phase_a_run.resolve()
    output_dir = output_dir.resolve()
    arms_path = arms_path.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    metadata_path = resolve_phase_a_metadata(phase_a_run)
    if instances_path is not None:
        instance_ids = load_instance_ids(instances_path.resolve())
        instances_ref = str(instances_path.resolve())
    else:
        instance_ids = instance_ids_from_phase_a(phase_a_run)
        instances_ref = str((phase_a_run / PHASE_A_SUMMARY).resolve())

    arms = load_arms_yaml(arms_path)
    tasks: list[CollectionTask] = []
    for arm in arms:
        for instance_id in instance_ids:
            for replicate in range(arm.replicates):
                tasks.append(
                    CollectionTask(
                        agent_id=arm.id,
                        instance_id=instance_id,
                        replicate=replicate,
                    )
                )

    for subdir in ("prompts", "patches", "trajectories"):
        (output_dir / subdir).mkdir(parents=True, exist_ok=True)

    effective_run_id = run_id or f"phase_c_{output_dir.name}"
    manifest = PhaseCRunManifest(
        schema_version=PHASE_C_SCHEMA_VERSION,
        scaffold_id=PHASE_C_SCAFFOLD_ID,
        phase_a_run=str(phase_a_run),
        metadata_path=str(metadata_path),
        output_dir=str(output_dir),
        arms_path=str(arms_path),
        instances_path=instances_ref,
        instance_ids=instance_ids,
        arms=arms,
        tasks=tuple(tasks),
        run_id=effective_run_id,
        prepared_at_utc=utc_timestamp(),
        base_seed=base_seed,
    )
    manifest_path = output_dir / RUN_MANIFEST_JSON
    manifest_path.write_text(
        json.dumps(manifest.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return PhaseCPrepareResult(
        manifest_path=manifest_path,
        task_count=len(tasks),
        instance_count=len(instance_ids),
        arm_count=len(arms),
    )


def load_run_manifest(path: Path) -> PhaseCRunManifest:
    """Load a prepared Phase C manifest."""
    if not path.is_file():
        msg = f"run manifest not found: {path}"
        raise PhaseCError(msg)
    payload = json.loads(path.read_text(encoding="utf-8"))
    return PhaseCRunManifest.from_dict(payload)


def _arm_lookup(manifest: PhaseCRunManifest) -> dict[str, AgentArmSpec]:
    return {arm.id: arm for arm in manifest.arms}


def _build_adapter(arm: AgentArmSpec) -> BaseAgentAdapter:
    if arm.provider == "ollama":
        return build_ollama_adapter(arm)
    if arm.provider == "external_cli":
        return build_external_cli_adapter(arm)
    msg = f"unsupported provider {arm.provider!r} for arm {arm.id!r}"
    raise PhaseCError(msg)


def _artifact_paths(
    output_dir: Path,
    *,
    agent_id: str,
    instance_id: str,
    replicate: int,
) -> tuple[Path, Path, Path]:
    rel_root = Path(agent_id) / instance_id
    stem = f"replicate_{replicate}"
    prompt_path = output_dir / "prompts" / rel_root / f"{stem}.txt"
    patch_path = output_dir / "patches" / rel_root / f"{stem}.patch"
    trajectory_path = output_dir / "trajectories" / rel_root / f"{stem}.log"
    return prompt_path, patch_path, trajectory_path


def _load_completed_task_keys(output_dir: Path) -> set[str]:
    path = output_dir / ATTEMPTS_JSONL
    if not path.is_file():
        return set()
    keys: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = AttemptRecord.from_dict(json.loads(line))
        keys.add(f"{record.agent}:{record.instance_id}:r{record.replicate}")
    return keys


def _append_attempt(output_dir: Path, record: AttemptRecord) -> None:
    path = output_dir / ATTEMPTS_JSONL
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record.to_dict(), sort_keys=True) + "\n")


def _write_attempts_csv(output_dir: Path, records: list[AttemptRecord]) -> Path:
    path = output_dir / ATTEMPTS_CSV
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=ATTEMPT_CSV_COLUMNS)
        writer.writeheader()
        for record in records:
            writer.writerow({key: record.to_dict()[key] for key in ATTEMPT_CSV_COLUMNS})
    return path


def _write_failures_csv(output_dir: Path, rows: list[dict[str, str]]) -> Path:
    path = output_dir / FAILURES_CSV
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FAILURE_CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return path


def _execute_task(
    manifest: PhaseCRunManifest,
    task: CollectionTask,
    *,
    output_dir: Path,
    metadata_path: Path,
    repair_patch: bool = False,
) -> AttemptRecord:
    arms = _arm_lookup(manifest)
    arm = arms[task.agent_id]
    adapter = _build_adapter(arm)
    instance_row = load_verified_instance_row(metadata_path, task.instance_id)
    prompt = build_repair_prompt(instance_row=instance_row)
    prompt_path, patch_path, trajectory_path = _artifact_paths(
        output_dir,
        agent_id=task.agent_id,
        instance_id=task.instance_id,
        replicate=task.replicate,
    )
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(prompt, encoding="utf-8")
    seed = replicate_seed(
        base_seed=manifest.base_seed,
        agent_id=task.agent_id,
        instance_id=task.instance_id,
        replicate=task.replicate,
    )
    context = AgentRunContext(
        output_dir=output_dir,
        arm=arm,
        instance_id=task.instance_id,
        replicate=task.replicate,
        seed=seed,
        prompt=prompt,
        prompt_path=prompt_path,
        patch_path=patch_path,
        trajectory_path=trajectory_path,
        scaffold_id=manifest.scaffold_id,
        repair_patch=repair_patch,
    )
    return adapter.collect_attempt(context)


def run_phase_c(
    *,
    manifest_path: Path,
    output_dir: Path | None = None,
    workers: int = 1,
    resume: bool = False,
    repair_patch: bool = False,
) -> PhaseCRunResult:
    """Execute Phase C patch collection for all manifest tasks."""
    manifest = load_run_manifest(manifest_path)
    resolved_output = (
        output_dir.resolve() if output_dir is not None else Path(manifest.output_dir)
    )
    metadata_path = Path(manifest.metadata_path)
    completed = _load_completed_task_keys(resolved_output) if resume else set()
    arms = _arm_lookup(manifest)

    pending: list[CollectionTask] = []
    skipped_records: list[AttemptRecord] = []
    for task in manifest.tasks:
        if resume and task.task_key in completed:
            continue
        pending.append(task)

    failures: list[dict[str, str]] = []
    new_records: list[AttemptRecord] = []

    def _run_one(task: CollectionTask) -> AttemptRecord:
        return _execute_task(
            manifest,
            task,
            output_dir=resolved_output,
            metadata_path=metadata_path,
            repair_patch=repair_patch,
        )

    if workers <= 1:
        for task in pending:
            try:
                record = _run_one(task)
            except Exception as exc:
                stamp = utc_timestamp()
                failures.append(
                    {
                        "agent": task.agent_id,
                        "instance_id": task.instance_id,
                        "replicate": str(task.replicate),
                        "stage": "collect",
                        "error": str(exc),
                        "timestamp_utc": stamp,
                    }
                )
                record = AttemptRecord(
                    agent=task.agent_id,
                    model=arms[task.agent_id].model,
                    provider=arms[task.agent_id].provider,
                    instance_id=task.instance_id,
                    replicate=task.replicate,
                    seed=replicate_seed(
                        base_seed=manifest.base_seed,
                        agent_id=task.agent_id,
                        instance_id=task.instance_id,
                        replicate=task.replicate,
                    ),
                    scaffold_id=manifest.scaffold_id,
                    prompt_sha256="",
                    patch_path="",
                    patch_sha256="",
                    trajectory_log_ref="",
                    status="error",
                    started_at_utc=stamp,
                    completed_at_utc=stamp,
                    error=str(exc),
                )
            _append_attempt(resolved_output, record)
            new_records.append(record)
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_run_one, task): task for task in pending}
            for future in as_completed(futures):
                task = futures[future]
                try:
                    record = future.result()
                except Exception as exc:
                    stamp = utc_timestamp()
                    failures.append(
                        {
                            "agent": task.agent_id,
                            "instance_id": task.instance_id,
                            "replicate": str(task.replicate),
                            "stage": "collect",
                            "error": str(exc),
                            "timestamp_utc": stamp,
                        }
                    )
                    record = AttemptRecord(
                        agent=task.agent_id,
                        model=arms[task.agent_id].model,
                        provider=arms[task.agent_id].provider,
                        instance_id=task.instance_id,
                        replicate=task.replicate,
                        seed=replicate_seed(
                            base_seed=manifest.base_seed,
                            agent_id=task.agent_id,
                            instance_id=task.instance_id,
                            replicate=task.replicate,
                        ),
                        scaffold_id=manifest.scaffold_id,
                        prompt_sha256="",
                        patch_path="",
                        patch_sha256="",
                        trajectory_log_ref="",
                        status="error",
                        started_at_utc=stamp,
                        completed_at_utc=stamp,
                        error=str(exc),
                    )
                _append_attempt(resolved_output, record)
                new_records.append(record)

    all_records = _load_all_attempts(resolved_output)
    attempts_csv = _write_attempts_csv(resolved_output, all_records)
    failures_path = _write_failures_csv(resolved_output, failures)

    ok_count = sum(1 for record in all_records if record.status == "ok")
    no_patch_count = sum(1 for record in all_records if record.status == "no_patch")
    invalid_patch_count = sum(
        1 for record in all_records if record.status == "invalid_patch"
    )
    error_count = sum(1 for record in all_records if record.status == "error")
    skipped_count = sum(1 for record in all_records if record.status == "skipped")
    skipped_count += len(skipped_records)

    return PhaseCRunResult(
        output_dir=resolved_output,
        attempt_count=len(all_records),
        ok_count=ok_count,
        no_patch_count=no_patch_count,
        invalid_patch_count=invalid_patch_count,
        error_count=error_count,
        skipped_count=skipped_count,
        failures_path=failures_path,
        attempts_csv=attempts_csv,
    )


def _load_all_attempts(output_dir: Path) -> list[AttemptRecord]:
    path = output_dir / ATTEMPTS_JSONL
    if not path.is_file():
        return []
    records: list[AttemptRecord] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        records.append(AttemptRecord.from_dict(json.loads(line)))
    return records


def summarize_phase_c(*, output_dir: Path) -> PhaseCSummary:
    """Summarize a completed Phase C run directory."""
    output_dir = output_dir.resolve()
    manifest_path = output_dir / RUN_MANIFEST_JSON
    manifest = load_run_manifest(manifest_path)
    records = _load_all_attempts(output_dir)

    by_agent: dict[str, dict[str, int]] = {}
    for record in records:
        bucket = by_agent.setdefault(
            record.agent,
            {
                "ok": 0,
                "no_patch": 0,
                "invalid_patch": 0,
                "error": 0,
                "skipped": 0,
                "total": 0,
            },
        )
        bucket["total"] += 1
        bucket[record.status] = bucket.get(record.status, 0) + 1

    summary = PhaseCSummary(
        run_id=manifest.run_id,
        attempt_count=len(records),
        ok_count=sum(1 for record in records if record.status == "ok"),
        no_patch_count=sum(1 for record in records if record.status == "no_patch"),
        invalid_patch_count=sum(
            1 for record in records if record.status == "invalid_patch"
        ),
        error_count=sum(1 for record in records if record.status == "error"),
        skipped_count=sum(1 for record in records if record.status == "skipped"),
        by_agent=by_agent,
        summarized_at_utc=utc_timestamp(),
    )
    summary_path = output_dir / SUMMARY_JSON
    summary_path.write_text(
        json.dumps(summary.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary
