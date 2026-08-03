"""A convex-relaxation alternative to `search.nsga2_search`: LinDistFlow branch-flow
physics + an octagon-linearized thermal limit, solved as a single MILP instead of
searched with a population-based metaheuristic.

Why this exists: NSGA-II (`search.py`) treats `AllocationProblem.evaluate` as a
black box and pays one `runpp` per individual -- ROADMAP.md's Hard Parts names
this as the scaling bottleneck. Every real feeder this repo has imported so far
(EULV, ENWL's network_15/Feeder_4) is *radial* -- no loops -- which is exactly
the structure LinDistFlow (Baran & Wu 1989) needs: with losses dropped, the
branch-flow power-balance and voltage-drop equations become linear in the branch
powers and squared bus voltages. Since every candidate request here is a fixed-
size accept/reject decision (no continuous sizing) and contributes zero
reactive power (checked below), the whole feasible region collapses to a single
MILP -- one accept/reject binary per request, one continuous power-flow variable
per branch, one continuous squared-voltage variable per bus -- solvable with
`scipy.optimize.milp` (HiGHS), no new dependency.

What this trades away, honestly:
- **Losses are dropped** (pure LinDistFlow, not the lossy DistFlow/SOCP variant).
  Validated below (`build_lindistflow_model`'s docstring) against pandapower's
  own `runpp` on EULV's transformer branch: the loss term contributes ~1e-5 pu
  to v^2, negligible at these loading levels.
- **Thermal limits are linearized** to an octagon (8-sided) inner approximation
  of the true P^2+Q^2 <= S_max^2 circle -- conservative (any octagon-feasible
  point is circle-feasible too), not exact.
- **Branch thermal ratings use nominal voltage**, not the actual (decision-
  dependent) voltage pandapower's own "current" trafo-loading mode uses --
  quantified as a real, direction-dependent gap in experiments/29.
- **Candidate requests must have `q_mvar == 0`** (every request builder in this
  repo already does this -- rooftop PV modeled at unity power factor); a
  nonzero-Q request raises `NotImplementedError` rather than silently
  mishandling it.
- Requires a genuinely *radial* network (checked: edges must equal buses - 1,
  every bus reachable from `ext_grid.bus`) with no shunts, switches, sgens, or
  off-nominal transformer taps -- all asserted, not assumed.

The MILP's own accept-set is **not** trusted as ground truth: `lindistflow_search`
always re-verifies its chosen accept-set through the real `AllocationProblem.evaluate`
(the same pandapower oracle NSGA-II uses), so the `AllocationResult` this module
returns is exactly as authoritative as any other search's. `RelaxedEvaluation`
carries the *relaxed model's own belief* about that same accept-set, for
quantifying the relaxation gap -- see experiments/29_convex_relaxation.py.

``lindistflow_search_fair`` (M12) adds a hard cap on `queue_fairness_violations`
to the same MILP -- the convex-search analogue of `search.nsga2_search_fair`'s
capacity-vs-fairness trade-off. The MILP's own objective is already single
(maximize accommodated MW), not multi-objective like NSGA-II's, so the trade-
off is traced by an epsilon-constraint sweep (solve once per violation-count
threshold) rather than by evolutionary search. `queue_fairness_violations` is
quadratic in the accept variables in general (each violation is an AND of
"later request accepted" and "earlier request rejected"), but exactly
linearizable here since accept variables are binary -- see `_solve`'s
``max_violations`` parameter.
"""

from __future__ import annotations

import math
import time
from collections import defaultdict, deque
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_matrix

from allocation.problem import AllocationProblem, AllocationResult, Request


@dataclass
class LinDistFlowModel:
    """Precomputed tree + branch parameters + constant injections for one
    `AllocationProblem`'s network -- built once, reused across many solves."""

    problem: AllocationProblem
    s_base_mva: float
    octagon_sides: int
    root: int
    nonroot_buses: list[int]           # BFS order, root excluded; also the edge index
    parent: dict[int, int]             # child bus -> parent bus
    children: dict[int, list[int]]     # bus -> list of child buses
    kind: dict[int, str]               # child bus -> "line" | "trafo" (edge into it)
    r_pu: dict[int, float]             # child bus -> edge resistance, system pu
    x_pu: dict[int, float]             # child bus -> edge reactance, system pu
    s_max_mva: dict[int, float]        # child bus -> edge thermal rating, MVA
    existing_p_mw: dict[int, float]    # bus -> existing net load P (MW), 0 if none
    existing_q_mvar: dict[int, float]  # bus -> existing net load Q (MVAr), 0 if none
    q_const_mvar: dict[int, float]     # child bus -> subtree existing Q (MVAr), no losses
    v2_root: float                     # fixed (ext_grid.vm_pu)^2
    request_index: dict[str, int]      # request id -> column index into accept vars


def _extract_tree(net) -> tuple[int, list[int], dict[int, int], dict[int, list[int]], dict[int, tuple[str, int]]]:
    """BFS from `ext_grid.bus`; raises if the network isn't a single radial tree."""
    adj: dict[int, list[tuple[int, str, int]]] = defaultdict(list)
    for i, r in net.line.iterrows():
        adj[int(r.from_bus)].append((int(r.to_bus), "line", i))
        adj[int(r.to_bus)].append((int(r.from_bus), "line", i))
    for i, r in net.trafo.iterrows():
        adj[int(r.hv_bus)].append((int(r.lv_bus), "trafo", i))
        adj[int(r.lv_bus)].append((int(r.hv_bus), "trafo", i))

    root = int(net.ext_grid.bus.iloc[0])
    parent: dict[int, int] = {}
    children: dict[int, list[int]] = defaultdict(list)
    edge_of: dict[int, tuple[str, int]] = {}
    order = [root]
    seen = {root}
    q = deque([root])
    while q:
        u = q.popleft()
        for v, ekind, eidx in adj[u]:
            if v not in seen:
                seen.add(v)
                parent[v] = u
                children[u].append(v)
                edge_of[v] = (ekind, eidx)
                order.append(v)
                q.append(v)

    n_edges = len(net.line) + len(net.trafo)
    if len(seen) != len(net.bus) or n_edges != len(net.bus) - 1:
        raise ValueError(
            f"network is not a single radial tree: {len(seen)}/{len(net.bus)} buses "
            f"reachable from ext_grid.bus, {n_edges} edges vs {len(net.bus) - 1} expected "
            "-- LinDistFlow needs a loop-free feeder (check for switches/loops/islands)")
    return root, order, parent, children, edge_of


def _assert_modeling_assumptions(net) -> None:
    if len(net.switch):
        raise NotImplementedError("LinDistFlow model doesn't handle net.switch elements")
    if len(net.shunt):
        raise NotImplementedError("LinDistFlow model doesn't handle net.shunt elements")
    if not net.line.in_service.all() or not net.trafo.in_service.all() or not net.bus.in_service.all():
        raise NotImplementedError("LinDistFlow model assumes every element is in service")
    if (net.line.c_nf_per_km.abs() > 1e-9).any() or (net.line.g_us_per_km.abs() > 1e-9).any():
        raise NotImplementedError("LinDistFlow model drops line charging/shunt -- "
                                   "this network has non-negligible c_nf_per_km/g_us_per_km")
    for _, t in net.trafo.iterrows():
        if pd.notna(t.tap_pos) and pd.notna(t.tap_neutral) and t.tap_pos != t.tap_neutral:
            raise NotImplementedError("LinDistFlow model assumes transformers sit at nominal tap")


def build_lindistflow_model(problem: AllocationProblem, s_base_mva: float = 1.0,
                             octagon_sides: int = 8) -> LinDistFlowModel:
    """Extract the radial tree + branch impedances/ratings + constant injections
    from `problem.net`. Independent of any accept/reject decision -- build once,
    reuse across `evaluate_relaxed`/`lindistflow_search` calls.
    """
    net = problem.net
    _assert_modeling_assumptions(net)
    if any(r.q_mvar != 0.0 for r in problem.requests):
        raise NotImplementedError(
            "LinDistFlow model treats reactive power as request-independent (every "
            "existing request builder in this repo uses q_mvar=0.0); a nonzero-Q "
            "request would need Q modeled as a decision variable, not implemented")

    root, order, parent, children, edge_of = _extract_tree(net)
    nonroot_buses = [b for b in order if b != root]

    kind: dict[int, str] = {}
    r_pu: dict[int, float] = {}
    x_pu: dict[int, float] = {}
    s_max_mva: dict[int, float] = {}
    for bus in nonroot_buses:
        ekind, eidx = edge_of[bus]
        kind[bus] = ekind
        if ekind == "line":
            row = net.line.iloc[eidx]
            vn_kv = float(net.bus.vn_kv[row.from_bus])
            r_ohm = row.r_ohm_per_km * row.length_km / row.parallel
            x_ohm = row.x_ohm_per_km * row.length_km / row.parallel
            r_pu[bus] = r_ohm * s_base_mva / vn_kv ** 2
            x_pu[bus] = x_ohm * s_base_mva / vn_kv ** 2
            s_max_mva[bus] = math.sqrt(3) * vn_kv * row.max_i_ka * row.parallel * row.df
        else:
            row = net.trafo.iloc[eidx]
            z_pu_own = row.vk_percent / 100
            r_pu_own = row.vkr_percent / 100
            x_pu_own = math.sqrt(max(z_pu_own ** 2 - r_pu_own ** 2, 0.0))
            scale = s_base_mva / (row.sn_mva * row.parallel)
            r_pu[bus] = r_pu_own * scale
            x_pu[bus] = x_pu_own * scale
            s_max_mva[bus] = row.sn_mva * row.parallel  # nominal-voltage approximation

    existing_p_mw: dict[int, float] = defaultdict(float)
    existing_q_mvar: dict[int, float] = defaultdict(float)
    for bus, grp in net.load.groupby("bus"):
        existing_p_mw[int(bus)] += float(grp.p_mw.sum())
        existing_q_mvar[int(bus)] += float(grp.q_mvar.sum())
    for bus, grp in net.sgen.groupby("bus"):
        existing_p_mw[int(bus)] -= float(grp.p_mw.sum())
        existing_q_mvar[int(bus)] -= float(grp.q_mvar.sum())

    existing_q_mvar = dict(existing_q_mvar)
    q_const_mvar = subtree_sum(order, children, nonroot_buses, existing_q_mvar)

    v2_root = float(net.ext_grid.vm_pu.iloc[0]) ** 2
    request_index = {r.id: i for i, r in enumerate(problem.requests)}

    return LinDistFlowModel(
        problem=problem, s_base_mva=s_base_mva, octagon_sides=octagon_sides,
        root=root, nonroot_buses=nonroot_buses, parent=parent, children=children, kind=kind,
        r_pu=r_pu, x_pu=x_pu, s_max_mva=s_max_mva,
        existing_p_mw=dict(existing_p_mw), existing_q_mvar=existing_q_mvar,
        q_const_mvar=q_const_mvar, v2_root=v2_root, request_index=request_index)


def subtree_sum(order: list[int], children: dict[int, list[int]], nonroot_buses: list[int],
                 per_bus_value: dict[int, float]) -> dict[int, float]:
    """Bottom-up aggregation: for every non-root bus j, sum `per_bus_value` over
    j's own bus plus every bus in its subtree. Used both for the (static)
    existing-Q baseline and, each round of `lindistflow_search_iterative`, to
    re-aggregate Q with that round's loss estimates folded in."""
    subtree: dict[int, float] = {}
    for bus in reversed(order):  # deepest first
        subtree[bus] = per_bus_value.get(bus, 0.0) + sum(subtree[c] for c in children[bus])
    return {bus: subtree[bus] for bus in nonroot_buses}


def _requests_by_bus(model: LinDistFlowModel) -> dict[int, list[tuple[int, Request]]]:
    by_bus: dict[int, list[tuple[int, Request]]] = defaultdict(list)
    for i, r in enumerate(model.problem.requests):
        by_bus[r.bus].append((i, r))
    return by_bus


def _fairness_pairs(model: LinDistFlowModel) -> list[tuple[int, int]]:
    """Column-index pairs (earlier, later) for every queue-order-inversion
    pair `problem.queue_fairness_violations` would count -- same sort and
    stable tie-break (`sorted(requests, key=lambda r: r.queue_order)`) so the
    MILP's linearized violation count matches that function exactly."""
    ordered = sorted(model.problem.requests, key=lambda r: r.queue_order)
    idx = [model.request_index[r.id] for r in ordered]
    return [(idx[a], idx[b]) for a in range(len(idx)) for b in range(a + 1, len(idx))]


def _solve(model: LinDistFlowModel, fixed_accept: dict[str, int] | None,
           time_limit_s: float, mip_gap: float, bounded: bool = True,
           voltage_correction: dict[int, float] | None = None,
           p_loss_correction: dict[int, float] | None = None,
           q_const_override: dict[int, float] | None = None,
           voltage_margin_pu: float = 0.0, thermal_margin_pct: float = 0.0,
           continuous: bool = False, max_violations: int | None = None):
    """Build and solve the MILP. `fixed_accept` (id -> 0/1) pins some/all accept
    variables (used for forward-evaluating one accept-set as a trivial LP);
    `None` leaves every accept variable free (the real optimization).

    `bounded` toggles whether voltage-band/thermal limits are enforced as hard
    constraints. Optimization (`fixed_accept=None`) needs `bounded=True` --
    that's what makes infeasible accept-sets unreachable. A forward parity
    check of one *given* accept-set needs `bounded=False`: the point is to
    observe what the relaxed physics predicts even when that's outside the
    band (e.g. right at the greedy baseline's boundary), not to assert
    feasibility -- with `bounded=True` a near-boundary accept-set can make the
    fixed-accept LP infeasible purely from the relaxed model's own small
    prediction error, which would make parity-checking impossible exactly
    where it matters most.

    `voltage_correction` (child bus -> multiplier, default 1.0 for every edge)
    rescales each edge's thermal rating `s_max_mva`. `s_max_mva` is computed
    at nominal voltage (v=1.0 pu); the true current-based rating scales with
    actual voltage (S = sqrt(3)*V*I, so S_max_true ~= S_max_nominal * v_pu).

    `p_loss_correction`/`q_const_override` are `lindistflow_search_iterative`'s
    *real* fix (see its docstring): pure LinDistFlow drops every edge's own
    resistive/reactive loss, and on a trunk edge near the source -- which
    carries the summed current of an entire deep downstream subtree -- those
    losses compound to something non-negligible even though each individual
    edge's own loss is tiny. `p_loss_correction[j]` is added as an extra local
    injection at bus j (so it propagates up through the same subtree-summing
    recursion real loads use); `q_const_override` replaces `model.q_const_mvar`
    wholesale (the iterative loop re-aggregates it with loss folded in before
    calling `_solve`, since Q is precomputed via `subtree_sum`, not a variable).

    `voltage_margin_pu`/`thermal_margin_pct` shrink the *search's own* feasible
    region below `problem`'s true planning limits (e.g. target 1.0995 pu, not
    1.10). Recovering the compounded-loss capacity above pushes solutions
    close enough to the true AC boundary that residual model error (untracked
    second-order terms, octagon coarseness) can occasionally land just past
    it -- a small margin trades back a little of that recovered capacity for
    making the search's own answer land inside the true feasible region more
    often, rather than relying solely on after-the-fact AC rejection.

    `continuous` (M7) relaxes the accept variables' integrality: `[0, 1]`
    binary becomes `[0, 1]` continuous, i.e. the MILP becomes a plain LP
    (`scipy.optimize.milp` with an all-zero `integrality` array still solves
    it, just without branch-and-bound). No other change is needed -- the
    power-balance equation already uses `accept_r * p_r` as a *linear* term,
    so a fractional value already means "fraction of `p_r` accepted" with no
    reinterpretation. Only `lindistflow_search` exposes this; the iterative
    loss-corrected solver (below) stays binary-only, a deliberate scope
    limit (the two concerns -- loss compounding vs. continuous sizing -- are
    orthogonal, and combining them isn't validated here).

    `max_violations` (M12) caps `queue_fairness_violations` on the chosen
    accept-set. That count is quadratic in general (each violation is an AND
    of "later request accepted" and "earlier request rejected"), but exactly
    linearizable when the accept variables are binary: for every queue-order
    pair (a before b), a new continuous variable `z_ab` in [0, 1] with the
    single constraint `z_ab >= accept_b - accept_a`. `z_ab` never appears in
    the objective, only in the aggregate cap `sum z_ab <= max_violations`, so
    the solver has no incentive to set any `z_ab` above the true violation
    indicator -- at the optimum `z_ab == max(0, accept_b - accept_a)`
    exactly, matching `queue_fairness_violations`'s own pairwise count. Not
    supported with `continuous=True` (the equivalence needs binary accept
    variables); combining them isn't validated here, the same orthogonal-
    scope-limit precedent as the paragraph above.
    """
    K = len(model.problem.requests)
    E = len(model.nonroot_buses)
    pos = {b: i for i, b in enumerate(model.nonroot_buses)}
    P_off, V2_off = K, K + E
    pairs = _fairness_pairs(model) if max_violations is not None else []
    Z_off = K + 2 * E
    n_vars = K + 2 * E + len(pairs)
    if max_violations is not None and continuous:
        raise NotImplementedError("max_violations requires binary accept variables (continuous=False)")

    by_bus = _requests_by_bus(model)
    q_const_map = q_const_override if q_const_override is not None else model.q_const_mvar

    rows: list[int] = []
    cols: list[int] = []
    data: list[float] = []
    b_eq: list[float] = []
    row = 0

    # power balance, per non-root bus j: P_e(j) - sum(P_e(child)) - sum(accept_r * p_r) = existing_p[j] (+ loss correction)
    for j in model.nonroot_buses:
        rows.append(row); cols.append(P_off + pos[j]); data.append(1.0)
        for c in model.children[j]:
            rows.append(row); cols.append(P_off + pos[c]); data.append(-1.0)
        for ridx, r in by_bus.get(j, []):
            rows.append(row); cols.append(ridx); data.append(-r.p_mw)
        rhs_p = model.existing_p_mw.get(j, 0.0)
        if p_loss_correction:
            rhs_p += p_loss_correction.get(j, 0.0)
        b_eq.append(rhs_p)
        row += 1

    # voltage drop, per non-root bus j (edge parent(j) -> j):
    # v2_j - v2_parent + 2*R*P_e = -2*X*Q_const
    for j in model.nonroot_buses:
        i = model.parent[j]
        r_e, x_e = model.r_pu[j], model.x_pu[j]
        rows.append(row); cols.append(V2_off + pos[j]); data.append(1.0)
        rhs = -2 * x_e * q_const_map[j]
        if i == model.root:
            rhs += model.v2_root
        else:
            rows.append(row); cols.append(V2_off + pos[i]); data.append(-1.0)
        rows.append(row); cols.append(P_off + pos[j]); data.append(2 * r_e)
        b_eq.append(rhs)
        row += 1

    A_eq = coo_matrix((data, (rows, cols)), shape=(row, n_vars)).tocsr()
    b_eq_arr = np.array(b_eq)

    lb = np.full(n_vars, -np.inf)
    ub = np.full(n_vars, np.inf)
    lb[:K], ub[:K] = 0.0, 1.0
    if bounded:
        vmin_eff = model.problem.vmin_pu + voltage_margin_pu
        vmax_eff = model.problem.vmax_pu - voltage_margin_pu
        for j in model.nonroot_buses:
            lb[V2_off + pos[j]] = vmin_eff ** 2
            ub[V2_off + pos[j]] = vmax_eff ** 2
    if fixed_accept is not None:
        for rid, val in fixed_accept.items():
            k = model.request_index[rid]
            lb[k] = ub[k] = float(val)
    if pairs:
        lb[Z_off:Z_off + len(pairs)], ub[Z_off:Z_off + len(pairs)] = 0.0, 1.0

    integrality = np.zeros(n_vars)
    integrality[:K] = 0 if continuous else 1

    c = np.zeros(n_vars)
    for i, r in enumerate(model.problem.requests):
        c[i] = -abs(r.p_mw)

    constraints = [LinearConstraint(A_eq, b_eq_arr, b_eq_arr)]
    if bounded:
        # thermal: octagon-linearized |P + jQ_const| <= S_max, per non-root bus/edge
        rows2: list[int] = []
        cols2: list[int] = []
        data2: list[float] = []
        b_ub: list[float] = []
        row2 = 0
        sides = model.octagon_sides
        thetas = [2 * math.pi * k / sides for k in range(sides)]
        for j in model.nonroot_buses:
            s_max = (model.s_max_mva[j] * (voltage_correction[j] if voltage_correction else 1.0)
                     * (1 - thermal_margin_pct / 100))
            q_const = q_const_map[j]
            for theta in thetas:
                c_, s_ = math.cos(theta), math.sin(theta)
                rows2.append(row2); cols2.append(P_off + pos[j]); data2.append(c_)
                b_ub.append(s_max - s_ * q_const)
                row2 += 1
        A_ub = coo_matrix((data2, (rows2, cols2)), shape=(row2, n_vars)).tocsr()
        constraints.append(LinearConstraint(A_ub, -np.inf, np.array(b_ub)))
    if pairs:
        # z_ab >= accept_b - accept_a, i.e. z_ab - accept_b + accept_a >= 0
        rows3: list[int] = []
        cols3: list[int] = []
        data3: list[float] = []
        for p, (a_idx, b_idx) in enumerate(pairs):
            rows3.append(p); cols3.append(Z_off + p); data3.append(1.0)
            rows3.append(p); cols3.append(b_idx); data3.append(-1.0)
            rows3.append(p); cols3.append(a_idx); data3.append(1.0)
        A_pairs = coo_matrix((data3, (rows3, cols3)), shape=(len(pairs), n_vars)).tocsr()
        constraints.append(LinearConstraint(A_pairs, 0.0, np.inf))
        # aggregate cap: sum of every pair's z_ab <= max_violations
        A_cap = coo_matrix((np.ones(len(pairs)), (np.zeros(len(pairs)), range(Z_off, Z_off + len(pairs)))),
                            shape=(1, n_vars)).tocsr()
        constraints.append(LinearConstraint(A_cap, -np.inf, float(max_violations)))
    res = milp(c, constraints=constraints, integrality=integrality,
               bounds=Bounds(lb, ub),
               options={"time_limit": time_limit_s, "mip_rel_gap": mip_gap, "disp": False})
    if res.x is None:
        raise RuntimeError(f"LinDistFlow MILP infeasible or unsolved: {res.message}")

    # Not rounded to int: for a binary solve the MILP already returns values
    # snapped to (numerically very close to) 0/1, and for a continuous solve
    # this is the actual fraction accepted -- `_summarize` needs the raw
    # value either way.
    accept_vals = {r.id: float(res.x[i]) for i, r in enumerate(model.problem.requests)}
    P_vals = {j: res.x[P_off + pos[j]] for j in model.nonroot_buses}
    V2_vals = {j: res.x[V2_off + pos[j]] for j in model.nonroot_buses}
    return accept_vals, P_vals, V2_vals, res


@dataclass
class RelaxedEvaluation:
    """What the LinDistFlow relaxation predicts for one accept-set -- not AC-verified.

    ``sizes`` is the fraction (0, 1] the relaxation assigned each accepted
    request -- 1.0 (up to solver tolerance) for every id in ``accepted_ids``
    from a binary solve, a genuine fraction from a continuous (M7) solve.
    """

    accepted_ids: frozenset[str]
    vmin_pu: float
    vmax_pu: float
    max_line_loading_percent: float
    max_trafo_loading_percent: float
    sizes: dict[str, float]


def _summarize(model: LinDistFlowModel, accept_vals: dict[str, float],
               P_vals: dict[int, float], V2_vals: dict[int, float],
               q_const_map: dict[int, float] | None = None) -> RelaxedEvaluation:
    q_const_map = q_const_map if q_const_map is not None else model.q_const_mvar
    v2_all = list(V2_vals.values()) + [model.v2_root]
    vmin_pu = math.sqrt(min(v2_all))
    vmax_pu = math.sqrt(max(v2_all))

    max_line_pct = 0.0
    max_trafo_pct = 0.0
    for j in model.nonroot_buses:
        s_mva = math.hypot(P_vals[j], q_const_map[j])
        pct = s_mva / model.s_max_mva[j] * 100 if model.s_max_mva[j] > 0 else 0.0
        if model.kind[j] == "line":
            max_line_pct = max(max_line_pct, pct)
        else:
            max_trafo_pct = max(max_trafo_pct, pct)

    # min(1.0, v): HiGHS's returned values are only "very close to" 0/1 for a
    # binary solve (see `_solve`'s comment) -- caught directly, not assumed,
    # after a real ENWL feeder (network_7/Feeder_3) returned 1.000000000000004
    # for a should-be-1.0 accept variable, which `evaluate_sizes`'s strict
    # [0, 1] range check then rejected.
    sizes = {rid: min(1.0, v) for rid, v in accept_vals.items() if v > 0}
    return RelaxedEvaluation(accepted_ids=frozenset(sizes), vmin_pu=vmin_pu, vmax_pu=vmax_pu,
                              max_line_loading_percent=max_line_pct, max_trafo_loading_percent=max_trafo_pct,
                              sizes=sizes)


def evaluate_relaxed(model: LinDistFlowModel, accepted_ids) -> RelaxedEvaluation:
    """Forward-solve the relaxed model at a *fixed* accept-set (a trivial LP,
    no branch-and-bound) -- what LinDistFlow believes voltages/loadings are for
    an accept-set someone else chose (e.g. the greedy baseline or NSGA-II's
    result), for parity-checking against the real `AllocationProblem.evaluate`.
    """
    accepted = frozenset(accepted_ids)
    fixed = {r.id: (1 if r.id in accepted else 0) for r in model.problem.requests}
    accept_vals, P_vals, V2_vals, _ = _solve(model, fixed, time_limit_s=30.0, mip_gap=0.0, bounded=False)
    return _summarize(model, accept_vals, P_vals, V2_vals)


@dataclass
class ConvexSearchResult:
    """The MILP's chosen accept-set, AC-verified, plus the relaxed model's own
    (possibly optimistic) belief about that same accept-set."""

    result: AllocationResult     # authoritative: real AllocationProblem.evaluate
    relaxed: RelaxedEvaluation   # what the LinDistFlow relaxation predicted
    solve_time_s: float
    mip_gap: float


def lindistflow_search(problem: AllocationProblem, s_base_mva: float = 1.0,
                        octagon_sides: int = 8, time_limit_s: float = 60.0,
                        mip_gap: float = 0.001, continuous: bool = False,
                        voltage_margin_pu: float = 0.0,
                        thermal_margin_pct: float = 0.0) -> ConvexSearchResult:
    """Solve the LinDistFlow MILP to maximize accommodated MW, then re-verify
    the chosen accept-set through the real AC oracle (`problem.evaluate_sizes`)
    -- the returned `.result` is exactly as authoritative as `nsga2_search`'s.

    ``continuous=True`` (M7) relaxes accept/reject to a continuous fraction
    per request -- see `_solve`'s docstring. Since {0, 1} is a strict subset
    of [0, 1], the continuous LP's *relaxed* optimum can never be worse than
    the binary MILP's on the same network/requests -- but a continuous
    optimum is also more likely to land exactly *on* a binding constraint's
    boundary (an LP optimum is generically a vertex where constraints are
    tight; a binary choice can only land on a discrete, typically less exact,
    set of loading levels), which makes it more exposed than a binary result
    to the octagon/nominal-voltage/dropped-losses approximation gap tipping
    an AC-reverified result into actual infeasibility. Observed directly on
    a tiny single-edge synthetic feeder (not on EULV or the M3 ENWL feeder in
    testing, but not provably impossible there either) -- this is exactly
    why `.result` is always the AC-reverified answer, never the relaxed
    model's own belief. ``voltage_margin_pu``/``thermal_margin_pct`` (see
    `_solve`'s docstring) shrink the search's own feasible region to trade a
    little of that recovered capacity back for a lower false-accept risk, the
    same knob `lindistflow_search_iterative` already exposes -- 0 (off) by
    default here, since it wasn't needed on either real validation network.
    """
    model = build_lindistflow_model(problem, s_base_mva=s_base_mva, octagon_sides=octagon_sides)
    t0 = time.time()
    accept_vals, P_vals, V2_vals, res = _solve(model, fixed_accept=None,
                                                time_limit_s=time_limit_s, mip_gap=mip_gap,
                                                continuous=continuous, voltage_margin_pu=voltage_margin_pu,
                                                thermal_margin_pct=thermal_margin_pct)
    dt = time.time() - t0

    relaxed = _summarize(model, accept_vals, P_vals, V2_vals)
    result = problem.evaluate_sizes(relaxed.sizes)
    achieved_gap = getattr(res, "mip_gap", float("nan"))
    return ConvexSearchResult(result=result, relaxed=relaxed, solve_time_s=dt, mip_gap=achieved_gap)


def lindistflow_search_fair(problem: AllocationProblem, max_violations: int,
                             s_base_mva: float = 1.0, octagon_sides: int = 8,
                             time_limit_s: float = 60.0, mip_gap: float = 0.001,
                             voltage_margin_pu: float = 0.0,
                             thermal_margin_pct: float = 0.0) -> ConvexSearchResult:
    """Same MILP as `lindistflow_search`, plus a hard cap of `max_violations`
    on `queue_fairness_violations` (M12) -- the convex-search analogue of
    `search.nsga2_search_fair`'s capacity-vs-fairness trade-off.

    The MILP's own objective is already single (maximize accommodated MW),
    not multi-objective like NSGA-II's, so unlike `nsga2_search_fair`'s one
    evolutionary run this traces a front by epsilon-constraint sweep: call
    once per threshold, e.g. ``[lindistflow_search_fair(problem, v) for v in
    range(len(problem.requests) + 1)]``. See `_solve`'s ``max_violations``
    docstring for the exact linearization and why it's equivalent to
    `queue_fairness_violations`'s own count. Binary accept variables only
    (`continuous=True` is not supported -- see `_solve`).

    ``.result`` is always AC-reverified, exactly like `lindistflow_search` --
    a caller must still check `.result.feasible`. Since AC-reverification
    doesn't change *who* was accepted, only whether that accept-set holds up
    under the real oracle, `queue_fairness_violations(problem.requests,
    result.accepted_ids) <= max_violations` holds by construction whenever
    the MILP solves at all.
    """
    model = build_lindistflow_model(problem, s_base_mva=s_base_mva, octagon_sides=octagon_sides)
    t0 = time.time()
    accept_vals, P_vals, V2_vals, res = _solve(model, fixed_accept=None,
                                                time_limit_s=time_limit_s, mip_gap=mip_gap,
                                                voltage_margin_pu=voltage_margin_pu,
                                                thermal_margin_pct=thermal_margin_pct,
                                                max_violations=max_violations)
    dt = time.time() - t0

    relaxed = _summarize(model, accept_vals, P_vals, V2_vals)
    result = problem.evaluate_sizes(relaxed.sizes)
    achieved_gap = getattr(res, "mip_gap", float("nan"))
    return ConvexSearchResult(result=result, relaxed=relaxed, solve_time_s=dt, mip_gap=achieved_gap)


@dataclass
class IterativeConvexSearchResult(ConvexSearchResult):
    """Same as `ConvexSearchResult`, plus the fixed-point iteration's trace."""

    n_iterations: int = 0
    converged: bool = False
    accommodated_mw_trace: tuple[float, ...] = ()  # AC-*unverified*, relaxed-model belief per round


def lindistflow_search_iterative(problem: AllocationProblem, s_base_mva: float = 1.0,
                                  octagon_sides: int = 8, time_limit_s: float = 60.0,
                                  mip_gap: float = 0.001, max_iters: int = 5,
                                  voltage_margin_pu: float = 0.0005,
                                  thermal_margin_pct: float = 1.5) -> IterativeConvexSearchResult:
    """Fixes the single-shot MILP's dominant approximation error: pure
    LinDistFlow drops every edge's own resistive/reactive loss entirely
    (that's the "Lin"). Losing an individual edge's own loss is genuinely
    negligible (validated to 5 significant figures against pandapower's own
    `runpp` on EULV's transformer branch), but a trunk edge near the source
    carries the *summed* current of its entire downstream subtree, and losses
    across thousands of downstream edges compound: on the M3 ENWL feeder
    (4,768 edges), tracing the actual solved accept-set back through
    pandapower found q_from_mvar=0.0504 MVAr on the busiest trunk line vs.
    this model's lossless q_const=0.0454 MVAr, and the lossless P_e was ~3.2%
    larger in magnitude than pandapower's true p_from_mw -- both consistent
    with real dissipation the lossless recursion never subtracts. That -- not
    the nominal-voltage thermal-rating approximation `voltage_correction`
    targets, which turned out to be under 0.1 percentage points at the actual
    binding edge -- is why the single-shot MILP underperformed the greedy
    baseline on that feeder (see experiments/29_convex_relaxation.py).

    Fix: a fixed-point / sequential-linear-approximation loop, still a MILP
    every round. After solving, estimate each edge e's own loss from its
    solved flow, `l_e ~= P_e^2 + Q_e^2` (nominal-voltage denominator, a small
    second-order simplification on top of an already-approximate model), then
    fold `R_e*l_e` into the *next* round's power-balance RHS at e's child bus
    (exactly like an extra local load there, so it propagates up through the
    same subtree-summing recursion real loads use) and `X_e*l_e` into the
    next round's reactive-power aggregation (re-run via `subtree_sum`, since
    Q is precomputed, not a variable). The nominal-voltage thermal-rating
    correction (`voltage_correction`) is kept too, folded in the same loop.

    Repeats until the accept-set stops changing (or `max_iters`).

    Recovering real capacity this way also gives up the single-shot model's
    "always conservative" property: pushing closer to the true boundary means
    an individual round can now land just past it (observed, with no margin:
    up to ~1.3 percentage points of thermal overload, or ~0.0002 pu of
    overvoltage, in testing -- not large, but real). Two things guard against
    that: `voltage_margin_pu`/`thermal_margin_pct` (see `_solve`'s docstring)
    shrink the search's own feasible region so its answer is *more likely* to
    already be truly feasible; and every round's accept-set is still AC-
    verified regardless (a handful of extra `runpp` calls, negligible next to
    NSGA-II's hundreds) -- the best round that is *actually* AC-feasible is
    what's returned, never an unverified or infeasible one, even if a later
    round reports a higher relaxed-model accommodated MW.
    """
    model = build_lindistflow_model(problem, s_base_mva=s_base_mva, octagon_sides=octagon_sides)
    order = [model.root] + model.nonroot_buses
    voltage_correction = {j: 1.0 for j in model.nonroot_buses}
    p_loss_correction = {j: 0.0 for j in model.nonroot_buses}
    q_const_map = dict(model.q_const_mvar)

    t0 = time.time()
    prev_accepted: frozenset[str] | None = None
    converged = False
    trace: list[float] = []
    best_result: AllocationResult | None = None
    best_relaxed: RelaxedEvaluation | None = None
    for it in range(max_iters):
        accept_vals, P_vals, V2_vals, _ = _solve(
            model, fixed_accept=None, time_limit_s=time_limit_s, mip_gap=mip_gap,
            voltage_correction=voltage_correction, p_loss_correction=p_loss_correction,
            q_const_override=q_const_map, voltage_margin_pu=voltage_margin_pu,
            thermal_margin_pct=thermal_margin_pct)
        relaxed = _summarize(model, accept_vals, P_vals, V2_vals, q_const_map=q_const_map)
        trace.append(sum(abs(r.p_mw) for r in problem.requests if r.id in relaxed.accepted_ids) * 1000)

        ac_result = problem.evaluate(relaxed.accepted_ids)
        if ac_result.feasible and (best_result is None
                                    or ac_result.accommodated_mw > best_result.accommodated_mw):
            best_result, best_relaxed = ac_result, relaxed

        if relaxed.accepted_ids == prev_accepted:
            converged = True
            break
        prev_accepted = relaxed.accepted_ids

        voltage_correction = {j: math.sqrt(max(V2_vals[j], 1e-6)) for j in model.nonroot_buses}
        l_e = {j: P_vals[j] ** 2 + q_const_map[j] ** 2 for j in model.nonroot_buses}
        p_loss_correction = {j: model.r_pu[j] * l_e[j] for j in model.nonroot_buses}
        q_loss_own = {j: model.existing_q_mvar.get(j, 0.0) + model.x_pu[j] * l_e[j] for j in model.nonroot_buses}
        q_const_map = subtree_sum(order, model.children, model.nonroot_buses, q_loss_own)
    dt = time.time() - t0

    if best_result is None:
        # every round was AC-infeasible -- not observed in testing (round 0
        # reproduces lindistflow_search's always-feasible-so-far baseline),
        # but don't silently hand back an infeasible/unverified result.
        raise RuntimeError("lindistflow_search_iterative: no round was AC-feasible; "
                            "fall back to lindistflow_search")
    return IterativeConvexSearchResult(
        result=best_result, relaxed=best_relaxed, solve_time_s=dt, mip_gap=mip_gap,
        n_iterations=it + 1, converged=converged, accommodated_mw_trace=tuple(trace))
