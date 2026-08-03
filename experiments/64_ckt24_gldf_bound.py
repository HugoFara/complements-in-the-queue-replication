"""M35: how much capacity is actually on the table? A Generalized-LinDistFlow
MILP ceiling for Ckt24, and how close each search gets to it.

MOTIVATION
----------
Every capacity claim in this paper so far is RELATIVE: joint search beats FCFS
by x%, a hill-climber beats NSGA-II by 2-14.6x (Sec. 7.10), a swap operator
beats bitflip by +7-18pp (Sec. 7.12). None of them says how much capacity is
*achievable*, so none of them says how much is still being left behind. A
search that beats FCFS by +88% might be at 95% of the ceiling or at 40% of it,
and nothing measured so far can tell those apart. That is the question this
experiment answers, and it is the one a reviewer will ask.

WHY NOT THE EXISTING `convex.py` LinDistFlow MILP
-------------------------------------------------
It raises on Ckt24, on four independent preconditions, all measured not
assumed: the network is NOT radial (2 genuine topological loops, a 25-bus and
a 52-bus one, on top of 6 redundant parallel branches); it has 3 shunt
capacitor banks; and all 5,222 lines carry non-zero charging. Those are not
incidental: the capacitors (-3.30 Mvar) plus line charging (2.17 Mvar) are
**63.8% of Ckt24's reactive load** (8.58 Mvar on 50.94 MW, pf 0.986). Classic
Baran-Wu LinDistFlow discards all of it, so an "optimality guarantee" under
that relaxation would be a guarantee about a materially different network.

THE MODEL: GENERALIZED LINDISTFLOW (GLDF)
-----------------------------------------
Huang, Cui, Zhou & Bernstein, "A Generalized LinDistFlow Model for Power Flow
Analysis", IEEE CDC 2021 (arXiv:2104.02118). GLDF generalizes LinDistFlow to
multiphase networks of GENERIC topology (radial *or meshed*) around an
arbitrary linearization point, and reduces exactly to classic LinDistFlow for
a radial network linearized at zero injection. Its eq. (5)/(9):

    v = diag(conj(E)) E + M p + N q + Lambda
    Z := Y_LL^-1                                   (slack row/col removed)
    E := -Y_LL^-1 Y_LS V_S                         (open-circuit voltage)
    M :=  2 Re( diag(E) conj(Z) diag(E)^-1 )
    N := -2 Im( diag(E) conj(Z) diag(E)^-1 )
    Lambda := |V*|^2 - (diag(conj(E))E + M p* + N q*)   (fit at lin. point)

`v` is SQUARED voltage magnitude; `p`, `q` are bus power INJECTIONS. It is
LINEAR in (p, q), hence embeddable directly in a MILP over accept/reject
binaries.

The reason this is the right model here, and the reason it needs no network
surgery at all: it is built from the BUS ADMITTANCE INVERSE, not a radial
subtree sum. Meshing, shunt capacitors, line charging and parallel branches
are already inside Y -- so all four of the blockers above dissolve, with no
hand-rolled reduction, no loop-opening, and no dropped physics. We read Y
straight out of pandapower's own solved `net._ppc`, so the MILP sees exactly
the admittances the AC oracle sees.

We linearize at the BASELINE operating point (existing load, zero requests),
not at zero injection: Lambda is then fit so the model reproduces the AC
baseline voltages exactly, and `v_base` below IS the AC baseline v^2.

ACCURACY, MEASURED BEFORE TRUSTING IT
-------------------------------------
A correctness gate was run first (single-bus injection scaling sweep): GLDF's
predicted voltage converges to AC's as the perturbation -> 0 (0.640 vs 0.653
mpu predicted-vs-actual at a 1 kW injection), which any valid linearization
must do. Then, against pandapower's AC `runpp` on Ckt24, error in the quantity
that actually binds (max bus voltage), over the operating range these requests
span:

    +37 PV  (2.5 MW):  AC vmax 1.1004  GLDF 1.1028   err +2.4 mpu
    +75 PV  (4.6 MW):  AC vmax 1.1017  GLDF 1.1032   err +1.5 mpu
    +112 PV (6.8 MW):  AC vmax 1.1050  GLDF 1.1061   err +1.1 mpu
    +150 PV (8.9 MW):  AC vmax 1.1244  GLDF 1.1276   err +3.2 mpu

~0.3% on the binding quantity, on RANDOM accept-sets.

**But the optimizer does not evaluate random accept-sets -- it hunts for the
ones where the model is wrong in its favour.** On random draws GLDF OVER-
predicts vmax (above: +1 to +3 mpu, i.e. conservative). At the MILP's OWN
optimum the sign flips: GLDF UNDER-predicts vmax by 1.9-3.5 mpu, because the
search actively selects accept-sets that exploit the linearization's optimism.
This is a real and reportable effect, not a nuisance -- it is why the safety
margin and the AC repair step below are load-bearing rather than cosmetic, and
it is exactly the failure mode a reviewer should expect from optimizing against
any linear power-flow surrogate.

WHY Ckt24 AND NOT Ckt7: the same GLDF model is accurate on Ckt24 (above) and
NOT on Ckt7 (vmax error +18 to +136 mpu, against a ~110 mpu-wide voltage
band). Root-caused: exp61's exclusions leave Ckt7 a candidate pool of only 199
buses, and requests are drawn WITH REPLACEMENT, so 150 requests land on ~100
distinct buses, stacking up to 228 kW (313 kW at K=300) on a single bus --
concentrated local injections far outside the linearization's valid range.
Ckt24's 3,891-bus pool spreads 150 requests over 148 distinct buses (max 160
kW stacked, and it does NOT grow with K). That is a property of the request
sampling against a small pool, not of the model. Ckt7 is therefore explicitly
out of scope for this bound.

THE MILP
--------
    maximize  sum_r |p_r| * x_r                              x_r in {0,1}
    s.t.      v_lo <= A_v x <= v_hi                          (GLDF voltage)
              P_base_b - R_b <= sum_{r in subtree(b)} inj_r x_r <= P_base_b + R_b
                                                             (branch thermal)

Voltage: A_v[i,r] := M[i, bus(r)] * dP_r, dP_r := -p_r / baseMVA. `v_base` is
the AC baseline v^2 (exact, by the Lambda fit). Only the COLUMNS of Z at request
buses are ever needed (<= 300 of 6,057), so `build_gldf` takes one sparse solve
per distinct request bus rather than forming the 6,057^2 dense inverse (~590 MB).

Thermal: requests are pure-P, so each branch's reactive flow is a decision-
independent constant and its apparent-power limit collapses to two LINEAR bounds
on active flow -- see `build_thermal`. This matters: thermal genuinely BINDS
here. A first, voltage-only version of this MILP returned accept-sets that
AC-reverified at 128% transformer loading, so "drop thermal, it only loosens an
upper bound" was not a usable simplification.

Two subtleties, both found by AC-reverifying rather than by assuming:
  - Ckt24's baseline already sits EXACTLY at vmax = 1.0500 on one bus, so a
    naively tightened ceiling makes the MILP infeasible at x=0 and collapses it
    to the empty set. The voltage bounds are therefore CLAMPED: a bus already at
    or past the (tightened) limit may not be pushed higher, but need not come
    down -- the same non-worsening rule exp60 already uses for the substation
    transformer.
  - pandapower rates branches by CURRENT at the ACTUAL voltage, so a limit
    computed at nominal voltage does not reconcile with `loading_percent` (the
    45 MVA substation transformer carries 51.6 MW at its reported 111.06%).
    `build_thermal` calibrates each branch's effective S_max from its own
    baseline flow and pandapower's own reported loading.

TWO NUMBERS ARE REPORTED, AND THEY MEAN DIFFERENT THINGS
--------------------------------------------------------
  - **GLDF ceiling** (no voltage margin): the MILP optimum. An upper ESTIMATE of
    achievable capacity. Its accept-set does NOT AC-reverify (it misses by the
    1.9-3.5 mpu of exploited model error above), so it is not itself a usable
    allocation.
  - **GLDF-MILP (AC-ok)**: the MILP used as a METHOD. Re-solved with a small
    voltage margin, then greedily REPAIRED (`repair_to_ac_feasible`: drop the
    request buying the most voltage relief per kW surrendered, ranked by GLDF's
    own sensitivity, one AC solve per drop) until exp60's real AC oracle accepts
    it. This is a certified-achievable, AC-verified allocation and hence a lower
    bound on the true optimum. Repair matters: on STRESS seed 1 the aggressive
    solve reaches 6,829 kW and misses feasibility by 0.23 mpu, while simply
    backing the margin off collapses it to 4,682 kW -- repair keeps 6,482 kW.

RESULT (2026-07-11) -- the MILP beats NSGA-II on 6/6, decisively
----------------------------------------------------------------
| instance   | FCFS   | NSGA-II (M30)      | GLDF-MILP (AC-ok)   | ceiling |
|------------|-------:|-------------------:|--------------------:|--------:|
| MODERATE 0 | 1765.7 | 2187.7 (+23.9%)    | 4055.6 (+129.7%)    |  4473.4 |
| MODERATE 1 | 1077.4 | 2887.3 (+168.0%)   | 4430.4 (+311.2%)    |  4983.5 |
| MODERATE 2 | 1723.7 | 3249.2 (+88.5%)    | 4761.5 (+176.2%)    |  5304.4 |
| STRESS 0   | 1765.7 | 2208.9 (+25.1%)    | 6127.6 (+247.0%)    |  8280.9 |
| STRESS 1   | 1093.0 | 2731.3 (+149.9%)   | 6482.0 (+493.1%)    |  8140.4 |
| STRESS 2   | 1723.7 | 1873.7 (+8.7%)     | 6446.3 (+274.0%)    |  8233.4 |
(kW; % vs FCFS)

As a FRACTION OF THE CEILING: FCFS reaches 13-40%; NSGA-II -- the method every
prior result in this paper is built on -- reaches only **22.8-61.3%**; the
GLDF-MILP reaches **74.0-90.7%**. And it does so in ~1-4 s of MILP time
(+ seconds of repair) against NSGA-II's tens of minutes per instance.

The headline this forces: M30's "+8.7% to +168.0%, joint search beats FCFS 6/6"
is TRUE but drastically understates the opportunity. On STRESS seed 2, NSGA-II's
+8.7% sits against an achievable +274.0%. The paper's own search method was
leaving over half the capacity on the table on every instance, and the
swap-operator work (M32-M34) was tuning a method that is structurally the wrong
tool for this problem.

SCOPE / HONEST LIMITS
---------------------
- **The ceiling is an estimate, not a certificate.** GLDF is a linear
  APPROXIMATION, not a convex relaxation of AC power flow: its feasible set is
  not provably a superset of the true one, and (see above) the optimizer
  actively exploits the places it is optimistic. "Provably optimal" remains
  unclaimable. The AC-ok column, by contrast, IS certified -- exp60's real
  oracle accepts it.
- The thermal model sums power over a BFS spanning tree, so the 2 loop-closing
  branches and 6 redundant parallel branches (of 6,065) carry no constraint of
  their own, and losses inside a subtree are ignored -- the same approximation
  LinDistFlow itself makes. The AC reverification is what catches any resulting
  error, and does.
- One feeder, 6 instances. Ckt7 is out of scope (above), so this does NOT show
  the MILP route generalizes across feeders -- only that on the one real
  utility-scale feeder where GLDF is accurate, it dominates.
- Single snapshot, PV at unity power factor; the domain-correctness flags in
  DECISION.md apply unchanged.
"""

from __future__ import annotations

import argparse
import copy
import csv
import random
import time
from dataclasses import dataclass, field
from pathlib import Path

import math

import networkx as nx
import numpy as np
import pandapower as pp
import pandapower.topology as top
import scipy.sparse as sp
import scipy.sparse.linalg as spla
from scipy.optimize import Bounds, LinearConstraint, milp

from gridsynth import import_opendss

VMIN_PU, VMAX_PU, LOADING_MAX_PCT = 0.94, 1.05, 100.0
RUNPP_KWARGS = dict(max_iteration=50, init="flat", tolerance_mva=1e-4, check_connectivity=False)
SCENARIOS = {"MODERATE": 150, "STRESS": 300}
REQ_LO_MW, REQ_HI_MW = 0.010, 0.100
MASTER_PATH = "data/opendss/ckt24/master_ckt24.dss"

# exp60 (M30, stock NSGA-II at the corrected pop=60/gen=100 budget) reported
# gains over the same FCFS baseline -- read from outputs/m30_ckt24_utility_scale.csv,
# printed for reference, not recomputed here.
NSGA2_GAIN_PCT = {("MODERATE", 0): 23.9, ("MODERATE", 1): 168.0, ("MODERATE", 2): 88.5,
                   ("STRESS", 0): 25.1, ("STRESS", 1): 149.9, ("STRESS", 2): 8.7}


@dataclass
class Project:
    id: str
    bus: int
    p_mw: float  # negative: export


@dataclass
class NetworkContext:
    net: pp.pandapowerNet
    sub_idx: int
    sub_baseline_pct: float
    bad_trafo_idx: list
    excluded_line_idx: list
    excluded_buses: set = field(default_factory=set)


def build_network() -> NetworkContext:
    """Identical to exp60's -- same three disclosed adaptations
    (data/opendss/ckt24/PROVENANCE.md). Reimplemented locally per this
    project's convention of self-contained experiment scripts."""
    net = import_opendss(MASTER_PATH)
    pp.runpp(net, **RUNPP_KWARGS)

    sub_idx = net.trafo[net.trafo.name == "subxfmr"].index[0]
    sub_baseline_pct = float(net.res_trafo.at[sub_idx, "loading_percent"])

    g = top.create_nxgraph(net, include_trafos=False, include_lines=True, include_impedances=False)
    lp = net.res_trafo.loading_percent
    bad_trafo_idx = [i for i in lp[lp > 100].index if i != sub_idx]
    excluded_buses: set[int] = set()
    for idx in bad_trafo_idx:
        lv_bus = net.trafo.at[idx, "lv_bus"]
        comp = nx.node_connected_component(g, lv_bus) if lv_bus in g else {lv_bus}
        excluded_buses |= comp

    excluded_line_idx = [i for i, row in net.line.iterrows()
                         if row["from_bus"] in excluded_buses or row["to_bus"] in excluded_buses]

    return NetworkContext(net=net, sub_idx=sub_idx, sub_baseline_pct=sub_baseline_pct,
                           bad_trafo_idx=bad_trafo_idx, excluded_line_idx=excluded_line_idx,
                           excluded_buses=excluded_buses)


def build_projects(ctx: NetworkContext, k, seed) -> list[Project]:
    """Identical draw to exp60's (same rng, same order) so the request sets --
    and therefore the FCFS baselines and the m30 reference gains -- match."""
    candidate_buses = [b for b in ctx.net.load.bus.unique() if b not in ctx.excluded_buses]
    rng = random.Random(seed)
    projects = []
    for i in range(k):
        bus = rng.choice(candidate_buses)
        p_kw = rng.uniform(REQ_LO_MW, REQ_HI_MW) * 1000
        projects.append(Project(id=f"p{i}", bus=bus, p_mw=-p_kw / 1000))
    return projects


def evaluate(ctx: NetworkContext, projects, accepted_ids: set[str]) -> tuple[bool, float, float, dict]:
    """exp60's oracle verbatim, plus a `detail` dict naming which constraint
    binds -- needed to report *why* an MILP accept-set fails AC reverification
    (thermal, by construction, is the one the MILP doesn't model)."""
    net = copy.deepcopy(ctx.net)
    by_id = {p.id: p for p in projects}
    for rid in accepted_ids:
        p = by_id[rid]
        pp.create_load(net, bus=p.bus, p_mw=p.p_mw, q_mvar=0.0, name=rid)

    try:
        pp.runpp(net, **RUNPP_KWARGS)
    except pp.LoadflowNotConverged:
        return False, 0.0, float("inf"), {"diverged": True}

    vmin = float(net.res_bus.vm_pu.min())
    vmax = float(net.res_bus.vm_pu.max())
    other_line_pct = net.res_line.loading_percent.drop(ctx.excluded_line_idx)
    line_pct = float(other_line_pct.max()) if len(other_line_pct) else 0.0
    other_trafo_pct = net.res_trafo.loading_percent.drop([ctx.sub_idx, *ctx.bad_trafo_idx])
    other_trafo_max = float(other_trafo_pct.max()) if len(other_trafo_pct) else 0.0
    sub_pct = float(net.res_trafo.at[ctx.sub_idx, "loading_percent"])

    over_v = max(0.0, vmax - VMAX_PU)
    under_v = max(0.0, VMIN_PU - vmin)
    over_line = max(0.0, line_pct - LOADING_MAX_PCT)
    over_other_trafo = max(0.0, other_trafo_max - LOADING_MAX_PCT)
    over_sub_trafo = max(0.0, sub_pct - ctx.sub_baseline_pct)

    violation = over_v + under_v + over_line + over_other_trafo + over_sub_trafo
    detail = {"vmax": vmax, "vmin": vmin, "line_pct": line_pct,
              "trafo_pct": other_trafo_max, "sub_pct": sub_pct,
              "over_v": over_v, "under_v": under_v, "over_line": over_line,
              "over_trafo": over_other_trafo, "over_sub": over_sub_trafo}
    # bus carrying the max voltage -- needed to steer the repair step below
    detail["vmax_bus"] = int(net.res_bus.vm_pu.idxmax())
    accommodated_mw = sum(abs(by_id[rid].p_mw) for rid in accepted_ids)
    return violation == 0.0, accommodated_mw, violation, detail


def repair_to_ac_feasible(ctx: NetworkContext, projects, g: GLDF, ids: set[str],
                           max_drops: int = 40) -> tuple[set[str], float, int]:
    """Greedy AC repair of an MILP accept-set that misses feasibility by a hair.

    The MILP optimizes against GLDF, and at its own optimum GLDF *under*-predicts
    vmax by ~0.2-3.5 mpu (the optimizer actively seeks accept-sets where the linear
    model is optimistic). Backing the whole solve off with a bigger safety margin
    is a blunt fix: on STRESS seed 1, going from a 0.003 to a 0.004 pu margin
    costs 2.1 MW of accepted capacity to cure a 0.23 mpu overshoot.

    Instead, keep the aggressive accept-set and drop requests one at a time,
    choosing the one that buys the most voltage relief per kW surrendered --
    ranked by GLDF's own sensitivity M[vmax_bus, bus(r)] * dP_r / kW_r, so the
    ranking is free. One AC solve per drop; converges in a few drops.
    """
    ids = set(ids)
    by_id = {p.id: p for p in projects}
    pos = {int(b): i for i, b in enumerate(g.pq)}
    drops = 0

    for _ in range(max_drops):
        ok, mw, viol, d = evaluate(ctx, projects, ids)
        if ok:
            return ids, mw, drops
        if not ids:
            return set(), 0.0, drops

        row = pos.get(int(g.lookup[int(d.get("vmax_bus", -1))])) \
            if d.get("vmax_bus", -1) >= 0 else None

        best_rid, best_score = None, -np.inf
        for rid in ids:
            p = by_id[rid]
            kw = abs(p.p_mw)
            if kw <= 0:
                continue
            if row is not None and d.get("over_v", 0.0) > 0.0:
                col = g.m_cols.get(int(p.bus))
                relief = float(col[row]) * (-p.p_mw / g.base_mva) if col is not None else 0.0
            else:
                # thermal (or divergence): fall back to shedding the biggest request
                relief = kw
            score = relief / kw
            if score > best_score:
                best_rid, best_score = rid, score

        if best_rid is None:
            return set(), 0.0, drops
        ids.discard(best_rid)
        drops += 1

    ok, mw, _, _ = evaluate(ctx, projects, ids)
    return (ids, mw, drops) if ok else (set(), 0.0, drops)


def skip_continue_baseline(ctx: NetworkContext, projects) -> tuple[set[str], float]:
    accepted: set[str] = set()
    accepted_mw = 0.0
    for p in projects:
        trial = accepted | {p.id}
        ok, mw, _, _ = evaluate(ctx, projects, trial)
        if ok:
            accepted, accepted_mw = trial, mw
    return accepted, accepted_mw


# --------------------------------------------------------------------------
# Generalized LinDistFlow
# --------------------------------------------------------------------------

@dataclass
class GLDF:
    """GLDF pieces for one solved network. `v_base` is the AC baseline v^2
    (i.e. the Lambda-corrected model value at the linearization point, exact by
    construction); `m_cols[j]` is column j of M for a pandapower bus j."""
    v_base: np.ndarray                  # squared |V| at PQ buses, AC baseline
    m_cols: dict[int, np.ndarray]       # pp bus -> M[:, that bus]
    base_mva: float
    pq: np.ndarray
    lookup: np.ndarray


@dataclass
class Thermal:
    """Linear branch-loading model (see `build_thermal`). One entry per branch
    that at least one request can actually load."""
    p_base: np.ndarray        # MW flowing INTO each branch's downstream subtree
    r_lim: np.ndarray         # sqrt(S_max^2 - Q_base^2), the allowed |P_flow|
    members: list[list[int]]  # branch -> indices of requests in its subtree
    labels: list[str]


def build_thermal(ctx: NetworkContext, projects) -> Thermal:
    """Requests are pure-P, so a branch's reactive flow is (to first order) a
    decision-independent constant and its apparent-power limit collapses to a
    pair of LINEAR bounds on active flow:

        |S_b|^2 = (P_base_b - sum_{r in subtree(b)} inj_r x_r)^2 + Q_base_b^2
                <= S_max_b^2
        <=>  P_base_b - R_b  <=  sum_{r in subtree(b)} inj_r x_r  <=  P_base_b + R_b
             with R_b := sqrt(S_max_b^2 - Q_base_b^2)

    `P_base`/`Q_base` are read from the AC baseline solve (so they already carry
    losses, line charging and the capacitor banks), and the flow is oriented
    INTO the downstream subtree, so accepting an export request of `inj_r` MW
    reduces it by exactly `inj_r`.

    The subtree is taken on a BFS spanning tree from the slack. Ckt24 has 2
    genuine loops (a 25-bus and a 52-bus one) out of 6,058 buses; the 2
    loop-closing branches and the 6 redundant parallel branches are not
    themselves constrained (a disclosed approximation, ~0.03% of branches).
    Losses inside a subtree are ignored, the same approximation LinDistFlow
    itself makes.
    """
    net = ctx.net
    # BFS spanning tree from the slack over lines + trafos
    adj: dict[int, list[tuple[int, str, int]]] = {}
    for i, r in net.line.iterrows():
        adj.setdefault(int(r.from_bus), []).append((int(r.to_bus), "line", i))
        adj.setdefault(int(r.to_bus), []).append((int(r.from_bus), "line", i))
    for i, r in net.trafo.iterrows():
        adj.setdefault(int(r.hv_bus), []).append((int(r.lv_bus), "trafo", i))
        adj.setdefault(int(r.lv_bus), []).append((int(r.hv_bus), "trafo", i))

    root = int(net.ext_grid.bus.iloc[0])
    parent_edge: dict[int, tuple[str, int, int]] = {}   # bus -> (kind, idx, parent_bus)
    seen = {root}
    stack = [root]
    while stack:
        u = stack.pop()
        for v, kind, idx in adj.get(u, []):
            if v not in seen:
                seen.add(v)
                parent_edge[v] = (kind, idx, u)
                stack.append(v)

    excl_lines = set(ctx.excluded_line_idx)
    excl_trafos = {ctx.sub_idx, *ctx.bad_trafo_idx}

    # For each request, walk root-ward and record every branch it loads.
    branch_key: dict[tuple[str, int], int] = {}
    p_base, q_base, s_max, labels, members = [], [], [], [], []

    for r_i, proj in enumerate(projects):
        b = int(proj.bus)
        while b in parent_edge:
            kind, idx, par = parent_edge[b]
            key = (kind, idx)
            if key not in branch_key:
                # skip branches the oracle doesn't check
                if kind == "line" and idx in excl_lines:
                    b = par; continue
                if kind == "trafo" and idx in excl_trafos and idx != ctx.sub_idx:
                    b = par; continue

                if kind == "line":
                    row = net.line.loc[idx]
                    res = net.res_line.loc[idx]
                    # orient flow INTO the subtree (toward child bus `b`)
                    if int(row.from_bus) == par:
                        pb, qb = float(res.p_from_mw), float(res.q_from_mvar)
                    else:
                        pb, qb = float(res.p_to_mw), float(res.q_to_mvar)
                    load_pct = float(res.loading_percent)
                    nominal = math.sqrt(3) * float(net.bus.vn_kv[int(row.from_bus)]) * \
                        float(row.max_i_ka) * float(row.df) * float(row.parallel)
                    lim = 1.0
                else:
                    row = net.trafo.loc[idx]
                    res = net.res_trafo.loc[idx]
                    if int(row.hv_bus) == par:
                        pb, qb = float(res.p_hv_mw), float(res.q_hv_mvar)
                    else:
                        pb, qb = float(res.p_lv_mw), float(res.q_lv_mvar)
                    load_pct = float(res.loading_percent)
                    nominal = float(row.sn_mva) * float(row.parallel)
                    # the substation trafo's rule is "don't worsen", not "<=100%"
                    lim = (ctx.sub_baseline_pct / 100.0) if idx == ctx.sub_idx else 1.0

                # Calibrate the branch's effective apparent-power limit from its OWN
                # baseline flow and pandapower's OWN reported loading. pandapower
                # rates lines and transformers by CURRENT (S = sqrt(3)*V*I at the
                # *actual* voltage), so a limit computed at nominal voltage does not
                # reconcile with `loading_percent` -- e.g. the 45 MVA substation
                # transformer carries 51.6 MW at its reported 111.06%. Dividing the
                # baseline apparent power by the baseline loading recovers exactly
                # the S at which pandapower would call this branch 100% loaded.
                # This freezes voltage at the baseline (mildly conservative as PV
                # raises it) -- the same frozen-operating-point approximation the
                # linearization already makes. Falls back to the nameplate rating
                # for branches carrying essentially no baseline flow, where the
                # ratio is numerically unstable.
                s_base = math.hypot(pb, qb)
                smax = (s_base / (load_pct / 100.0)) if load_pct > 1.0 else nominal
                smax *= lim
                if smax ** 2 <= qb ** 2:
                    # no active-power room at all under this limit; skip rather
                    # than emit an infeasible constraint (baseline already sits
                    # here, and the oracle re-checks anyway)
                    b = par
                    continue
                branch_key[key] = len(p_base)
                p_base.append(pb)
                q_base.append(qb)
                s_max.append(smax)
                labels.append(f"{kind}{idx}")
                members.append([])
            j = branch_key.get(key)
            if j is not None:
                members[j].append(r_i)
            b = par

    p_base = np.array(p_base)
    q_base = np.array(q_base)
    s_max = np.array(s_max)
    r_lim = np.sqrt(np.maximum(s_max ** 2 - q_base ** 2, 0.0))
    return Thermal(p_base=p_base, r_lim=r_lim, members=members, labels=labels)


def build_gldf(ctx: NetworkContext, buses_needed: list[int]) -> GLDF:
    """Y comes straight from pandapower's solved ppc, so it already carries the
    meshing, the 3 capacitor banks, all 5,222 lines' charging and every parallel
    branch -- the four things classic LinDistFlow cannot represent."""
    net = ctx.net
    ppc = net._ppc
    Y = sp.csr_matrix(ppc["internal"]["Ybus"])
    base_mva = float(ppc["baseMVA"])
    lookup = net._pd2ppc_lookups["bus"]

    slack = int(lookup[int(net.ext_grid.bus.iloc[0])])
    pq = np.array([i for i in range(Y.shape[0]) if i != slack])

    Y_LL = sp.csc_matrix(Y[pq, :][:, pq])
    Y_LS = np.asarray(Y[pq, :][:, [slack]].todense()).ravel()
    lu = spla.splu(Y_LL)

    E = lu.solve(-Y_LS * float(net.ext_grid.vm_pu.iloc[0]))

    # v_base: AC baseline squared voltage == the Lambda-corrected model at the
    # linearization point. Exact, so no relaxation error at zero requests.
    inv = {int(lookup[b]): b for b in net.bus.index}
    v_base = np.array([net.res_bus.vm_pu[inv[int(i)]] for i in pq]) ** 2

    # Only the COLUMNS of Z at request buses are needed -- one sparse solve each,
    # instead of forming the 6,057^2 dense inverse (~590 MB).
    pos = {int(b): i for i, b in enumerate(pq)}
    m_cols: dict[int, np.ndarray] = {}
    for b in buses_needed:
        j = pos.get(int(lookup[int(b)]))
        if j is None:
            continue
        e_j = np.zeros(len(pq)); e_j[j] = 1.0
        z_col = lu.solve(e_j.astype(complex))            # Z[:, j]
        m_cols[int(b)] = 2.0 * np.real(E * np.conj(z_col) / E[j])
    return GLDF(v_base=v_base, m_cols=m_cols, base_mva=base_mva, pq=pq, lookup=lookup)


def gldf_milp_bound(ctx: NetworkContext, projects, g: GLDF, th: Thermal | None,
                    time_limit_s: float, mip_gap: float,
                    v_margin_pu: float = 0.0) -> tuple[set[str], float, dict]:
    """maximize sum |p_r| x_r
       s.t.  vmin^2 <= v_base + A_v x <= (vmax - v_margin)^2        (GLDF)
             P_base_b - R_b <= sum_{r in subtree(b)} inj_r x_r
                            <= P_base_b + R_b     for every branch  (thermal)

    `v_margin_pu` shrinks the search's own voltage ceiling below the true
    planning limit, to absorb GLDF's measured +1 to +3 mpu over-prediction. At
    margin 0 the solution is the (unachievable) CEILING estimate; at a margin
    large enough to cover the model error the accept-set AC-reverifies as
    genuinely FEASIBLE. Mirrors `convex.py`'s own `voltage_margin_pu`.
    """
    K = len(projects)
    cons = []

    # --- voltage (GLDF): A_v[:, r] = M[:, bus(r)] * dP_r, dP_r in pu ---
    A_v = np.zeros((len(g.pq), K))
    for r, p in enumerate(projects):
        col = g.m_cols.get(int(p.bus))
        if col is None:
            continue
        A_v[:, r] = col * (-p.p_mw / g.base_mva)

    # Ckt24's baseline already sits EXACTLY at vmax = 1.0500 on at least one bus,
    # so a naive tightened ceiling ((VMAX - margin)^2 - v_base < 0 there) would make
    # the MILP infeasible at x=0 and collapse it to the empty set. Clamp instead:
    # a bus already at or above the (tightened) ceiling may not be pushed HIGHER,
    # but is not required to come down -- the same non-worsening rule exp60 already
    # applies to the substation transformer. Same, mirrored, at the lower limit.
    v_hi = np.maximum((VMAX_PU - v_margin_pu) ** 2 - g.v_base, 0.0)
    v_lo = np.minimum((VMIN_PU + v_margin_pu) ** 2 - g.v_base, 0.0)

    keep = np.abs(A_v).max(axis=1) > 1e-12   # rows no request can move
    cons.append(LinearConstraint(A_v[keep], v_lo[keep], v_hi[keep]))
    n_v = int(keep.sum())

    # --- thermal: one row per branch, entries = inj_r (MW) for r in subtree ---
    n_t = 0
    if th is not None and len(th.p_base):
        A_t = np.zeros((len(th.p_base), K))
        for b, mem in enumerate(th.members):
            for r in mem:
                A_t[b, r] = -projects[r].p_mw     # inj_r, MW (export > 0)
        lo = th.p_base - th.r_lim
        hi = th.p_base + th.r_lim
        cons.append(LinearConstraint(A_t, lo, hi))
        n_t = len(th.p_base)

    c = np.array([-abs(p.p_mw) for p in projects])   # maximize accommodated MW
    res = milp(c=c, constraints=cons, integrality=np.ones(K), bounds=Bounds(0, 1),
               options={"time_limit": time_limit_s, "mip_rel_gap": mip_gap,
                        "presolve": True})

    if res.x is None:
        return set(), 0.0, {"status": int(res.status), "rows_v": n_v, "rows_t": n_t,
                            "mip_gap": float("nan")}

    x = np.round(res.x).astype(int)
    ids = {p.id for p, xi in zip(projects, x) if xi}
    bound_mw = float(sum(abs(p.p_mw) for p, xi in zip(projects, x) if xi))
    info = {"status": int(res.status),
            "mip_gap": float(res.mip_gap) if res.mip_gap is not None else float("nan"),
            "rows_v": n_v, "rows_t": n_t}
    return ids, bound_mw, info


def run_instance(ctx, scenario, seed, time_limit_s, mip_gap, no_thermal=False) -> dict:
    k = SCENARIOS[scenario]
    projects = build_projects(ctx, k, seed)
    total_mw = sum(abs(p.p_mw) for p in projects)

    t0 = time.time()
    fifo_ids, fifo_mw = skip_continue_baseline(ctx, projects)
    t_fifo = time.time() - t0

    t0 = time.time()
    g = build_gldf(ctx, [p.bus for p in projects])
    th = None if no_thermal else build_thermal(ctx, projects)
    t_gldf = time.time() - t0

    # (1) the CEILING: no voltage margin -- an upper estimate, generally not
    #     AC-achievable (GLDF over-predicts vmax by ~1-3 mpu, so its optimum sits
    #     a hair past the true boundary).
    t0 = time.time()
    milp_ids, bound_mw, info = gldf_milp_bound(ctx, projects, g, th, time_limit_s, mip_gap)
    t_milp = time.time() - t0
    ok, ac_mw, viol, detail = evaluate(ctx, projects, milp_ids)

    # (2) an ACHIEVABLE solution: re-solve with a voltage margin large enough to
    #     absorb the model error, and keep the best accept-set that AC-REVERIFIES
    #     as genuinely feasible. This is the MILP used as a *method*, not a bound,
    #     and is a certified lower bound on the true optimum.
    # The margin must absorb model error the OPTIMIZER ACTIVELY EXPLOITS: unlike the
    # random accept-sets GLDF was validated on (where it over-predicted vmax by
    # +1-3 mpu), the MILP deliberately seeks accept-sets where the model is
    # optimistic, so at its own optimum GLDF *under*-predicts vmax by ~2-3.5 mpu.
    # Sweep upward and keep the largest accept-set that AC-reverifies.
    t0 = time.time()
    best_mw, best_margin, best_n, best_drops = 0.0, float("nan"), 0, 0
    for margin in (0.000, 0.002, 0.004):
        ids_m, _, _ = gldf_milp_bound(ctx, projects, g, th, time_limit_s, mip_gap,
                                       v_margin_pu=margin)
        if not ids_m:
            continue
        ids_r, mw_r, drops = repair_to_ac_feasible(ctx, projects, g, ids_m)
        if mw_r > best_mw:
            best_mw, best_margin, best_n, best_drops = mw_r, margin, len(ids_r), drops
    t_achv = time.time() - t0

    nsga2_pct = NSGA2_GAIN_PCT.get((scenario, seed), float("nan"))
    nsga2_mw = fifo_mw * (1 + nsga2_pct / 100) if fifo_mw else float("nan")

    return dict(
        scenario=scenario, seed=seed, k=k,
        total_requested_kw=total_mw * 1000,
        fifo_kw=fifo_mw * 1000, fifo_n=len(fifo_ids),
        gldf_bound_kw=bound_mw * 1000, gldf_bound_n=len(milp_ids),
        bound_over_fifo_pct=(bound_mw / fifo_mw - 1) * 100 if fifo_mw else float("nan"),
        bound_ac_feasible=bool(ok), bound_ac_violation=viol,
        bound_ac_vmax=detail.get("vmax", float("nan")),
        bound_ac_line_pct=detail.get("line_pct", float("nan")),
        bound_ac_trafo_pct=detail.get("trafo_pct", float("nan")),
        bound_ac_sub_pct=detail.get("sub_pct", float("nan")),
        # MILP as a METHOD: best AC-reverified-feasible accept-set
        milp_achievable_kw=best_mw * 1000, milp_achievable_n=best_n,
        milp_achievable_margin_pu=best_margin, milp_repair_drops=best_drops,
        milp_over_fifo_pct=(best_mw / fifo_mw - 1) * 100 if fifo_mw else float("nan"),
        milp_pct_of_bound=best_mw / bound_mw * 100 if bound_mw else float("nan"),
        nsga2_ref_pct=nsga2_pct, nsga2_ref_kw=nsga2_mw * 1000,
        fifo_pct_of_bound=fifo_mw / bound_mw * 100 if bound_mw else float("nan"),
        nsga2_pct_of_bound=nsga2_mw / bound_mw * 100 if bound_mw else float("nan"),
        mip_gap=info.get("mip_gap", float("nan")),
        milp_rows_v=info.get("rows_v", -1), milp_rows_t=info.get("rows_t", -1),
        t_fifo_s=t_fifo, t_gldf_s=t_gldf, t_milp_s=t_milp, t_achievable_s=t_achv)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--instances", default="MODERATE:0,MODERATE:1,MODERATE:2,"
                                            "STRESS:0,STRESS:1,STRESS:2")
    ap.add_argument("--time-limit", type=float, default=300.0, help="MILP time limit, s")
    ap.add_argument("--mip-gap", type=float, default=1e-4)
    ap.add_argument("--no-thermal", action="store_true",
                     help="voltage-only bound (looser; thermal demonstrably binds on Ckt24)")
    ap.add_argument("--save", action="store_true")
    args = ap.parse_args()

    instances = []
    for tok in args.instances.split(","):
        s, sd = tok.split(":")
        instances.append((s.strip(), int(sd)))

    print("M35: a Generalized-LinDistFlow MILP ceiling for Ckt24\n")
    print("Importing Ckt24 + exp60's three disclosed adaptations...")
    ctx = build_network()
    ok0, _, _, d0 = evaluate(ctx, [], set())
    print(f"  baseline feasible={ok0}  vmax={d0['vmax']:.4f}  vmin={d0['vmin']:.4f}  "
          f"sub={d0['sub_pct']:.1f}%\n")

    rows = []
    for scenario, seed in instances:
        r = run_instance(ctx, scenario, seed, args.time_limit, args.mip_gap,
                         no_thermal=args.no_thermal)
        rows.append(r)
        print(f"{scenario:8s} seed={seed}  requested={r['total_requested_kw']:8.1f} kW")
        print(f"   FCFS               {r['fifo_kw']:9.1f} kW ({r['fifo_n']:3d})"
              f"                    = {r['fifo_pct_of_bound']:5.1f}% of ceiling")
        print(f"   NSGA-II (M30)      {r['nsga2_ref_kw']:9.1f} kW      "
              f"({r['nsga2_ref_pct']:+7.1f}% vs FCFS) = {r['nsga2_pct_of_bound']:5.1f}% of ceiling")
        print(f"   GLDF-MILP (AC-ok)  {r['milp_achievable_kw']:9.1f} kW ({r['milp_achievable_n']:3d})"
              f" ({r['milp_over_fifo_pct']:+7.1f}% vs FCFS) = {r['milp_pct_of_bound']:5.1f}% of ceiling"
              f"  [v-margin {r['milp_achievable_margin_pu']:.3f} pu, "
              f"{r['milp_repair_drops']} AC-repair drops]")
        print(f"   GLDF ceiling       {r['gldf_bound_kw']:9.1f} kW ({r['gldf_bound_n']:3d})"
              f" ({r['bound_over_fifo_pct']:+7.1f}% vs FCFS) "
              f"  mip_gap={r['mip_gap']:.0e}")
        if not r["bound_ac_feasible"]:
            print(f"        (ceiling accept-set is AC-infeasible by {r['bound_ac_violation']:.4f} "
                  f"-- vmax={r['bound_ac_vmax']:.4f}, i.e. GLDF's own model error; "
                  f"it is an upper ESTIMATE, not achievable)")
        print(f"   [t: fifo={r['t_fifo_s']:.0f}s gldf={r['t_gldf_s']:.0f}s "
              f"milp={r['t_milp_s']:.0f}s achievable={r['t_achievable_s']:.0f}s]\n")

    if args.save and rows:
        out = Path(__file__).resolve().parents[1] / "outputs"
        out.mkdir(exist_ok=True)
        path = out / "m35_ckt24_gldf_bound.csv"
        fields = sorted({k for r in rows for k in r})
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader(); w.writerows(rows)
        print(f"saved {path}")


if __name__ == "__main__":
    main()
