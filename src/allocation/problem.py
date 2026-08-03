"""The joint-allocation constraint oracle: given a subset of requests, is it feasible?

Single-site hosting-capacity *scoring* looks at each interconnection request in
isolation. That misses the coupling that actually decides feeder capacity: five
requests that each look fine alone can jointly overload a shared line or
transformer, or push a shared bus over its voltage limit. `AllocationProblem`
is the oracle a search over accept/reject subsets needs to see that coupling --
it takes a candidate subset, adds the corresponding load/generation to a copy of
the base network, runs `runpp`, and reports whether the *combination* is
power-flow-feasible plus the objective values (accommodated MW, a
reinforcement-cost proxy) the search optimizes.

This module is M0 of docs/joint-allocation-scope.md: the formulation + the
`problem` object. The baseline and the actual search (greedy queue-order vs.
NSGA-II over subsets) are M1, built on top of `AllocationProblem.evaluate`.
"""

from __future__ import annotations

import copy
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

import pandapower as pp


@dataclass(frozen=True)
class Request:
    """One candidate interconnection request.

    ``p_mw`` is signed from the network's point of view: positive is added
    demand (e.g. an EV charger), negative is export (e.g. rooftop PV) -- either
    sign stresses a feeder, just in opposite directions (undervoltage/thermal
    for demand, overvoltage for export). ``queue_order`` is the position the
    request arrived in the interconnection queue, lower = earlier; it is not
    used by `evaluate` itself, only by the queue-order baseline built on top.

    ``phase`` (M14) is ``"a"``/``"b"``/``"c"`` for a single-phase-tap request
    -- the real US residential PV/EV interconnection pattern -- or ``None``
    (default) for a balanced 3-phase attach. Only read by
    `evaluate_sizes_3ph`/`evaluate_3ph`; the balanced `evaluate`/
    `evaluate_sizes` path (M0-M13) ignores it entirely, so adding this field
    changes no already-validated result.
    """

    id: str
    bus: int
    p_mw: float
    q_mvar: float = 0.0
    queue_order: int = 0
    phase: str | None = None


@dataclass(frozen=True)
class AllocationResult:
    """Feasibility + objective values for one evaluated subset.

    ``sizes`` is the fraction (0, 1] of each accepted request's full
    ``p_mw``/``q_mvar`` actually granted -- 1.0 for every id in
    ``accepted_ids`` when built via `evaluate` (fixed-size accept/reject);
    a genuine fraction when built via `evaluate_sizes` (continuous request
    sizing). ``accepted_ids`` is always exactly the keys of ``sizes``.
    """

    accepted_ids: frozenset[str]
    converged: bool
    feasible: bool
    accommodated_mw: float
    vmin_pu: float
    vmax_pu: float
    max_line_loading_percent: float
    max_trafo_loading_percent: float
    voltage_violation_pu: float
    thermal_violation_percent: float
    reinforcement_cost_proxy: float
    sizes: Mapping[str, float] = field(default_factory=dict)


def queue_fairness_violations(requests: Iterable[Request], accepted_ids: Iterable[str]) -> int:
    """Count queue-order inversions: pairs of requests where the earlier-queued
    one (lower ``queue_order``) was rejected while a later-queued one was
    accepted -- "jumping the queue," the fairness cost of joint optimization
    over the status-quo serial process (M0/M1's ``greedy_baseline``, which by
    construction always accepts a prefix of the queue and so always scores 0
    here -- see `test_allocation_fairness.py`).

    Computed as, for each accepted request in queue order, how many earlier
    requests were rejected so far -- summing this is exactly the number of
    (rejected-earlier, accepted-later) pairs, in O(N log N) rather than the
    O(N^2) all-pairs count.
    """
    accepted = set(accepted_ids)
    ordered = sorted(requests, key=lambda r: r.queue_order)
    n_earlier_rejected = 0
    violations = 0
    for r in ordered:
        if r.id in accepted:
            violations += n_earlier_rejected
        else:
            n_earlier_rejected += 1
    return violations


def max_queue_displacement(requests: Iterable[Request], accepted_ids: Iterable[str]) -> int:
    """The *worst-case* (L-infinity) queue-fairness metric, the max-displacement
    companion to `queue_fairness_violations`'s total (L1) inversion count.

    For each accepted request, its displacement is how many *earlier-queued*
    requests were rejected ahead of it -- i.e. how many positions it effectively
    jumped up the queue. `queue_fairness_violations` sums this over every
    accepted request; this returns the single maximum instead: the largest
    number of positions any one admitted request cut in front of.

    This is the quantity Fontinele et al. (SSRN 6561541) cap directly -- their
    fairness constraint bounds any request's queue displacement at N/2 positions
    -- and it captures a normatively distinct harm from the inversion *count*:
    a schedule can rack up many small one-position jumps (high inversion count,
    low max displacement) or park one early request behind a wall of later ones
    (low inversion count, high max displacement). Reporting both is what lets a
    "fairness is usually free" claim be checked for metric-robustness rather
    than asserted under one definition. Returns 0 for any prefix accept-set
    (the greedy baseline), the same clean zero `queue_fairness_violations` gives.
    """
    accepted = set(accepted_ids)
    ordered = sorted(requests, key=lambda r: r.queue_order)
    n_earlier_rejected = 0
    worst = 0
    for r in ordered:
        if r.id in accepted:
            worst = max(worst, n_earlier_rejected)
        else:
            n_earlier_rejected += 1
    return worst


@dataclass
class AllocationProblem:
    """A base network plus a pool of candidate requests competing for its headroom.

    ``vmin_pu``/``vmax_pu``/``max_loading_percent`` are the planning band: a
    subset is only ``feasible`` if every bus stays inside the voltage band and
    every line/transformer stays at or under the loading limit. Defaults
    (0.94-1.10 pu, 100%) are the loose end of typical LV statutory limits
    (EN 50160 +-10%).
    ``runpp_kwargs`` passes through to every `pandapower.runpp` call -- some
    real feeders (e.g. EPRI's Ckt5, ~3000 buses with near-zero-impedance
    metering-zone jumpers) need a relaxed tolerance/iteration count that a
    small synthetic network doesn't; see docs/joint-allocation-scope.md's M2.
    """

    net: pp.pandapowerNet
    requests: list[Request]
    vmin_pu: float = 0.94
    vmax_pu: float = 1.10
    max_loading_percent: float = 100.0
    runpp_kwargs: dict = field(default_factory=dict)
    # M10: memoizes `evaluate_sizes` by its normalized (id, fraction) accept-set.
    # Measured, not assumed: instrumenting a real 40-request EULV `nsga2_search`
    # run (pop=40, gen=40) found 7.4% of its 1,640 `runpp` calls were exact
    # repeats of an already-evaluated accept-set (NSGA-II's own duplicate
    # elimination only dedupes *within* one generation's offspring, not across
    # generations -- an elite individual is never re-evaluated, but crossover/
    # mutation can regenerate a genotype tried and discarded earlier). Per-
    # instance, not shared across `n_jobs` worker processes (see `search.py`),
    # so parallel runs see a smaller share of this benefit than sequential ones.
    _cache: dict[frozenset, AllocationResult] = field(default_factory=dict, init=False, repr=False, compare=False)
    # M14: a *separate* cache from `_cache` -- the same (id, fraction)
    # genotype means something different solved via `runpp` vs. `runpp_3ph`,
    # so sharing one cache between the two would silently return a
    # balanced-solve result for an unbalanced query (or vice versa).
    _cache_3ph: dict[frozenset, AllocationResult] = field(default_factory=dict, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        ids = [r.id for r in self.requests]
        if len(ids) != len(set(ids)):
            raise ValueError("request ids must be unique")

    def evaluate(self, accepted_ids: Iterable[str]) -> AllocationResult:
        """Accept the given request ids at full requested size, solve, and
        report feasibility + objectives. A thin wrapper around
        `evaluate_sizes` -- the fixed-size special case where every accepted
        request gets fraction 1.0. See `evaluate_sizes` to accept a request
        at a reduced size (continuous request sizing).
        """
        return self.evaluate_sizes({rid: 1.0 for rid in accepted_ids})

    def evaluate_sizes(self, sizes: Mapping[str, float]) -> AllocationResult:
        """Accept each request id in ``sizes`` at the given fraction (0-1] of
        its full requested magnitude, solve, and report feasibility +
        objectives.

        A fraction models a *partial* interconnection award -- e.g. a PV
        request curtailed/derated to some percentage of its inverter rating
        -- rather than an all-or-nothing accept/reject; ``p_mw``/``q_mvar``
        are both scaled by the same fraction, preserving the request's power
        factor. Every fraction must be in [0, 1] (can't grant more than
        requested); a fraction of exactly 0.0 is equivalent to omitting that
        id.

        Accommodated MW is the sum of *granted* magnitude (``fraction * abs
        p_mw`` for every entry) -- how much interconnection capacity the
        subset actually connects, not the net power flowing into the feeder,
        which demand and export requests would otherwise cancel out in the
        sum. `evaluate`'s "sum of requested magnitude of every accepted
        request" is this formula's fraction=1.0 special case.
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

        net = copy.deepcopy(self.net)
        for rid, frac in granted.items():
            r = by_id[rid]
            pp.create_load(net, bus=r.bus, p_mw=frac * r.p_mw, q_mvar=frac * r.q_mvar, name=r.id)

        try:
            pp.runpp(net, **self.runpp_kwargs)
        except pp.LoadflowNotConverged:
            result = AllocationResult(
                accepted_ids=accepted, converged=False, feasible=False,
                accommodated_mw=0.0, vmin_pu=float("nan"), vmax_pu=float("nan"),
                max_line_loading_percent=float("nan"), max_trafo_loading_percent=float("nan"),
                voltage_violation_pu=float("inf"), thermal_violation_percent=float("inf"),
                reinforcement_cost_proxy=float("inf"), sizes=granted)
            self._cache[cache_key] = result
            return result

        vmin = float(net.res_bus.vm_pu.min())
        vmax = float(net.res_bus.vm_pu.max())
        max_line_pct = float(net.res_line.loading_percent.max()) if len(net.line) else 0.0
        max_trafo_pct = float(net.res_trafo.loading_percent.max()) if len(net.trafo) else 0.0

        voltage_violation = max(0.0, self.vmin_pu - vmin) + max(0.0, vmax - self.vmax_pu)
        thermal_violation = (max(0.0, max_line_pct - self.max_loading_percent)
                              + max(0.0, max_trafo_pct - self.max_loading_percent))
        feasible = voltage_violation == 0.0 and thermal_violation == 0.0
        accommodated_mw = sum(frac * abs(by_id[rid].p_mw) for rid, frac in granted.items())

        result = AllocationResult(
            accepted_ids=accepted, converged=True, feasible=feasible,
            accommodated_mw=accommodated_mw, vmin_pu=vmin, vmax_pu=vmax,
            max_line_loading_percent=max_line_pct, max_trafo_loading_percent=max_trafo_pct,
            voltage_violation_pu=voltage_violation, thermal_violation_percent=thermal_violation,
            # M0 placeholder: monotonic in "how much upgrade is needed", not yet
            # a catalogue price -- see docs/joint-allocation-scope.md and
            # ROADMAP.md's "What 'cost' means". Combines both violation kinds
            # (voltage_violation_pu scaled to percentage points so a 1 pp
            # overvoltage and a 1% thermal overload land in the same range) so
            # an overvoltage-limited network (like the EULV demo) doesn't get a
            # degenerate always-zero cost just because nothing runs hot.
            reinforcement_cost_proxy=thermal_violation + voltage_violation * 100,
            sizes=granted)
        self._cache[cache_key] = result
        return result

    def evaluate_3ph(self, accepted_ids: Iterable[str]) -> AllocationResult:
        """The unbalanced analogue of `evaluate` (M14): accept the given
        request ids at full requested size via `evaluate_sizes_3ph`."""
        return self.evaluate_sizes_3ph({rid: 1.0 for rid in accepted_ids})

    def evaluate_sizes_3ph(self, sizes: Mapping[str, float]) -> AllocationResult:
        """The unbalanced analogue of `evaluate_sizes` (M14): solves via
        `pandapower.runpp_3ph` instead of `runpp`, folding all three phases'
        voltage/loading into the same scalar `AllocationResult` shape
        (min/max across phases, matching ``vmin_pu``/``vmax_pu``'s existing
        meaning) rather than adding new per-phase fields. A `Request` with
        ``phase=None`` attaches as a balanced 3-phase load (`pp.create_load`
        -- pandapower's 3ph solver splits it evenly across phases on its
        own); ``phase="a"``/``"b"``/``"c"`` attaches as a single-phase-tap
        load (`pp.create_asymmetric_load`), the real US residential PV/EV
        interconnection pattern this axis exists to check.

        Only valid on a network whose topology `runpp_3ph` can actually
        represent -- every bus/line genuinely 3-phase throughout, e.g.
        `pandapower.networks.ieee_european_lv_asymmetric`. Pandapower's
        symmetrical-component 3ph solver cannot correctly model single-phase
        laterals or split-phase secondaries (pandapower issue #873), so this
        is *not* a drop-in replacement for `evaluate_sizes` on
        `import_opendss`-imported North-American test feeders (IEEE13/123,
        SMART-DS) -- see docs/joint-allocation-scope.md's M14. The network
        also needs `ext_grid`'s zero/negative-sequence fields set
        (``s_sc_max_mva``/``s_sc_min_mva``/``rx_max``/``rx_min``/
        ``r0x0_max``/``x0x_max``/``r0x0_min``/``x0x_min``) and every line's
        ``r0_ohm_per_km``/``x0_ohm_per_km``/``c0_nf_per_km`` -- present on
        pandapower's own native asymmetric networks, absent from
        `import_opendss`'s positive-sequence-only import.

        Unlike `runpp`, `runpp_3ph` does not raise on non-convergence -- it
        leaves ``net['converged']`` `True` and returns an empty/all-NaN
        `res_bus_3ph` (checked directly against a deliberately overloaded
        probe network, not assumed). Checked explicitly here and treated as
        the same infeasible/non-converged case `evaluate_sizes` gets from
        `LoadflowNotConverged`, since silently propagating NaN through the
        violation-margin arithmetic would make ``max(0.0, nan)`` (== ``0.0``
        in Python) report a diverged solve as *feasible*.
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
        cached = self._cache_3ph.get(cache_key)
        if cached is not None:
            return cached

        net = copy.deepcopy(self.net)
        phase_kwarg = {"a": ("p_a_mw", "q_a_mvar"), "b": ("p_b_mw", "q_b_mvar"), "c": ("p_c_mw", "q_c_mvar")}
        for rid, frac in granted.items():
            r = by_id[rid]
            if r.phase is None:
                pp.create_load(net, bus=r.bus, p_mw=frac * r.p_mw, q_mvar=frac * r.q_mvar, name=r.id)
            else:
                p_kwarg, q_kwarg = phase_kwarg[r.phase]
                pp.create_asymmetric_load(net, bus=r.bus, name=r.id,
                                           **{p_kwarg: frac * r.p_mw, q_kwarg: frac * r.q_mvar})

        def _infeasible() -> AllocationResult:
            result = AllocationResult(
                accepted_ids=accepted, converged=False, feasible=False,
                accommodated_mw=0.0, vmin_pu=float("nan"), vmax_pu=float("nan"),
                max_line_loading_percent=float("nan"), max_trafo_loading_percent=float("nan"),
                voltage_violation_pu=float("inf"), thermal_violation_percent=float("inf"),
                reinforcement_cost_proxy=float("inf"), sizes=granted)
            self._cache_3ph[cache_key] = result
            return result

        try:
            pp.runpp_3ph(net, **self.runpp_kwargs)
        except pp.LoadflowNotConverged:
            return _infeasible()

        vm = net.res_bus_3ph[["vm_a_pu", "vm_b_pu", "vm_c_pu"]]
        if vm.empty or bool(vm.isna().any().any()):
            return _infeasible()

        vmin = float(vm.min().min())
        vmax = float(vm.max().max())
        max_line_pct = float(net.res_line_3ph.loading_percent.max()) if len(net.line) else 0.0
        max_trafo_pct = float(net.res_trafo_3ph.loading_percent.max()) if len(net.trafo) else 0.0

        voltage_violation = max(0.0, self.vmin_pu - vmin) + max(0.0, vmax - self.vmax_pu)
        thermal_violation = (max(0.0, max_line_pct - self.max_loading_percent)
                              + max(0.0, max_trafo_pct - self.max_loading_percent))
        feasible = voltage_violation == 0.0 and thermal_violation == 0.0
        accommodated_mw = sum(frac * abs(by_id[rid].p_mw) for rid, frac in granted.items())

        result = AllocationResult(
            accepted_ids=accepted, converged=True, feasible=feasible,
            accommodated_mw=accommodated_mw, vmin_pu=vmin, vmax_pu=vmax,
            max_line_loading_percent=max_line_pct, max_trafo_loading_percent=max_trafo_pct,
            voltage_violation_pu=voltage_violation, thermal_violation_percent=thermal_violation,
            reinforcement_cost_proxy=thermal_violation + voltage_violation * 100,
            sizes=granted)
        self._cache_3ph[cache_key] = result
        return result
