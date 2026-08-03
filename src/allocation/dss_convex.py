"""A convex-relaxation alternative to `search.nsga2_search_dss` (M18): the
same per-phase decoupled idea `convex.py`'s LinDistFlow MILP applies to
`AllocationProblem`, extended to `AllocationProblemDSS`'s OpenDSS-solved,
single-phase-lateral topology (IEEE13/123, SMART-DS) -- the gap
`docs/paper.md` Section 7 named after M17: "the convex/MILP route has no
counterpart for `AllocationProblemDSS`".

Why a *new* module rather than extending `convex.py` in place: that model's
whole MILP is built from one shared 3-phase branch-flow equation per edge,
which only works because every pandapower network validated so far carries
one P/Q per edge, not per phase -- exact for EULV/ENWL's balanced European
LV topology, wrong by construction for a feeder with single-phase laterals
and mutual coupling between phases (IEEE13's own line-code matrices have
real, non-negligible off-diagonal terms -- e.g. mtx601's r12=0.156 vs. its
own r11=0.3465, not a rounding-scale difference). Getting an *exact* radial
multiphase relaxation right (tracking the full complex coupling between
phases via each line's own impedance matrix, e.g. Gan & Low 2014's
multiphase branch-flow SOCP) is real research, not attempted here --
Section 7 already flagged the meshed-network case as needing a similar-scale
lift and out of scope. What's built here instead, and is genuinely
achievable in one milestone: drop the mutual-coupling terms and solve one
*decoupled* single-phase LinDistFlow per phase, sharing only the request
accept variables (a request taps exactly one phase, per `DSSRequest.phase`,
so a phase's own tree is the only place that accept variable's power
appears). This is a real, quantifiable approximation -- exactly the kind
`convex.py` already makes and validates for the balanced case (dropping
losses, linearizing the thermal circle to an octagon) -- not assumed
harmless, checked against the AC oracle by `experiments/42_convex_dss_
validation.py`.

**Topology extraction is genuinely different from `convex.py`'s**, since
there is no pandapower `net` object to read tables from -- everything here
comes from a solved `opendssdirect` context: `Lines`/`Transformers`
elements as graph edges, `Lines.RMatrix()/XMatrix()` (per-unit-length rate;
multiplied by `Lines.Length()` gives actual ohms -- checked directly against
hand-computed values from IEEE13's own line codes, not assumed from the
API's docstring alone, since a first guess -- that the matrix is already
length-scaled -- was off by exactly the declared length) for line
impedances, and each element's own `Bus1`/`Bus2` node-suffix order (not
sorted phase order -- e.g. `Line.632645`'s `Bus1=632.3.2` declares phase 3
before phase 2, and its impedance matrix is indexed in that same order) to
map matrix diagonal entries to phase numbers.

**The root is not `ext_grid`,** because there isn't one on this oracle's
side: `AllocationProblemDSS` drives OpenDSS directly, with no equivalent of
pandapower's fixed-voltage slack bus concept exposed to this module. Every
network this validates against needs an explicit ``root_bus`` -- deliberately
not defaulted or auto-detected, since picking the wrong one (e.g. upstream
of a delta-connected substation transformer, which this model cannot
represent per-phase at all) would silently mis-model the network rather
than raise. For IEEE13, `root_bus="650"` -- confirmed, not assumed, to sit
within 0.02pu of 1.0 regardless of downstream loading (checked at both the
published base case and an all-loads-disabled solve) thanks to the
vendored master file's own deliberately-crushed substation-transformer
impedance ("to make this happen, we will change the impedance on the
transformer to something tiny", per that file's own comment) and a stiffened
`Vsource` (`MVAsc3=20000`) upstream of it -- a genuine stiff source, not
merely assumed to behave like one.

**Voltage regulators are folded into a per-edge ratio, not modeled as
impedance.** IEEE13's three single-phase head-end regulators (`Reg1/2/3`,
650->rg60) retap in response to downstream loading -- a control loop this
linear model does not represent. Any 2-winding transformer edge whose two
windings declare the same rated kV is treated as a regulator: its own
impedance is dropped (assumed folded into the ratio below, not modeled
separately -- its `XHL`=0.01% and `%R`=0.005% are two orders of magnitude
below IEEE13's smallest line impedance, i.e. negligible on their own terms
too) and its per-phase voltage ratio is fixed at whatever the *baseline*
(existing-load-only, zero candidate requests) AC solve actually measured,
`ratio = v_child_baseline / v_parent_baseline` -- not the regulator's own
declared tap position, since the measured ratio already folds in that tiny
impedance drop at the baseline operating point for free. This freezes the
regulator's response at the baseline operating point: a genuine
simplification, not re-solved as candidate requests are accepted, exactly
the same class of "frozen snapshot" approximation `AllocationProblemDSS`
itself already makes implicitly (every `evaluate_sizes` call is a single
snapshot solve, not a control-loop simulation).

**Existing per-bus injections are read from load/capacitor *nameplate*
data, not a power-flow solve** (unlike the regulator ratio above, which
needs an actual solve) -- mirroring `convex.py`'s own `net.load`/`net.sgen`
table reads. A delta- or multi-phase-wye-connected load's declared kW/kvar
is split evenly across however many phases its own bus spec names (e.g.
`Load.671`'s 1155kW/660kvar, `Bus1=671.1.2.3`, splits into three 385kW/220kvar
shares) -- an explicit, documented approximation for delta connections
(which do not decompose into independent per-phase injections in reality),
not a delta-to-wye electrical equivalent. `Capacitor` elements contribute
Q only (negative, i.e. generation), same even split rule.

Every candidate request must still have ``q_kvar == 0`` (mirrors `convex.
py`'s `q_mvar == 0` restriction on `Request` -- Q is a decision-independent
constant here too, not a variable), and must land on the phase's own
already-built tree (its ``bus`` reachable from ``root_bus`` on its declared
``phase`` without crossing a delta-connected or multi-phase-coupled
transformer this model cannot represent) -- both checked, raising rather
than silently mishandling.

The MILP's own accept-set is never trusted directly: `lindistflow_search_
dss` always re-verifies its chosen accept-set through `AllocationProblemDSS.
evaluate_sizes`, the same real OpenDSS oracle `nsga2_search_dss` uses --
`.result` is exactly as authoritative as any other DSS-route search's.
"""

from __future__ import annotations

import math
import time
from collections import defaultdict, deque
from dataclasses import dataclass

import numpy as np
import opendssdirect as dss_base
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_matrix

from allocation.convex import ConvexSearchResult, RelaxedEvaluation, subtree_sum
from allocation.dss_problem import AllocationProblemDSS, DSSRequest


@dataclass
class _Edge:
    name: str
    kind: str            # "line" | "trafo" | "regulator"
    bus1: str
    bus2: str
    phases: list[int]     # this element's own conductor order (matches its matrix index order)
    r_ohm: list[list[float]] | None   # None for a regulator edge (impedance dropped, see module docstring)
    x_ohm: list[list[float]] | None
    norm_amps: float | None           # lines only
    kva_total: float | None           # transformers/regulators only


@dataclass
class _PhaseTree:
    phase: int
    root: str
    order: list[str]                  # BFS order, root excluded
    parent: dict[str, str]
    children: dict[str, list[str]]
    r_pu: dict[str, float]
    x_pu: dict[str, float]
    s_max_mva: dict[str, float]
    ratio: dict[str, float]           # voltage ratio multiplier, 1.0 unless a regulator edge
    kind: dict[str, str]
    existing_p_mw: dict[str, float]
    existing_q_mvar: dict[str, float]
    q_const_mvar: dict[str, float]
    v2_root: float
    calibration_v2: dict[str, float]  # see `_calibrate_baseline` -- 0.0 until calibrated


@dataclass
class LinDistFlowDSSModel:
    """Precomputed per-phase radial trees + a shared request index -- built
    once from a solved `opendssdirect` context, reused across many solves."""

    problem: AllocationProblemDSS
    s_base_mva: float
    octagon_sides: int
    root_bus: str
    trees: dict[int, _PhaseTree]      # phase number -> tree
    request_index: dict[str, int]     # request id -> column index into accept vars


def _resolve_nodes(bus_spec: str, nphases: int) -> tuple[str, list[int]]:
    """`"632.3.2"` -> `("632", [3, 2])`; `"671"` (no explicit node suffix,
    OpenDSS's own default) -> `("671", [1, 2, ..., nphases])`."""
    parts = bus_spec.split(".")
    name = parts[0].lower()
    if len(parts) > 1:
        return name, [int(p) for p in parts[1:]]
    return name, list(range(1, nphases + 1))


def _extract_dss_edges(ctx) -> tuple[list[_Edge], dict[str, list[int]]]:
    """One `_Edge` per `Line`/non-substation `Transformer` element, plus the
    bus-name -> edge-index adjacency needed for a per-phase BFS."""
    edges: list[_Edge] = []
    adjacency: dict[str, list[int]] = defaultdict(list)

    def _add(name: str, kind: str, bus1: str, bus2: str, phases: list[int],
              r_ohm, x_ohm, norm_amps, kva_total) -> None:
        idx = len(edges)
        edges.append(_Edge(name=name, kind=kind, bus1=bus1, bus2=bus2, phases=phases,
                            r_ohm=r_ohm, x_ohm=x_ohm, norm_amps=norm_amps, kva_total=kva_total))
        adjacency[bus1].append(idx)
        adjacency[bus2].append(idx)

    i = ctx.Lines.First()
    while i:
        name = ctx.Lines.Name()
        nph = ctx.Lines.Phases()
        bus1, ph1 = _resolve_nodes(ctx.Lines.Bus1(), nph)
        bus2, ph2 = _resolve_nodes(ctx.Lines.Bus2(), nph)
        if ph1 != ph2:
            raise NotImplementedError(f"Line.{name}: bus1/bus2 phase order mismatch {ph1} vs {ph2}")
        ctx.Circuit.SetActiveElement("Line." + name)
        if ctx.CktElement.IsOpen(1, 0) or ctx.CktElement.IsOpen(2, 0):
            raise NotImplementedError(f"Line.{name} is open -- open switches/ties not supported here")
        length = ctx.Lines.Length()
        rmat_flat = ctx.Lines.RMatrix()
        xmat_flat = ctx.Lines.XMatrix()
        r_ohm = [[rmat_flat[r * nph + c] * length for c in range(nph)] for r in range(nph)]
        x_ohm = [[xmat_flat[r * nph + c] * length for c in range(nph)] for r in range(nph)]
        _add(name, "line", bus1, bus2, ph1, r_ohm, x_ohm, ctx.Lines.NormAmps(), None)
        i = ctx.Lines.Next()

    i = ctx.Transformers.First()
    while i:
        name = ctx.Transformers.Name()
        ctx.Circuit.SetActiveElement("Transformer." + name)
        bus_specs = ctx.CktElement.BusNames()
        # Transformers this model cannot represent (>2 windings, delta-
        # connected, mismatched bus node order) are skipped here rather than
        # raised -- most such elements (e.g. IEEE13's own delta-wye
        # substation transformer) sit upstream of any sensible `root_bus`
        # and are never actually reached by the per-phase BFS below. If a
        # request's own bus genuinely needs to cross one, that surfaces
        # instead as an explicit "not reachable" error once requests are
        # checked against the built trees, a clearer failure than blocking
        # model construction over an irrelevant element.
        if len(bus_specs) != 2:
            i = ctx.Transformers.Next()
            continue
        ctx.Transformers.Wdg(1)
        kv1, r1 = ctx.Transformers.kV(), ctx.Transformers.R()
        ctx.Transformers.Wdg(2)
        kv2, r2, kva = ctx.Transformers.kV(), ctx.Transformers.R(), ctx.Transformers.kVA()
        ctx.Text.Command(f"? Transformer.{name}.conns")
        conns = ctx.Text.Result()
        if "delta" in conns.lower():
            i = ctx.Transformers.Next()
            continue
        nph = ctx.CktElement.NumPhases()
        bus1, ph1 = _resolve_nodes(bus_specs[0], nph)
        bus2, ph2 = _resolve_nodes(bus_specs[1], nph)
        if ph1 != ph2:
            i = ctx.Transformers.Next()
            continue
        is_regulator = abs(kv1 - kv2) < 1e-9
        if is_regulator:
            _add(name, "regulator", bus1, bus2, ph1, None, None, None, kva)
        else:
            xhl = ctx.Transformers.Xhl()
            r_pct_total = r1 + r2
            n = len(ph1)
            kva_per_phase = kva / n
            # OpenDSS declares a wdg's kV line-to-line for a 3-phase wye
            # winding, line-to-neutral directly for a 1-phase winding --
            # checked against xfm1 (3ph wye-wye, kv=0.48 declared, bus 634's
            # own solved kVBase=0.27713=0.48/sqrt(3)), not assumed.
            kv_ln = kv2 / math.sqrt(3) if n == 3 else kv2
            # diagonal-only (no inter-phase coupling modeled): off-diagonal
            # entries left as 0.0, consistent with this module's decoupled
            # per-phase scope (see module docstring).
            r_ohm = [[0.0] * n for _ in range(n)]
            x_ohm = [[0.0] * n for _ in range(n)]
            for k in range(n):
                r_ohm[k][k] = (r_pct_total / 100) * kv_ln ** 2 / (kva_per_phase / 1000)
                x_ohm[k][k] = (xhl / 100) * kv_ln ** 2 / (kva_per_phase / 1000)
            _add(name, "trafo", bus1, bus2, ph1, r_ohm, x_ohm, None, kva)
        i = ctx.Transformers.Next()

    return edges, adjacency


def _bfs_phase_tree(root_bus: str, phase: int, edges: list[_Edge],
                     adjacency: dict[str, list[int]]) -> tuple[list[str], dict[str, str], dict[str, list[str]], dict[str, int]]:
    """BFS from `root_bus`, following only edges that carry `phase` -- the
    decoupled per-phase analogue of `convex._extract_tree`.

    A plain BFS never notices a cycle on its own: it visits each bus once,
    discovering it via whichever edge reaches it first, and silently *skips*
    any other edge into an already-seen bus -- i.e. it would quietly pick an
    arbitrary spanning tree of a meshed network rather than raise, unlike
    `convex._extract_tree`'s explicit `edges == buses - 1` check. Caught,
    not assumed safe: on the looped IEEE123 network (M19), an earlier
    version of this function returned a tree with no error at all, silently
    dropping the one edge that actually closes the loop. Fixed below by a
    second pass over every phase-`phase` edge whose *both* endpoints landed
    in the BFS's own `seen` set -- more of those than `len(seen) - 1` means
    at least one edge was never used to reach a new bus, i.e. a real cycle.
    """
    parent: dict[str, str] = {}
    children: dict[str, list[str]] = defaultdict(list)
    edge_of: dict[str, int] = {}
    order: list[str] = [root_bus]
    seen = {root_bus}
    q = deque([root_bus])
    while q:
        u = q.popleft()
        for eidx in adjacency.get(u, []):
            e = edges[eidx]
            if phase not in e.phases:
                continue
            v = e.bus2 if e.bus1 == u else e.bus1 if e.bus2 == u else None
            if v is None or v in seen:
                continue
            seen.add(v)
            parent[v] = u
            children[u].append(v)
            edge_of[v] = eidx
            order.append(v)
            q.append(v)

    n_internal_edges = sum(1 for e in edges if phase in e.phases and e.bus1 in seen and e.bus2 in seen)
    if n_internal_edges != len(seen) - 1:
        raise ValueError(
            f"phase {phase}'s own subnetwork (rooted at {root_bus!r}) is not a single radial "
            f"tree: {len(seen)} buses reachable but {n_internal_edges} edges among them "
            f"(expected {len(seen) - 1}) -- LinDistFlow-DSS needs a loop-free network per phase "
            "(check for a closed tie switch/loop)")
    return order, parent, children, edge_of


def build_lindistflow_model_dss(problem: AllocationProblemDSS, root_bus: str,
                                 s_base_mva: float = 1.0, octagon_sides: int = 8) -> LinDistFlowDSSModel:
    """Extract per-phase radial trees + impedances + existing injections
    from `problem.master_path`, rooted at `root_bus` -- see this module's
    docstring for why `root_bus` is never auto-detected."""
    root_bus = root_bus.lower()
    if any(r.q_kvar != 0.0 for r in problem.requests):
        raise NotImplementedError(
            "LinDistFlow-DSS model treats reactive power as request-independent "
            "(every request builder in this repo uses q_kvar=0.0); a nonzero-Q "
            "request would need Q modeled as a decision variable, not implemented")

    ctx = dss_base.NewContext()
    ctx.Command("Clear")
    ctx.Command(f'Redirect "{problem.master_path}"')
    ctx.Solution.Solve()
    if not ctx.Solution.Converged():
        raise ValueError("baseline (zero-request) solve did not converge -- cannot build a model")

    edges, adjacency = _extract_dss_edges(ctx)

    ctx.Circuit.SetActiveBus(root_bus)
    root_nodes = ctx.Bus.Nodes()
    if not root_nodes:
        raise ValueError(f"root_bus {root_bus!r} not found in the solved circuit")
    root_kv_ln = ctx.Bus.kVBase()
    root_vmag = ctx.Bus.puVmagAngle()[::2]
    v_baseline_at_bus: dict[tuple[str, int], float] = {}
    for name in ctx.Circuit.AllBusNames():
        ctx.Circuit.SetActiveBus(name)
        nodes = ctx.Bus.Nodes()
        mags = ctx.Bus.puVmagAngle()[::2]
        for node, mag in zip(nodes, mags):
            v_baseline_at_bus[(name.lower(), node)] = mag

    trees: dict[int, _PhaseTree] = {}
    for phase in root_nodes:
        order, parent, children, edge_of = _bfs_phase_tree(root_bus, phase, edges, adjacency)
        nonroot = order[1:]

        r_pu: dict[str, float] = {}
        x_pu: dict[str, float] = {}
        s_max_mva: dict[str, float] = {}
        ratio: dict[str, float] = {}
        kind: dict[str, str] = {}
        for bus in nonroot:
            e = edges[edge_of[bus]]
            pos = e.phases.index(phase)
            kind[bus] = e.kind
            if e.kind == "regulator":
                ratio[bus] = (v_baseline_at_bus[(bus, phase)]
                               / v_baseline_at_bus[(parent[bus], phase)])
                r_pu[bus] = 0.0
                x_pu[bus] = 0.0
                s_max_mva[bus] = e.kva_total / 1000.0
            else:
                ratio[bus] = 1.0
                child_kv_ln = None
                ctx.Circuit.SetActiveBus(bus)
                child_kv_ln = ctx.Bus.kVBase()
                z_base = child_kv_ln ** 2 / s_base_mva
                r_pu[bus] = e.r_ohm[pos][pos] / z_base
                x_pu[bus] = e.x_ohm[pos][pos] / z_base
                if e.kind == "line":
                    s_max_mva[bus] = child_kv_ln * e.norm_amps / 1000.0
                else:
                    s_max_mva[bus] = e.kva_total / 1000.0 / len(e.phases)

        existing_p_mw: dict[str, float] = defaultdict(float)
        existing_q_mvar: dict[str, float] = defaultdict(float)
        reachable = set(order)
        i = ctx.Loads.First()
        while i:
            name = ctx.Loads.Name()
            ctx.Circuit.SetActiveElement("Load." + name)
            bus_spec = ctx.CktElement.BusNames()[0]
            bus, phases = _resolve_nodes(bus_spec, ctx.CktElement.NumPhases())
            if phase in phases and bus in reachable and bus != root_bus:
                existing_p_mw[bus] += ctx.Loads.kW() / 1000.0 / len(phases)
                existing_q_mvar[bus] += ctx.Loads.kvar() / 1000.0 / len(phases)
            i = ctx.Loads.Next()
        i = ctx.Capacitors.First()
        while i:
            name = ctx.Capacitors.Name()
            ctx.Circuit.SetActiveElement("Capacitor." + name)
            bus_spec = ctx.CktElement.BusNames()[0]
            bus, phases = _resolve_nodes(bus_spec, ctx.CktElement.NumPhases())
            if phase in phases and bus in reachable and bus != root_bus:
                existing_q_mvar[bus] -= ctx.Capacitors.kvar() / 1000.0 / len(phases)
            i = ctx.Capacitors.Next()

        existing_q_mvar = dict(existing_q_mvar)
        q_const_mvar = subtree_sum(order, children, nonroot, existing_q_mvar)

        v2_root = v_baseline_at_bus.get((root_bus, phase), 1.0) ** 2

        trees[phase] = _PhaseTree(
            phase=phase, root=root_bus, order=nonroot, parent=parent, children=children,
            r_pu=r_pu, x_pu=x_pu, s_max_mva=s_max_mva, ratio=ratio, kind=kind,
            existing_p_mw=dict(existing_p_mw), existing_q_mvar=existing_q_mvar,
            q_const_mvar=q_const_mvar, v2_root=v2_root, calibration_v2={})

    request_index = {r.id: i for i, r in enumerate(problem.requests)}
    for r in problem.requests:
        tree = trees.get(r.phase)
        if tree is None or r.bus.lower() not in tree.order:
            raise ValueError(f"request {r.id!r}: bus {r.bus!r} phase {r.phase} not reachable "
                              f"from root_bus {root_bus!r} -- check bus name/phase, or that no "
                              "delta-connected transformer sits between them (unsupported, see "
                              "module docstring)")

    model = LinDistFlowDSSModel(problem=problem, s_base_mva=s_base_mva, octagon_sides=octagon_sides,
                                 root_bus=root_bus, trees=trees, request_index=request_index)
    _calibrate_baseline(model, v_baseline_at_bus)
    return model


def _calibrate_baseline(model: LinDistFlowDSSModel, v_baseline_at_bus: dict[tuple[str, int], float]) -> None:
    """Anchor the decoupled model to the *true* AC baseline (zero candidate
    requests), the same real solve `v_baseline_at_bus` was already read from
    during extraction. Dropping every phase's mutual-coupling term is not a
    small effect on this topology -- checked directly, not assumed: on
    IEEE13's own head-end trunk line, recomputing the voltage-drop implied
    by the full impedance matrix vs. the diagonal-only term this model uses,
    against that same line's actual solved per-phase currents, differs by
    3.4-4.5 percentage points of pu voltage from that one edge alone (mutual
    terms partially cancel a correlated multiphase current in reality; a
    decoupled model can't see that cancellation at all). Compounded down the
    rest of the feeder, the uncalibrated model's own baseline prediction
    (zero accepted requests) landed 0.025 pu below the true AC baseline --
    enough, uncorrected, to make the search's own MILP infeasible even
    before any request is considered, since the existing-load-only power
    balance is a fixed equality, not something the solver can choose around.

    Fix, in the same spirit as this module's regulator-ratio handling: don't
    attempt the angle-exact multiphase relaxation this would really need
    (real research, not attempted here, per `docs/paper.md` Section 7) --
    instead calibrate a fixed per-bus additive correction, once, against the
    true baseline, then carry it forward unchanged as candidate requests are
    considered. The correction has to be computed top-down (a flat, per-node
    "true minus this-node's-own-uncalibrated-value" comparison does *not*
    work -- checked directly, since node j's own gap is not independent of
    its parent's: it silently compounds the parent's own already-corrected
    error into node j's target too). Solved instead directly from `v_
    baseline_at_bus` (already the true AC baseline at *every* bus, read
    during extraction, not re-derived): for the equation `v2_j - ratio^2 *
    v2_parent + 2*r*P_e = -2*x*q_const + calib_j`, plugging in the true
    voltages at j and its parent plus the relaxed model's own (calibration-
    independent, since the P-balance equations never reference calibration)
    baseline flow `P_e` pins down exactly the `calib_j` that makes v2_j land
    on the true value whenever v2_parent already does -- which holds all the
    way down from the root by induction, since `v2_root` is already exactly
    the true root voltage by construction. This makes the model agree with
    the true baseline *exactly* at zero accepted requests; its accuracy for
    a nonzero accept-set rests on the assumption that a handful of new
    single-phase PV/EV requests -- individually tiny next to IEEE13's
    existing multi-hundred-kW loads -- don't materially shift the phase-
    imbalance pattern the calibration was measured under. Not proven in
    general; checked empirically per accept-set via the same AC
    reverification `lindistflow_search_dss` always does regardless.
    """
    fixed_zero = {r.id: 0 for r in model.problem.requests}
    _, P_vals, _, _ = _solve_dss(model, fixed_accept=fixed_zero, time_limit_s=30.0, mip_gap=0.0, bounded=False)
    for phase, tree in model.trees.items():
        for j in tree.order:
            v_true_j = v_baseline_at_bus.get((j, phase))
            v_true_parent = v_baseline_at_bus.get((tree.parent[j], phase))
            if v_true_j is None or v_true_parent is None:
                continue
            a_e, r_e, x_e = tree.ratio[j], tree.r_pu[j], tree.x_pu[j]
            tree.calibration_v2[j] = (v_true_j ** 2 - (a_e ** 2) * v_true_parent ** 2
                                       + 2 * r_e * P_vals[phase][j] + 2 * x_e * tree.q_const_mvar[j])


def _requests_by_bus(model: LinDistFlowDSSModel, phase: int) -> dict[str, list[tuple[int, DSSRequest]]]:
    by_bus: dict[str, list[tuple[int, DSSRequest]]] = defaultdict(list)
    for i, r in enumerate(model.problem.requests):
        if r.phase == phase:
            by_bus[r.bus.lower()].append((i, r))
    return by_bus


def _solve_dss(model: LinDistFlowDSSModel, fixed_accept: dict[str, int] | None,
                time_limit_s: float, mip_gap: float, bounded: bool = True):
    """Build and solve the multi-phase MILP -- three decoupled single-phase
    LinDistFlow blocks (see module docstring) sharing one accept-variable
    block. Structurally the same equations as `convex._solve`, just walked
    once per phase with that phase's own tree/impedances/ratio."""
    K = len(model.problem.requests)
    phase_list = sorted(model.trees)
    offsets: dict[int, tuple[int, int]] = {}
    pos: dict[int, dict[str, int]] = {}
    off = K
    for phase in phase_list:
        tree = model.trees[phase]
        pos[phase] = {b: i for i, b in enumerate(tree.order)}
        offsets[phase] = (off, off + len(tree.order))
        off += 2 * len(tree.order)
    n_vars = off

    rows: list[int] = []
    cols: list[int] = []
    data: list[float] = []
    b_eq: list[float] = []
    row = 0

    for phase in phase_list:
        tree = model.trees[phase]
        P_off, V2_off = offsets[phase]
        by_bus = _requests_by_bus(model, phase)
        for j in tree.order:
            rows.append(row); cols.append(P_off + pos[phase][j]); data.append(1.0)
            for c in tree.children.get(j, []):
                rows.append(row); cols.append(P_off + pos[phase][c]); data.append(-1.0)
            for ridx, r in by_bus.get(j, []):
                rows.append(row); cols.append(ridx); data.append(-r.p_kw / 1000.0)
            b_eq.append(tree.existing_p_mw.get(j, 0.0))
            row += 1

        for j in tree.order:
            i_bus = tree.parent[j]
            r_e, x_e, a_e = tree.r_pu[j], tree.x_pu[j], tree.ratio[j]
            rows.append(row); cols.append(V2_off + pos[phase][j]); data.append(1.0)
            rhs = -2 * x_e * tree.q_const_mvar[j] + tree.calibration_v2.get(j, 0.0)
            if i_bus == tree.root:
                rhs += (a_e ** 2) * tree.v2_root
            else:
                rows.append(row); cols.append(V2_off + pos[phase][i_bus]); data.append(-(a_e ** 2))
            rows.append(row); cols.append(P_off + pos[phase][j]); data.append(2 * r_e)
            b_eq.append(rhs)
            row += 1

    A_eq = coo_matrix((data, (rows, cols)), shape=(row, n_vars)).tocsr()
    b_eq_arr = np.array(b_eq)

    lb = np.full(n_vars, -np.inf)
    ub = np.full(n_vars, np.inf)
    lb[:K], ub[:K] = 0.0, 1.0
    if bounded:
        for phase in phase_list:
            tree = model.trees[phase]
            P_off, V2_off = offsets[phase]
            vmin_eff, vmax_eff = model.problem.vmin_pu, model.problem.vmax_pu
            for j in tree.order:
                lb[V2_off + pos[phase][j]] = vmin_eff ** 2
                ub[V2_off + pos[phase][j]] = vmax_eff ** 2
    if fixed_accept is not None:
        for rid, val in fixed_accept.items():
            k = model.request_index[rid]
            lb[k] = ub[k] = float(val)

    integrality = np.zeros(n_vars)
    integrality[:K] = 1

    c = np.zeros(n_vars)
    for i, r in enumerate(model.problem.requests):
        c[i] = -abs(r.p_kw) / 1000.0

    constraints = [LinearConstraint(A_eq, b_eq_arr, b_eq_arr)]
    if bounded:
        rows2: list[int] = []
        cols2: list[int] = []
        data2: list[float] = []
        b_ub: list[float] = []
        row2 = 0
        sides = model.octagon_sides
        thetas = [2 * math.pi * k / sides for k in range(sides)]
        loading_frac = model.problem.max_loading_percent / 100.0
        for phase in phase_list:
            tree = model.trees[phase]
            P_off, _ = offsets[phase]
            for j in tree.order:
                s_max = tree.s_max_mva[j] * loading_frac
                q_const = tree.q_const_mvar[j]
                for theta in thetas:
                    c_, s_ = math.cos(theta), math.sin(theta)
                    rows2.append(row2); cols2.append(P_off + pos[phase][j]); data2.append(c_)
                    b_ub.append(s_max - s_ * q_const)
                    row2 += 1
        A_ub = coo_matrix((data2, (rows2, cols2)), shape=(row2, n_vars)).tocsr()
        constraints.append(LinearConstraint(A_ub, -np.inf, np.array(b_ub)))

    res = milp(c, constraints=constraints, integrality=integrality,
               bounds=Bounds(lb, ub),
               options={"time_limit": time_limit_s, "mip_rel_gap": mip_gap, "disp": False})
    if res.x is None:
        raise RuntimeError(f"LinDistFlow-DSS MILP infeasible or unsolved: {res.message}")

    accept_vals = {r.id: float(res.x[i]) for i, r in enumerate(model.problem.requests)}
    P_vals: dict[int, dict[str, float]] = {}
    V2_vals: dict[int, dict[str, float]] = {}
    for phase in phase_list:
        tree = model.trees[phase]
        P_off, V2_off = offsets[phase]
        P_vals[phase] = {j: res.x[P_off + pos[phase][j]] for j in tree.order}
        V2_vals[phase] = {j: res.x[V2_off + pos[phase][j]] for j in tree.order}
    return accept_vals, P_vals, V2_vals, res


def _summarize_dss(model: LinDistFlowDSSModel, accept_vals: dict[str, float],
                    P_vals: dict[int, dict[str, float]], V2_vals: dict[int, dict[str, float]]) -> RelaxedEvaluation:
    v2_all = [model.trees[phase].v2_root for phase in model.trees]
    for phase, vals in V2_vals.items():
        v2_all.extend(vals.values())
    vmin_pu = math.sqrt(min(v2_all))
    vmax_pu = math.sqrt(max(v2_all))

    max_line_pct = 0.0
    max_trafo_pct = 0.0
    for phase, tree in model.trees.items():
        for j in tree.order:
            s_mva = math.hypot(P_vals[phase][j], tree.q_const_mvar[j])
            pct = s_mva / tree.s_max_mva[j] * 100 if tree.s_max_mva[j] > 0 else 0.0
            if tree.kind[j] == "line":
                max_line_pct = max(max_line_pct, pct)
            else:
                max_trafo_pct = max(max_trafo_pct, pct)

    sizes = {rid: min(1.0, v) for rid, v in accept_vals.items() if v > 0}
    return RelaxedEvaluation(accepted_ids=frozenset(sizes), vmin_pu=vmin_pu, vmax_pu=vmax_pu,
                              max_line_loading_percent=max_line_pct, max_trafo_loading_percent=max_trafo_pct,
                              sizes=sizes)


def evaluate_relaxed_dss(model: LinDistFlowDSSModel, accepted_ids) -> RelaxedEvaluation:
    """Forward-solve the relaxed model at a fixed accept-set -- what the
    per-phase-decoupled LinDistFlow model believes voltages/loadings are,
    for parity-checking against the real `AllocationProblemDSS.evaluate`."""
    accepted = frozenset(accepted_ids)
    fixed = {r.id: (1 if r.id in accepted else 0) for r in model.problem.requests}
    accept_vals, P_vals, V2_vals, _ = _solve_dss(model, fixed, time_limit_s=30.0, mip_gap=0.0, bounded=False)
    return _summarize_dss(model, accept_vals, P_vals, V2_vals)


def lindistflow_search_dss(problem: AllocationProblemDSS, root_bus: str,
                            s_base_mva: float = 1.0, octagon_sides: int = 8,
                            time_limit_s: float = 60.0, mip_gap: float = 0.001) -> ConvexSearchResult:
    """Solve the per-phase-decoupled LinDistFlow-DSS MILP to maximize
    accommodated kW, then re-verify the chosen accept-set through the real
    OpenDSS oracle (`problem.evaluate_sizes`) -- `.result` is exactly as
    authoritative as `nsga2_search_dss`'s. See module docstring for what
    this model does and does not represent.
    """
    model = build_lindistflow_model_dss(problem, root_bus=root_bus, s_base_mva=s_base_mva,
                                         octagon_sides=octagon_sides)
    t0 = time.time()
    accept_vals, P_vals, V2_vals, res = _solve_dss(model, fixed_accept=None,
                                                     time_limit_s=time_limit_s, mip_gap=mip_gap)
    dt = time.time() - t0

    relaxed = _summarize_dss(model, accept_vals, P_vals, V2_vals)
    result = problem.evaluate_sizes(relaxed.sizes)
    achieved_gap = getattr(res, "mip_gap", float("nan"))
    return ConvexSearchResult(result=result, relaxed=relaxed, solve_time_s=dt, mip_gap=achieved_gap)
