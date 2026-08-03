"""allocation: joint interconnection-request allocation on top of a gridsynth network.

Kept a separate package from `gridsynth` (network I/O and conductor
parameters) on purpose: this package -- deciding *which* competing
interconnection requests a shared feeder can jointly host -- is independently
licensable from the model-building core it sits on.
"""

from allocation.baseline import (
    ReinforcedBaselineResult,
    continuous_greedy_baseline,
    greedy_baseline,
    greedy_baseline_3ph,
    greedy_baseline_dss,
    reinforced_greedy_baseline,
    skip_continue_baseline,
    skip_continue_baseline_dss,
)
from allocation.convex import (
    ConvexSearchResult,
    IterativeConvexSearchResult,
    LinDistFlowModel,
    RelaxedEvaluation,
    build_lindistflow_model,
    evaluate_relaxed,
    lindistflow_search,
    lindistflow_search_fair,
    lindistflow_search_iterative,
)
from allocation.dss_convex import (
    LinDistFlowDSSModel,
    build_lindistflow_model_dss,
    evaluate_relaxed_dss,
    lindistflow_search_dss,
)
from allocation.dss_problem import AllocationProblemDSS, DSSRequest
from allocation.locational import feeder_hosting_capacity, locational_hosting_capacity
from allocation.problem import (
    AllocationProblem,
    AllocationResult,
    Request,
    max_queue_displacement,
    queue_fairness_violations,
)
from allocation.reinforcement import (
    DEFAULT_CATALOGUE,
    LineUpgrade,
    ReinforcementCatalogue,
    ReinforcementPlan,
    TrafoUpgrade,
    price_reinforcement,
)
from allocation.search import (
    ParetoComparison,
    compare_to_baseline,
    nsga2_search,
    nsga2_search_3ph,
    nsga2_search_continuous,
    nsga2_search_continuous_fair,
    nsga2_search_dss,
    nsga2_search_dss_seeded,
    nsga2_search_fair,
)

__all__ = [
    "DEFAULT_CATALOGUE",
    "AllocationProblem",
    "AllocationProblemDSS",
    "AllocationResult",
    "ConvexSearchResult",
    "DSSRequest",
    "IterativeConvexSearchResult",
    "LinDistFlowDSSModel",
    "LinDistFlowModel",
    "LineUpgrade",
    "ParetoComparison",
    "ReinforcedBaselineResult",
    "ReinforcementCatalogue",
    "ReinforcementPlan",
    "RelaxedEvaluation",
    "Request",
    "TrafoUpgrade",
    "build_lindistflow_model",
    "build_lindistflow_model_dss",
    "compare_to_baseline",
    "continuous_greedy_baseline",
    "evaluate_relaxed",
    "evaluate_relaxed_dss",
    "feeder_hosting_capacity",
    "greedy_baseline",
    "greedy_baseline_3ph",
    "greedy_baseline_dss",
    "lindistflow_search",
    "lindistflow_search_dss",
    "lindistflow_search_fair",
    "lindistflow_search_iterative",
    "locational_hosting_capacity",
    "max_queue_displacement",
    "nsga2_search",
    "nsga2_search_3ph",
    "nsga2_search_continuous",
    "nsga2_search_continuous_fair",
    "nsga2_search_dss",
    "nsga2_search_dss_seeded",
    "nsga2_search_fair",
    "price_reinforcement",
    "queue_fairness_violations",
    "reinforced_greedy_baseline",
    "skip_continue_baseline",
    "skip_continue_baseline_dss",
]
