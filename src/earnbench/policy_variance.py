"""Phase C′ policy-level earned-credit variance decomposition."""

from __future__ import annotations

import csv
import json
import math
import random
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from earnbench.rank_stability import spearman_rank_correlation
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

OPTIONAL_COLUMNS = (
    "difficulty_bin",
    "patch_loc",
    "files_touched",
    "trajectory_tokens",
    "wall_time_seconds",
)

COLUMN_ALIASES = {
    "failed": "failed_mechanisms",
    "difficulty": "difficulty_bin",
}

POLICY_VARIANCE_BY_AGENT_CSV = "policy_variance_by_agent.csv"
POLICY_VARIANCE_BY_AGENT_INSTANCE_CSV = "policy_variance_by_agent_instance.csv"
POLICY_VARIANCE_COMPONENTS_CSV = "policy_variance_components.csv"
POLICY_VARIANCE_BOOTSTRAP_JSON = "policy_variance_bootstrap.json"
POLICY_VARIANCE_PAIRWISE_FLIPS_CSV = "policy_variance_pairwise_flips.csv"
EXPLOITATION_FRONTIER_CSV = "exploitation_frontier.csv"
POLICY_VARIANCE_REPORT_MD = "policy_variance_report.md"

BY_AGENT_COLUMNS = (
    "agent",
    "model",
    "provider",
    "instance_count",
    "replicate_count",
    "attempt_count",
    "nominal_pass_rate",
    "earned_pass_rate",
    "earned_pass_rate_undefined_as_zero",
    "mean_ef_conditional_on_pass",
    "undefined_rate",
    "undefined_on_success_count",
    "invalid_pi_rate_mean",
    "within_cell_variance",
    "between_instance_variance",
    "nominal_rank",
    "single_run_earned_rank",
    "policy_earned_rank",
)

BY_AGENT_INSTANCE_COLUMNS = (
    "agent",
    "instance_id",
    "attempt_count",
    "nominal_success_count",
    "earned_score_mean",
    "earned_score_variance",
    "ef_conditional_mean",
    "ef_conditional_variance",
    "dominant_failed_mechanism",
    "undefined_on_success_count",
)

COMPONENT_COLUMNS = (
    "scope",
    "agent",
    "component",
    "value",
    "fraction_of_total_variance",
)

PAIRWISE_FLIP_COLUMNS = (
    "agent_a",
    "agent_b",
    "ranking_a",
    "ranking_b",
    "rate_a",
    "rate_b",
    "rank_flip",
    "bootstrap_flip_probability",
)

FRONTIER_COLUMNS = (
    "agent",
    "frontier_bin",
    "bin_type",
    "instance_count",
    "attempt_count",
    "nominal_pass_rate",
    "earned_score_mean",
    "earned_pass_rate",
    "mean_ef_conditional_on_pass",
)

DEFAULT_BOOTSTRAP_DRAWS = 10_000
BOOTSTRAP_SEED = 0
CI_LOW_QUANTILE = 0.025
CI_HIGH_QUANTILE = 0.975


@dataclass(frozen=True, slots=True)
class PolicyVarianceRow:
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
    difficulty_bin: str | None
    patch_loc: str | None
    files_touched: str | None
    trajectory_tokens: str | None
    wall_time_seconds: float | None
    earned_score: float
    earned_score_undefined_as_zero: float
    undefined_on_success: bool
    ef_defined_on_success: bool


@dataclass(frozen=True, slots=True)
class PolicyVarianceReportResult:
    output_dir: Path
    by_agent_csv: Path
    by_agent_instance_csv: Path
    components_csv: Path
    bootstrap_json: Path
    pairwise_flips_csv: Path
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


def _optional_str(value: object) -> str | None:
    if value in ("", None):
        return None
    text = str(value).strip()
    return text or None


def _parse_failed_mechanisms(raw: object) -> tuple[str, ...]:
    if raw in ("", None):
        return ()
    text = str(raw).strip()
    if not text:
        return ()
    parts: list[str] = []
    for chunk in text.replace("|", ";").split(";"):
        for piece in chunk.split(","):
            token = piece.strip()
            if token:
                parts.append(token)
    return tuple(dict.fromkeys(parts))


def _score_policies(
    *,
    y0: bool,
    ef_pi: float | None,
    ef_status: str,
) -> tuple[float, float, bool, bool]:
    if not y0:
        return 0.0, 0.0, False, False
    if ef_status != EarnedFractionStatus.DEFINED.value or ef_pi is None:
        return 0.0, 0.0, True, False
    earned = float(y0) * float(ef_pi)
    return earned, earned, False, True


def _normalize_fieldnames(fieldnames: tuple[str, ...] | list[str]) -> list[str]:
    normalized: list[str] = []
    for name in fieldnames:
        canonical = COLUMN_ALIASES.get(name, name)
        if canonical in normalized:
            msg = f"duplicate logical column {canonical!r} after alias normalization"
            raise ValueError(msg)
        normalized.append(canonical)
    return normalized


def _row_with_canonical_columns(raw: dict[str, str | None]) -> dict[str, str | None]:
    return {
        COLUMN_ALIASES.get(key, key): value
        for key, value in raw.items()
        if key is not None
    }


def load_policy_variance_results(path: Path) -> list[PolicyVarianceRow]:
    """Load long-format Phase C′ attempt rows."""
    resolved = path.resolve()
    if not resolved.is_file():
        msg = f"agent results file not found: {resolved}"
        raise FileNotFoundError(msg)

    with resolved.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            msg = f"{resolved} is empty or missing a header row"
            raise ValueError(msg)
        fieldnames = _normalize_fieldnames(tuple(reader.fieldnames))
        missing = [column for column in REQUIRED_COLUMNS if column not in fieldnames]
        if missing:
            msg = f"{resolved} missing required columns: {', '.join(missing)}"
            raise ValueError(msg)

        rows: list[PolicyVarianceRow] = []
        seen: set[tuple[str, str, int]] = set()
        for line_number, raw_row in enumerate(reader, start=2):
            raw = _row_with_canonical_columns(raw_row)
            agent = str(raw.get("agent", "")).strip()
            instance_id = str(raw.get("instance_id", "")).strip()
            replicate_raw = str(raw.get("replicate", "")).strip()
            if not agent or not instance_id or not replicate_raw:
                msg = f"{resolved}:{line_number} missing agent, instance_id, or replicate"
                raise ValueError(msg)
            replicate = int(replicate_raw)
            key = (agent, instance_id, replicate)
            if key in seen:
                msg = (
                    f"{resolved}:{line_number} duplicate row for "
                    f"agent={agent!r} instance_id={instance_id!r} replicate={replicate}"
                )
                raise ValueError(msg)
            seen.add(key)

            y0 = _as_bool(raw.get("y0"))
            ef_pi = _optional_float(raw.get("ef_pi"))
            ef_status = str(raw.get("ef_status", "")).strip()
            earned_score, earned_zero, undefined_on_success, ef_defined = _score_policies(
                y0=y0,
                ef_pi=ef_pi,
                ef_status=ef_status,
            )
            rows.append(
                PolicyVarianceRow(
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
                    difficulty_bin=_optional_str(raw.get("difficulty_bin")),
                    patch_loc=_optional_str(raw.get("patch_loc")),
                    files_touched=_optional_str(raw.get("files_touched")),
                    trajectory_tokens=_optional_str(raw.get("trajectory_tokens")),
                    wall_time_seconds=_optional_float(raw.get("wall_time_seconds")),
                    earned_score=earned_score,
                    earned_score_undefined_as_zero=earned_zero,
                    undefined_on_success=undefined_on_success,
                    ef_defined_on_success=ef_defined,
                )
            )

    if not rows:
        msg = f"{resolved} contains no data rows"
        raise ValueError(msg)
    return rows


def _population_variance(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return sum((value - mean) ** 2 for value in values) / len(values)


def _sample_variance(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return sum((value - mean) ** 2 for value in values) / (len(values) - 1)


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


def _decompose_agent_variance(rows: list[PolicyVarianceRow]) -> dict[str, float]:
    """Transparent ANOVA-style decomposition on earned_score for one agent."""
    if not rows:
        return {
            "within_cell_variance": 0.0,
            "between_instance_variance": 0.0,
            "total_variance": 0.0,
            "residual_or_missing_component": 0.0,
        }

    scores = [row.earned_score for row in rows]
    n = len(scores)
    grand_mean = sum(scores) / n
    total_variance = _population_variance(scores)

    by_instance: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        by_instance[row.instance_id].append(row.earned_score)

    within_parts: list[tuple[float, int]] = []
    between_instance_sum = 0.0
    agent_mean = grand_mean
    for instance_scores in by_instance.values():
        if len(instance_scores) >= 2:
            within_parts.append((_sample_variance(instance_scores), len(instance_scores)))
        instance_mean = sum(instance_scores) / len(instance_scores)
        between_instance_sum += len(instance_scores) * (instance_mean - agent_mean) ** 2

    within_cell = (
        sum(value * weight for value, weight in within_parts) / sum(weight for _, weight in within_parts)
        if within_parts
        else 0.0
    )
    between_instance = between_instance_sum / n
    undefined_rate = sum(int(row.undefined_on_success) for row in rows) / n
    residual = max(0.0, total_variance - within_cell - between_instance)

    return {
        "within_cell_variance": within_cell,
        "between_instance_variance": between_instance,
        "total_variance": total_variance,
        "residual_or_missing_component": max(residual, undefined_rate * (1.0 - grand_mean)),
        "undefined_rate": undefined_rate,
    }


def _decompose_global_variance(rows: list[PolicyVarianceRow]) -> dict[str, float]:
    """Pooled ANOVA-style decomposition across all agents."""
    if not rows:
        return {
            "within_cell_variance": 0.0,
            "between_instance_variance": 0.0,
            "between_agent_variance": 0.0,
            "total_variance": 0.0,
            "residual_or_missing_component": 0.0,
            "undefined_rate": 0.0,
        }

    scores = [row.earned_score for row in rows]
    n = len(scores)
    grand_mean = sum(scores) / n
    total_variance = _population_variance(scores)

    cells: dict[tuple[str, str], list[float]] = defaultdict(list)
    agent_scores: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        cells[(row.agent, row.instance_id)].append(row.earned_score)
        agent_scores[row.agent].append(row.earned_score)

    agent_means = {
        agent: sum(values) / len(values) for agent, values in agent_scores.items()
    }
    cell_means = {
        key: sum(values) / len(values) for key, values in cells.items()
    }

    within_parts: list[tuple[float, int]] = []
    for values in cells.values():
        if len(values) >= 2:
            within_parts.append((_sample_variance(values), len(values)))
    within_cell = (
        sum(value * weight for value, weight in within_parts) / sum(weight for _, weight in within_parts)
        if within_parts
        else 0.0
    )

    between_instance_sum = 0.0
    for (agent, _instance_id), cell_mean in cell_means.items():
        between_instance_sum += len(cells[(agent, _instance_id)]) * (
            cell_mean - agent_means[agent]
        ) ** 2
    between_instance = between_instance_sum / n

    between_agent_sum = sum(
        len(values) * (agent_means[agent] - grand_mean) ** 2
        for agent, values in agent_scores.items()
    )
    between_agent = between_agent_sum / n

    undefined_rate = sum(int(row.undefined_on_success) for row in rows) / n
    explained = within_cell + between_instance + between_agent
    residual = max(0.0, total_variance - explained)

    return {
        "within_cell_variance": within_cell,
        "between_instance_variance": between_instance,
        "between_agent_variance": between_agent,
        "total_variance": total_variance,
        "residual_or_missing_component": max(residual, undefined_rate),
        "undefined_rate": undefined_rate,
    }


def _dominant_mechanism(rows: list[PolicyVarianceRow]) -> str:
    counter: Counter[str] = Counter()
    for row in rows:
        if row.failed_mechanisms:
            counter.update(row.failed_mechanisms)
    if not counter:
        return ""
    return counter.most_common(1)[0][0]


def _agent_instance_rows(rows: list[PolicyVarianceRow]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[PolicyVarianceRow]] = defaultdict(list)
    for row in rows:
        grouped[(row.agent, row.instance_id)].append(row)

    output: list[dict[str, Any]] = []
    for (agent, instance_id), cell_rows in sorted(grouped.items()):
        earned_scores = [item.earned_score for item in cell_rows]
        ef_values = [
            float(item.ef_pi)
            for item in cell_rows
            if item.ef_defined_on_success and item.ef_pi is not None
        ]
        output.append(
            {
                "agent": agent,
                "instance_id": instance_id,
                "attempt_count": len(cell_rows),
                "nominal_success_count": sum(int(item.y0) for item in cell_rows),
                "earned_score_mean": sum(earned_scores) / len(earned_scores),
                "earned_score_variance": _sample_variance(earned_scores),
                "ef_conditional_mean": (
                    sum(ef_values) / len(ef_values) if ef_values else None
                ),
                "ef_conditional_variance": (
                    _sample_variance(ef_values) if len(ef_values) >= 2 else 0.0
                ),
                "dominant_failed_mechanism": _dominant_mechanism(cell_rows),
                "undefined_on_success_count": sum(
                    int(item.undefined_on_success) for item in cell_rows
                ),
            }
        )
    return output


def _single_run_earned_rate(rows: list[PolicyVarianceRow]) -> float:
    min_replicate: dict[str, int] = {}
    for row in rows:
        current = min_replicate.get(row.instance_id)
        if current is None or row.replicate < current:
            min_replicate[row.instance_id] = row.replicate
    scores = [
        row.earned_score
        for row in rows
        if row.replicate == min_replicate[row.instance_id]
    ]
    return sum(scores) / len(scores) if scores else 0.0


def _agent_summary_rows(rows: list[PolicyVarianceRow]) -> list[dict[str, Any]]:
    grouped = defaultdict(list)
    for row in rows:
        grouped[row.agent].append(row)

    agents = sorted(grouped)
    nominal_rates: dict[str, float] = {}
    earned_rates: dict[str, float] = {}
    single_run_rates: dict[str, float] = {}

    summaries: list[dict[str, Any]] = []
    for agent in agents:
        agent_rows = grouped[agent]
        attempt_count = len(agent_rows)
        undefined_count = sum(int(item.undefined_on_success) for item in agent_rows)
        defined_success_ef = [
            float(item.ef_pi)
            for item in agent_rows
            if item.ef_defined_on_success and item.ef_pi is not None
        ]
        decomp = _decompose_agent_variance(agent_rows)
        model = agent_rows[0].model
        provider = agent_rows[0].provider
        for item in agent_rows[1:]:
            if item.model != model or item.provider != provider:
                model = "mixed"
                provider = "mixed"
                break

        nominal = sum(int(item.y0) for item in agent_rows) / attempt_count
        earned = sum(item.earned_score for item in agent_rows) / attempt_count
        earned_zero = sum(item.earned_score_undefined_as_zero for item in agent_rows) / attempt_count
        single_run_rate = _single_run_earned_rate(agent_rows)

        nominal_rates[agent] = nominal
        earned_rates[agent] = earned
        single_run_rates[agent] = single_run_rate

        instances = sorted({item.instance_id for item in agent_rows})
        replicates = sorted({item.replicate for item in agent_rows})
        summaries.append(
            {
                "agent": agent,
                "model": model,
                "provider": provider,
                "instance_count": len(instances),
                "replicate_count": len(replicates),
                "attempt_count": attempt_count,
                "nominal_pass_rate": nominal,
                "earned_pass_rate": earned,
                "earned_pass_rate_undefined_as_zero": earned_zero,
                "mean_ef_conditional_on_pass": (
                    sum(defined_success_ef) / len(defined_success_ef)
                    if defined_success_ef
                    else None
                ),
                "undefined_rate": undefined_count / attempt_count,
                "undefined_on_success_count": undefined_count,
                "invalid_pi_rate_mean": sum(item.invalid_pi_count for item in agent_rows)
                / attempt_count,
                "within_cell_variance": decomp["within_cell_variance"],
                "between_instance_variance": decomp["between_instance_variance"],
            }
        )

    if len(agents) >= 1:
        nominal_rank = _rank_map(agents, nominal_rates)
        earned_rank = _rank_map(agents, earned_rates)
        single_rank = _rank_map(agents, single_run_rates)
        for item in summaries:
            agent = item["agent"]
            item["nominal_rank"] = nominal_rank[agent]
            item["policy_earned_rank"] = earned_rank[agent]
            item["single_run_earned_rank"] = single_rank[agent]

    return summaries


def _component_rows(rows: list[PolicyVarianceRow]) -> list[dict[str, Any]]:
    global_decomp = _decompose_global_variance(rows)
    total = global_decomp["total_variance"]
    output: list[dict[str, Any]] = []

    def fraction(value: float) -> float | None:
        if total <= 0:
            return None
        return value / total

    for component, value in global_decomp.items():
        if component in {"total_variance", "undefined_rate"}:
            continue
        output.append(
            {
                "scope": "global",
                "agent": "",
                "component": component,
                "value": value,
                "fraction_of_total_variance": fraction(value),
            }
        )

    grouped = defaultdict(list)
    for row in rows:
        grouped[row.agent].append(row)
    for agent, agent_rows in sorted(grouped.items()):
        decomp = _decompose_agent_variance(agent_rows)
        agent_total = decomp["total_variance"]
        for component in (
            "within_cell_variance",
            "between_instance_variance",
            "residual_or_missing_component",
        ):
            value = decomp[component]
            output.append(
                {
                    "scope": "agent",
                    "agent": agent,
                    "component": component,
                    "value": value,
                    "fraction_of_total_variance": (
                        value / agent_total if agent_total > 0 else None
                    ),
                }
            )
    return output


def _frontier_rows(rows: list[PolicyVarianceRow]) -> list[dict[str, Any]]:
    has_difficulty = any(row.difficulty_bin for row in rows)
    has_patch_loc = any(row.patch_loc for row in rows)
    if not has_difficulty and not has_patch_loc:
        return []

    output: list[dict[str, Any]] = []
    bin_specs: list[tuple[str, str]] = []
    if has_difficulty:
        bin_specs.append(("difficulty_bin", "difficulty_bin"))
    if has_patch_loc:
        bin_specs.append(("patch_loc", "patch_loc"))

    for field_name, bin_type in bin_specs:
        grouped: dict[tuple[str, str], list[PolicyVarianceRow]] = defaultdict(list)
        for row in rows:
            bin_value = getattr(row, field_name)
            if bin_value is None:
                continue
            grouped[(row.agent, bin_value)].append(row)

        for (agent, frontier_bin), bin_rows in sorted(grouped.items()):
            attempt_count = len(bin_rows)
            instances = {row.instance_id for row in bin_rows}
            nominal = sum(int(row.y0) for row in bin_rows) / attempt_count
            earned_mean = sum(row.earned_score for row in bin_rows) / attempt_count
            ef_values = [
                float(row.ef_pi)
                for row in bin_rows
                if row.ef_defined_on_success and row.ef_pi is not None
            ]
            output.append(
                {
                    "agent": agent,
                    "frontier_bin": frontier_bin,
                    "bin_type": bin_type,
                    "instance_count": len(instances),
                    "attempt_count": attempt_count,
                    "nominal_pass_rate": nominal,
                    "earned_score_mean": earned_mean,
                    "earned_pass_rate": earned_mean,
                    "mean_ef_conditional_on_pass": (
                        sum(ef_values) / len(ef_values) if ef_values else None
                    ),
                }
            )
    return output


def _pairwise_flip_rows(
    agents: list[str],
    left_rates: dict[str, float],
    right_rates: dict[str, float],
    *,
    ranking_a: str,
    ranking_b: str,
    bootstrap_probs: dict[str, float | None] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for left in range(len(agents)):
        for right in range(left + 1, len(agents)):
            agent_a = agents[left]
            agent_b = agents[right]
            delta_left = left_rates[agent_a] - left_rates[agent_b]
            delta_right = right_rates[agent_a] - right_rates[agent_b]
            pair_key = f"{agent_a}|{agent_b}|{ranking_a}|{ranking_b}"
            rows.append(
                {
                    "agent_a": agent_a,
                    "agent_b": agent_b,
                    "ranking_a": ranking_a,
                    "ranking_b": ranking_b,
                    "rate_a": left_rates[agent_a],
                    "rate_b": left_rates[agent_b],
                    "rank_flip": int(delta_left * delta_right < 0),
                    "bootstrap_flip_probability": (
                        bootstrap_probs.get(pair_key) if bootstrap_probs else None
                    ),
                }
            )
    return rows


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


def _bootstrap_payload(
    rows: list[PolicyVarianceRow],
    *,
    bootstrap_draws: int,
    bootstrap_seed: int,
) -> dict[str, Any]:
    grouped: dict[str, list[PolicyVarianceRow]] = defaultdict(list)
    for row in rows:
        grouped[row.agent].append(row)

    agents = sorted(grouped)
    instances_by_agent = {
        agent: sorted({row.instance_id for row in grouped[agent]}) for agent in agents
    }
    if agents:
        reference = instances_by_agent[agents[0]]
        for agent in agents[1:]:
            if instances_by_agent[agent] != reference:
                msg = (
                    "all agents must share the same instance_id set for policy variance analysis; "
                    f"mismatch for agent {agent!r}"
                )
                raise ValueError(msg)
        instance_ids = reference
    else:
        instance_ids = []

    rng = random.Random(bootstrap_seed)
    flip_keys = (
        ("nominal_pass_rate", "policy_earned_pass_rate"),
        ("nominal_pass_rate", "single_run_earned_pass_rate"),
        ("single_run_earned_pass_rate", "policy_earned_pass_rate"),
    )
    flip_samples: dict[str, list[int]] = defaultdict(list)
    spearman_samples: dict[str, list[float]] = defaultdict(list)

    for _ in range(bootstrap_draws):
        sampled_instances = [
            instance_ids[rng.randrange(len(instance_ids))] for _ in range(len(instance_ids))
        ]
        sample_rows: list[PolicyVarianceRow] = []
        for agent in agents:
            lookup = {
                (row.instance_id, row.replicate): row for row in grouped[agent]
            }
            replicates = sorted({row.replicate for row in grouped[agent]})
            for instance_id in sampled_instances:
                for replicate in replicates:
                    sample_rows.append(lookup[(instance_id, replicate)])

        summaries = _agent_summary_rows(sample_rows)
        summary_by_agent = {item["agent"]: item for item in summaries}
        nominal = {agent: summary_by_agent[agent]["nominal_pass_rate"] for agent in agents}
        policy = {agent: summary_by_agent[agent]["earned_pass_rate"] for agent in agents}
        single = {
            agent: _single_run_earned_rate([row for row in sample_rows if row.agent == agent])
            for agent in agents
        }

        rate_maps = {
            "nominal_pass_rate": nominal,
            "policy_earned_pass_rate": policy,
            "single_run_earned_pass_rate": single,
        }
        for left_name, right_name in flip_keys:
            flips = _pairwise_flip_rows(
                agents,
                rate_maps[left_name],
                rate_maps[right_name],
                ranking_a=left_name,
                ranking_b=right_name,
            )
            for flip in flips:
                key = (
                    f"{flip['agent_a']}|{flip['agent_b']}|{left_name}|{right_name}"
                )
                flip_samples[key].append(int(flip["rank_flip"]))
            left_rank = _rank_map(agents, rate_maps[left_name])
            right_rank = _rank_map(agents, rate_maps[right_name])
            rho = spearman_rank_correlation(
                [left_rank[agent] for agent in agents],
                [right_rank[agent] for agent in agents],
            )
            if rho is not None:
                spearman_samples[f"{left_name}|{right_name}"].append(rho)

    flip_probability = {
        key: (sum(samples) / len(samples) if samples else None)
        for key, samples in flip_samples.items()
    }
    return {
        "bootstrap_draws": bootstrap_draws,
        "bootstrap_seed": bootstrap_seed,
        "ers_spearman": {
            key: _ci(samples) for key, samples in spearman_samples.items()
        },
        "pairwise_flip_probability": flip_probability,
    }


def analyze_policy_variance(
    rows: list[PolicyVarianceRow],
    *,
    bootstrap_draws: int = DEFAULT_BOOTSTRAP_DRAWS,
    bootstrap_seed: int = BOOTSTRAP_SEED,
) -> dict[str, Any]:
    """Compute Phase C′ variance decomposition and ERS bridge metrics."""
    if not rows:
        msg = "policy variance analysis requires at least one attempt row"
        raise ValueError(msg)

    by_agent = _agent_summary_rows(rows)
    by_agent_instance = _agent_instance_rows(rows)
    components = _component_rows(rows)
    frontier = _frontier_rows(rows)
    global_decomp = _decompose_global_variance(rows)
    bootstrap = _bootstrap_payload(
        rows,
        bootstrap_draws=bootstrap_draws,
        bootstrap_seed=bootstrap_seed,
    )

    agents = sorted({row.agent for row in rows})
    nominal = {item["agent"]: item["nominal_pass_rate"] for item in by_agent}
    policy = {item["agent"]: item["earned_pass_rate"] for item in by_agent}
    single = {
        item["agent"]: _single_run_earned_rate([row for row in rows if row.agent == item["agent"]])
        for item in by_agent
    }

    pairwise: list[dict[str, Any]] = []
    for left_name, right_name, left_rates, right_rates in (
        ("nominal_pass_rate", "policy_earned_pass_rate", nominal, policy),
        ("nominal_pass_rate", "single_run_earned_pass_rate", nominal, single),
        ("single_run_earned_pass_rate", "policy_earned_pass_rate", single, policy),
    ):
        pairwise.extend(
            _pairwise_flip_rows(
                agents,
                left_rates,
                right_rates,
                ranking_a=left_name,
                ranking_b=right_name,
                bootstrap_probs=bootstrap["pairwise_flip_probability"],
            )
        )

    if len(agents) >= 2:
        nominal_rank = _rank_map(agents, nominal)
        policy_rank = _rank_map(agents, policy)
        ers_bridge = {
            "spearman_nominal_vs_policy": spearman_rank_correlation(
                [nominal_rank[agent] for agent in agents],
                [policy_rank[agent] for agent in agents],
            ),
            "spearman_nominal_vs_single_run": spearman_rank_correlation(
                [nominal_rank[agent] for agent in agents],
                [_rank_map(agents, single)[agent] for agent in agents],
            ),
        }
    else:
        ers_bridge = {
            "spearman_nominal_vs_policy": None,
            "spearman_nominal_vs_single_run": None,
        }

    return {
        "schema_version": "earnbench.policy_variance.v1",
        "agent_count": len(agents),
        "attempt_count": len(rows),
        "instance_count": len({row.instance_id for row in rows}),
        "replicate_count": len({row.replicate for row in rows}),
        "by_agent": by_agent,
        "by_agent_instance": by_agent_instance,
        "components": components,
        "global_decomposition": global_decomp,
        "exploitation_frontier": frontier,
        "has_frontier_bins": bool(frontier),
        "pairwise_flips": pairwise,
        "ers_bridge": ers_bridge,
        "bootstrap": bootstrap,
        "bootstrap_draws": bootstrap_draws,
        "bootstrap_seed": bootstrap_seed,
    }


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


def render_policy_variance_report(payload: dict[str, Any]) -> str:
    lines = [
        "# Policy-Level Earned-Credit Variance Decomposition",
        "",
        "Outcome-level **EF@Π** semantics are unchanged. This report decomposes "
        "**earned_score = y0 × ef_pi** (primary: exclude undefined from conditional EF; "
        "undefined successes contribute zero to earned_pass_rate and are counted as missingness).",
        "",
        "## Overview",
        "",
        f"- **Agents:** {payload['agent_count']}",
        f"- **Instances:** {payload['instance_count']}",
        f"- **Replicates:** {payload['replicate_count']}",
        f"- **Attempts:** {payload['attempt_count']}",
        "",
        "## Global variance decomposition (ANOVA-style)",
        "",
        "_Components are transparent population-variance partitions; they may not sum "
        "exactly to total variance when cells have single replicates (documented limitation)._",
        "",
    ]
    for item in payload["components"]:
        if item["scope"] != "global":
            continue
        lines.append(
            f"- **{item['component']}:** {_format_float(item['value'])} "
            f"(fraction={_format_float(item['fraction_of_total_variance'])})"
        )

    lines.extend(["", "## Agent summary", ""])
    lines.append(
        "| Agent | Nominal | Earned | EF|pass | Undefined | Within-cell var | "
        "Between-inst var | Rank nom | Rank policy | Rank single |"
    )
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for row in payload["by_agent"]:
        lines.append(
            "| {agent} | {nominal_pass_rate:.4f} | {earned_pass_rate:.4f} | "
            "{mean_ef_conditional_on_pass} | {undefined_rate:.4f} | "
            "{within_cell_variance} | {between_instance_variance} | "
            "{nominal_rank:.2f} | {policy_earned_rank:.2f} | {single_run_earned_rank:.2f} |".format(
                agent=row["agent"],
                nominal_pass_rate=row["nominal_pass_rate"],
                earned_pass_rate=row["earned_pass_rate"],
                mean_ef_conditional_on_pass=_format_float(
                    row["mean_ef_conditional_on_pass"],
                    precision=4,
                ),
                undefined_rate=row["undefined_rate"],
                within_cell_variance=_format_float(row["within_cell_variance"]),
                between_instance_variance=_format_float(row["between_instance_variance"]),
                nominal_rank=row["nominal_rank"],
                policy_earned_rank=row["policy_earned_rank"],
                single_run_earned_rank=row["single_run_earned_rank"],
            )
        )

    if payload.get("exploitation_frontier"):
        lines.extend(["", "## Exploitation frontier", ""])
        for row in payload["exploitation_frontier"]:
            lines.append(
                f"- **{row['agent']}** / {row['bin_type']}={row['frontier_bin']}: "
                f"earned_score_mean={_format_float(row['earned_score_mean'], precision=4)} "
                f"(nominal={row['nominal_pass_rate']:.4f})"
            )
    else:
        lines.extend(
            [
                "",
                "## Exploitation frontier",
                "",
                "No `difficulty_bin` or `patch_loc` columns present — frontier table omitted.",
            ]
        )

    lines.extend(["", "## ERS bridge", ""])
    for key, value in payload["ers_bridge"].items():
        lines.append(f"- **{key}:** {_format_float(value)}")

    return "\n".join(lines) + "\n"


def generate_policy_variance_report(
    agent_results_path: Path,
    output_dir: Path,
    *,
    bootstrap_draws: int = DEFAULT_BOOTSTRAP_DRAWS,
    bootstrap_seed: int = BOOTSTRAP_SEED,
) -> PolicyVarianceReportResult:
    """Load Phase C′ rows and write policy variance artifacts."""
    rows = load_policy_variance_results(agent_results_path)
    payload = analyze_policy_variance(
        rows,
        bootstrap_draws=bootstrap_draws,
        bootstrap_seed=bootstrap_seed,
    )

    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    by_agent_csv = output_dir / POLICY_VARIANCE_BY_AGENT_CSV
    by_agent_instance_csv = output_dir / POLICY_VARIANCE_BY_AGENT_INSTANCE_CSV
    components_csv = output_dir / POLICY_VARIANCE_COMPONENTS_CSV
    bootstrap_json = output_dir / POLICY_VARIANCE_BOOTSTRAP_JSON
    pairwise_flips_csv = output_dir / POLICY_VARIANCE_PAIRWISE_FLIPS_CSV
    report_md = output_dir / POLICY_VARIANCE_REPORT_MD

    _write_csv(by_agent_csv, BY_AGENT_COLUMNS, payload["by_agent"])
    _write_csv(by_agent_instance_csv, BY_AGENT_INSTANCE_COLUMNS, payload["by_agent_instance"])
    _write_csv(components_csv, COMPONENT_COLUMNS, payload["components"])
    _write_csv(pairwise_flips_csv, PAIRWISE_FLIP_COLUMNS, payload["pairwise_flips"])

    bootstrap_payload = payload.pop("bootstrap")
    bootstrap_payload["schema_version"] = "earnbench.policy_variance_bootstrap.v1"
    bootstrap_payload["ers_bridge"] = payload["ers_bridge"]
    with bootstrap_json.open("w", encoding="utf-8") as handle:
        json.dump(bootstrap_payload, handle, indent=2, sort_keys=True)
        handle.write("\n")

    exploitation_frontier_csv: Path | None = None
    if payload.get("exploitation_frontier"):
        exploitation_frontier_csv = output_dir / EXPLOITATION_FRONTIER_CSV
        _write_csv(exploitation_frontier_csv, FRONTIER_COLUMNS, payload["exploitation_frontier"])

    report_md.write_text(render_policy_variance_report(payload), encoding="utf-8")

    return PolicyVarianceReportResult(
        output_dir=output_dir,
        by_agent_csv=by_agent_csv,
        by_agent_instance_csv=by_agent_instance_csv,
        components_csv=components_csv,
        bootstrap_json=bootstrap_json,
        pairwise_flips_csv=pairwise_flips_csv,
        report_md=report_md,
        exploitation_frontier_csv=exploitation_frontier_csv,
    )


__all__ = [
    "BY_AGENT_COLUMNS",
    "BY_AGENT_INSTANCE_COLUMNS",
    "COMPONENT_COLUMNS",
    "DEFAULT_BOOTSTRAP_DRAWS",
    "EXPLOITATION_FRONTIER_CSV",
    "OPTIONAL_COLUMNS",
    "POLICY_VARIANCE_BOOTSTRAP_JSON",
    "POLICY_VARIANCE_BY_AGENT_CSV",
    "POLICY_VARIANCE_BY_AGENT_INSTANCE_CSV",
    "POLICY_VARIANCE_COMPONENTS_CSV",
    "POLICY_VARIANCE_PAIRWISE_FLIPS_CSV",
    "POLICY_VARIANCE_REPORT_MD",
    "REQUIRED_COLUMNS",
    "PolicyVarianceReportResult",
    "PolicyVarianceRow",
    "analyze_policy_variance",
    "generate_policy_variance_report",
    "load_policy_variance_results",
    "render_policy_variance_report",
]
