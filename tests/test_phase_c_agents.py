"""Tests for Phase C agent patch collection scaffolding."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from earnbench.agents.base import (
    AgentRunContext,
    AgentRunResult,
    build_repair_prompt,
    extract_unified_diff,
    prompt_sha256,
    replicate_seed,
)
from earnbench.agents.ollama import OllamaAdapter
from earnbench.agents.schemas import (
    PHASE_C_SCAFFOLD_ID,
    AgentArmSpec,
    AttemptRecord,
    PhaseCRunManifest,
)
from earnbench.phase_c_agents import (
    PhaseCError,
    load_arms_yaml,
    load_instance_ids,
    prepare_phase_c,
    run_phase_c,
    summarize_phase_c,
)

FIXTURES = Path(__file__).parent / "fixtures"
METADATA_FIXTURE = FIXTURES / "swebench_smoke_metadata.json"
ARMS_FIXTURE = FIXTURES / "phase_c_arms.yaml"
ARMS_MINIMAL = FIXTURES / "phase_c_arms_minimal.yaml"
INSTANCE_ID = "psf__requests-1724"

SAMPLE_DIFF = (
    "diff --git a/requests/models.py b/requests/models.py\n"
    "index 1111111..2222222 100644\n"
    "--- a/requests/models.py\n"
    "+++ b/requests/models.py\n"
    "@@ -1,3 +1,4 @@\n"
    " # header\n"
    "+fixed prod change\n"
)


def _write_phase_a_run(tmp_path: Path, *, metadata_path: Path | None = None) -> Path:
    phase_a = tmp_path / "phase_a"
    phase_a.mkdir()
    metadata = metadata_path or METADATA_FIXTURE
    (phase_a / "run_manifest.json").write_text(
        json.dumps({"metadata_path": str(metadata.resolve())}, indent=2) + "\n",
        encoding="utf-8",
    )
    with (phase_a / "summary.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["instance_id", "retained"])
        writer.writeheader()
        writer.writerow({"instance_id": INSTANCE_ID, "retained": "true"})
    return phase_a


def _mock_ollama_run(_self: OllamaAdapter, context: AgentRunContext) -> AgentRunResult:
    return AgentRunResult(
        trajectory_text='{"mock": true}\n',
        patch_text=SAMPLE_DIFF,
        status="ok",
        model=context.arm.model,
    )


def test_agent_arm_spec_validation() -> None:
    valid = AgentArmSpec(
        id="ollama_qwen3_coder_30b",
        provider="ollama",
        model="qwen3-coder:30b",
    )
    assert valid.validate() == []

    invalid = AgentArmSpec(id="", provider="unknown", replicates=0)
    errors = invalid.validate()
    assert any("id must be non-empty" in item for item in errors)
    assert any("provider must be" in item for item in errors)
    assert any("replicates must be >= 1" in item for item in errors)


def test_attempt_record_validation() -> None:
    record = AttemptRecord(
        agent="ollama_qwen3_coder_30b",
        model="qwen3-coder:30b",
        provider="ollama",
        instance_id=INSTANCE_ID,
        replicate=0,
        seed=123,
        scaffold_id=PHASE_C_SCAFFOLD_ID,
        prompt_sha256="abc",
        patch_path="patches/x.patch",
        patch_sha256="def",
        trajectory_log_ref="trajectories/x.log",
        status="ok",
        started_at_utc="2026-06-27T00:00:00Z",
        completed_at_utc="2026-06-27T00:00:01Z",
    )
    assert record.validate() == []
    bad = AttemptRecord.from_dict({**record.to_dict(), "status": "bogus"})
    assert any("invalid status" in item for item in bad.validate())


def test_load_arms_yaml_parses_fixture() -> None:
    arms = load_arms_yaml(ARMS_FIXTURE)
    assert len(arms) == 7
    ids = {arm.id for arm in arms}
    assert ids == {
        "ollama_qwen3_coder_30b",
        "ollama_qwen25_coder_32b",
        "ollama_deepseek_coder_v2_lite",
        "ollama_devstral",
        "claude_code",
        "codex",
        "gemini",
    }
    ollama = next(arm for arm in arms if arm.id == "ollama_qwen25_coder_32b")
    assert ollama.model == "qwen2.5-coder:32b"
    assert ollama.replicates == 3


def test_load_arms_yaml_rejects_duplicate_ids(tmp_path: Path) -> None:
    path = tmp_path / "dup.yaml"
    path.write_text(
        "arms:\n"
        "  - id: a\n    provider: ollama\n    model: m1\n"
        "  - id: a\n    provider: ollama\n    model: m2\n",
        encoding="utf-8",
    )
    with pytest.raises(PhaseCError, match="duplicate arm id"):
        load_arms_yaml(path)


def test_load_instance_ids_csv_and_json(tmp_path: Path) -> None:
    csv_path = tmp_path / "instances.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["instance_id"])
        writer.writerow([INSTANCE_ID])

    json_path = tmp_path / "instances.json"
    json_path.write_text(json.dumps({"instance_ids": [INSTANCE_ID]}), encoding="utf-8")

    assert load_instance_ids(csv_path) == (INSTANCE_ID,)
    assert load_instance_ids(json_path) == (INSTANCE_ID,)


def test_build_repair_prompt_is_deterministic_and_excludes_leakage() -> None:
    row = {
        "instance_id": INSTANCE_ID,
        "repo": "psf/requests",
        "base_commit": "deadbeef",
        "problem_statement": "Fix redirect handling.",
        "FAIL_TO_PASS": (
            '["tests.test_models.TestCase.test_redirect", '
            '"tests.test_models.TestCase.test_other"]'
        ),
        "test_patch": "diff --git a/tests/test_models.py",
        "patch": "diff --git a/requests/models.py",
    }
    prompt = build_repair_prompt(instance_row=row)
    assert INSTANCE_ID in prompt
    assert "psf/requests" in prompt
    assert "Fix redirect handling." in prompt
    assert "Repository layout:" in prompt
    assert "`requests/`" in prompt
    assert "`tests/test_models.py`" in prompt
    assert "diff --git a/... b/..." in prompt
    assert "Markdown code fences" in prompt
    assert "Forbidden output:" in prompt
    assert "complete hunks" in prompt
    assert "exact repository-relative filenames" in prompt
    assert "Unified diff shape" in prompt
    assert "test_patch" not in prompt
    assert "requests/models.py" not in prompt
    assert prompt_sha256(prompt) == prompt_sha256(build_repair_prompt(instance_row=row))


def test_extract_unified_diff_from_fence_and_raw_text() -> None:
    fenced = f"Here is the patch:\n```diff\n{SAMPLE_DIFF}```\n"
    normalized = SAMPLE_DIFF.rstrip("\n") + "\n"
    assert extract_unified_diff(fenced) == normalized
    assert extract_unified_diff(SAMPLE_DIFF) == normalized
    assert extract_unified_diff("no diff here") is None


def test_ollama_adapter_mocked_http(tmp_path: Path) -> None:
    arm = AgentArmSpec(
        id="ollama_qwen3_coder_30b",
        provider="ollama",
        model="qwen3-coder:30b",
    )
    adapter = OllamaAdapter()
    context = AgentRunContext(
        output_dir=tmp_path,
        arm=arm,
        instance_id=INSTANCE_ID,
        replicate=0,
        seed=replicate_seed(
            base_seed=20260627,
            agent_id=arm.id,
            instance_id=INSTANCE_ID,
            replicate=0,
        ),
        prompt="prompt",
        prompt_path=tmp_path / "prompt.txt",
        patch_path=tmp_path / "patch.patch",
        trajectory_path=tmp_path / "trajectory.log",
    )
    tags = {"models": [{"name": "qwen3-coder:30b"}]}
    generate_payload = {"response": f"```diff\n{SAMPLE_DIFF}```"}

    with patch.object(adapter, "_get_json", return_value=tags):
        with patch.object(
            adapter,
            "_post_json",
            return_value=(generate_payload["response"], generate_payload),
        ):
            record = adapter.collect_attempt(context)

    assert record.status == "ok"
    assert record.model == "qwen3-coder:30b"
    assert record.prompt_sha256 == prompt_sha256("prompt")
    assert context.patch_path.is_file()
    assert context.trajectory_path.is_file()


def test_ollama_adapter_no_patch_status(tmp_path: Path) -> None:
    arm = AgentArmSpec(
        id="ollama_qwen3_coder_30b",
        provider="ollama",
        model="qwen3-coder:30b",
    )
    adapter = OllamaAdapter()
    context = AgentRunContext(
        output_dir=tmp_path,
        arm=arm,
        instance_id=INSTANCE_ID,
        replicate=0,
        seed=1,
        prompt="prompt",
        prompt_path=tmp_path / "prompt.txt",
        patch_path=tmp_path / "patch.patch",
        trajectory_path=tmp_path / "trajectory.log",
    )

    with patch.object(adapter, "_get_json", return_value={"models": [{"name": arm.model}]}):
        with patch.object(adapter, "_post_json", return_value=("plain text only", {"response": "plain text only"})):
            record = adapter.collect_attempt(context)

    assert record.status == "no_patch"
    assert record.patch_path == ""
    assert not context.patch_path.is_file()


def test_prepare_phase_c_writes_manifest(tmp_path: Path) -> None:
    phase_a = _write_phase_a_run(tmp_path)
    output = tmp_path / "phase_c"
    result = prepare_phase_c(
        phase_a_run=phase_a,
        output_dir=output,
        arms_path=ARMS_MINIMAL,
    )
    assert result.task_count == 1
    assert result.arm_count == 1
    assert result.instance_count == 1
    manifest = PhaseCRunManifest.from_dict(
        json.loads((output / "run_manifest.json").read_text(encoding="utf-8"))
    )
    assert manifest.scaffold_id == PHASE_C_SCAFFOLD_ID
    assert len(manifest.tasks) == 1


def test_run_phase_c_and_resume(tmp_path: Path) -> None:
    phase_a = _write_phase_a_run(tmp_path)
    output = tmp_path / "phase_c"
    prepare_phase_c(
        phase_a_run=phase_a,
        output_dir=output,
        arms_path=ARMS_MINIMAL,
    )
    manifest_path = output / "run_manifest.json"

    with patch.object(OllamaAdapter, "run", _mock_ollama_run):
        first = run_phase_c(manifest_path=manifest_path, workers=1, resume=False)
        second = run_phase_c(manifest_path=manifest_path, workers=1, resume=True)

    assert first.attempt_count == 1
    assert first.ok_count == 1
    assert second.attempt_count == 1
    assert (output / "attempts.jsonl").is_file()
    assert (output / "attempts.csv").is_file()
    assert (output / "patches" / "ollama_qwen3_coder_30b" / INSTANCE_ID / "replicate_0.patch").is_file()


def test_summarize_phase_c(tmp_path: Path) -> None:
    phase_a = _write_phase_a_run(tmp_path)
    output = tmp_path / "phase_c"
    prepare_phase_c(
        phase_a_run=phase_a,
        output_dir=output,
        arms_path=ARMS_MINIMAL,
    )

    with patch.object(OllamaAdapter, "run", _mock_ollama_run):
        run_phase_c(manifest_path=output / "run_manifest.json", workers=1)

    summary = summarize_phase_c(output_dir=output)
    assert summary.attempt_count == 1
    assert summary.ok_count == 1
    assert summary.by_agent["ollama_qwen3_coder_30b"]["ok"] == 1
    assert (output / "summary.json").is_file()


def test_cli_phase_c_prepare_and_summarize(tmp_path: Path, capsys) -> None:
    from earnbench.cli import main

    phase_a = _write_phase_a_run(tmp_path)
    output = tmp_path / "phase_c"
    exit_code = main(
        [
            "phase-c",
            "prepare",
            "--phase-a-run",
            str(phase_a),
            "--output",
            str(output),
            "--agent-arms",
            str(ARMS_MINIMAL),
        ]
    )
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["task_count"] == 1

    with patch.object(OllamaAdapter, "run", _mock_ollama_run):
        assert main(["phase-c", "run", "--manifest", str(output / "run_manifest.json"), "--output", str(output)]) == 0
    capsys.readouterr()

    assert main(["phase-c", "summarize", "--run", str(output)]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["ok_count"] == 1
