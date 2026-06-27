"""Earned Rank Stability (ERS) analysis over agent × instance result tables."""

from __future__ import annotations

import csv
import json
import math
import random
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REQUIRED_COLUMNS = (
    "agent",
    "instance_id",
    "y0",
    "ef_exclude_invalid",
    "ef_invalid_as_fail",
    "failed_mechanisms",
    "invalid_pi_count",
)

RANK_STABILITY_SUMMARY_CSV = "rank_stability_summary.csv"
PAIRWISE_FLIPS_CSV = "pairwise_flips.csv"
CHANNEL_RANK_CONTRIBUTIONS_CSV = "channel_rank_contributions.csv"
RANK_STABILITY_REPORT_MD = "rank_stability_report.md"
RANK_STABILITY_JSON = "rank_stability.json"

SUMMARY_COLUMNS = (
    "agent",
    "instance_count",
    "nominal_pass_rate",
    "earned_pass_rate_exclude_invalid",
    "earned_pass_rate_invalid_as_fail",
    "sensitivity_band",
    "nominal_rank",
    "earned_rank",
    "rank_shift",
    "rank_shift_ci_low",
    "rank_shift_ci_high",
    "undefined_ef_on_success_count",
)

PAIRWISE_COLUMNS = (
    "agent_a",
    "agent_b",
    "nominal_pass_rate_a",
    "nominal_pass_rate_b",
    "earned_pass_rate_a",
    "earned_pass_rate_b",
    "flip",
)

CHANNEL_COLUMNS = (
    "agent",
    "channel",
    "lost_credit",
    "failure_instance_count",
)

DEFAULT_BOOTSTRAP_DRAWS = 10_000
BOOTSTRAP_SEED = 0
CI_LOW_QUANTILE = 0.025
CI_HIGH_QUANTILE = 0.975

CHANNEL_ALIASES = {
    "pi_vtest.v1": "visible_test_overfitting",
    "pi_verif.v1": "verifier_tampering",
    "pi_env.v1": "environment_hijack",
    "vtest": "visible_test_overfitting",
    "verif": "verifier_tampering",
    "env": "environment_hijack",
}


@dataclass(frozen=True, slots=True)
class RankStabilityResult:
    output_dir: Path
    summary_csv: Path
    pairwise_flips_csv: Path
    channel_rank_contributions_csv: Path
    report_md: Path
    report_json: Path


@dataclass(frozen=True, slots=True)
class AgentInstanceRow:
    agent: str
    instance_id: str
    y0: bool
    ef_exclude_invalid: float
    ef_invalid_as_fail: float
    failed_mechanisms: tuple[str, ...]
    invalid_pi_count: int
    ef_exclude_invalid_undefined: bool = False


@dataclass(frozen=True, slots=True)
class AgentSummary:
    agent: str
    instance_count: int
    nominal_pass_rate: float
    earned_pass_rate_exclude_invalid: float
    earned_pass_rate_invalid_as_fail: float
    sensitivity_band: float
    nominal_rank: float
    earned_rank: float
    rank_shift: float
    rank_shift_ci_low: float
    rank_shift_ci_high: float
    undefined_ef_on_success_count: int


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
                parts.append(CHANNEL_ALIASES.get(token, token))
    return tuple(dict.fromkeys(parts))


def _normalize_channel(name: str) -> str:
    return CHANNEL_ALIASES.get(name.strip(), name.strip())


def _load_agent_results(path: Path) -> list[AgentInstanceRow]:
    if not path.is_file():
        msg = f"agent results file not found: {path}"
        raise FileNotFoundError(msg)
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            msg = f"{path} is empty or missing a header row"
            raise ValueError(msg)
        missing = [col for col in REQUIRED_COLUMNS if col not in reader.fieldnames]
        if missing:
            msg = f"{path} missing required columns: {', '.join(missing)}"
            raise ValueError(msg)
        rows: list[AgentInstanceRow] = []
        for index, raw in enumerate(reader, start=2):
            agent = str(raw.get("agent", "")).strip()
            instance_id = str(raw.get("instance_id", "")).strip()
            if not agent or not instance_id:
                msg = f"{path}:{index} missing agent or instance_id"
                raise ValueError(msg)
            y0 = _as_bool(raw.get("y0"))
            ef_ex = _optional_float(raw.get("ef_exclude_invalid"))
            ef_fail = _optional_float(raw.get("ef_invalid_as_fail"))
            ef_exclude_invalid_undefined = False
            if y0:
                ef_exclude_invalid_undefined = ef_ex is None
                ef_exclude_invalid = 0.0 if ef_ex is None else ef_ex
                ef_invalid_as_fail = 0.0 if ef_fail is None else ef_fail
            else:
                ef_exclude_invalid = 0.0
                ef_invalid_as_fail = 0.0
            rows.append(
                AgentInstanceRow(
                    agent=agent,
                    instance_id=instance_id,
                    y0=y0,
                    ef_exclude_invalid=ef_exclude_invalid,
                    ef_invalid_as_fail=ef_invalid_as_fail,
                    failed_mechanisms=_parse_failed_mechanisms(
                        raw.get("failed_mechanisms"),
                    ),
                    invalid_pi_count=_optional_int(raw.get("invalid_pi_count")),
                    ef_exclude_invalid_undefined=ef_exclude_invalid_undefined,
                )
            )
    if not rows:
        msg = f"{path} contains no data rows"
        raise ValueError(msg)
    return rows


def _group_rows(rows: list[AgentInstanceRow]) -> dict[str, dict[str, AgentInstanceRow]]:
    grouped: dict[str, dict[str, AgentInstanceRow]] = defaultdict(dict)
    for row in rows:
        if row.instance_id in grouped[row.agent]:
            msg = (
                f"duplicate row for agent={row.agent!r} "
                f"instance_id={row.instance_id!r}"
            )
            raise ValueError(msg)
        grouped[row.agent][row.instance_id] = row
    return grouped


def _validate_common_instance_set(
    grouped: dict[str, dict[str, AgentInstanceRow]],
) -> tuple[str, ...]:
    agents = sorted(grouped)
    instance_sets = [frozenset(grouped[agent]) for agent in agents]
    reference = instance_sets[0]
    for agent, instance_set in zip(agents[1:], instance_sets[1:], strict=False):
        if instance_set != reference:
            msg = (
                "all agents must share the same instance_id set; "
                f"mismatch for agent {agent!r}"
            )
            raise ValueError(msg)
    return tuple(sorted(reference))


def _average_ranks(values: list[float]) -> list[float]:
    """Return average ranks where 1 = best (highest value)."""
    indexed = sorted(enumerate(values), key=lambda item: item[1], reverse=True)
    ranks = [0.0] * len(values)
    index = 0
    while index < len(values):
        end = index
        while (
            end + 1 < len(values)
            and indexed[end + 1][1] == indexed[index][1]
        ):
            end += 1
        avg_rank = (index + end) / 2.0 + 1.0
        for position in range(index, end + 1):
            ranks[indexed[position][0]] = avg_rank
        index = end + 1
    return ranks


def _pearson(x: list[float], y: list[float]) -> float | None:
    if len(x) != len(y) or len(x) < 2:
        return None
    mean_x = sum(x) / len(x)
    mean_y = sum(y) / len(y)
    num = sum((a - mean_x) * (b - mean_y) for a, b in zip(x, y, strict=True))
    den_x = math.sqrt(sum((a - mean_x) ** 2 for a in x))
    den_y = math.sqrt(sum((b - mean_y) ** 2 for b in y))
    if den_x == 0.0 or den_y == 0.0:
        return None
    return num / (den_x * den_y)


def spearman_rank_correlation(
    nominal_ranks: list[float],
    earned_ranks: list[float],
) -> float | None:
    return _pearson(nominal_ranks, earned_ranks)


def kendall_tau(x: list[float], y: list[float]) -> float | None:
    count = len(x)
    if count < 2:
        return None
    concordant = 0
    discordant = 0
    for left in range(count):
        for right in range(left + 1, count):
            delta = (x[left] - x[right]) * (y[left] - y[right])
            if delta > 0:
                concordant += 1
            elif delta < 0:
                discordant += 1
    pairs = count * (count - 1) / 2
    if pairs == 0:
        return None
    return (concordant - discordant) / pairs


def _compute_agent_rates(
    agent_rows: dict[str, AgentInstanceRow],
    instance_ids: tuple[str, ...],
) -> dict[str, float | int]:
    count = len(instance_ids)
    nominal = 0
    earned_ex = 0.0
    earned_fail = 0.0
    undefined_on_success = 0
    for instance_id in instance_ids:
        row = agent_rows[instance_id]
        nominal += int(row.y0)
        earned_ex += row.y0 * row.ef_exclude_invalid
        earned_fail += row.y0 * row.ef_invalid_as_fail
        if row.y0 and row.ef_exclude_invalid_undefined:
            undefined_on_success += 1
    return {
        "instance_count": count,
        "nominal_pass_rate": nominal / count,
        "earned_pass_rate_exclude_invalid": earned_ex / count,
        "earned_pass_rate_invalid_as_fail": earned_fail / count,
        "undefined_ef_on_success_count": undefined_on_success,
    }


def _rank_values(
    agents: list[str],
    values: dict[str, float],
) -> dict[str, float]:
    ordered_values = [values[agent] for agent in agents]
    ranks = _average_ranks(ordered_values)
    return {agent: ranks[index] for index, agent in enumerate(agents)}


def _pairwise_flips(
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
                    "flip": flip,
                }
            )
    return rows


def _channel_lost_credit_rows(
    agents: list[str],
    grouped: dict[str, dict[str, AgentInstanceRow]],
    instance_ids: tuple[str, ...],
) -> list[dict[str, Any]]:
    count = len(instance_ids)
    rows: list[dict[str, Any]] = []
    for agent in agents:
        per_channel_credit: dict[str, float] = defaultdict(float)
        per_channel_instances: dict[str, int] = defaultdict(int)
        for instance_id in instance_ids:
            row = grouped[agent][instance_id]
            if not row.y0:
                continue
            lost = row.y0 * (1.0 - row.ef_exclude_invalid)
            if lost <= 0.0 or not row.failed_mechanisms:
                continue
            share = lost / len(row.failed_mechanisms)
            for channel in row.failed_mechanisms:
                normalized = _normalize_channel(channel)
                per_channel_credit[normalized] += share
                per_channel_instances[normalized] += 1
        for channel in sorted(per_channel_credit):
            rows.append(
                {
                    "agent": agent,
                    "channel": channel,
                    "lost_credit": per_channel_credit[channel] / count,
                    "failure_instance_count": per_channel_instances[channel],
                }
            )
    return rows


def _summaries_for_instances(
    agents: list[str],
    grouped: dict[str, dict[str, AgentInstanceRow]],
    instance_ids: tuple[str, ...],
) -> list[AgentSummary]:
    nominal_rates: dict[str, float] = {}
    earned_rates: dict[str, float] = {}
    rate_payloads: dict[str, dict[str, float | int]] = {}
    for agent in agents:
        rates = _compute_agent_rates(grouped[agent], instance_ids)
        rate_payloads[agent] = rates
        nominal_rates[agent] = float(rates["nominal_pass_rate"])
        earned_rates[agent] = float(rates["earned_pass_rate_exclude_invalid"])
    nominal_rank = _rank_values(agents, nominal_rates)
    earned_rank = _rank_values(agents, earned_rates)
    summaries: list[AgentSummary] = []
    for agent in agents:
        rates = rate_payloads[agent]
        earned_ex = float(rates["earned_pass_rate_exclude_invalid"])
        earned_fail = float(rates["earned_pass_rate_invalid_as_fail"])
        nom_rank = nominal_rank[agent]
        ear_rank = earned_rank[agent]
        summaries.append(
            AgentSummary(
                agent=agent,
                instance_count=int(rates["instance_count"]),
                nominal_pass_rate=float(rates["nominal_pass_rate"]),
                earned_pass_rate_exclude_invalid=earned_ex,
                earned_pass_rate_invalid_as_fail=earned_fail,
                sensitivity_band=earned_ex - earned_fail,
                nominal_rank=nom_rank,
                earned_rank=ear_rank,
                rank_shift=nom_rank - ear_rank,
                rank_shift_ci_low=nom_rank - ear_rank,
                rank_shift_ci_high=nom_rank - ear_rank,
                undefined_ef_on_success_count=int(
                    rates["undefined_ef_on_success_count"]
                ),
            )
        )
    return summaries


def _bootstrap_cis(
    agents: list[str],
    grouped: dict[str, dict[str, AgentInstanceRow]],
    instance_ids: tuple[str, ...],
    *,
    bootstrap_draws: int,
    seed: int = BOOTSTRAP_SEED,
) -> tuple[
    dict[str, tuple[float, float]],
    dict[str, float | None],
    dict[str, float | None],
    dict[str, int],
]:
    rng = random.Random(seed)
    rank_shift_samples: dict[str, list[float]] = {agent: [] for agent in agents}
    spearman_samples: list[float] = []
    kendall_samples: list[float] = []
    flip_samples: list[int] = []
    for _ in range(bootstrap_draws):
        sampled_instances = tuple(
            instance_ids[rng.randrange(len(instance_ids))]
            for _ in range(len(instance_ids))
        )
        summaries = _summaries_for_instances(agents, grouped, sampled_instances)
        nominal_rates = {item.agent: item.nominal_pass_rate for item in summaries}
        earned_rates = {
            item.agent: item.earned_pass_rate_exclude_invalid for item in summaries
        }
        for item in summaries:
            rank_shift_samples[item.agent].append(item.rank_shift)
        nominal_rank_list = [item.nominal_rank for item in summaries]
        earned_rank_list = [item.earned_rank for item in summaries]
        rho = spearman_rank_correlation(nominal_rank_list, earned_rank_list)
        tau = kendall_tau(nominal_rank_list, earned_rank_list)
        if rho is not None:
            spearman_samples.append(rho)
        if tau is not None:
            kendall_samples.append(tau)
        flips = _pairwise_flips(agents, nominal_rates, earned_rates)
        flip_samples.append(sum(int(row["flip"]) for row in flips))
    rank_shift_ci: dict[str, tuple[float, float]] = {}
    for agent in agents:
        samples = sorted(rank_shift_samples[agent])
        rank_shift_ci[agent] = (
            _quantile(samples, CI_LOW_QUANTILE),
            _quantile(samples, CI_HIGH_QUANTILE),
        )
    return (
        rank_shift_ci,
        _ci_dict(spearman_samples),
        _ci_dict(kendall_samples),
        _ci_dict_int(flip_samples),
    )


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


def _ci_dict(samples: list[float]) -> dict[str, float | None]:
    if not samples:
        return {"low": None, "high": None, "mean": None}
    ordered = sorted(samples)
    return {
        "low": _quantile(ordered, CI_LOW_QUANTILE),
        "high": _quantile(ordered, CI_HIGH_QUANTILE),
        "mean": sum(ordered) / len(ordered),
    }


def _ci_dict_int(samples: list[int]) -> dict[str, float | None]:
    if not samples:
        return {"low": None, "high": None, "mean": None}
    ordered = sorted(float(value) for value in samples)
    return {
        "low": _quantile(ordered, CI_LOW_QUANTILE),
        "high": _quantile(ordered, CI_HIGH_QUANTILE),
        "mean": sum(ordered) / len(ordered),
    }


def analyze_rank_stability(
    rows: list[AgentInstanceRow],
    *,
    bootstrap_draws: int = DEFAULT_BOOTSTRAP_DRAWS,
    bootstrap_seed: int = BOOTSTRAP_SEED,
) -> dict[str, Any]:
    """Compute ERS metrics from parsed agent × instance rows."""
    grouped = _group_rows(rows)
    agents = sorted(grouped)
    if len(agents) < 2:
        msg = "rank stability analysis requires at least 2 agents"
        raise ValueError(msg)
    instance_ids = _validate_common_instance_set(grouped)
    summaries = _summaries_for_instances(agents, grouped, instance_ids)
    nominal_rates = {item.agent: item.nominal_pass_rate for item in summaries}
    earned_rates = {
        item.agent: item.earned_pass_rate_exclude_invalid for item in summaries
    }
    earned_rates_fail = {
        item.agent: item.earned_pass_rate_invalid_as_fail for item in summaries
    }
    nominal_rank_map = {item.agent: item.nominal_rank for item in summaries}
    earned_rank_map = {item.agent: item.earned_rank for item in summaries}
    spearman = spearman_rank_correlation(
        [nominal_rank_map[agent] for agent in agents],
        [earned_rank_map[agent] for agent in agents],
    )
    kendall = kendall_tau(
        [nominal_rank_map[agent] for agent in agents],
        [earned_rank_map[agent] for agent in agents],
    )
    earned_rank_fail = _rank_values(agents, earned_rates_fail)
    spearman_fail = spearman_rank_correlation(
        [nominal_rank_map[agent] for agent in agents],
        [earned_rank_fail[agent] for agent in agents],
    )
    flips = _pairwise_flips(agents, nominal_rates, earned_rates)
    flip_count = sum(int(row["flip"]) for row in flips)
    pair_count = len(flips)
    rank_shift_ci, spearman_ci, kendall_ci, flip_ci = _bootstrap_cis(
        agents,
        grouped,
        instance_ids,
        bootstrap_draws=bootstrap_draws,
        seed=bootstrap_seed,
    )
    summary_rows: list[dict[str, Any]] = []
    for item in summaries:
        ci_low, ci_high = rank_shift_ci[item.agent]
        summary_rows.append(
            {
                "agent": item.agent,
                "instance_count": item.instance_count,
                "nominal_pass_rate": item.nominal_pass_rate,
                "earned_pass_rate_exclude_invalid": (
                    item.earned_pass_rate_exclude_invalid
                ),
                "earned_pass_rate_invalid_as_fail": (
                    item.earned_pass_rate_invalid_as_fail
                ),
                "sensitivity_band": item.sensitivity_band,
                "nominal_rank": item.nominal_rank,
                "earned_rank": item.earned_rank,
                "rank_shift": item.rank_shift,
                "rank_shift_ci_low": ci_low,
                "rank_shift_ci_high": ci_high,
                "undefined_ef_on_success_count": item.undefined_ef_on_success_count,
            }
        )
    rank_shifts = [abs(item.rank_shift) for item in summaries]
    return {
        "agent_count": len(agents),
        "instance_count": len(instance_ids),
        "instance_ids": list(instance_ids),
        "bootstrap_draws": bootstrap_draws,
        "bootstrap_seed": bootstrap_seed,
        "summary": summary_rows,
        "pairwise_flips": flips,
        "channel_rank_contributions": _channel_lost_credit_rows(
            agents,
            grouped,
            instance_ids,
        ),
        "ers": {
            "spearman": spearman,
            "spearman_ci_low": spearman_ci["low"],
            "spearman_ci_high": spearman_ci["high"],
            "kendall_tau": kendall,
            "kendall_tau_ci_low": kendall_ci["low"],
            "kendall_tau_ci_high": kendall_ci["high"],
            "spearman_invalid_as_fail": spearman_fail,
            "ers_gap": (
                None
                if spearman is None or spearman_fail is None
                else spearman - spearman_fail
            ),
            "pairwise_flip_count": flip_count,
            "pairwise_flip_rate": flip_count / pair_count if pair_count else None,
            "pairwise_flip_count_ci_low": flip_ci["low"],
            "pairwise_flip_count_ci_high": flip_ci["high"],
            "mean_abs_rank_shift": sum(rank_shifts) / len(rank_shifts),
            "max_abs_rank_shift": max(rank_shifts) if rank_shifts else 0.0,
        },
    }


def _write_csv(path: Path, fieldnames: tuple[str, ...], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def _format_float(value: float | None, *, precision: int = 6) -> str:
    if value is None:
        return "—"
    return f"{value:.{precision}f}"


def render_rank_stability_report(payload: dict[str, Any]) -> str:
    """Render markdown report for rank stability analysis."""
    ers = payload["ers"]
    lines = [
        "# Earned Rank Stability (ERS) Report",
        "",
        "## Overview",
        "",
        f"- **Agents:** {payload['agent_count']}",
        f"- **Instances:** {payload['instance_count']}",
        f"- **Bootstrap draws:** {payload['bootstrap_draws']} "
        f"(seed={payload['bootstrap_seed']})",
        "",
        "EF@Π is aggregated at the agent level as earned pass rate "
        "(mean of `y0 * ef_exclude_invalid`). ERS compares nominal vs earned "
        "leaderboard order; it is **not** a capability score.",
        "",
        "## ERS metrics",
        "",
        "| Metric | Value | 95% CI |",
        "| --- | --- | --- |",
        (
            f"| Spearman ρ | {_format_float(ers['spearman'])} | "
            f"[{_format_float(ers['spearman_ci_low'])}, "
            f"{_format_float(ers['spearman_ci_high'])}] |"
        ),
        (
            f"| Kendall τ | {_format_float(ers['kendall_tau'])} | "
            f"[{_format_float(ers['kendall_tau_ci_low'])}, "
            f"{_format_float(ers['kendall_tau_ci_high'])}] |"
        ),
        (
            f"| Pairwise flips | {ers['pairwise_flip_count']} / "
            f"{payload['agent_count'] * (payload['agent_count'] - 1) // 2} | "
            f"[{_format_float(ers['pairwise_flip_count_ci_low'], precision=1)}, "
            f"{_format_float(ers['pairwise_flip_count_ci_high'], precision=1)}] |"
        ),
        (
            f"| Mean |rank shift| | {_format_float(ers['mean_abs_rank_shift'])} | — |"
        ),
        (
            f"| Max |rank shift| | {_format_float(ers['max_abs_rank_shift'])} | — |"
        ),
        (
            f"| Spearman (invalid-as-fail) | "
            f"{_format_float(ers['spearman_invalid_as_fail'])} | — |"
        ),
        (
            f"| ERS gap (exclude-invalid − invalid-as-fail) | "
            f"{_format_float(ers['ers_gap'])} | — |"
        ),
        "",
        "## Agent summary",
        "",
        "| Agent | Nominal | Earned (ex) | Earned (fail) | Band | "
        "Rank nom | Rank earned | Shift | Shift CI |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in payload["summary"]:
        lines.append(
            "| {agent} | {nominal_pass_rate:.4f} | "
            "{earned_pass_rate_exclude_invalid:.4f} | "
            "{earned_pass_rate_invalid_as_fail:.4f} | "
            "{sensitivity_band:.4f} | {nominal_rank:.2f} | "
            "{earned_rank:.2f} | {rank_shift:.2f} | "
            "[{rank_shift_ci_low:.2f}, {rank_shift_ci_high:.2f}] |".format(
                **row,
            )
        )
    lines.extend(["", "## Pairwise flips", ""])
    if payload["pairwise_flips"]:
        lines.extend(
            [
                "| Agent A | Agent B | Flip |",
                "| --- | --- | --- |",
            ]
        )
        for row in payload["pairwise_flips"]:
            lines.append(
                f"| {row['agent_a']} | {row['agent_b']} | {row['flip']} |"
            )
    else:
        lines.append("_No agent pairs._")
    lines.extend(["", "## Channel-attributed lost credit", ""])
    if payload["channel_rank_contributions"]:
        lines.extend(
            [
                "| Agent | Channel | Lost credit | Failures |",
                "| --- | --- | --- | --- |",
            ]
        )
        for row in payload["channel_rank_contributions"]:
            lines.append(
                f"| {row['agent']} | {row['channel']} | "
                f"{row['lost_credit']:.6f} | {row['failure_instance_count']} |"
            )
    else:
        lines.append("_No channel-attributed lost credit._")
    lines.append("")
    return "\n".join(lines)


def generate_rank_stability_report(
    agent_results_path: Path,
    output_dir: Path,
    *,
    bootstrap_draws: int = DEFAULT_BOOTSTRAP_DRAWS,
    bootstrap_seed: int = BOOTSTRAP_SEED,
) -> RankStabilityResult:
    """Load agent results CSV and write ERS artifacts."""
    rows = _load_agent_results(agent_results_path)
    payload = analyze_rank_stability(
        rows,
        bootstrap_draws=bootstrap_draws,
        bootstrap_seed=bootstrap_seed,
    )
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / RANK_STABILITY_SUMMARY_CSV
    flips_path = output_dir / PAIRWISE_FLIPS_CSV
    channel_path = output_dir / CHANNEL_RANK_CONTRIBUTIONS_CSV
    report_path = output_dir / RANK_STABILITY_REPORT_MD
    json_path = output_dir / RANK_STABILITY_JSON
    _write_csv(summary_path, SUMMARY_COLUMNS, payload["summary"])
    _write_csv(flips_path, PAIRWISE_COLUMNS, payload["pairwise_flips"])
    _write_csv(channel_path, CHANNEL_COLUMNS, payload["channel_rank_contributions"])
    report_path.write_text(render_rank_stability_report(payload), encoding="utf-8")
    json_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return RankStabilityResult(
        output_dir=output_dir,
        summary_csv=summary_path,
        pairwise_flips_csv=flips_path,
        channel_rank_contributions_csv=channel_path,
        report_md=report_path,
        report_json=json_path,
    )


__all__ = [
    "CHANNEL_RANK_CONTRIBUTIONS_CSV",
    "DEFAULT_BOOTSTRAP_DRAWS",
    "PAIRWISE_FLIPS_CSV",
    "RANK_STABILITY_JSON",
    "RANK_STABILITY_REPORT_MD",
    "RANK_STABILITY_SUMMARY_CSV",
    "REQUIRED_COLUMNS",
    "RankStabilityResult",
    "analyze_rank_stability",
    "generate_rank_stability_report",
    "kendall_tau",
    "load_agent_results",
    "render_rank_stability_report",
    "spearman_rank_correlation",
]

# Public alias used by tests and CLI imports.
load_agent_results = _load_agent_results
