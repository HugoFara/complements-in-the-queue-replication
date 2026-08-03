"""The OpenDSS-direct constraint oracle (M15): the same joint-allocation
question `AllocationProblem` answers, but for topology pandapower's solvers
cannot represent at all.

M14 built an unbalanced oracle (`AllocationProblem.evaluate_sizes_3ph`) via
`pandapower.runpp_3ph`, but scoped it to networks whose topology is
symmetric three-phase throughout -- `runpp_3ph` is a symmetrical-component
solver that cannot correctly represent single-phase laterals or split-phase
secondaries at all (pandapower issue #873, open, no workaround). True
North-American topology (IEEE13/123, SMART-DS) needs a genuinely different
power-flow engine, not an extension of the pandapower-based oracle.

Rather than attempt to fix #873 itself -- the pandapower maintainers' own
framing is a from-scratch phase-domain solver rewrite, essentially
duplicating what OpenDSS's own engine already does -- `AllocationProblemDSS`
drives OpenDSS directly via `opendssdirect.NewContext()`, an isolated DSS
engine instance per evaluation (the analogue of `copy.deepcopy(net)` in the
pandapower-based oracle). This sidesteps the pandapower limitation entirely
rather than fixing it, and turns out faster per-solve than either
`AllocationProblem.evaluate`/`evaluate_sizes_3ph` on the networks measured
so far (~20-25 ms vs. ~150-260 ms), since OpenDSS's own compiled engine is
solving the master file directly rather than through pandapower's Python/
pandas data model.

Requests attach as a single-phase, wye-connected `New Load` at
``bus.phase`` (OpenDSS's own dot-notation), the natural single-phase-tap
attach point on this topology -- matching every vendored IEEE13/123 master
file's own convention for its single-phase loads.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

import opendssdirect as dss_base

from allocation.problem import AllocationResult


@dataclass(frozen=True)
class DSSRequest:
    """One candidate interconnection request, for `AllocationProblemDSS`.

    Unlike `allocation.problem.Request`, ``bus`` is an OpenDSS bus name (a
    string, not a pandapower bus index) and ``phase`` (1, 2, or 3) selects
    which single line-to-neutral phase the request taps -- the real
    single-phase-tap attach point this axis exists to check, matching how
    every vendored IEEE13/123 master file declares its own single-phase
    loads. ``p_kw`` is signed exactly like `Request.p_mw`: positive demand,
    negative export.
    """

    id: str
    bus: str
    phase: int
    p_kw: float
    q_kvar: float = 0.0
    queue_order: int = 0


@dataclass
class AllocationProblemDSS:
    """The OpenDSS-direct analogue of `AllocationProblem` (M15).

    ``master_path`` is redirected fresh, in a fresh `opendssdirect.
    NewContext()`, on every `evaluate_sizes` call -- the same "start from
    the known-good base state every time" discipline `AllocationProblem`
    gets from `copy.deepcopy(self.net)`, just achieved differently since an
    OpenDSS circuit isn't a plain Python object that can be deep-copied.

    ``vmin_pu``/``vmax_pu``/``max_loading_percent`` mean exactly what they
    mean on `AllocationProblem` -- the planning band a subset must stay
    inside to be `feasible`.
    """

    master_path: str
    requests: list[DSSRequest]
    vmin_pu: float = 0.94
    vmax_pu: float = 1.10
    max_loading_percent: float = 100.0
    _cache: dict[frozenset, AllocationResult] = field(default_factory=dict, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        ids = [r.id for r in self.requests]
        if len(ids) != len(set(ids)):
            raise ValueError("request ids must be unique")

    def evaluate(self, accepted_ids: Iterable[str]) -> AllocationResult:
        """Accept the given request ids at full requested size. A thin
        wrapper around `evaluate_sizes`, mirroring `AllocationProblem.evaluate`."""
        return self.evaluate_sizes({rid: 1.0 for rid in accepted_ids})

    def evaluate_sizes(self, sizes: Mapping[str, float]) -> AllocationResult:
        """Accept each request id in ``sizes`` at the given fraction (0-1] of
        its full requested magnitude, solve via a fresh OpenDSS context, and
        report feasibility + objectives -- the same `AllocationResult` shape
        `AllocationProblem.evaluate_sizes` returns, so every downstream
        consumer (search, `compare_to_baseline`, `queue_fairness_violations`)
        works unmodified.
        """
        by_id = {r.id: r for r in self.requests}
        unknown = sizes.keys() - by_id.keys()
        if unknown:
            raise ValueError(f"unknown request id(s): {sorted(unknown)}")
        out_of_range = {rid: frac for rid, frac in sizes.items() if not 0.0 <= frac <= 1.0}
        if out_of_range:
            raise ValueError(f"size fraction(s) outside [0, 1]: {out_of_range}")

        granted = {rid: frac for rid, frac in sizes.items() if frac > 0.0}
        accepted = frozenset(granted)

        cache_key = frozenset(granted.items())
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        def _infeasible() -> AllocationResult:
            result = AllocationResult(
                accepted_ids=accepted, converged=False, feasible=False,
                accommodated_mw=0.0, vmin_pu=float("nan"), vmax_pu=float("nan"),
                max_line_loading_percent=float("nan"), max_trafo_loading_percent=float("nan"),
                voltage_violation_pu=float("inf"), thermal_violation_percent=float("inf"),
                reinforcement_cost_proxy=float("inf"), sizes=granted)
            self._cache[cache_key] = result
            return result

        ctx = dss_base.NewContext()
        ctx.Command("Clear")
        ctx.Command(f'Redirect "{self.master_path}"')
        for rid, frac in granted.items():
            r = by_id[rid]
            ctx.Circuit.SetActiveBus(r.bus)
            kv_ln = ctx.Bus.kVBase()
            ctx.Command(
                f"New Load.{r.id} Bus1={r.bus}.{r.phase} Phases=1 Conn=Wye "
                f"kV={kv_ln} kW={frac * r.p_kw} kvar={frac * r.q_kvar}")
        ctx.Solution.Solve()

        if not ctx.Solution.Converged():
            return _infeasible()

        vmin, vmax = float("inf"), float("-inf")
        for name in ctx.Circuit.AllBusNames():
            ctx.Circuit.SetActiveBus(name)
            mags = [m for m in ctx.Bus.puVmagAngle()[::2] if m > 0]
            if mags:
                vmin = min(vmin, min(mags))
                vmax = max(vmax, max(mags))
        if vmin > vmax:  # no bus contributed a voltage -- treat as a failed solve, not a silent pass
            return _infeasible()

        max_line_pct = _max_line_loading_percent(ctx)
        max_trafo_pct = _max_trafo_loading_percent(ctx)

        voltage_violation = max(0.0, self.vmin_pu - vmin) + max(0.0, vmax - self.vmax_pu)
        thermal_violation = (max(0.0, max_line_pct - self.max_loading_percent)
                              + max(0.0, max_trafo_pct - self.max_loading_percent))
        feasible = voltage_violation == 0.0 and thermal_violation == 0.0
        accommodated_mw = sum(frac * abs(by_id[rid].p_kw) / 1000.0 for rid, frac in granted.items())

        result = AllocationResult(
            accepted_ids=accepted, converged=True, feasible=feasible,
            accommodated_mw=accommodated_mw, vmin_pu=vmin, vmax_pu=vmax,
            max_line_loading_percent=max_line_pct, max_trafo_loading_percent=max_trafo_pct,
            voltage_violation_pu=voltage_violation, thermal_violation_percent=thermal_violation,
            reinforcement_cost_proxy=thermal_violation + voltage_violation * 100,
            sizes=granted)
        self._cache[cache_key] = result
        return result


def _max_line_loading_percent(ctx) -> float:
    """Peak line current vs. `Lines.NormAmps` -- valid directly as a percent
    since both ends of a line sit at the same voltage."""
    worst = 0.0
    i = ctx.Lines.First()
    while i:
        name = ctx.Lines.Name()
        normal_amps = ctx.Lines.NormAmps()
        ctx.Circuit.SetActiveElement("Line." + name)
        currents = [c for c in ctx.CktElement.CurrentsMagAng()[::2] if c > 0]
        if normal_amps > 0 and currents:
            worst = max(worst, max(currents) / normal_amps * 100)
        i = ctx.Lines.Next()
    return worst


def _max_trafo_loading_percent(ctx) -> float:
    """Peak per-terminal apparent power vs. `Transformers.kVA` (the rated
    capacity) -- current-vs-`NormalAmps` (the `_max_line_loading_percent`
    approach) is wrong here, checked directly rather than assumed to
    generalize: a transformer's two windings sit at different voltage
    levels, so the same power flow means very different currents on each
    side, and `NormalAmps` reports only one winding's rating. Comparing raw
    current against it reported IEEE13's own substation transformer -- a
    5000 kVA 115/4.16 kV unit serving its base-case load at a real ~79%
    -- as 2263% "overloaded" before this was caught and fixed. Apparent
    power is voltage-independent, so summing each terminal's per-conductor
    P/Q and comparing the larger terminal's |S| against the rated kVA
    avoids the mismatch entirely (and matches what pandapower's own
    `res_trafo.loading_percent` measures)."""
    worst = 0.0
    i = ctx.Transformers.First()
    while i:
        name = ctx.Transformers.Name()
        rated_kva = ctx.Transformers.kVA()
        ctx.Circuit.SetActiveElement("Transformer." + name)
        powers = ctx.CktElement.Powers()
        n_term = ctx.CktElement.NumTerminals()
        n_cond = ctx.CktElement.NumConductors()
        if rated_kva > 0:
            for t in range(n_term):
                p = sum(powers[2 * (t * n_cond + c)] for c in range(n_cond))
                q = sum(powers[2 * (t * n_cond + c) + 1] for c in range(n_cond))
                worst = max(worst, (p * p + q * q) ** 0.5 / rated_kva * 100)
        i = ctx.Transformers.Next()
    return worst
