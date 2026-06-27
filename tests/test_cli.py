import json
from pathlib import Path

import pytest

from earnbench.cli import CLIError, main, run_grading, validate_run_arguments

FIXTURES = Path(__file__).parent / "fixtures"


def test_compute_prints_earned_fraction_report(capsys) -> None:
    exit_code = main(["compute", str(FIXTURES / "compute_input.json")])
    captured = capsys.readouterr()

    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["status"] == "defined"
    assert payload["earned_fraction"] == pytest.approx(2 / 3)
    assert payload["valid_count"] == 3
    assert payload["successful_count"] == 2
    assert payload["run_id"] == "cli-run-001"
    assert "provenance" in payload
    assert payload["provenance"]["perturbation_registry_version"]


def test_compute_missing_nominal_exits_nonzero(capsys, tmp_path: Path) -> None:
    bad_input = tmp_path / "bad.json"
    bad_input.write_text(json.dumps({"perturbations": []}), encoding="utf-8")

    exit_code = main(["compute", str(bad_input)])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "nominal" in captured.err.lower()


def test_compute_missing_file_exits_nonzero(capsys) -> None:
    exit_code = main(["compute", "/nonexistent/compute_input.json"])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "file not found" in captured.err


def test_compute_honors_input_provenance_overrides(capsys, tmp_path) -> None:
    input_path = tmp_path / "compute_with_provenance.json"
    input_path.write_text(
        json.dumps(
            {
                "nominal": {
                    "run_id": "run-prov",
                    "task_id": "task-prov",
                    "success": True,
                },
                "perturbations": [
                    {
                        "perturbation_id": "pi_vtest.v1",
                        "status": "ok",
                        "success": True,
                        "channel": "vtest",
                    }
                ],
                "config_digest": "sha256:from-input",
                "random_seed": 99,
            }
        ),
        encoding="utf-8",
    )

    exit_code = main(["compute", str(input_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["provenance"]["config_digest"] == "sha256:from-input"
    assert payload["provenance"]["random_seed"] == 99


def test_validate_audit_success(capsys) -> None:
    exit_code = main(["validate-audit", str(FIXTURES / "valid_audit.json")])
    captured = capsys.readouterr()

    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["instance_id"] == "django__django-13279"
    assert payload["status"] == "ok"
    assert "provenance" in payload


def test_validate_audit_quiet(capsys) -> None:
    exit_code = main(["validate-audit", "--quiet", str(FIXTURES / "valid_audit.json")])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured.out == ""


def test_validate_audit_invalid_exits_nonzero(capsys) -> None:
    exit_code = main(["validate-audit", str(FIXTURES / "invalid_audit.json")])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "invalid audit record" in captured.err


def test_validate_audit_missing_file_exits_nonzero(capsys) -> None:
    exit_code = main(["validate-audit", "/nonexistent/audit.json"])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "file not found" in captured.err


def test_run_validates_arguments_and_returns_not_implemented(
    capsys,
    tmp_path: Path,
) -> None:
    patch = tmp_path / "patch.diff"
    config = tmp_path / "run_config.yaml"
    patch.write_text("diff --git a/foo b/foo\n", encoding="utf-8")
    config.write_text("dataset:\n  name: test\n", encoding="utf-8")

    exit_code = main(
        [
            "run",
            "--instance",
            "django__django-13279",
            "--patch",
            str(patch),
            "--perturbation",
            "pi_vtest.v1",
            "--config",
            str(config),
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "not implemented" in captured.err.lower()
    assert "arguments validated" in captured.err.lower()


def test_run_missing_patch_file_exits_nonzero(capsys, tmp_path: Path) -> None:
    config = tmp_path / "run_config.yaml"
    config.write_text("dataset:\n  name: test\n", encoding="utf-8")

    exit_code = main(
        [
            "run",
            "--instance",
            "django__django-13279",
            "--patch",
            str(tmp_path / "missing.patch"),
            "--perturbation",
            "pi_vtest.v1",
            "--config",
            str(config),
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "patch file not found" in captured.err


def test_run_missing_required_flags_exits_nonzero() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["run", "--instance", "django__django-13279"])

    assert exc.value.code != 0


def test_run_grading_raises_not_implemented(tmp_path: Path) -> None:
    patch = tmp_path / "patch.diff"
    config = tmp_path / "run_config.yaml"
    patch.write_text("diff --git a/foo b/foo\n", encoding="utf-8")
    config.write_text("dataset:\n  name: test\n", encoding="utf-8")

    with pytest.raises(NotImplementedError, match="not implemented"):
        run_grading(
            instance="django__django-13279",
            patch=patch,
            perturbation="pi_vtest.v1",
            config=config,
        )


def test_validate_run_arguments_rejects_empty_instance(tmp_path: Path) -> None:
    patch = tmp_path / "patch.diff"
    config = tmp_path / "run_config.yaml"
    patch.write_text("patch\n", encoding="utf-8")
    config.write_text("config\n", encoding="utf-8")

    with pytest.raises(CLIError, match="instance"):
        validate_run_arguments(
            instance="  ",
            patch=patch,
            perturbation="pi_vtest.v1",
            config=config,
        )


def test_registry_list_cli(capsys) -> None:
    exit_code = main(["registry", "list"])
    captured = capsys.readouterr()

    assert exit_code == 0
    payload = json.loads(captured.out)
    ids = [item["id"] for item in payload["perturbations"]]
    assert ids == ["pi_vtest.v1", "pi_verif.v1", "pi_env.v1"]


def test_registry_show_cli(capsys) -> None:
    exit_code = main(["registry", "show", "pi_vtest.v1"])
    captured = capsys.readouterr()

    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["id"] == "pi_vtest.v1"
    assert "visible_test_overfitting" in payload["supported_channels"]


def test_registry_show_unknown_exits_nonzero(capsys) -> None:
    exit_code = main(["registry", "show", "pi_missing.v1"])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "unknown perturbation" in captured.err


def test_registry_validate_cli(capsys) -> None:
    exit_code = main(["registry", "validate"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert json.loads(captured.out)["status"] == "ok"


def test_swebench_prepare_smoke_cli(capsys, tmp_path: Path) -> None:
    output_dir = tmp_path / "out"
    exit_code = main(
        [
            "swebench",
            "prepare-smoke",
            "--metadata-parquet",
            str(FIXTURES / "swebench_smoke_metadata.json"),
            "--instance-id",
            "psf__requests-1724",
            "--output",
            str(output_dir),
            "--run-id",
            "cli-smoke",
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    plan = json.loads(captured.out)
    assert plan["instance_id"] == "psf__requests-1724"
    assert plan["dry_run"] is True
    assert (output_dir / "psf__requests-1724" / "plan.json").is_file()


def test_swebench_prepare_smoke_missing_instance(capsys, tmp_path: Path) -> None:
    exit_code = main(
        [
            "swebench",
            "prepare-smoke",
            "--metadata-parquet",
            str(FIXTURES / "swebench_smoke_metadata.json"),
            "--instance-id",
            "missing__instance-1",
            "--output",
            str(tmp_path / "out"),
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "not found" in captured.err.lower()
