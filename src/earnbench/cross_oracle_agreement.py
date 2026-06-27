"""Cross-oracle agreement analysis for paired grading outcomes."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path

CROSS_ORACLE_AGREEMENT_JSON = "cross_oracle_agreement.json"
CROSS_ORACLE_DISAGREEMENTS_CSV = "cross_oracle_disagreements.csv"

REQUIRED_COLUMNS = (
    "instance_id",
    "oracle_a",
    "oracle_b",
    "oracle_a_outcome",
    "oracle_b_outcome",
)

DISAGREEMENT_COLUMNS = (
    "instance_id",
    "oracle_a",
    "oracle_b",
    "oracle_a_outcome",
    "oracle_b_outcome",
)


@dataclass(frozen=True, slots=True)
class CrossOracleValidationResult:
    path: Path
    row_count: int
    errors: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass(frozen=True, slots=True)
class CrossOracleAgreementResult:
    output_dir: Path
    agreement_json: Path
    disagreements_csv: Path


def _parse_binary_outcome(value: object, *, prefix: str) -> tuple[bool | None, str | None]:
    if value is None:
        return None, f"{prefix}: outcome must be non-empty"
    text = str(value).strip().lower()
    if text in {"1", "true", "pass", "success", "yes"}:
        return True, None
    if text in {"0", "false", "fail", "failure", "no"}:
        return False, None
    return None, f"{prefix}: invalid outcome {value!r}"


def validate_cross_oracle_table(path: Path) -> CrossOracleValidationResult:
    """Validate a cross-oracle comparison CSV schema."""
    resolved = path.resolve()
    if not resolved.is_file():
        return CrossOracleValidationResult(
            path=resolved,
            row_count=0,
            errors=(f"table file not found: {resolved}",),
        )

    errors: list[str] = []
    with resolved.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            return CrossOracleValidationResult(
                path=resolved,
                row_count=0,
                errors=(f"{resolved}: empty file or missing header row",),
            )

        header = [name.strip() for name in reader.fieldnames if name is not None]
        missing = [column for column in REQUIRED_COLUMNS if column not in header]
        if missing:
            errors.append(f"{resolved}: missing required columns: {', '.join(missing)}")

        seen_ids: set[str] = set()
        row_count = 0
        for line_number, raw in enumerate(reader, start=2):
            row_count += 1
            prefix = f"{resolved}:{line_number}"
            instance_id = str(raw.get("instance_id", "")).strip()
            if not instance_id:
                errors.append(f"{prefix}: instance_id must be non-empty")
            elif instance_id in seen_ids:
                errors.append(f"{prefix}: duplicate instance_id {instance_id!r}")
            else:
                seen_ids.add(instance_id)

            for column in ("oracle_a_outcome", "oracle_b_outcome"):
                _, error = _parse_binary_outcome(raw.get(column), prefix=f"{prefix}:{column}")
                if error:
                    errors.append(error)

    return CrossOracleValidationResult(
        path=resolved,
        row_count=row_count,
        errors=tuple(errors),
    )


def _cohen_kappa(a: list[bool], b: list[bool]) -> float | None:
    if len(a) != len(b) or len(a) < 2:
        return None
    categories = (False, True)
    total = len(a)
    observed = sum(1 for left, right in zip(a, b, strict=True) if left == right) / total

    marginals = {}
    for label in categories:
        marginals[("a", label)] = sum(1 for item in a if item == label) / total
        marginals[("b", label)] = sum(1 for item in b if item == label) / total

    expected = sum(
        marginals[("a", label)] * marginals[("b", label)] for label in categories
    )
    if expected == 1.0:
        return None
    return (observed - expected) / (1.0 - expected)


def load_cross_oracle_rows(path: Path) -> list[dict[str, str]]:
    validation = validate_cross_oracle_table(path)
    if not validation.ok:
        msg = "; ".join(validation.errors)
        raise ValueError(msg)

    with path.open(encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def analyze_cross_oracle_agreement(rows: list[dict[str, str]]) -> dict[str, object]:
    """Compute agreement rate and Cohen's kappa for paired oracle outcomes."""
    parsed_a: list[bool] = []
    parsed_b: list[bool] = []
    disagreements: list[dict[str, str]] = []

    for row in rows:
        outcome_a, _ = _parse_binary_outcome(
            row.get("oracle_a_outcome"),
            prefix=row.get("instance_id", "?"),
        )
        outcome_b, _ = _parse_binary_outcome(
            row.get("oracle_b_outcome"),
            prefix=row.get("instance_id", "?"),
        )
        if outcome_a is None or outcome_b is None:
            continue
        parsed_a.append(outcome_a)
        parsed_b.append(outcome_b)
        if outcome_a != outcome_b:
            disagreements.append(
                {
                    "instance_id": row.get("instance_id", ""),
                    "oracle_a": row.get("oracle_a", ""),
                    "oracle_b": row.get("oracle_b", ""),
                    "oracle_a_outcome": row.get("oracle_a_outcome", ""),
                    "oracle_b_outcome": row.get("oracle_b_outcome", ""),
                }
            )

    pair_count = len(parsed_a)
    agreement_count = sum(
        1 for left, right in zip(parsed_a, parsed_b, strict=True) if left == right
    )
    agreement_rate = agreement_count / pair_count if pair_count else None
    kappa = _cohen_kappa(parsed_a, parsed_b)

    return {
        "schema_version": "earnbench.cross_oracle_agreement.v1",
        "pair_count": pair_count,
        "agreement_count": agreement_count,
        "agreement_rate": agreement_rate,
        "cohen_kappa": kappa,
        "disagreement_count": len(disagreements),
        "pass_criteria": {
            "cohen_kappa_min": 0.70,
        },
        "disagreements": disagreements,
    }


def _write_csv(path: Path, columns: tuple[str, ...], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def generate_cross_oracle_agreement_report(
    table_path: Path,
    output_dir: Path,
) -> CrossOracleAgreementResult:
    """Validate table, analyze agreement, and write artifacts."""
    rows = load_cross_oracle_rows(table_path)
    payload = analyze_cross_oracle_agreement(rows)

    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    json_path = output_dir / CROSS_ORACLE_AGREEMENT_JSON
    csv_path = output_dir / CROSS_ORACLE_DISAGREEMENTS_CSV

    disagreements = payload.pop("disagreements")
    with json_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")

    _write_csv(csv_path, DISAGREEMENT_COLUMNS, disagreements)  # type: ignore[arg-type]

    return CrossOracleAgreementResult(
        output_dir=output_dir,
        agreement_json=json_path,
        disagreements_csv=csv_path,
    )
