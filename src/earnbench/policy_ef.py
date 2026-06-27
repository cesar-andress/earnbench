"""Policy-level Earned Fraction analysis for stochastic coding agents."""

from __future__ import annotations

import csv
import json
import math
import random
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from earnbench.rank_stability import (
    kendall_tau,
    spearman_rank_correlation,
)
from earnbench.reports import EarnedFractionStatus

REQUIRED_COLUMNS = (
    "agent",
    "model",
    "provider",
    "instance_id",
    "replicate",
    "y0",
    "ef_pi",
    "ef_status",
    "failed_mechanisms",
    "invalid_pi_count",
    "status",
)

POLICY_EF_BY_AGENT_CSV = "policy_ef_by_agent.csv"
POLICY_EF_VARIANCE_CSV = "policy_ef_variance.csv"
POLICY_EF_PAIRWISE_FLIPS_CSV = "policy_ef_pairwise_flips.csv"
POLICY_EF_EXPLOITATION_FRONTIER_CSV = "policy_ef_exploitation_frontier.csv"
POLICY_EF_BOOTSTRAP_JSON = "policy_ef_bootstrap.json"
POLICY_EF_REPORT_MD = "policy_ef_report.md"
OPTIONAL_COLUMNS = ("difficulty",)

BY_AGENT_COLUMNS = (
    "agent",
    "model",
    "provider",
    "instance_count",
    "replicate_count",
    "attempt_count",
    "nominal_pass_rate",
    "earned_pass_rate",
    "mean_ef_given_pass",
    "policy_ef",
    "inter_replicate_variance",
    "instance_level_variance",
    "within_agent_outcome_variance",
    "undefined_ef_on_success_count",
    "nominal_rank",
    "earned_rank",
    "rank_shift",
)

VARIANCE_COLUMNS = (
    "scope",
    "agent",
    "metric",
    "value",
)

PAIRWISE_FLIP_COLUMNS = (
    "agent_a",
    "agent_b",
    "nominal_pass_rate_a",
    "nominal_pass_rate_b",
    "earned_pass_rate_a",
    "earned_pass_rate_b",
    "rank_flip",
    "bootstrap_flip_probability",
)

EXPLOITATION_FRONTIER_COLUMNS = (
    "agent",
    "difficulty_bin",
    "instance_count",
    "attempt_count",
    "nominal_pass_rate",
    "earned_pass_rate",
    "mean_ef_given_pass",
    "policy_ef",
)

DEFAULT_BOOTSTRAP_DRAWS = 10_000
BOOTSTRAP_SEED = 0
CI_LOW_QUANTILE = 0.025
CI_HIGH_QUANTILE = 0.975


@dataclass(frozen=True, slots=True)
class PolicyAttemptRow:
    agent: str
    model: str
    provider: str
    instance_id: str
    replicate: int
    y0: bool
    ef_pi: float | None
    ef_status: str
    failed_mechanisms: tuple[str, ...]
    invalid_pi_count: int
    status: str
    difficulty: str | None
    earned_contribution: float
    undefined_on_success: bool


@dataclass(frozen=True, slots=True)
class PolicyEFReportResult:
    output_dir: Path
    by_agent_csv: Path
    variance_csv: Path
    pairwise_flips_csv: Path
    bootstrap_json: Path
    report_md: Path
    exploitation_frontier_csv: Path | None = None


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "t"}


def _optional_float(value: object) -> float | None:
    if value in ("", None):
        return None
    return float(value)


def _optional_int(value: object) -> int:
    if value in ("", None):
        return 0
    return int(value)


def _parse_failed_mechanisms(raw: object) -> tuple[str, ...]:
    if raw in ("", None):
        return ()
    text = str(raw).strip()
    if not text:
        return ()
    parts = []
    for chunk in text.replace("|", ";").split(";"):
        for piece in chunk.split(","):
            token = piece.strip()
            if token:
                parts.append(token)
    return tuple(dict.fromkeys(parts))


def _earned_contribution(
    *,
    y0: bool,
    ef_pi: float | None,
    ef_status: str,
) -> tuple[float, bool]:
    undefined_on_success = False
    if not y0:
        return 0.0, False
    if ef_status != EarnedFractionStatus.DEFINED.value or ef_pi is None:
        undefined_on_success = True
        return 0.0, True
    return y0 * ef_pi, False


def load_policy_agent_results(path: Path) -> list[PolicyAttemptRow]:
    """Load long-format agent attempt rows after validation."""
    resolved = path.resolve()
    if not resolved.is_file():
        msg = f"agent results file not found: {resolved}"
        raise FileNotFoundError(msg)

    with resolved.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            msg = f"{resolved} is empty or missing a header row"
            raise ValueError(msg)
        missing = [column for column in REQUIRED_COLUMNS if column not in reader.fieldnames]
        if missing:
            msg = f"{resolved} missing required columns: {', '.join(missing)}"
            raise ValueError(msg)

        rows: list[PolicyAttemptRow] = []
        seen_keys: set[tuple[str, str, int]] = set()
        for line_number, raw in enumerate(reader, start=2):
            agent = str(raw.get("agent", "")).strip()
            instance_id = str(raw.get("instance_id", "")).strip()
            replicate_raw = str(raw.get("replicate", "")).strip()
            if not agent or not instance_id or not replicate_raw:
                msg = f"{resolved}:{line_number} missing agent, instance_id, or replicate"
                raise ValueError(msg)
            replicate = int(replicate_raw)
            key = (agent, instance_id, replicate)
            if key in seen_keys:
                msg = (
                    f"{resolved}:{line_number} duplicate row for "
                    f"agent={agent!r} instance_id={instance_id!r} replicate={replicate}"
                )
                raise ValueError(msg)
            seen_keys.add(key)

            y0 = _as_bool(raw.get("y0"))
            ef_pi = _optional_float(raw.get("ef_pi"))
            ef_status = str(raw.get("ef_status", "")).strip()
            earned, undefined_on_success = _earned_contribution(
                y0=y0,
                ef_pi=ef_pi,
                ef_status=ef_status,
            )
            difficulty_raw = raw.get("difficulty")
            difficulty = (
                str(difficulty_raw).strip()
                if difficulty_raw not in ("", None)
                else None
            )
            rows.append(
                PolicyAttemptRow(
                    agent=agent,
                    model=str(raw.get("model", "")).strip(),
                    provider=str(raw.get("provider", "")).strip(),
                    instance_id=instance_id,
                    replicate=replicate,
                    y0=y0,
                    ef_pi=ef_pi,
                    ef_status=ef_status,
                    failed_mechanisms=_parse_failed_mechanisms(raw.get("failed_mechanisms")),
                    invalid_pi_count=_optional_int(raw.get("invalid_pi_count")),
                    status=str(raw.get("status", "")).strip(),
                    difficulty=difficulty,
                    earned_contribution=earned,
                    undefined_on_success=undefined_on_success,
                )
            )

    if not rows:
        msg = f"{resolved} contains no data rows"
        raise ValueError(msg)
    return rows


def _group_rows(
    rows: list[PolicyAttemptRow],
) -> dict[str, list[PolicyAttemptRow]]:
    grouped: dict[str, list[PolicyAttemptRow]] = defaultdict(list)
    for row in rows:
        grouped[row.agent].append(row)
    return grouped


def _average_ranks(values: list[float]) -> list[float]:
    indexed = sorted(enumerate(values), key=lambda item: item[1], reverse=True)
    ranks = [0.0] * len(values)
    index = 0
    while index < len(values):
        end = index
        while end + 1 < len(values) and indexed[end + 1][1] == indexed[index][1]:
            end += 1
        avg_rank = (index + end) / 2.0 + 1.0
        for position in range(index, end + 1):
            ranks[indexed[position][0]] = avg_rank
        index = end + 1
    return ranks


def _rank_map(agents: list[str], values: dict[str, float]) -> dict[str, float]:
    ordered = [values[agent] for agent in agents]
    ranks = _average_ranks(ordered)
    return {agent: ranks[index] for index, agent in enumerate(agents)}


def _variance(values: list[float]) -> float | None:
    if len(values) < 2:
        return 0.0 if values else None
    mean = sum(values) / len(values)
    return sum((value - mean) ** 2 for value in values) / (len(values) - 1)


def _population_variance(values: list[float]) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return 0.0
    mean = sum(values) / len(values)
    return sum((value - mean) ** 2 for value in values) / len(values)


def _quantile(sorted_samples: list[float], q: float) -> float:
    if not sorted_samples:
        return 0.0
    if len(sorted_samples) == 1:
        return sorted_samples[0]
    position = q * (len(sorted_samples) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return sorted_samples[lower]
    weight = position - lower
    return sorted_samples[lower] * (1.0 - weight) + sorted_samples[upper] * weight


def _ci(samples: list[float]) -> dict[str, float | None]:
    if not samples:
        return {"low": None, "high": None, "mean": None}
    ordered = sorted(samples)
    return {
        "low": _quantile(ordered, CI_LOW_QUANTILE),
        "high": _quantile(ordered, CI_HIGH_QUANTILE),
        "mean": sum(ordered) / len(ordered),
    }


def _agent_metrics(agent_rows: list[PolicyAttemptRow]) -> dict[str, Any]:
    attempt_count = len(agent_rows)
    instances = sorted({row.instance_id for row in agent_rows})
    replicates = sorted({row.replicate for row in agent_rows})
    nominal_values = [float(row.y0) for row in agent_rows]
    earned_values = [row.earned_contribution for row in agent_rows]
    pass_ef_values = [
        row.ef_pi
        for row in agent_rows
        if row.y0 and row.ef_status == EarnedFractionStatus.DEFINED.value and row.ef_pi is not None
    ]
    undefined_on_success = sum(int(row.undefined_on_success) for row in agent_rows)

    replicate_earned_rates: list[float] = []
    by_replicate: dict[int, list[PolicyAttemptRow]] = defaultdict(list)
    for row in agent_rows:
        by_replicate[row.replicate].append(row)
    for replicate_rows in by_replicate.values():
        replicate_earned_rates.append(
            sum(item.earned_contribution for item in replicate_rows) / len(replicate_rows)
        )

    instance_earned_rates: list[float] = []
    by_instance: dict[str, list[PolicyAttemptRow]] = defaultdict(list)
    for row in agent_rows:
        by_instance[row.instance_id].append(row)
    for instance_rows in by_instance.values():
        instance_earned_rates.append(
            sum(item.earned_contribution for item in instance_rows) / len(instance_rows)
        )

    model = agent_rows[0].model
    provider = agent_rows[0].provider
    for row in agent_rows[1:]:
        if row.model != model or row.provider != provider:
            model = "mixed"
            provider = "mixed"
            break

    return {
        "agent": agent_rows[0].agent,
        "model": model,
        "provider": provider,
        "instance_count": len(instances),
        "replicate_count": len(replicates),
        "attempt_count": attempt_count,
        "nominal_pass_rate": sum(nominal_values) / attempt_count,
        "earned_pass_rate": sum(earned_values) / attempt_count,
        "mean_ef_given_pass": (
            sum(pass_ef_values) / len(pass_ef_values) if pass_ef_values else None
        ),
        "policy_ef": (
            sum(pass_ef_values) / len(pass_ef_values) if pass_ef_values else None
        ),
        "inter_replicate_variance": _variance(replicate_earned_rates),
        "instance_level_variance": _variance(instance_earned_rates),
        "within_agent_outcome_variance": _population_variance(earned_values),
        "undefined_ef_on_success_count": undefined_on_success,
        "instances": tuple(instances),
        "replicates": tuple(replicates),
    }


def _pairwise_flip_rows(
    agents: list[str],
    nominal_rates: dict[str, float],
    earned_rates: dict[str, float],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for left in range(len(agents)):
        for right in range(left + 1, len(agents)):
            agent_a = agents[left]
            agent_b = agents[right]
            delta_nom = nominal_rates[agent_a] - nominal_rates[agent_b]
            delta_earned = earned_rates[agent_a] - earned_rates[agent_b]
            flip = int(delta_nom * delta_earned < 0)
            rows.append(
                {
                    "agent_a": agent_a,
                    "agent_b": agent_b,
                    "nominal_pass_rate_a": nominal_rates[agent_a],
                    "nominal_pass_rate_b": nominal_rates[agent_b],
                    "earned_pass_rate_a": earned_rates[agent_a],
                    "earned_pass_rate_b": earned_rates[agent_b],
                    "rank_flip": flip,
                }
            )
    return rows


def _exploitation_frontier_rows(rows: list[PolicyAttemptRow]) -> list[dict[str, Any]]:
    if not rows or not any(row.difficulty is not None for row in rows):
        return []

    grouped: dict[tuple[str, str], list[PolicyAttemptRow]] = defaultdict(list)
    for row in rows:
        if row.difficulty is None:
            continue
        grouped[(row.agent, row.difficulty)].append(row)

    frontier_rows: list[dict[str, Any]] = []
    for (agent, difficulty_bin), agent_rows in sorted(grouped.items()):
        metrics = _agent_metrics(agent_rows)
        frontier_rows.append(
            {
                "agent": agent,
                "difficulty_bin": difficulty_bin,
                "instance_count": metrics["instance_count"],
                "attempt_count": metrics["attempt_count"],
                "nominal_pass_rate": metrics["nominal_pass_rate"],
                "earned_pass_rate": metrics["earned_pass_rate"],
                "mean_ef_given_pass": metrics["mean_ef_given_pass"],
                "policy_ef": metrics["policy_ef"],
            }
        )
    return frontier_rows


def _variance_decomposition(rows: list[PolicyAttemptRow]) -> dict[str, float | None]:
    if not rows:
        return {
            "between_agent_variance": None,
            "within_agent_variance": None,
            "total_outcome_variance": None,
        }
    grouped = _group_rows(rows)
    agents = sorted(grouped)
    all_values = [row.earned_contribution for row in rows]
    grand_mean = sum(all_values) / len(all_values)
    total_outcome_variance = _population_variance(all_values)

    if len(agents) < 2:
        within = _population_variance(all_values)
        return {
            "between_agent_variance": 0.0,
            "within_agent_variance": within,
            "total_outcome_variance": total_outcome_variance,
        }

    agent_means = {
        agent: sum(row.earned_contribution for row in grouped[agent]) / len(grouped[agent])
        for agent in agents
    }
    between = sum(
        len(grouped[agent]) * (agent_means[agent] - grand_mean) ** 2 for agent in agents
    ) / len(all_values)
    within = sum(
        (row.earned_contribution - agent_means[row.agent]) ** 2 for row in rows
    ) / len(all_values)
    return {
        "between_agent_variance": between,
        "within_agent_variance": within,
        "total_outcome_variance": total_outcome_variance,
    }


def _bootstrap_payload(
    rows: list[PolicyAttemptRow],
    *,
    bootstrap_draws: int,
    bootstrap_seed: int,
) -> dict[str, Any]:
    grouped = _group_rows(rows)
    agents = sorted(grouped)

    instances_by_agent = {
        agent: sorted({row.instance_id for row in grouped[agent]}) for agent in agents
    }
    if agents:
        reference_instances = instances_by_agent[agents[0]]
        for agent in agents[1:]:
            if instances_by_agent[agent] != reference_instances:
                msg = (
                    "all agents must share the same instance_id set for policy EF analysis; "
                    f"mismatch for agent {agent!r}"
                )
                raise ValueError(msg)
        instance_ids = reference_instances
    else:
        instance_ids = []

    rng = random.Random(bootstrap_seed)

    metric_samples: dict[str, dict[str, list[float]]] = {
        agent: {
            "nominal_pass_rate": [],
            "earned_pass_rate": [],
            "mean_ef_given_pass": [],
            "policy_ef": [],
        }
        for agent in agents
    }
    spearman_samples: list[float] = []
    kendall_samples: list[float] = []
    flip_count_samples: list[int] = []
    flip_prob_by_pair: dict[str, list[int]] = {}

    for _ in range(bootstrap_draws):
        sampled_instances = [
            instance_ids[rng.randrange(len(instance_ids))]
            for _ in range(len(instance_ids))
        ]
        bootstrap_rows: list[PolicyAttemptRow] = []
        for agent in agents:
            agent_lookup = {
                (row.instance_id, row.replicate): row for row in grouped[agent]
            }
            for instance_id in sampled_instances:
                for replicate in sorted({row.replicate for row in grouped[agent]}):
                    bootstrap_rows.append(agent_lookup[(instance_id, replicate)])

        metrics = [
            _agent_metrics(grouped_rows)
            for grouped_rows in _group_rows(bootstrap_rows).values()
        ]
        metrics_by_agent = {item["agent"]: item for item in metrics}
        nominal_rates = {
            agent: metrics_by_agent[agent]["nominal_pass_rate"] for agent in agents
        }
        earned_rates = {
            agent: metrics_by_agent[agent]["earned_pass_rate"] for agent in agents
        }

        for agent in agents:
            item = metrics_by_agent[agent]
            for metric in ("nominal_pass_rate", "earned_pass_rate"):
                metric_samples[agent][metric].append(float(item[metric]))
            for metric in ("mean_ef_given_pass", "policy_ef"):
                value = item[metric]
                if value is not None:
                    metric_samples[agent][metric].append(float(value))

        if len(agents) >= 2:
            nominal_rank = _rank_map(agents, nominal_rates)
            earned_rank = _rank_map(agents, earned_rates)
            rho = spearman_rank_correlation(
                [nominal_rank[agent] for agent in agents],
                [earned_rank[agent] for agent in agents],
            )
            tau = kendall_tau(
                [nominal_rank[agent] for agent in agents],
                [earned_rank[agent] for agent in agents],
            )
            if rho is not None:
                spearman_samples.append(rho)
            if tau is not None:
                kendall_samples.append(tau)

            flips = _pairwise_flip_rows(agents, nominal_rates, earned_rates)
            flip_count_samples.append(sum(int(row["rank_flip"]) for row in flips))
            for row in flips:
                pair_key = f"{row['agent_a']}|{row['agent_b']}"
                flip_prob_by_pair.setdefault(pair_key, []).append(int(row["rank_flip"]))

    by_agent_ci = {
        agent: {
            metric: _ci(samples)
            for metric, samples in metric_samples[agent].items()
        }
        for agent in agents
    }
    pair_flip_probability = {
        pair_key: (sum(samples) / len(samples) if samples else None)
        for pair_key, samples in flip_prob_by_pair.items()
    }

    ers_payload: dict[str, Any] = {
        "spearman": _ci(spearman_samples),
        "kendall_tau": _ci(kendall_samples),
        "pairwise_flip_count": _ci([float(value) for value in flip_count_samples]),
        "pairwise_flip_probability": (
            sum(flip_count_samples) / (len(flip_count_samples) * len(flip_prob_by_pair))
            if flip_count_samples and flip_prob_by_pair
            else None
        ),
        "pairwise_flip_probability_by_pair": {
            pair_key: {
                "probability": probability,
                "ci_low": _quantile(sorted(float(v) for v in samples), CI_LOW_QUANTILE),
                "ci_high": _quantile(sorted(float(v) for v in samples), CI_HIGH_QUANTILE),
            }
            for pair_key, samples in flip_prob_by_pair.items()
            for probability in [sum(samples) / len(samples)]
        },
    }

    return {
        "bootstrap_draws": bootstrap_draws,
        "bootstrap_seed": bootstrap_seed,
        "by_agent": by_agent_ci,
        "ers": ers_payload,
        "pairwise_flip_probability_by_pair": pair_flip_probability,
    }


def analyze_policy_ef(
    rows: list[PolicyAttemptRow],
    *,
    bootstrap_draws: int = DEFAULT_BOOTSTRAP_DRAWS,
    bootstrap_seed: int = BOOTSTRAP_SEED,
) -> dict[str, Any]:
    """Compute policy-level EF metrics from long-format attempt rows."""
    grouped = _group_rows(rows)
    agents = sorted(grouped)
    if len(agents) < 1:
        msg = "policy EF analysis requires at least 1 agent"
        raise ValueError(msg)

    agent_metrics = [_agent_metrics(grouped[agent]) for agent in agents]
    nominal_rates = {item["agent"]: float(item["nominal_pass_rate"]) for item in agent_metrics}
    earned_rates = {item["agent"]: float(item["earned_pass_rate"]) for item in agent_metrics}
    nominal_rank = _rank_map(agents, nominal_rates)
    earned_rank = _rank_map(agents, earned_rates)

    summary_rows: list[dict[str, Any]] = []
    for item in agent_metrics:
        agent = item["agent"]
        summary_rows.append(
            {
                "agent": agent,
                "model": item["model"],
                "provider": item["provider"],
                "instance_count": item["instance_count"],
                "replicate_count": item["replicate_count"],
                "attempt_count": item["attempt_count"],
                "nominal_pass_rate": item["nominal_pass_rate"],
                "earned_pass_rate": item["earned_pass_rate"],
                "mean_ef_given_pass": item["mean_ef_given_pass"],
                "policy_ef": item["policy_ef"],
                "inter_replicate_variance": item["inter_replicate_variance"],
                "instance_level_variance": item["instance_level_variance"],
                "within_agent_outcome_variance": item["within_agent_outcome_variance"],
                "undefined_ef_on_success_count": item["undefined_ef_on_success_count"],
                "nominal_rank": nominal_rank[agent],
                "earned_rank": earned_rank[agent],
                "rank_shift": nominal_rank[agent] - earned_rank[agent],
            }
        )

    variance_rows: list[dict[str, Any]] = []
    decomposition = _variance_decomposition(rows)
    for metric, value in decomposition.items():
        variance_rows.append(
            {"scope": "global", "agent": "", "metric": metric, "value": value}
        )
    for item in agent_metrics:
        for metric in (
            "inter_replicate_variance",
            "instance_level_variance",
            "within_agent_outcome_variance",
        ):
            variance_rows.append(
                {
                    "scope": "agent",
                    "agent": item["agent"],
                    "metric": metric,
                    "value": item[metric],
                }
            )

    ers_payload: dict[str, Any]
    bootstrap_payload = _bootstrap_payload(
        rows,
        bootstrap_draws=bootstrap_draws,
        bootstrap_seed=bootstrap_seed,
    )
    exploitation_frontier = _exploitation_frontier_rows(rows)

    if len(agents) >= 2:
        flips = _pairwise_flip_rows(agents, nominal_rates, earned_rates)
        flip_count = sum(int(row["rank_flip"]) for row in flips)
        pair_count = len(flips)
        spearman = spearman_rank_correlation(
            [nominal_rank[agent] for agent in agents],
            [earned_rank[agent] for agent in agents],
        )
        kendall = kendall_tau(
            [nominal_rank[agent] for agent in agents],
            [earned_rank[agent] for agent in agents],
        )
        ers_payload = {
            "spearman": spearman,
            "kendall_tau": kendall,
            "pairwise_flip_count": flip_count,
            "pairwise_flip_rate": flip_count / pair_count if pair_count else None,
            "pairwise_flips": flips,
            "mean_abs_rank_shift": (
                sum(abs(row["rank_shift"]) for row in summary_rows) / len(summary_rows)
            ),
        }
    else:
        ers_payload = {
            "spearman": None,
            "kendall_tau": None,
            "pairwise_flip_count": None,
            "pairwise_flip_rate": None,
            "pairwise_flips": [],
            "mean_abs_rank_shift": None,
        }

    return {
        "schema_version": "earnbench.policy_ef.v1",
        "agent_count": len(agents),
        "attempt_count": len(rows),
        "instance_count": len({row.instance_id for row in rows}),
        "replicate_count": len({row.replicate for row in rows}),
        "bootstrap_draws": bootstrap_draws,
        "bootstrap_seed": bootstrap_seed,
        "by_agent": summary_rows,
        "variance_rows": variance_rows,
        "variance_decomposition": decomposition,
        "ers": ers_payload,
        "exploitation_frontier": exploitation_frontier,
        "has_difficulty": bool(exploitation_frontier),
        "bootstrap": bootstrap_payload,
    }


def _pairwise_flip_export_rows(
    flips: list[dict[str, Any]],
    *,
    bootstrap_flip_probability_by_pair: dict[str, float | None] | None = None,
) -> list[dict[str, Any]]:
    export_rows: list[dict[str, Any]] = []
    for row in flips:
        pair_key = f"{row['agent_a']}|{row['agent_b']}"
        probability = None
        if bootstrap_flip_probability_by_pair is not None:
            probability = bootstrap_flip_probability_by_pair.get(pair_key)
        export_rows.append(
            {
                **row,
                "bootstrap_flip_probability": probability,
            }
        )
    return export_rows


def _write_csv(
    path: Path,
    fieldnames: tuple[str, ...],
    rows: list[dict[str, Any]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            formatted: dict[str, Any] = {}
            for key in fieldnames:
                value = row.get(key, "")
                if isinstance(value, float):
                    formatted[key] = f"{value:.6f}"
                elif value is None:
                    formatted[key] = ""
                else:
                    formatted[key] = value
            writer.writerow(formatted)


def _format_float(value: float | None, *, precision: int = 6) -> str:
    if value is None:
        return "—"
    return f"{value:.{precision}f}"


def render_policy_ef_report(payload: dict[str, Any]) -> str:
    """Render markdown report for policy-level EF analysis."""
    lines = [
        "# Policy-Level Earned Fraction Report",
        "",
        "## Purpose",
        "",
        "Outcome-level **EF@Π** remains defined on each fixed patch attempt. "
        "This report aggregates **policy-level** estimands over stochastic replicates: "
        "expected earned pass rate, conditional policy EF, variance decomposition, and "
        "ERS using expected earned pass rates.",
        "",
        "## Overview",
        "",
        f"- **Agents:** {payload['agent_count']}",
        f"- **Instances:** {payload['instance_count']}",
        f"- **Replicates:** {payload['replicate_count']}",
        f"- **Attempts:** {payload['attempt_count']}",
        f"- **Bootstrap draws:** {payload['bootstrap_draws']} "
        f"(seed={payload['bootstrap_seed']})",
        "",
        "## Agent summary",
        "",
        "| Agent | Nominal | Earned pass | EF|pass | Policy EF | Inter-run var | "
        "Inst var | Rank nom | Rank earned | Shift |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in payload["by_agent"]:
        lines.append(
            "| {agent} | {nominal_pass_rate:.4f} | {earned_pass_rate:.4f} | "
            "{mean_ef_given_pass} | {policy_ef} | {inter_replicate_variance} | "
            "{instance_level_variance} | {nominal_rank:.2f} | {earned_rank:.2f} | "
            "{rank_shift:.2f} |".format(
                agent=row["agent"],
                nominal_pass_rate=row["nominal_pass_rate"],
                earned_pass_rate=row["earned_pass_rate"],
                mean_ef_given_pass=_format_float(row["mean_ef_given_pass"], precision=4),
                policy_ef=_format_float(row["policy_ef"], precision=4),
                inter_replicate_variance=_format_float(
                    row["inter_replicate_variance"],
                    precision=6,
                ),
                instance_level_variance=_format_float(
                    row["instance_level_variance"],
                    precision=6,
                ),
                nominal_rank=row["nominal_rank"],
                earned_rank=row["earned_rank"],
                rank_shift=row["rank_shift"],
            )
        )

    lines.extend(["", "## Variance decomposition", ""])
    for metric, value in payload["variance_decomposition"].items():
        lines.append(f"- **{metric}:** {_format_float(value)}")

    ers = payload["ers"]
    lines.extend(
        [
            "",
            "## ERS (expected earned pass rate)",
            "",
            "| Metric | Value |",
            "| --- | --- |",
            (f"| Spearman ρ | {_format_float(ers['spearman'])} |"),
            (f"| Kendall τ | {_format_float(ers['kendall_tau'])} |"),
            (
                f"| Pairwise flips | {ers['pairwise_flip_count']} / "
                f"{payload['agent_count'] * (payload['agent_count'] - 1) // 2 if payload['agent_count'] >= 2 else 0} |"
            ),
            (f"| Mean |rank shift| | {_format_float(ers['mean_abs_rank_shift'])} |"),
        ]
    )

    bootstrap = payload.get("bootstrap")
    if bootstrap is not None:
        lines.extend(["", "## Bootstrap confidence intervals", ""])
        for agent, metrics in bootstrap["by_agent"].items():
            lines.append(f"### {agent}")
            for metric, ci in metrics.items():
                if not ci["mean"]:
                    continue
                lines.append(
                    f"- **{metric}:** {_format_float(ci['mean'], precision=4)} "
                    f"[{_format_float(ci['low'], precision=4)}, "
                    f"{_format_float(ci['high'], precision=4)}]"
                )
        lines.extend(["", "### ERS bootstrap", ""])
        spearman_ci = bootstrap["ers"]["spearman"]
        lines.append(
            f"- **Spearman ρ:** {_format_float(spearman_ci['mean'])} "
            f"[{_format_float(spearman_ci['low'])}, {_format_float(spearman_ci['high'])}]"
        )
        flip_prob = bootstrap["ers"]["pairwise_flip_probability"]
        lines.append(f"- **Mean pairwise flip probability:** {_format_float(flip_prob)}")
        pair_probs = bootstrap["ers"]["pairwise_flip_probability_by_pair"]
        if pair_probs:
            lines.extend(["", "### Pairwise flip probability", ""])
            for pair_key, stats in sorted(pair_probs.items()):
                agent_a, agent_b = pair_key.split("|", maxsplit=1)
                lines.append(
                    f"- **{agent_a} vs {agent_b}:** "
                    f"{_format_float(stats['probability'])} "
                    f"[{_format_float(stats['ci_low'])}, {_format_float(stats['ci_high'])}]"
                )

    if payload.get("exploitation_frontier"):
        lines.extend(
            [
                "",
                "## Exploitation frontier (by agent and difficulty bin)",
                "",
                "| Agent | Difficulty | Nominal | Earned pass | EF|pass | Policy EF |",
                "| --- | --- | --- | --- | --- | --- |",
            ]
        )
        for row in payload["exploitation_frontier"]:
            lines.append(
                "| {agent} | {difficulty_bin} | {nominal_pass_rate:.4f} | "
                "{earned_pass_rate:.4f} | {mean_ef_given_pass} | {policy_ef} |".format(
                    agent=row["agent"],
                    difficulty_bin=row["difficulty_bin"],
                    nominal_pass_rate=row["nominal_pass_rate"],
                    earned_pass_rate=row["earned_pass_rate"],
                    mean_ef_given_pass=_format_float(row["mean_ef_given_pass"], precision=4),
                    policy_ef=_format_float(row["policy_ef"], precision=4),
                )
            )

    lines.extend(
        [
            "",
            "## Definitions",
            "",
            "- **Outcome-level EF@Π:** existing per-attempt `ef_pi` on a fixed patch.",
            "- **Policy-level EF:** E[EF@Π | Y₀=1] averaged over replicates (alias: mean EF|pass).",
            "- **Earned pass rate:** mean(Y₀ · EF@Π) over instances and replicates.",
            "- **Inter-run variance:** variance of replicate-level earned pass rates.",
            "- **Instance-level variance:** variance of instance-level mean earned contributions.",
            "",
        ]
    )
    return "\n".join(lines)


def generate_policy_ef_report(
    agent_results_path: Path,
    output_dir: Path,
    *,
    bootstrap_draws: int = DEFAULT_BOOTSTRAP_DRAWS,
    bootstrap_seed: int = BOOTSTRAP_SEED,
) -> PolicyEFReportResult:
    """Load long-format agent results and write policy EF artifacts."""
    rows = load_policy_agent_results(agent_results_path)
    payload = analyze_policy_ef(
        rows,
        bootstrap_draws=bootstrap_draws,
        bootstrap_seed=bootstrap_seed,
    )

    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    by_agent_csv = output_dir / POLICY_EF_BY_AGENT_CSV
    variance_csv = output_dir / POLICY_EF_VARIANCE_CSV
    pairwise_flips_csv = output_dir / POLICY_EF_PAIRWISE_FLIPS_CSV
    bootstrap_json = output_dir / POLICY_EF_BOOTSTRAP_JSON
    report_md = output_dir / POLICY_EF_REPORT_MD

    _write_csv(by_agent_csv, BY_AGENT_COLUMNS, payload["by_agent"])
    _write_csv(variance_csv, VARIANCE_COLUMNS, payload["variance_rows"])

    bootstrap_payload = payload.pop("bootstrap")
    flip_probability_by_pair = bootstrap_payload.get("pairwise_flip_probability_by_pair")
    pairwise_rows = _pairwise_flip_export_rows(
        payload["ers"].get("pairwise_flips", []),
        bootstrap_flip_probability_by_pair=flip_probability_by_pair,
    )
    _write_csv(pairwise_flips_csv, PAIRWISE_FLIP_COLUMNS, pairwise_rows)

    exploitation_frontier_csv: Path | None = None
    if payload.get("exploitation_frontier"):
        exploitation_frontier_csv = output_dir / POLICY_EF_EXPLOITATION_FRONTIER_CSV
        _write_csv(
            exploitation_frontier_csv,
            EXPLOITATION_FRONTIER_COLUMNS,
            payload["exploitation_frontier"],
        )
    bootstrap_payload["schema_version"] = "earnbench.policy_ef_bootstrap.v1"
    bootstrap_payload["agent_count"] = payload["agent_count"]
    bootstrap_payload["instance_count"] = payload["instance_count"]
    bootstrap_payload["replicate_count"] = payload["replicate_count"]
    with bootstrap_json.open("w", encoding="utf-8") as handle:
        json.dump(bootstrap_payload, handle, indent=2, sort_keys=True)
        handle.write("\n")

    report_md.write_text(render_policy_ef_report(payload), encoding="utf-8")

    return PolicyEFReportResult(
        output_dir=output_dir,
        by_agent_csv=by_agent_csv,
        variance_csv=variance_csv,
        pairwise_flips_csv=pairwise_flips_csv,
        bootstrap_json=bootstrap_json,
        report_md=report_md,
        exploitation_frontier_csv=exploitation_frontier_csv,
    )


__all__ = [
    "BY_AGENT_COLUMNS",
    "DEFAULT_BOOTSTRAP_DRAWS",
    "EXPLOITATION_FRONTIER_COLUMNS",
    "OPTIONAL_COLUMNS",
    "PAIRWISE_FLIP_COLUMNS",
    "POLICY_EF_BOOTSTRAP_JSON",
    "POLICY_EF_BY_AGENT_CSV",
    "POLICY_EF_EXPLOITATION_FRONTIER_CSV",
    "POLICY_EF_PAIRWISE_FLIPS_CSV",
    "POLICY_EF_REPORT_MD",
    "POLICY_EF_VARIANCE_CSV",
    "REQUIRED_COLUMNS",
    "PolicyAttemptRow",
    "PolicyEFReportResult",
    "analyze_policy_ef",
    "generate_policy_ef_report",
    "load_policy_agent_results",
    "render_policy_ef_report",
]
