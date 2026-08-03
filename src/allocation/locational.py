"""Locational hosting capacity: the largest single request one bus can host alone.

Named for the metric the external ground truth ROADMAP.md's M2 validates
against uses: Sandia's SAND2015-9712C (Reno & Broderick, "Statistical Analysis
of Feeder and Locational PV Hosting Capacity for 216 Feeders") and the earlier
Coogan/Reno/Grijalva (2014) paper define Locational Hosting Capacity (LHC) as
the largest PV placeable at one bus in isolation, and feeder Hosting Capacity
(HC) as the *minimum* LHC over every candidate bus -- the single-site,
worst-case number this repo's whole joint-allocation thesis argues undersells
what a feeder can actually host once requests are considered together (see
docs/joint-allocation-scope.md).

This is the single-request special case of `AllocationProblem.evaluate` --
useful on its own as the M2 comparison point, and as the baseline M3+ work can
contrast a multi-request search against.
"""

from __future__ import annotations

from allocation.problem import AllocationProblem, Request

_PROBE_ID = "_lhc_probe"


def locational_hosting_capacity(
    problem: AllocationProblem, bus: int, direction: float = -1.0,
    hi_mw: float = 1.0, max_hi_mw: float = 64.0, tol_mw: float = 0.01,
) -> float:
    """Bisect the largest single request (MW magnitude) ``bus`` can host alone.

    ``direction`` is the sign convention: -1.0 (default) is export/PV, +1.0 is
    demand. Doubles ``hi_mw`` until an infeasible upper bound is found (giving
    up at ``max_hi_mw``, in which case the returned value is that cap, not a
    true capacity -- widen ``max_hi_mw`` if that matters), then bisects to
    ``tol_mw``.
    """
    def feasible(size_mw: float) -> bool:
        trial = AllocationProblem(
            net=problem.net, requests=[Request(id=_PROBE_ID, bus=bus, p_mw=direction * size_mw)],
            vmin_pu=problem.vmin_pu, vmax_pu=problem.vmax_pu,
            max_loading_percent=problem.max_loading_percent, runpp_kwargs=problem.runpp_kwargs)
        return trial.evaluate([_PROBE_ID]).feasible

    lo, hi = 0.0, hi_mw
    while hi < max_hi_mw and feasible(hi):
        lo, hi = hi, hi * 2
    if feasible(hi):
        return hi
    while hi - lo > tol_mw:
        mid = (lo + hi) / 2
        if feasible(mid):
            lo = mid
        else:
            hi = mid
    return lo


def feeder_hosting_capacity(
    problem: AllocationProblem, buses: list[int], **kwargs,
) -> tuple[float, int, dict[int, float]]:
    """Feeder HC = the minimum locational hosting capacity over ``buses``.

    A *sampled* estimate, not exhaustive over every bus on the feeder (that's
    the honest caveat to carry into any comparison against a published HC that
    swept all ~60,000 candidate locations) -- see docs/joint-allocation-scope.md.
    Returns (hc_mw, worst_bus, all locational results) for transparency.
    """
    results = {bus: locational_hosting_capacity(problem, bus, **kwargs) for bus in buses}
    worst_bus = min(results, key=results.get)
    return results[worst_bus], worst_bus, results
