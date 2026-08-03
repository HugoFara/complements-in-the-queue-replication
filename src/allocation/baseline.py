"""The status-quo serial process: accept requests in queue order until the first violation.

This is what M1 (ROADMAP.md) needs a search to *beat* -- the FCFS study
process every ISO ran before cluster/"first-ready, first-served" processing
(FERC Order 2023). It is deliberately dumb: no look-ahead, no reordering, just
"does the queue so far still fit" -- so a later, smaller request never gets a
chance to fill headroom a stalled larger one is blocking.

``reinforced_greedy_baseline`` (M6) is the same serial walk, but instead of
stopping dead at the first violation, it asks `allocation.reinforcement` what
fixing it would actually cost and keeps going if that's affordable -- a
utility-planner strategy, not just a study-queue one.

``continuous_greedy_baseline`` (M7) is the same serial walk again, but
instead of skipping a request outright when it doesn't fit at full size, it
bisects for the largest fraction of it that does -- a *partial award*
strategy, closer to how a real DNO study sometimes responds ("we can't give
you 100%, but here's what does fit") than either of the above.

``greedy_baseline_3ph`` (M14) is the same serial walk as `greedy_baseline`,
but through `AllocationProblem.evaluate_3ph` instead of `evaluate` -- the
unbalanced analogue, for networks `runpp_3ph` can actually represent.

``greedy_baseline_dss`` (M15) is the same serial walk again, over
`AllocationProblemDSS` -- true North-American single-phase-lateral
topology `runpp_3ph` cannot represent at all (pandapower issue #873).
"""

from __future__ import annotations

from dataclasses import dataclass

from allocation.dss_problem import AllocationProblemDSS
from allocation.problem import AllocationProblem, AllocationResult
from allocation.reinforcement import (
    DEFAULT_CATALOGUE,
    LineUpgrade,
    ReinforcementCatalogue,
    TrafoUpgrade,
    price_reinforcement,
)


def greedy_baseline(problem: AllocationProblem) -> AllocationResult:
    """Accept requests in ascending `queue_order`, stopping at the first violation."""
    ordered = sorted(problem.requests, key=lambda r: r.queue_order)
    accepted: list[str] = []
    result = problem.evaluate(accepted)
    for r in ordered:
        candidate = problem.evaluate(accepted + [r.id])
        if not candidate.feasible:
            break
        accepted.append(r.id)
        result = candidate
    return result


def skip_continue_baseline(problem: AllocationProblem) -> AllocationResult:
    """Accept requests in ascending `queue_order`, but *skip* a request that
    doesn't fit and keep walking the rest of the queue, instead of stopping
    dead at the first violation (`greedy_baseline`).

    This is the more honest stand-in for how real first-come-first-served
    interconnection studies actually run: an infeasible request is set aside
    ("your project doesn't fit as-is") and the study continues down the queue,
    rather than freezing the entire queue behind the first project that fails.
    It is exactly `reinforced_greedy_baseline(problem, budget_eur=0.0).result`
    with none of the reinforcement machinery (no re-pricing `runpp` solves on
    each miss) -- a pure, binary, full-size skip-and-continue walk -- and is
    the primary comparator M22 (docs) argues the joint search should be judged
    against, since `greedy_baseline`'s stop-dead behavior understates what the
    status quo already achieves on thermal-limited feeders (where a later
    request on an unstressed part of the feeder genuinely does still fit).

    Still perfectly fair on queue order by a weaker argument than
    `greedy_baseline`'s prefix property: it never *reorders*, only omits, so an
    admitted request can jump ahead of a skipped earlier one (nonzero
    `queue_fairness_violations` is possible, unlike `greedy_baseline`).
    """
    ordered = sorted(problem.requests, key=lambda r: r.queue_order)
    accepted: list[str] = []
    result = problem.evaluate(accepted)
    for r in ordered:
        candidate = problem.evaluate(accepted + [r.id])
        if not candidate.feasible:
            continue
        accepted.append(r.id)
        result = candidate
    return result


def greedy_baseline_3ph(problem: AllocationProblem) -> AllocationResult:
    """The unbalanced analogue of `greedy_baseline` (M14): identical serial-
    queue logic, walking `problem.evaluate_3ph` instead of `problem.evaluate`."""
    ordered = sorted(problem.requests, key=lambda r: r.queue_order)
    accepted: list[str] = []
    result = problem.evaluate_3ph(accepted)
    for r in ordered:
        candidate = problem.evaluate_3ph(accepted + [r.id])
        if not candidate.feasible:
            break
        accepted.append(r.id)
        result = candidate
    return result


def greedy_baseline_dss(problem: AllocationProblemDSS) -> AllocationResult:
    """The OpenDSS-direct analogue of `greedy_baseline` (M15): identical
    serial-queue logic, over `AllocationProblemDSS.evaluate`."""
    ordered = sorted(problem.requests, key=lambda r: r.queue_order)
    accepted: list[str] = []
    result = problem.evaluate(accepted)
    for r in ordered:
        candidate = problem.evaluate(accepted + [r.id])
        if not candidate.feasible:
            break
        accepted.append(r.id)
        result = candidate
    return result


def skip_continue_baseline_dss(problem: AllocationProblemDSS) -> AllocationResult:
    """The OpenDSS-direct analogue of `skip_continue_baseline` (M22): accept
    requests in ascending `queue_order`, but *skip* an infeasible request and
    keep walking the queue, instead of stopping dead at the first violation
    (`greedy_baseline_dss`). The honest FCFS comparator for the DSS oracle --
    the one M22's corpus re-analysis argues the joint search should be judged
    against, rather than the strawman stop-dead walk."""
    ordered = sorted(problem.requests, key=lambda r: r.queue_order)
    accepted: list[str] = []
    result = problem.evaluate(accepted)
    for r in ordered:
        candidate = problem.evaluate(accepted + [r.id])
        if not candidate.feasible:
            continue
        accepted.append(r.id)
        result = candidate
    return result


@dataclass(frozen=True)
class ReinforcedBaselineResult:
    """`reinforced_greedy_baseline`'s outcome: the final AC-verified result plus
    what it cost, in total, to get there."""

    result: AllocationResult
    total_cost_eur: float
    line_upgrades: tuple[LineUpgrade, ...]
    trafo_upgrades: tuple[TrafoUpgrade, ...]


def reinforced_greedy_baseline(
    problem: AllocationProblem,
    catalogue: ReinforcementCatalogue = DEFAULT_CATALOGUE,
    budget_eur: float = float("inf"),
    safety_margin_pct: float = 2.0,
) -> ReinforcedBaselineResult:
    """Walk the queue in order; when a request would violate a limit, price
    reinforcing past it (`allocation.reinforcement.price_reinforcement`) and
    accept it anyway if that's resolvable within ``budget_eur`` -- otherwise
    skip that one request and keep walking the rest of the queue (unlike
    `greedy_baseline`, which stops dead at the first violation).

    Each candidate is re-priced from scratch against ``problem.net``'s
    original, unreinforced ratings (not incrementally against a network a
    previous round already upgraded) -- simpler to reason about than tracking
    upgrade state across rounds, at the cost of some redundant `runpp` solves;
    fine at this package's problem sizes (ROADMAP.md's Hard Parts).
    ``price_reinforcement``'s ``total_cost_eur`` is already the *total* cost
    to serve the whole accepted-so-far set, so it's assigned directly here,
    not accumulated.
    """
    ordered = sorted(problem.requests, key=lambda r: r.queue_order)
    accepted: list[str] = []
    result = problem.evaluate(accepted)
    total_cost = 0.0
    line_upgrades: tuple[LineUpgrade, ...] = ()
    trafo_upgrades: tuple[TrafoUpgrade, ...] = ()

    for r in ordered:
        trial = accepted + [r.id]
        candidate = problem.evaluate(trial)
        if candidate.feasible:
            accepted = trial
            result = candidate
            continue

        plan = price_reinforcement(problem, trial, catalogue=catalogue, safety_margin_pct=safety_margin_pct)
        if not plan.feasible_after_reinforcement or plan.total_cost_eur > budget_eur:
            continue  # this one request can't be resolved (at any price, or not within budget) -- skip, keep going

        accepted = trial
        result = plan.result_after_reinforcement
        total_cost = plan.total_cost_eur
        line_upgrades = plan.line_upgrades
        trafo_upgrades = plan.trafo_upgrades

    return ReinforcedBaselineResult(result=result, total_cost_eur=total_cost,
                                     line_upgrades=line_upgrades, trafo_upgrades=trafo_upgrades)


def continuous_greedy_baseline(
    problem: AllocationProblem, tol_fraction: float = 0.01, min_fraction: float = 0.0,
) -> AllocationResult:
    """Accept requests in ascending `queue_order`; a request that doesn't fit
    at full size gets bisected down to the largest fraction of it that does,
    instead of being skipped outright (`greedy_baseline`'s behavior).

    The continuous-sizing analogue of `locational.locational_hosting_capacity`'s
    bisection, applied request-by-request against the network state already
    built up from earlier accepted (fractional) requests, rather than a
    single request against the bare network.

    A fraction below ``min_fraction`` is dropped entirely (treated as not
    worth a partial award) rather than accepted at a token size; the default
    (0.0) accepts anything feasible, however small. ``tol_fraction`` is the
    bisection resolution (the default resolves each request's fraction to
    within 1% of its full requested size).
    """
    ordered = sorted(problem.requests, key=lambda r: r.queue_order)
    sizes: dict[str, float] = {}
    result = problem.evaluate_sizes(sizes)
    for r in ordered:
        trial = dict(sizes)
        trial[r.id] = 1.0
        candidate = problem.evaluate_sizes(trial)
        if candidate.feasible:
            sizes, result = trial, candidate
            continue

        lo, hi = 0.0, 1.0
        while hi - lo > tol_fraction:
            mid = (lo + hi) / 2
            trial[r.id] = mid
            if problem.evaluate_sizes(trial).feasible:
                lo = mid
            else:
                hi = mid
        if lo > min_fraction:
            trial[r.id] = lo
            sizes = trial
            result = problem.evaluate_sizes(sizes)
        # else: not worth a partial award -- leave this request out entirely (fraction 0)
    return result
