"""EarnBench: executable counterfactual measurement of earned agent success."""

from earnbench.metrics import compute_earned_fraction
from earnbench.outcomes import NominalOutcome, OutcomeStatus, PerturbationResult
from earnbench.perturbations import Perturbation
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
    "Perturbation",
    "PerturbationResult",
    "Task",
    "compute_earned_fraction",
    "__version__",
]
