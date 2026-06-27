import pytest

from earnbench.adapters import (
    AdapterConfig,
    BenchmarkInstance,
    EvaluationArtifact,
    NominalEvaluationRequest,
    PatchArtifact,
    PerturbationEvaluationRequest,
    SWEBenchAdapter,
)
from earnbench.outcomes import NominalOutcome, PerturbationResult

TASK_ID = "django__django-13279"


def _sample_instance() -> BenchmarkInstance:
    return BenchmarkInstance(
        instance_id="django__django-13279",
        repo="django/django",
        base_commit="abc123",
        fail_to_pass=("tests.test_foo.TestCase.test_bar",),
        pass_to_pass=("tests.test_other.TestCase.test_ok",),
    )


def _sample_patch() -> PatchArtifact:
    return PatchArtifact(content="diff --git a/foo.py b/foo.py\n")


def _sample_config(**overrides: object) -> AdapterConfig:
    defaults = {
        "dataset_revision": "hf:princeton-nlp/SWE-bench_Verified@main",
        "holdout_salt": "earnbench_v0.1_holdout_salt",
    }
    defaults.update(overrides)
    return AdapterConfig(**defaults)  # type: ignore[arg-type]


def test_patch_artifact_computes_sha256() -> None:
    patch = _sample_patch()
    assert patch.content_sha256
    assert len(patch.content_sha256) == 64


def test_patch_artifact_rejects_empty_content() -> None:
    with pytest.raises(ValueError, match="patch content"):
        PatchArtifact(content="   ")


def test_benchmark_instance_rejects_empty_fail_to_pass() -> None:
    with pytest.raises(ValueError, match="fail_to_pass"):
        BenchmarkInstance(
            instance_id="x",
            repo="org/repo",
            base_commit="deadbeef",
            fail_to_pass=(),
        )


def test_adapter_config_computes_digest() -> None:
    config = _sample_config()
    assert config.config_digest.startswith("sha256:")


def test_evaluation_artifact_requires_exactly_one_outcome() -> None:
    nominal = NominalOutcome(run_id="run-1", task_id=TASK_ID, success=True)
    with pytest.raises(ValueError, match="requires nominal or perturbation"):
        EvaluationArtifact(run_id="run-1", task_id=TASK_ID)

    with pytest.raises(ValueError, match="cannot set both"):
        EvaluationArtifact(
            run_id="run-1",
            task_id=TASK_ID,
            nominal=nominal,
            perturbation=PerturbationResult.ok("pi_vtest.v1", success=True),
        )


def test_swebench_adapter_validates_nominal_request() -> None:
    adapter = SWEBenchAdapter()
    request = NominalEvaluationRequest(
        run_id="run-1",
        instance=_sample_instance(),
        patch=_sample_patch(),
        config=_sample_config(),
    )
    adapter.validate_nominal_request(request)


def test_swebench_adapter_rejects_disabled_perturbation() -> None:
    adapter = SWEBenchAdapter()
    request = PerturbationEvaluationRequest(
        run_id="run-1",
        instance=_sample_instance(),
        patch=_sample_patch(),
        config=_sample_config(enabled_perturbations=("pi_verif.v1",)),
        perturbation_id="pi_vtest.v1",
    )
    with pytest.raises(ValueError, match="not enabled"):
        adapter.validate_perturbation_request(request)


def test_swebench_adapter_rejects_non_mvp_enabled_perturbations() -> None:
    adapter = SWEBenchAdapter()
    with pytest.raises(ValueError, match="unsupported perturbation ids"):
        adapter.validate_config(_sample_config(enabled_perturbations=("pi_oracle.v1",)))


def test_swebench_adapter_evaluate_nominal_not_implemented() -> None:
    adapter = SWEBenchAdapter()
    request = NominalEvaluationRequest(
        run_id="run-1",
        instance=_sample_instance(),
        patch=_sample_patch(),
        config=_sample_config(),
    )
    with pytest.raises(NotImplementedError, match="not implemented"):
        adapter.evaluate_nominal(request)


def test_swebench_adapter_evaluate_perturbation_not_implemented() -> None:
    adapter = SWEBenchAdapter()
    request = PerturbationEvaluationRequest(
        run_id="run-1",
        instance=_sample_instance(),
        patch=_sample_patch(),
        config=_sample_config(),
        perturbation_id="pi_env.v1",
    )
    with pytest.raises(NotImplementedError, match="not implemented"):
        adapter.evaluate_perturbation(request)


def test_evaluation_artifact_with_nominal_outcome() -> None:
    nominal = NominalOutcome(run_id="run-1", task_id=TASK_ID, success=True)
    artifact = EvaluationArtifact(
        run_id="run-1",
        task_id=TASK_ID,
        nominal=nominal,
    )
    assert artifact.nominal is nominal
    assert artifact.perturbation is None
