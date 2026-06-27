"""EarnBench: executable counterfactual measurement of earned agent success."""

from earnbench.metrics import compute_earned_fraction
from earnbench.outcomes import NominalOutcome, OutcomeStatus, PerturbationResult
from earnbench.perturbations import Perturbation
from earnbench.provenance import (
    PERTURBATION_REGISTRY_VERSION,
    Provenance,
    build_provenance,
)
from earnbench.reports import EarnedFractionReport, EarnedFractionStatus
from earnbench.runs import AgentRun
from earnbench.tasks import Task

__version__ = "0.1.0"

__all__ = [
    "AgentRun",
    "EarnedFractionReport",
    "EarnedFractionStatus",
    "NominalOutcome",
    "OutcomeStatus",
    "PERTURBATION_REGISTRY_VERSION",
    "Perturbation",
    "PerturbationResult",
    "Provenance",
    "Task",
    "build_provenance",
    "compute_earned_fraction",
    "__version__",
]
