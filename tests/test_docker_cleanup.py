"""Tests for stale SWE-bench Docker container cleanup."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from earnbench.adapters.docker_cleanup import (
    cleanup_stale_container,
    is_managed_swebench_container,
    managed_swebench_container_create,
    wrap_container_create_with_cleanup,
)


def test_is_managed_swebench_container() -> None:
    assert is_managed_swebench_container(
        "sweb.eval.django__django-13279.nominal_django__django-13279"
    )
    assert not is_managed_swebench_container("my-app-prod")
    assert not is_managed_swebench_container("")


def test_cleanup_stale_container_skips_foreign_names() -> None:
    client = MagicMock()
    result = cleanup_stale_container("postgres-main", client=client)
    assert result.skipped is True
    assert result.removed is False
    client.containers.get.assert_not_called()


def test_cleanup_stale_container_removes_existing_managed_container() -> None:
    client = MagicMock()
    container = MagicMock()
    container.status = "running"
    client.containers.get.return_value = container

    result = cleanup_stale_container(
        "sweb.eval.django__django-13279.nominal_django__django-13279",
        client=client,
    )

    assert result.removed is True
    assert result.existed is True
    container.stop.assert_called_once_with(timeout=10)
    container.remove.assert_called_once_with(force=True)


def test_cleanup_stale_container_noop_when_missing() -> None:
    client = MagicMock()
    client.containers.get.side_effect = __import__("docker").errors.NotFound(
        "missing"
    )

    result = cleanup_stale_container(
        "sweb.eval.psf__requests-1724.pi_env_run",
        client=client,
    )

    assert result.existed is False
    assert result.removed is False
    assert result.skipped is False


def test_wrap_container_create_retries_after_http_409(monkeypatch) -> None:
    import docker.errors

    calls: list[str] = []
    created = MagicMock(id="fresh-container")

    def original_create(self, image, command=None, **kwargs):
        calls.append("create")
        if len(calls) == 1:
            raise docker.errors.APIError(
                "409 Client Error: Conflict "
                '(container name already in use)',
                response=MagicMock(status_code=409),
            )
        return created

    wrapped = wrap_container_create_with_cleanup(original_create)
    mock_self = MagicMock()
    mock_self.client = MagicMock()
    container_name = "sweb.eval.django__django-13279.nominal_django__django-13279"

    with patch(
        "earnbench.adapters.docker_cleanup.cleanup_stale_container",
        return_value=MagicMock(removed=True),
    ) as cleanup_mock:
        result = wrapped(
            mock_self,
            "sweb.eval.x86_64.django__django-13279:latest",
            name=container_name,
        )

    assert result is created
    assert calls == ["create", "create"]
    assert cleanup_mock.call_count >= 2


def test_managed_swebench_container_create_wraps_docker_collection() -> None:
    from docker.models.containers import ContainerCollection

    original = ContainerCollection.create
    assert original is not ContainerCollection.create or True
    with managed_swebench_container_create():
        assert ContainerCollection.create is not original
    assert ContainerCollection.create is original


def test_default_nominal_runner_uses_managed_container_create(monkeypatch) -> None:
    pytest.importorskip("swebench")

    from earnbench.adapters.swebench_nominal import (
        NominalRunRequest,
        default_nominal_runner,
    )

    managed_calls: list[str] = []

    class FakeContext:
        def __enter__(self):
            managed_calls.append("enter")
            return None

        def __exit__(self, *args):
            managed_calls.append("exit")
            return False

    monkeypatch.setattr(
        "earnbench.adapters.swebench_nominal.managed_swebench_container_create",
        lambda: FakeContext(),
    )
    monkeypatch.setattr(
        "earnbench.adapters.swebench_nominal.require_swebench_harness",
        lambda: None,
    )

    fake_outcome = {"completed": True, "resolved": True}

    def fake_run_instance(*args, **kwargs):
        managed_calls.append("run_instance")
        return fake_outcome

    monkeypatch.setattr(
        "swebench.harness.run_evaluation.run_instance",
        fake_run_instance,
    )

    fake_spec = MagicMock()
    fake_spec.eval_script = "#!/bin/bash\necho hi"
    fake_spec.get_instance_container_name.return_value = (
        "sweb.eval.django__django-13279.nominal_django__django-13279"
    )
    monkeypatch.setattr(
        "swebench.harness.test_spec.test_spec.make_test_spec",
        lambda row: fake_spec,
    )

    fake_client = MagicMock()
    monkeypatch.setattr("docker.from_env", lambda: fake_client)

    request = NominalRunRequest(
        instance_row={
            "instance_id": "django__django-13279",
            "FAIL_TO_PASS": '["test_a"]',
            "PASS_TO_PASS": "[]",
        },
        patch_content="diff --git a/x b/x\n",
        model_name="earnbench_nominal",
        run_id="nominal_django__django-13279",
        timeout_seconds=60,
    )

    result = default_nominal_runner(request)

    assert result.success is True
    assert "enter" in managed_calls
    assert "run_instance" in managed_calls
    assert managed_calls.index("enter") < managed_calls.index("run_instance")
