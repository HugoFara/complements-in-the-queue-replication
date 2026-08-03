"""M1's optimizer: NSGA-II over accept/reject subsets, using `AllocationProblem` as
the constraint oracle.

Each individual is a length-K bitstring (one bit per candidate request); NSGA2
searches it as a genuine two-objective problem -- minimize [-accommodated_mw,
reinforcement_cost_proxy] -- rather than hard-rejecting infeasible subsets, per
ROADMAP.md's Hard Parts note that an infeasible candidate needs a gradient to
climb out of, not a wall. `reinforcement_cost_proxy` is 0 exactly for fully
feasible subsets and positive otherwise, so filtering the final population to
cost == 0 recovers the population `AllocationProblem.evaluate` would call
feasible -- the set M1's "done when" bar compares against the greedy baseline.

``nsga2_search_continuous`` (M7) is the same idea over a different decision
space: each gene is a continuous fraction in [0, 1] (`evaluate_sizes`, not
`evaluate`) instead of a binary accept/reject bit, so it needs real-valued
crossover/mutation operators (SBX, polynomial mutation) instead of the
binary ones `nsga2_search` uses. Since {0, 1} is a strict subset of [0, 1],
its optimum can never be worse than binary `nsga2_search`'s.

``nsga2_search_fair`` (M9) adds `queue_fairness_violations` as a genuine
third objective -- the queue-order-fairness term ROADMAP.md's one-line
target named from the start but no prior milestone implemented. A separate
function rather than a third objective bolted onto `nsga2_search` itself:
spreading the same generation budget across a 3rd objective could shift the
converged front away from the max-accommodated-MW point M1-M8's own results
already validated `nsga2_search` against, so those stay untouched.

``n_jobs`` (M10) parallelizes the one `runpp` solve per individual --
ROADMAP.md's own named bottleneck -- across worker processes via pymoo's
`StarmapParallelization`, using `multiprocessing.Pool`. Unlike the
objective-adding milestones above, this changes *nothing* about what is
searched (same seed, same operators, same per-individual fitness), only how
fast the fixed sequence of evaluations gets computed, so it is added as a
parameter on the existing functions rather than a new one -- `n_jobs=1`
(the default) reproduces the exact pre-M10 sequential code path.

``nsga2_search_continuous_fair`` (M12) is the continuous-sizing analogue of
``nsga2_search_fair`` -- real-valued genes (like ``nsga2_search_continuous``)
plus ``queue_fairness_violations`` as a genuine third objective (like
``nsga2_search_fair``). "Accepted," for fairness purposes, means a nonzero
granted fraction (matching ``AllocationResult.accepted_ids``'s own
definition) -- a partial award still jumps the queue past an earlier request
that got nothing. A new function, not a flag on either existing one, for the
same reason M7/M9 themselves were new functions: it changes what is
searched, so M1-M10's already-validated results on the two prior functions
stay untouched.

``nsga2_search_3ph`` (M14) is the unbalanced analogue of `nsga2_search`:
identical binary-subset search, evaluated through
`AllocationProblem.evaluate_3ph` (``pandapower.runpp_3ph``) instead of
`evaluate`, for networks whose topology that solver can actually represent
(see `evaluate_sizes_3ph`'s docstring).

``nsga2_search_dss`` (M15) is the OpenDSS-direct analogue of `nsga2_search`:
identical binary-subset search, evaluated through
`AllocationProblemDSS.evaluate` -- true North-American single-phase-lateral
topology `runpp_3ph` cannot represent at all (pandapower issue #873).

``nsga2_search_dss_seeded`` (M28) is `nsga2_search_dss` with generation 0
warm-started from given accept-sets (e.g. a baseline's own result) instead of
pure `BinaryRandomSampling` -- isolating whether a search loses to a baseline
because there's genuinely no better subset, or because a huge feeder's random
initial population never got close enough to one within the generation budget.
A new function, not a flag on `nsga2_search_dss`, so M15's already-cited numbers
stay untouched.

``callback`` (plumbing only, no new search behavior) is a passthrough to
pymoo's own per-generation `Callback` hook, added to `nsga2_search`,
`nsga2_search_continuous`, and `nsga2_search_continuous_fair` so a single
long run can be sampled at every generation instead of restarting from
scratch at each budget level -- the instrumentation the still-open
question from M7/M12 (does continuous NSGA-II's underperformance at equal
budget close with more budget, or is it a convergence-operator ceiling
regardless) needs to actually be settled with a measurement rather than
left as a diagnosed-but-unresolved guess.
"""

from __future__ import annotations

import multiprocessing
from contextlib import nullcontext
from dataclasses import dataclass

import numpy as np
from pymoo.algorithms.moo.nsga2 import NSGA2
from pymoo.core.problem import ElementwiseProblem
from pymoo.operators.crossover.pntx import TwoPointCrossover
from pymoo.operators.crossover.sbx import SBX
from pymoo.operators.mutation.bitflip import BitflipMutation
from pymoo.operators.mutation.pm import PM
from pymoo.operators.sampling.rnd import BinaryRandomSampling, FloatRandomSampling
from pymoo.optimize import minimize
from pymoo.parallelization import StarmapParallelization

from allocation.dss_problem import AllocationProblemDSS
from allocation.problem import AllocationProblem, AllocationResult, queue_fairness_violations


def _pool(n_jobs: int):
    """A `multiprocessing.Pool` context manager for `n_jobs > 1`, or a no-op
    context yielding `None` for `n_jobs == 1` -- so callers get the exact
    pre-M10 sequential `elementwise_runner` default (`LoopedElementwiseEvaluation`)
    when parallelization isn't requested, rather than a one-worker Pool that
    would behave equivalently but slower (process-spawn overhead for nothing).
    """
    if n_jobs == 1:
        return nullcontext(None)
    return multiprocessing.Pool(processes=n_jobs if n_jobs > 0 else None)


def _runner(pool) -> StarmapParallelization | None:
    return StarmapParallelization(pool.starmap) if pool is not None else None


class _SubsetProblem(ElementwiseProblem):
    """Wraps AllocationProblem as a pymoo binary problem: one gene per request."""

    def __init__(self, problem: AllocationProblem, elementwise_runner=None):
        self.problem = problem
        self.request_ids = [r.id for r in problem.requests]
        kwargs = {"elementwise_runner": elementwise_runner} if elementwise_runner is not None else {}
        super().__init__(n_var=len(self.request_ids), n_obj=2, xl=0, xu=1, vtype=bool, **kwargs)

    def _evaluate(self, x, out, *args, **kwargs):
        accepted = [rid for rid, bit in zip(self.request_ids, x) if bit]
        result = self.problem.evaluate(accepted)
        out["F"] = [-result.accommodated_mw, result.reinforcement_cost_proxy]


class _SubsetProblem3ph(ElementwiseProblem):
    """Same as `_SubsetProblem`, but evaluated via `evaluate_3ph` (M14) --
    `pandapower.runpp_3ph` instead of `runpp`."""

    def __init__(self, problem: AllocationProblem, elementwise_runner=None):
        self.problem = problem
        self.request_ids = [r.id for r in problem.requests]
        kwargs = {"elementwise_runner": elementwise_runner} if elementwise_runner is not None else {}
        super().__init__(n_var=len(self.request_ids), n_obj=2, xl=0, xu=1, vtype=bool, **kwargs)

    def _evaluate(self, x, out, *args, **kwargs):
        accepted = [rid for rid, bit in zip(self.request_ids, x) if bit]
        result = self.problem.evaluate_3ph(accepted)
        out["F"] = [-result.accommodated_mw, result.reinforcement_cost_proxy]


class _SubsetProblemDSS(ElementwiseProblem):
    """Same as `_SubsetProblem`, but wraps `AllocationProblemDSS` (M15) --
    OpenDSS's own solver, driven directly, instead of pandapower."""

    def __init__(self, problem: AllocationProblemDSS, elementwise_runner=None):
        self.problem = problem
        self.request_ids = [r.id for r in problem.requests]
        kwargs = {"elementwise_runner": elementwise_runner} if elementwise_runner is not None else {}
        super().__init__(n_var=len(self.request_ids), n_obj=2, xl=0, xu=1, vtype=bool, **kwargs)

    def _evaluate(self, x, out, *args, **kwargs):
        accepted = [rid for rid, bit in zip(self.request_ids, x) if bit]
        result = self.problem.evaluate(accepted)
        out["F"] = [-result.accommodated_mw, result.reinforcement_cost_proxy]


class _FairSubsetProblem(ElementwiseProblem):
    """Same as `_SubsetProblem`, plus `queue_fairness_violations` as a third
    objective -- no extra `runpp` cost, since it's a pure function of the
    accept-set and each request's `queue_order`."""

    def __init__(self, problem: AllocationProblem, elementwise_runner=None):
        self.problem = problem
        self.request_ids = [r.id for r in problem.requests]
        kwargs = {"elementwise_runner": elementwise_runner} if elementwise_runner is not None else {}
        super().__init__(n_var=len(self.request_ids), n_obj=3, xl=0, xu=1, vtype=bool, **kwargs)

    def _evaluate(self, x, out, *args, **kwargs):
        accepted = [rid for rid, bit in zip(self.request_ids, x) if bit]
        result = self.problem.evaluate(accepted)
        violations = queue_fairness_violations(self.problem.requests, accepted)
        out["F"] = [-result.accommodated_mw, result.reinforcement_cost_proxy, violations]


class _SizingProblem(ElementwiseProblem):
    """Wraps AllocationProblem as a pymoo real-valued problem: one fraction per request."""

    def __init__(self, problem: AllocationProblem, elementwise_runner=None):
        self.problem = problem
        self.request_ids = [r.id for r in problem.requests]
        kwargs = {"elementwise_runner": elementwise_runner} if elementwise_runner is not None else {}
        super().__init__(n_var=len(self.request_ids), n_obj=2, xl=0.0, xu=1.0, **kwargs)

    def _evaluate(self, x, out, *args, **kwargs):
        sizes = {rid: float(frac) for rid, frac in zip(self.request_ids, x)}
        result = self.problem.evaluate_sizes(sizes)
        out["F"] = [-result.accommodated_mw, result.reinforcement_cost_proxy]


class _FairSizingProblem(ElementwiseProblem):
    """Same as `_SizingProblem`, plus `queue_fairness_violations` as a third
    objective -- the continuous-sizing analogue of `_FairSubsetProblem`.
    "Accepted" means a nonzero granted fraction (`result.accepted_ids`),
    matching `AllocationResult`'s own definition."""

    def __init__(self, problem: AllocationProblem, elementwise_runner=None):
        self.problem = problem
        self.request_ids = [r.id for r in problem.requests]
        kwargs = {"elementwise_runner": elementwise_runner} if elementwise_runner is not None else {}
        super().__init__(n_var=len(self.request_ids), n_obj=3, xl=0.0, xu=1.0, **kwargs)

    def _evaluate(self, x, out, *args, **kwargs):
        sizes = {rid: float(frac) for rid, frac in zip(self.request_ids, x)}
        result = self.problem.evaluate_sizes(sizes)
        violations = queue_fairness_violations(self.problem.requests, result.accepted_ids)
        out["F"] = [-result.accommodated_mw, result.reinforcement_cost_proxy, violations]


def nsga2_search(problem: AllocationProblem, pop_size: int = 40, n_gen: int = 40,
                  seed: int = 0, n_jobs: int = 1, callback=None) -> list[AllocationResult]:
    """Run NSGA-II over accept/reject subsets; return the final population, re-evaluated.

    Reproducible from ``seed`` -- independent of ``n_jobs`` (see
    `test_nsga2_search_parallel_matches_sequential`): each individual's
    fitness is a pure function of its own bitstring, computed on a fresh
    `copy.deepcopy` of the base network, so which worker process happens to
    run it doesn't change the result, only how long the generation takes wall-
    clock. Returns every individual in the final generation (not just the
    non-dominated front) re-evaluated through `AllocationProblem.evaluate` so
    callers get the full `AllocationResult`, not just the two objective values
    pymoo tracks internally.

    Each individual costs one `runpp` solve (ROADMAP.md's Hard Parts flags
    this as the scaling bottleneck), so the defaults are sized for a ~40-request
    problem (~0.15 s/solve on the EULV feeder -> ~1600 evals, a few minutes).
    ``n_jobs=1`` (default) evaluates sequentially, identical to the pre-M10
    code path; ``n_jobs>1`` evaluates each generation's population across that
    many worker processes via pymoo's `StarmapParallelization`; ``n_jobs<=0``
    uses `os.cpu_count()` workers. `AllocationProblem`'s own M10 evaluation
    cache is per-instance, so it is *not* shared across worker processes --
    parallel runs trade some of that cache's hit rate for the parallelism.

    ``callback`` is passed straight through to pymoo's `minimize` (a
    `pymoo.core.callback.Callback` instance, notified once per generation with
    the live `algorithm` object) -- plumbing for a convergence-vs-budget study
    (one long run, sampled at every generation, instead of restarting from
    scratch at each budget level), not a new search behavior; `None` (default)
    reproduces the exact pre-existing code path.
    """
    with _pool(n_jobs) as pool:
        subset_problem = _SubsetProblem(problem, elementwise_runner=_runner(pool))
        algorithm = NSGA2(
            pop_size=pop_size,
            sampling=BinaryRandomSampling(),
            crossover=TwoPointCrossover(),
            mutation=BitflipMutation(),
            eliminate_duplicates=True,
        )
        res = minimize(subset_problem, algorithm, ("n_gen", n_gen), seed=seed, verbose=False,
                        **({"callback": callback} if callback is not None else {}))

    xs = res.pop.get("X")
    seen: set[frozenset[str]] = set()
    results = []
    for x in xs:
        accepted = frozenset(rid for rid, bit in zip(subset_problem.request_ids, x) if bit)
        if accepted in seen:
            continue
        seen.add(accepted)
        results.append(problem.evaluate(accepted))
    return results


def nsga2_search_3ph(problem: AllocationProblem, pop_size: int = 40, n_gen: int = 40,
                      seed: int = 0, n_jobs: int = 1) -> list[AllocationResult]:
    """Same as `nsga2_search` (M14), but evaluated through
    `AllocationProblem.evaluate_3ph` (``pandapower.runpp_3ph``) instead of
    `evaluate` -- for a network whose topology that solver can actually
    represent (see `evaluate_sizes_3ph`'s docstring: every bus/line
    genuinely 3-phase, not a network with single-phase laterals).
    """
    with _pool(n_jobs) as pool:
        subset_problem = _SubsetProblem3ph(problem, elementwise_runner=_runner(pool))
        algorithm = NSGA2(
            pop_size=pop_size,
            sampling=BinaryRandomSampling(),
            crossover=TwoPointCrossover(),
            mutation=BitflipMutation(),
            eliminate_duplicates=True,
        )
        res = minimize(subset_problem, algorithm, ("n_gen", n_gen), seed=seed, verbose=False)

    xs = res.pop.get("X")
    seen: set[frozenset[str]] = set()
    results = []
    for x in xs:
        accepted = frozenset(rid for rid, bit in zip(subset_problem.request_ids, x) if bit)
        if accepted in seen:
            continue
        seen.add(accepted)
        results.append(problem.evaluate_3ph(accepted))
    return results


def nsga2_search_dss(problem: AllocationProblemDSS, pop_size: int = 40, n_gen: int = 40,
                      seed: int = 0, n_jobs: int = 1) -> list[AllocationResult]:
    """The OpenDSS-direct analogue of `nsga2_search` (M15): identical
    binary-subset search, over `AllocationProblemDSS.evaluate` -- true
    North-American single-phase-lateral topology `runpp_3ph` cannot
    represent at all (pandapower issue #873; see `dss_problem.py`'s module
    docstring). Each individual solves via a fresh `opendssdirect.
    NewContext()`, independent of every other -- safe to parallelize the
    same way as `nsga2_search`.
    """
    with _pool(n_jobs) as pool:
        subset_problem = _SubsetProblemDSS(problem, elementwise_runner=_runner(pool))
        algorithm = NSGA2(
            pop_size=pop_size,
            sampling=BinaryRandomSampling(),
            crossover=TwoPointCrossover(),
            mutation=BitflipMutation(),
            eliminate_duplicates=True,
        )
        res = minimize(subset_problem, algorithm, ("n_gen", n_gen), seed=seed, verbose=False)

    xs = res.pop.get("X")
    seen: set[frozenset[str]] = set()
    results = []
    for x in xs:
        accepted = frozenset(rid for rid, bit in zip(subset_problem.request_ids, x) if bit)
        if accepted in seen:
            continue
        seen.add(accepted)
        results.append(problem.evaluate(accepted))
    return results


def nsga2_search_dss_seeded(problem: AllocationProblemDSS, seed_accept_sets: list[set[str]],
                             pop_size: int = 40, n_gen: int = 40, seed: int = 0,
                             n_jobs: int = 1) -> list[AllocationResult]:
    """M28: same search as `nsga2_search_dss`, except generation 0 is warm-started
    with `seed_accept_sets` (e.g. `skip_continue_baseline_dss` and
    `greedy_baseline_dss`'s own accept-sets) instead of drawn purely at random.

    `nsga2_search_dss`'s `BinaryRandomSampling()` starts blind: on a SMART-DS
    feeder with thousands of candidate buses, a population of `pop_size` random
    bitstrings has no realistic chance of resembling a good accept-set, so a
    fixed generation budget can run out before the search rediscovers what a
    baseline already knows for free -- a search-budget confound experiment 57
    flagged but could not separate from a genuine "no room to improve" result.
    Seeding removes that confound: NSGA-II is elitist, so its final population
    can never do worse than the best seed handed to it. If it *still* fails to
    beat a seeded baseline, that is real evidence of no exploitable room, not
    an artifact of where generation 0 started.

    Non-seed rows of generation 0 are drawn from the same `random_state.random(
    (n, n_var)) < 0.5` distribution `BinaryRandomSampling` uses internally, just
    generated directly here (via `numpy`) so the seeded rows can be spliced in
    -- the only difference from `nsga2_search_dss` is what's IN generation 0,
    not how the rest of it is generated or how later generations evolve.
    pymoo's `Initialization.do` accepts a raw `np.ndarray` for `sampling`
    exactly like `Population.new(X=sampling)`, so no custom `Sampling` subclass
    is needed.
    """
    request_ids = [r.id for r in problem.requests]
    n_var = len(request_ids)
    rng = np.random.default_rng(seed)
    x0 = rng.random((pop_size, n_var)) < 0.5
    for i, accepted in enumerate(seed_accept_sets[:pop_size]):
        x0[i] = [rid in accepted for rid in request_ids]

    with _pool(n_jobs) as pool:
        subset_problem = _SubsetProblemDSS(problem, elementwise_runner=_runner(pool))
        algorithm = NSGA2(
            pop_size=pop_size,
            sampling=x0,
            crossover=TwoPointCrossover(),
            mutation=BitflipMutation(),
            eliminate_duplicates=True,
        )
        res = minimize(subset_problem, algorithm, ("n_gen", n_gen), seed=seed, verbose=False)

    xs = res.pop.get("X")
    seen: set[frozenset[str]] = set()
    results = []
    for x in xs:
        accepted = frozenset(rid for rid, bit in zip(subset_problem.request_ids, x) if bit)
        if accepted in seen:
            continue
        seen.add(accepted)
        results.append(problem.evaluate(accepted))
    return results


def nsga2_search_fair(problem: AllocationProblem, pop_size: int = 40, n_gen: int = 40,
                       seed: int = 0, n_jobs: int = 1) -> list[AllocationResult]:
    """Same as `nsga2_search`, but a genuine 3-objective search: minimize
    [-accommodated_mw, reinforcement_cost_proxy, queue_fairness_violations]
    (M9). `AllocationResult` itself carries no fairness field (it's a
    property of the accept-set relative to every request's ``queue_order``,
    not of one evaluated result in isolation) -- call
    `queue_fairness_violations(problem.requests, result.accepted_ids)` on
    each returned result to see where it sits on the capacity-vs-fairness
    trade-off.

    ``n_jobs`` (M10) parallelizes the same way as `nsga2_search` -- see its
    docstring.
    """
    with _pool(n_jobs) as pool:
        fair_problem = _FairSubsetProblem(problem, elementwise_runner=_runner(pool))
        algorithm = NSGA2(
            pop_size=pop_size,
            sampling=BinaryRandomSampling(),
            crossover=TwoPointCrossover(),
            mutation=BitflipMutation(),
            eliminate_duplicates=True,
        )
        res = minimize(fair_problem, algorithm, ("n_gen", n_gen), seed=seed, verbose=False)

    xs = res.pop.get("X")
    seen: set[frozenset[str]] = set()
    results = []
    for x in xs:
        accepted = frozenset(rid for rid, bit in zip(fair_problem.request_ids, x) if bit)
        if accepted in seen:
            continue
        seen.add(accepted)
        results.append(problem.evaluate(accepted))
    return results


def nsga2_search_continuous(problem: AllocationProblem, pop_size: int = 40, n_gen: int = 40,
                             seed: int = 0, n_jobs: int = 1, callback=None) -> list[AllocationResult]:
    """Run NSGA-II over continuous request-size fractions; return the final
    population, re-evaluated (M7's continuous request sizing).

    Same idea as `nsga2_search`, but each gene is a fraction in [0, 1]
    (`AllocationProblem.evaluate_sizes`) instead of a binary accept/reject
    bit, so it uses real-valued operators (`SBX` crossover, polynomial
    mutation `PM`, `FloatRandomSampling`) instead of the binary ones
    `nsga2_search` uses. No id-based deduplication of the final population
    (unlike `nsga2_search`'s `seen` set): exact duplicate float vectors are
    rare, and pymoo's own `eliminate_duplicates` already collapses
    near-identical individuals during the search.

    ``n_jobs`` (M10) parallelizes the same way as `nsga2_search` -- see its
    docstring. ``callback`` (see `nsga2_search`'s docstring) is the same
    convergence-logging passthrough -- this is the function M7's own
    genuine negative result (continuous NSGA-II underperforming binary at
    equal budget, a partial larger-budget check left inconclusive) was found
    on, so it is the one this hook exists for.
    """
    with _pool(n_jobs) as pool:
        sizing_problem = _SizingProblem(problem, elementwise_runner=_runner(pool))
        algorithm = NSGA2(
            pop_size=pop_size,
            sampling=FloatRandomSampling(),
            crossover=SBX(),
            mutation=PM(),
            eliminate_duplicates=True,
        )
        res = minimize(sizing_problem, algorithm, ("n_gen", n_gen), seed=seed, verbose=False,
                        **({"callback": callback} if callback is not None else {}))

    xs = res.pop.get("X")
    results = []
    for x in xs:
        sizes = {rid: float(frac) for rid, frac in zip(sizing_problem.request_ids, x)}
        results.append(problem.evaluate_sizes(sizes))
    return results


def nsga2_search_continuous_fair(problem: AllocationProblem, pop_size: int = 40, n_gen: int = 40,
                                  seed: int = 0, n_jobs: int = 1, callback=None) -> list[AllocationResult]:
    """Same as `nsga2_search_continuous`, plus `queue_fairness_violations` as
    a genuine third objective (M12) -- the continuous-sizing analogue of
    `nsga2_search_fair`. "Accepted," for fairness purposes, means a nonzero
    granted fraction (`AllocationResult.accepted_ids`): a partial award still
    counts as jumping the queue past an earlier request that got nothing.

    ``n_jobs`` (M10) parallelizes the same way as `nsga2_search` -- see its
    docstring. ``callback`` is the same convergence-logging passthrough as
    `nsga2_search_continuous`'s -- M12 already found this variant's own
    result inherits M7's convergence gap rather than showing a fresh
    trade-off; this is the hook for checking whether a larger budget changes
    that once `nsga2_search_continuous` itself is understood.
    """
    with _pool(n_jobs) as pool:
        fair_sizing_problem = _FairSizingProblem(problem, elementwise_runner=_runner(pool))
        algorithm = NSGA2(
            pop_size=pop_size,
            sampling=FloatRandomSampling(),
            crossover=SBX(),
            mutation=PM(),
            eliminate_duplicates=True,
        )
        res = minimize(fair_sizing_problem, algorithm, ("n_gen", n_gen), seed=seed, verbose=False,
                        **({"callback": callback} if callback is not None else {}))

    xs = res.pop.get("X")
    results = []
    for x in xs:
        sizes = {rid: float(frac) for rid, frac in zip(fair_sizing_problem.request_ids, x)}
        results.append(problem.evaluate_sizes(sizes))
    return results


@dataclass
class ParetoComparison:
    """M1's acceptance check: does the search dominate the greedy baseline?"""

    baseline: AllocationResult
    best_feasible_search: AllocationResult | None
    dominates: bool


def compare_to_baseline(search_results: list[AllocationResult],
                         baseline: AllocationResult) -> ParetoComparison:
    """Best fully-feasible (cost == 0) search result vs. the greedy baseline.

    "Dominates" here means the M1 bar from ROADMAP.md: at least as much
    accommodated MW, at equal (feasible) or lower reinforcement cost, strictly
    better on at least one.
    """
    feasible = [r for r in search_results if r.feasible]
    if not feasible:
        return ParetoComparison(baseline=baseline, best_feasible_search=None, dominates=False)
    best = max(feasible, key=lambda r: r.accommodated_mw)
    dominates = (best.accommodated_mw >= baseline.accommodated_mw
                 and best.reinforcement_cost_proxy <= baseline.reinforcement_cost_proxy)
    return ParetoComparison(baseline=baseline, best_feasible_search=best, dominates=dominates)
