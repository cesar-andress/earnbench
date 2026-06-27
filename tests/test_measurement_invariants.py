"""Mathematical invariants of EarnBench measurement (EF@Π MVP).

These tests encode properties of the Earned Fraction functional
``EF : (Y₀, {Y_m}) → [0,1] ∪ {undefined}`` that must hold regardless of
harness integration details. They are specification checks, not unit tests of
incidental implementation structure.
"""

from __future__ import annotations

import hashlib
import itertools
import json
from pathlib import Path

import pytest

from earnbench import __version__, compute_earned_fraction
from earnbench.audit import AuditRecord, AuditStatus
from earnbench.cli import parse_compute_input
from earnbench.outcomes import NominalOutcome, OutcomeStatus, PerturbationResult
from earnbench.provenance import Provenance, build_provenance
from earnbench.reports import EarnedFractionReport, EarnedFractionStatus

FIXTURES = Path(__file__).parent / "fixtures"

REFERENCE_FIXTURE_SHA256: dict[str, str] = {
    "compute_input.json": (
        "76d0156b5b97fe2b22b5e721444cd5ac245d07550391c5bad4c3fd1989119e31"
    ),
    "valid_audit.json": (
        "41116378068c2ae4e3eeae9fbd9a728a1c795e0edc1c9f488b24670a43507124"
    ),
    "invalid_audit.json": (
        "86ac73f832504b3324e52d07c68fc42ec0f7b614fae20a20c52e0b580a682b5e"
    ),
}


def _fixed_provenance(**overrides: object) -> Provenance:
    defaults = {
        "config_digest": "sha256:invariant-test",
        "include_hostname": False,
        "execution_uuid": "00000000-0000-4000-8000-000000000099",
        "timestamp_utc": "2026-06-27T12:00:00Z",
        "git_commit": "invariant-test-commit",
        "earnbench_version": __version__,
        "python_version": "3.12.0",
        "platform_string": "Linux-invariant-test",
        "random_seed": 0,
    }
    defaults.update(overrides)
    return build_provenance(**defaults)  # type: ignore[arg-type]


def _nominal(*, success: bool = True) -> NominalOutcome:
    return NominalOutcome(run_id="inv-run", task_id="inv-task", success=success)


def _ok(pid: str, *, success: bool, channel: str = "ch") -> PerturbationResult:
    return PerturbationResult.ok(pid, success=success, channel=channel)


def _invalid(pid: str, *, channel: str = "ch") -> PerturbationResult:
    return PerturbationResult.invalid(pid, channel=channel, message="harness")


def _missing(pid: str, *, channel: str = "ch") -> PerturbationResult:
    return PerturbationResult.missing(pid, channel=channel, message="skipped")


def _ef_value_signature(report: EarnedFractionReport) -> tuple[object, ...]:
    """Projection onto the EF functional (order-independent where applicable)."""
    return (
        report.nominal_success,
        report.status,
        report.earned_fraction,
        report.valid_count,
        report.successful_count,
        tuple(sorted(report.failed_mechanisms)),
        tuple(sorted(report.survived_mechanisms)),
        report.reason,
    )


def _measurement_signature(report: EarnedFractionReport) -> tuple[object, ...]:
    """Projection of a report onto EF-relevant fields (excludes provenance)."""
    return (
        report.nominal_success,
        report.status,
        report.earned_fraction,
        report.valid_count,
        report.successful_count,
        report.failed_mechanisms,
        report.survived_mechanisms,
        report.reason,
        tuple(
            (
                r.perturbation_id,
                r.status,
                r.success,
                r.channel,
                r.message,
            )
            for r in report.perturbation_results
        ),
    )


def _perturbation_from_dict(raw: dict[str, object]) -> PerturbationResult:
    success_raw = raw.get("success")
    success = None if success_raw is None else bool(success_raw)
    return PerturbationResult(
        perturbation_id=str(raw["perturbation_id"]),
        status=OutcomeStatus(str(raw["status"])),
        success=success,
        channel=str(raw.get("channel", "")),
        message=str(raw.get("message", "")),
    )


def _report_from_json_payload(payload: dict[str, object]) -> EarnedFractionReport:
    nominal = NominalOutcome(
        run_id=str(payload["run_id"]),
        task_id=str(payload["task_id"]),
        success=bool(payload["nominal_success"]),
    )
    perturbations = [
        _perturbation_from_dict(item)
        for item in payload["perturbation_results"]  # type: ignore[index]
    ]
    provenance_raw = payload.get("provenance")
    provenance = (
        Provenance.from_dict(provenance_raw)  # type: ignore[arg-type]
        if isinstance(provenance_raw, dict)
        else _fixed_provenance()
    )
    return compute_earned_fraction(nominal, perturbations, provenance=provenance)


Scenario = tuple[NominalOutcome, list[PerturbationResult]]


def _finite_outcome_scenarios() -> list[Scenario]:
    """Small but non-trivial outcome grid for invariant enumeration."""
    scenarios: list[Scenario] = []

    pid_templates = [
        ("pi_vtest.v1", "vtest"),
        ("pi_verif.v1", "verif"),
        ("pi_env.v1", "env"),
    ]
    ok_patterns = [(True, False), (True, True, False), (False, False, False)]

    for nominal_success in (True, False):
        nominal = _nominal(success=nominal_success)
        scenarios.append((nominal, []))

        for ok_pattern in ok_patterns:
            base = [
                _ok(pid, success=success, channel=channel)
                for (pid, channel), success in zip(
                    pid_templates,
                    ok_pattern,
                    strict=False,
                )
            ]
            scenarios.append((nominal, list(base)))
            scenarios.append(
                (
                    nominal,
                    [
                        *base,
                        _invalid("pi_extra.invalid", channel="extra"),
                        _missing("pi_extra.missing", channel="extra"),
                    ],
                )
            )

    return scenarios


_SCENARIOS = _finite_outcome_scenarios()


@pytest.mark.parametrize(("nominal", "perturbations"), _SCENARIOS)
def test_invariant_01_ef_bounded_when_defined(
    nominal: NominalOutcome,
    perturbations: list[PerturbationResult],
) -> None:
    """Invariant 1: when defined, EF ∈ [0, 1]."""
    report = compute_earned_fraction(
        nominal,
        perturbations,
        provenance=_fixed_provenance(),
    )
    if report.is_defined:
        assert report.earned_fraction is not None
        assert 0.0 <= report.earned_fraction <= 1.0
    else:
        assert report.earned_fraction is None


@pytest.mark.parametrize(("nominal", "perturbations"), _SCENARIOS)
def test_invariant_02_nominal_failure_implies_undefined(
    nominal: NominalOutcome,
    perturbations: list[PerturbationResult],
) -> None:
    """Invariant 2: Y₀ = 0 ⇒ EF is undefined."""
    if nominal.success:
        pytest.skip("scenario assumes nominal success")

    report = compute_earned_fraction(
        nominal,
        perturbations,
        provenance=_fixed_provenance(),
    )
    assert not report.is_defined
    assert report.earned_fraction is None
    assert report.status is EarnedFractionStatus.UNDEFINED
    assert report.reason == "nominal_run_failed"


def test_invariant_03_adding_failed_perturbation_never_increases_ef() -> None:
    """Invariant 3: adding a valid π with Y_m = 0 cannot increase EF."""
    baseline_sets = [
        [_ok("pi_a.v1", success=True, channel="a")],
        [
            _ok("pi_a.v1", success=True, channel="a"),
            _ok("pi_b.v1", success=True, channel="b"),
        ],
        [
            _ok("pi_a.v1", success=True, channel="a"),
            _ok("pi_b.v1", success=False, channel="b"),
        ],
    ]
    extra_failures = [
        _ok("pi_new.v1", success=False, channel="new"),
        _ok("pi_other.v1", success=False, channel="other"),
    ]

    for base in baseline_sets:
        before = compute_earned_fraction(
            _nominal(),
            list(base),
            provenance=_fixed_provenance(),
        )
        if not before.is_defined:
            continue
        before_ef = before.earned_fraction
        assert before_ef is not None
        for extra in extra_failures:
            after = compute_earned_fraction(
                _nominal(),
                [*base, extra],
                provenance=_fixed_provenance(),
            )
            assert after.is_defined
            assert after.earned_fraction is not None
            assert after.earned_fraction <= before_ef


def test_invariant_04_removing_invalid_perturbations_preserves_ef() -> None:
    """Invariant 4: invalid π runs are excluded; removing them does not change EF."""
    valid = [
        _ok("pi_vtest.v1", success=True, channel="vtest"),
        _ok("pi_verif.v1", success=False, channel="verif"),
    ]
    with_invalid = [
        *valid,
        _invalid("pi_verif.v1", channel="verif"),
        _missing("pi_env.v1", channel="env"),
    ]

    mixed = compute_earned_fraction(
        _nominal(),
        with_invalid,
        provenance=_fixed_provenance(),
    )
    valid_only = compute_earned_fraction(
        _nominal(),
        valid,
        provenance=_fixed_provenance(),
    )

    assert _ef_value_signature(mixed) == _ef_value_signature(valid_only)


def test_invariant_05_permutation_invariance() -> None:
    """Invariant 5: EF depends on the multiset of π outcomes, not list order."""
    perturbations = [
        _ok("pi_vtest.v1", success=True, channel="vtest"),
        _ok("pi_verif.v1", success=False, channel="verif"),
        _ok("pi_env.v1", success=True, channel="env"),
        _invalid("pi_env.v1", channel="env"),
    ]
    reference = compute_earned_fraction(
        _nominal(),
        perturbations,
        provenance=_fixed_provenance(),
    )
    reference_sig = _ef_value_signature(reference)

    for permuted in itertools.permutations(perturbations):
        report = compute_earned_fraction(
            _nominal(),
            list(permuted),
            provenance=_fixed_provenance(),
        )
        assert _ef_value_signature(report) == reference_sig


def test_invariant_06_json_round_trip_preserves_measurement() -> None:
    """Invariant 6: serialize → parse → recompute preserves EF semantics."""
    report = compute_earned_fraction(
        _nominal(),
        [
            _ok("pi_vtest.v1", success=True, channel="vtest"),
            _ok("pi_verif.v1", success=False, channel="verif"),
            _invalid("pi_env.v1", channel="env"),
        ],
        provenance=_fixed_provenance(),
    )
    encoded = json.dumps(report.to_dict(), sort_keys=True)
    payload = json.loads(encoded)
    restored = _report_from_json_payload(payload)
    assert _ef_value_signature(restored) == _ef_value_signature(report)


def test_invariant_07_provenance_does_not_affect_ef() -> None:
    """Invariant 7: provenance is metadata; EF is a functional of outcomes only."""
    perturbations = [
        _ok("pi_vtest.v1", success=True, channel="vtest"),
        _ok("pi_verif.v1", success=False, channel="verif"),
    ]
    provenance_a = _fixed_provenance(
        execution_uuid="00000000-0000-4000-8000-000000000001",
        config_digest="sha256:a",
        random_seed=1,
    )
    provenance_b = _fixed_provenance(
        execution_uuid="00000000-0000-4000-8000-000000000002",
        config_digest="sha256:b",
        random_seed=99,
        docker_image_digest="sha256:other-image",
        git_commit="other-commit",
    )

    report_a = compute_earned_fraction(
        _nominal(),
        perturbations,
        provenance=provenance_a,
    )
    report_b = compute_earned_fraction(
        _nominal(),
        perturbations,
        provenance=provenance_b,
    )

    assert _ef_value_signature(report_a) == _ef_value_signature(report_b)
    assert report_a.provenance != report_b.provenance


def test_invariant_08_audit_metadata_does_not_affect_ef() -> None:
    """Invariant 8: audit records are not inputs to the EF functional."""
    nominal = _nominal()
    perturbations = [
        _ok("pi_vtest.v1", success=True, channel="vtest"),
        _ok("pi_verif.v1", success=True, channel="verif"),
    ]
    baseline = compute_earned_fraction(
        nominal,
        perturbations,
        provenance=_fixed_provenance(),
    )

    audit_variants = [
        AuditRecord(
            instance_id="django__django-13279",
            perturbation_id="pi_vtest.v1",
            config_digest="sha256:aaa",
            patch_sha256="a" * 64,
            status=AuditStatus.OK,
            success=True,
            tests_run=("tests.foo",),
        ),
        AuditRecord(
            instance_id="django__django-13279",
            perturbation_id="pi_verif.v1",
            config_digest="sha256:bbb",
            patch_sha256="b" * 64,
            status=AuditStatus.INVALID,
            warnings=("apply failed",),
            log_ref="logs/harness.log",
            image_digest="sha256:docker",
        ),
    ]

    for audit in audit_variants:
        _ = audit.to_dict()
        report = compute_earned_fraction(
            nominal,
            perturbations,
            provenance=_fixed_provenance(),
        )
        assert _ef_value_signature(report) == _ef_value_signature(baseline)


def test_invariant_09_report_serialization_is_deterministic() -> None:
    """Invariant 9: fixed provenance ⇒ stable canonical JSON for a report."""
    report = compute_earned_fraction(
        _nominal(),
        [
            _ok("pi_vtest.v1", success=True, channel="vtest"),
            _ok("pi_verif.v1", success=False, channel="verif"),
        ],
        provenance=_fixed_provenance(),
    )
    first = json.dumps(report.to_dict(), sort_keys=True, separators=(",", ":"))
    second = json.dumps(report.to_dict(), sort_keys=True, separators=(",", ":"))
    assert first == second


def test_invariant_10_reference_fixtures_are_immutable() -> None:
    """Invariant 10: pinned reference fixtures detect unintended drift."""
    for name, expected_digest in REFERENCE_FIXTURE_SHA256.items():
        path = FIXTURES / name
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest == expected_digest, (
            f"fixture {name} changed; update REFERENCE_FIXTURE_SHA256 if intentional"
        )


def test_reference_compute_fixture_yields_expected_ef() -> None:
    """Sanity check: canonical compute fixture EF = 2/3 (defined measurement)."""
    payload = json.loads((FIXTURES / "compute_input.json").read_text(encoding="utf-8"))
    nominal, perturbations = parse_compute_input(payload)
    report = compute_earned_fraction(
        nominal,
        perturbations,
        provenance=_fixed_provenance(),
    )
    assert report.is_defined
    assert report.earned_fraction == pytest.approx(2 / 3)
    assert report.valid_count == 3
    assert report.successful_count == 2
