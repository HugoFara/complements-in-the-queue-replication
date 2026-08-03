"""M39: re-running Ckt7's headline margins (M31, Sec. 7.9) with the GLDF-MILP.

MOTIVATION
----------
The ceiling audit (M35-M38) established that NSGA-II's failure is a SCALE
effect: it reaches ~100% of achievable capacity at 40-50 accept/reject binaries
(EULV, Fontinele, SMART-DS) and collapses to 22.8-61.3% at Ckt24's 150-300
binaries on 6,058 buses. Ckt7 is the paper's OTHER real utility-scale feeder,
with the same 150-300-binary request sets -- so its reported margins (M31:
+6.8% to +61.4%, four of them disclosed as lower bounds) are exactly the kind of
number the audit says should be badly understated. M32 already showed as much
from the other side: a plain swap-aware hill-climber beat those NSGA-II numbers
on 6/6 instances by 2-14.6x.

This re-runs the SAME six instances (identical draws, identical exclusions,
identical oracle -- exp61's `build_network`/`build_projects`/`evaluate`
reimplemented verbatim per this project's self-contained-experiment convention)
with the MILP route, and reports the result against every method tried so far.

THE HONEST COMPLICATION, STATED UP FRONT: GLDF IS INACCURATE ON Ckt7
--------------------------------------------------------------------
!! WRONG. THIS ENTIRE SECTION IS RETRACTED -- see the M40 block below, exp70/M42,
!! and docs/logs/ckt7.md (M40, retracted). GLDF is NOT inaccurate on Ckt7. The +18..+136 mpu was
!! measured on RANDOM accept-sets 3-8x outside the feasible region, which no
!! optimizer ever visits; at the MILP's own optimum the error is +0.01..+0.77 mpu
!! and its zero-margin optimum is AC-feasible 6/6 with ZERO repair drops. The
!! "propose, don't decide" design below was therefore unnecessary -- the LP was
!! deciding all along. The reasoning is left in place, struck through, because the
!! mistake is the point: a surrogate must be validated ON THE OPTIMIZER'S OWN
!! TRAJECTORY, not on random samples of the decision space.

~~M35 measured it: GLDF's vmax error on Ckt7 is +18 to +136 mpu against a ~110
mpu-wide voltage band, versus +1 to +3 mpu on Ckt24. Root-caused there, and NOT
a defect of the model: exp61's exclusions leave Ckt7 a candidate pool of only
**199 buses**, and `build_projects` samples **WITH replacement**, so 150 requests
land on ~100 distinct buses, stacking up to 228 kW (313 kW at K=300) onto a
single bus -- concentrated local injections far outside any linearization's valid
range. Ckt24's 3,891-bus pool spreads 150 requests over 148 distinct buses (max
160 kW stacked, and it does not grow with K).~~ (The stacking figures are real but
describe the DRAWN QUEUE; the MILP accepts only 35-55 of 150-300 requests and
avoids stacking, since stacking is expensive in voltage. Max stacked at the
optimum: 82-135 kW.)

~~So on Ckt7 the MILP cannot be trusted to *decide* -- only to *propose*. The
design reflects that honestly:~~

  1. `gldf_milp`   -- propose. Its optimum is NOT reported as a ceiling here (on
                      Ckt24 it was; here the model error is too large for the
                      word to mean anything). It is a starting point only.
  2. `ac_repair`   -- drop, ranked by GLDF sensitivity per kW, one REAL AC solve
                      per drop, until exp61's actual oracle accepts the set.
  3. `ac_swap_climb` -- a bounded randomized swap hill-climb on the REAL oracle
                      (exp62's move set and budget), seeded from the repaired
                      set. This is what actually does the optimizing here, and
                      every number reported is AC-verified by it.

An exhaustive 1-swap polish (as used in M37/M38) is NOT affordable at this size:
with ~150 accepted and ~150 rejected the neighbourhood is ~22,500 AC solves per
instance. The randomized climb with a fixed budget is the bounded substitute, and
its budget is disclosed rather than tuned per instance.

WHAT THIS CAN AND CANNOT SHOW. If MILP+repair+climb beats NSGA-II decisively, the
headline is understated exactly as the audit predicts. If it merely matches M32's
plain hill-climber, that is ALSO a real finding -- it would say the MILP route
buys nothing on Ckt7 *because the linear model is broken there*, which in turn
says the fix is the request-sampling protocol (see `--no-replacement`), not the
optimizer. Both outcomes are reported as found.

`--no-replacement` runs the SAME experiment with requests drawn WITHOUT
replacement -- a diagnostic, and arguably the more realistic queue model (two
interconnection requests at the identical bus is an odd thing to draw by
accident). It changes the instances, so its numbers are NOT comparable to M31's
headline and are reported separately.

RESULT (2026-07-11) -- the MILP route beats EVERY prior method on 6/6
----------------------------------------------------------------------
Gain over the same honest FCFS baseline (swap-climb budget 3,000 AC evals per
margin; every reported number AC-verified by exp61's own oracle):

| instance   | FCFS    | NSGA-II (M31) | hill-climb (M32) | GLDF-MILP+climb |
|------------|--------:|--------------:|-----------------:|----------------:|
| MODERATE 0 | 336.5kW |        +49.9% |          +154.7% |     **+235.6%** |
| MODERATE 1 | 780.3   |         +6.8% |           +17.9% |      **+43.3%** |
| MODERATE 2 | 351.6   |        +10.5% |          +153.3% |     **+204.5%** |
| STRESS 0   | 413.2   |        +40.5% |          +163.8% |     **+264.1%** |
| STRESS 1   | 798.5   |         +9.8% |           +19.6% |      **+81.1%** |
| STRESS 2   | 497.6   |        +61.4% |           +66.4% |     **+224.1%** |
| MEDIAN     |         |        +25.5% |          +109.9% |     **+214.3%** |

**Beats NSGA-II on 6/6 and the M32 hill-climber on 6/6.** So the answer to this
file's own open question is the first one: Ckt7's headline was understated
exactly as the ceiling audit predicted -- and understated by MORE than M32
already showed. M31's reported +6.8% to +61.4% (four of them disclosed as lower
bounds) are superseded by +43.3% to +264.1%.

Note what this does NOT say. The MILP wins here DESPITE GLDF being inaccurate on
Ckt7, not because the model is good: the linear model only *proposes*, and the
AC repair + bounded swap climb do the deciding against the real oracle. The right
reading is that a good starting point plus the swap move is worth far more than
either NSGA-II's operators (M31) or a cold-start hill-climb (M32) -- the MILP's
proposal, even from a badly-fitting model, lands the climb in a much better basin
than random restarts do.

This also SUPERSEDES M32's own conclusion. M32 established that a plain
swap-aware hill-climber beats NSGA-II 2-14.6x on Ckt7 and read that as "NSGA-II's
operators are the problem". True, but the hill-climber is itself now beaten 6/6,
by a wide margin, so it was never the ceiling either -- it was just a better
heuristic than the one being used.

M40 -- RETRACTED (2026-07-12). See exp70/M42 and docs/logs/ckt7.md (M40, retracted).
--------------------------------------------------------------------------------
This block previously reported that GLDF's Ckt7 error "collapses" from +18..+136 mpu
to -0.2 mpu when requests are drawn WITHOUT replacement, and concluded "the model was
never broken -- the request sampler was." **That conclusion is withdrawn. The
comparison was invalid.**

The +18..+136 mpu figure (asserted at the top of this file, and carried from M35) was
measured on **RANDOM accept-sets** -- sets taking 25-50% of the queue, i.e. 1.9-8.7 MW
of PV on a feeder that hosts ~1.1-1.5 MW. Those points sit 3-8x outside the feasible
region (AC vmax 1.08-1.10 pu; the fuller ones diverge outright), so a linearization
about the base point is naturally hopeless there. The -0.2 mpu figure was measured at
the MILP's OPTIMUM. Comparing them compared two different quantities.

Like for like, at the MILP's own optimum, WITH replacement (measured with this file's
own `gldf_vmax_pred`):

| accept-set (WITH replacement)          | accepted     | GLDF vmax error   |
|----------------------------------------|-------------:|------------------:|
| random 25-50% of queue -- NEVER VISITED | 1861-8692 kW | +11 to +76 mpu    |
| **the MILP's own optimum**              | 1067-1593 kW | **+0.01..+0.77**  |
| **the FCFS accept-set**                 |  337-498 kW  | **+0.04..+0.05**  |

There was no collapse because there was nothing to collapse. GLDF on Ckt7 was accurate
all along. The mechanism is mundane: **the MILP accepts only 35-55 of the 150-300
requests**, and stacking export on one bus is expensive in voltage, so the optimizer
AVOIDS the very stacking that breaks the model. The 228-313 kW of stacked export is a
property of the DRAWN QUEUE, not of any ACCEPTED set -- at the optimum the most any
single bus carries is 82-135 kW.

Consequences for this file:
  * The "the MILP wins DESPITE GLDF being inaccurate / the LP only proposes" reading in
    the RESULT section above is **WRONG**. The zero-margin MILP optimum is AC-feasible
    6/6 here with zero repair drops; the LP was deciding all along. exp70/M42 removes
    the repair AND the swap climb and still beats every number above on 6/6.
  * The stacking mechanism is still real -- it does explain why the RANDOM-SET error is
    far worse on Ckt7 (199 candidate buses) than Ckt24 (~3,650). It is just not a fact
    about anything any method does.
  * Sampling without replacement remains arguably the better queue model, but it does
    NOT rescue the model. The STRESS clamp (300 requests, 199-bus pool) still argues
    against adopting it casually.

What SURVIVES from the M40 run, because it was measured correctly: Ckt7's binding
constraint is **pure overvoltage** (vmax = 1.0500 exactly at every optimum, thermal
slack everywhere), and on the no-replacement instances the achievable total is nearly
INVARIANT (~1.1-1.5 MW) while FCFS swings 247-570 kW -- the optimum belongs to the
FEEDER; FCFS's variance is luck of the arrival order.

**The transferable lesson: validate a surrogate on the trajectory the optimizer
actually follows -- at its optimum, and at the incumbents it passes through -- NEVER on
random samples of the decision space.** Random-set error is easy to compute and was
actively misleading here for five consecutive experiments.

Run:  uv run python experiments/68_ckt7_gldf_headline.py [--save]
      uv run python experiments/68_ckt7_gldf_headline.py --no-replacement [--save]
"""

from __future__ import annotations

import argparse
import copy
import csv
import math
import random
import time
from dataclasses import dataclass, field
from pathlib import Path

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
MASTER_PATH = "data/opendss/ckt7/Master_ckt7.dss"
REPO = Path(__file__).resolve().parents[1]

# Reference gains over the SAME FCFS baseline, from the CSVs -- not recomputed.
NSGA2_M31 = {("MODERATE", 0): 49.9, ("MODERATE", 1): 6.8, ("MODERATE", 2): 10.5,
             ("STRESS", 0): 40.5, ("STRESS", 1): 9.8, ("STRESS", 2): 61.4}
HILLCLIMB_M32 = {("MODERATE", 0): 154.7, ("MODERATE", 1): 17.9, ("MODERATE", 2): 153.3,
                 ("STRESS", 0): 163.8, ("STRESS", 1): 19.6, ("STRESS", 2): 66.4}


@dataclass
class Project:
    id: str
    bus: int
    p_mw: float          # negative: export


@dataclass
class NetworkContext:
    net: pp.pandapowerNet
    excluded_line_idx: list
    excluded_buses: set = field(default_factory=set)


def source_bus(net) -> int:
    """The feeder head -- the bus the OpenDSS Vsource sits on.

    Not the same thing as `net.ext_grid.bus` once the source impedance is modelled:
    the ext_grid then sits on its own EMF bus, one impedance branch upstream. Use
    this for anything TOPOLOGICAL (tree roots, reachability), and `ext_grid.bus` for
    what is genuinely pinned (the voltage-check drop, the GLDF slack)."""
    rep = net.get("opendss_import") or {}
    b = rep.get("source_bus")
    return int(b) if b is not None else int(net.ext_grid.bus.iloc[0])


def build_network() -> NetworkContext:
    """exp61's verbatim: exclude the islands behind the 11 pre-broken secondary
    service lines (data/opendss/ckt7/PROVENANCE.md).

    M100: imported with the Vsource's Thevenin impedance modelled. Ckt7 declares
    `r1=2.314 x1=28.52` on 115 kV and `pu=` sets the EMF behind it, so at the
    76.5 MW peak the source bus sags 0.081 pu. Pinning an ideal slack on it instead
    ran this feeder 0.08 pu high and made the balanced compliant setpoint window
    [1.040, 1.050] disjoint from the shipped model's [1.055, 1.120]."""
    net = import_opendss(MASTER_PATH, source_impedance=True)
    pp.runpp(net, **RUNPP_KWARGS)

    lp = net.res_line.loading_percent
    bad_line_idx = list(lp[lp > 100].index)
    # impedances included: the EMF bus hangs off the feeder head by one, and excluding
    # them would strand it in its own component -- which inverts the reachability test
    # below and excludes the entire feeder.
    g = top.create_nxgraph(net, include_trafos=True, include_lines=True, include_impedances=True)
    for idx in bad_line_idx:
        fb, tb = net.line.at[idx, "from_bus"], net.line.at[idx, "to_bus"]
        if g.has_edge(fb, tb):
            g.remove_edge(fb, tb)

    src = source_bus(net)
    excluded_buses: set[int] = set()
    for idx in bad_line_idx:
        tb = net.line.at[idx, "to_bus"]
        comp = nx.node_connected_component(g, tb) if tb in g else {tb}
        if src not in comp:
            excluded_buses |= comp

    excluded_line_idx = [i for i, row in net.line.iterrows()
                         if row["from_bus"] in excluded_buses or row["to_bus"] in excluded_buses]
    return NetworkContext(net=net, excluded_line_idx=excluded_line_idx,
                           excluded_buses=excluded_buses)


def build_projects(ctx, k, seed, replacement=True) -> list[Project]:
    """exp61's draw verbatim when `replacement=True` (same RNG, same order, so the
    request sets -- and hence the FCFS baselines and the M31/M32 references --
    match exactly)."""
    candidate_buses = [b for b in ctx.net.load.bus.unique() if b not in ctx.excluded_buses]
    rng = random.Random(seed)
    projects = []
    if replacement:
        for i in range(k):
            bus = rng.choice(candidate_buses)
            p_kw = rng.uniform(REQ_LO_MW, REQ_HI_MW) * 1000
            projects.append(Project(id=f"p{i}", bus=bus, p_mw=-p_kw / 1000))
    else:
        buses = rng.sample(candidate_buses, min(k, len(candidate_buses)))
        for i, bus in enumerate(buses):
            p_kw = rng.uniform(REQ_LO_MW, REQ_HI_MW) * 1000
            projects.append(Project(id=f"p{i}", bus=bus, p_mw=-p_kw / 1000))
    return projects


def evaluate(ctx, projects, accepted_ids) -> tuple[bool, float, float, dict]:
    """exp61's oracle verbatim, plus the argmax-voltage bus (to steer repair)."""
    net = copy.deepcopy(ctx.net)
    by_id = {p.id: p for p in projects}
    for rid in accepted_ids:
        p = by_id[rid]
        pp.create_load(net, bus=p.bus, p_mw=p.p_mw, q_mvar=0.0, name=rid)
    try:
        pp.runpp(net, **RUNPP_KWARGS)
    except pp.LoadflowNotConverged:
        return False, 0.0, float("inf"), {"diverged": True}

    # M54: the ext_grid bus is EXCLUDED from the voltage-constraint set. pandapower pins
    # res_bus.vm_pu at the slack to exactly its setpoint (here 1.05, = VMAX_PU), so no
    # interconnection request can move it -- constraining it is meaningless. It is also
    # provably VERDICT-NEUTRAL: its contribution to a max()-based overvoltage check is
    # always max(0, 1.05 - 1.05) = 0, so it can neither trip nor mask a real violation.
    # (Verified: solo seatability is bit-identical either way across 199 buses x 5 sizes.)
    # What it DID do is censor the diagnostics -- with the slack counted, 196/199 probe
    # buses report exactly zero dvmax/dP, because the reported vmax is pinned by a bus
    # nothing can move. Excluding it, the locational spread is 5.2e5. See exp82.
    #
    # M100: with the source impedance modelled these are two different buses, and BOTH
    # come out. The EMF bus for M54's reason (still pinned). SOURCEBUS because it is the
    # 115 kV transmission side, where a 0.94-1.05 distribution band does not apply -- and
    # because the unbalanced arm excludes it too (exp126's `v_mask`), so the two engines
    # would otherwise be scoring different node sets.
    #
    # M100, second defect: the excluded POCKETS were never dropped from the voltage check,
    # only from the line check and the candidate pool. The pockets are the islands behind
    # the 11 pre-broken service lines -- the whole point of excluding them is that the
    # shipped model does not hold them up -- and they sit 0.06-0.08 pu below the rest of
    # the feeder, so they own vmin whenever the check reaches them. This never showed
    # because the missing source impedance held the whole feeder 0.083 pu high, putting the
    # pockets at ~0.95 and just clear of the 0.94 floor. With the source corrected they
    # land at ~0.868 and take over. exp126 excludes them (`v_mask`); this now matches.
    drop = {int(net.ext_grid.bus.iloc[0]), source_bus(net)} | set(ctx.excluded_buses)
    vm = net.res_bus.vm_pu.drop(index=sorted(drop & set(net.res_bus.index)))
    vmin = float(vm.min())
    vmax = float(vm.max())
    other_line = net.res_line.loading_percent.drop(ctx.excluded_line_idx)
    line_pct = float(other_line.max()) if len(other_line) else 0.0
    trafo_pct = float(net.res_trafo.loading_percent.max()) if len(net.trafo) else 0.0

    violation = (max(0.0, vmax - VMAX_PU) + max(0.0, VMIN_PU - vmin)
                 + max(0.0, line_pct - LOADING_MAX_PCT) + max(0.0, trafo_pct - LOADING_MAX_PCT))
    mw = sum(abs(by_id[rid].p_mw) for rid in accepted_ids)
    detail = {"vmax": vmax, "vmin": vmin, "line_pct": line_pct, "trafo_pct": trafo_pct,
              "over_v": max(0.0, vmax - VMAX_PU),
              "vmax_bus": int(vm.idxmax())}
    return violation == 0.0, mw, violation, detail


def skip_continue_baseline(ctx, projects):
    accepted, mw = set(), 0.0
    for p in projects:
        trial = accepted | {p.id}
        ok, m, _, _ = evaluate(ctx, projects, trial)
        if ok:
            accepted, mw = trial, m
    return accepted, mw


# ------------------------------------------------------------------ GLDF + thermal
def build_gldf(ctx, buses):
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

    inv = {int(lookup[b]): b for b in net.bus.index}
    v_base = np.array([net.res_bus.vm_pu[inv[int(i)]] for i in pq]) ** 2

    pos = {int(b): i for i, b in enumerate(pq)}
    m_cols = {}
    for b in set(buses):
        j = pos.get(int(lookup[int(b)]))
        if j is None:
            continue
        e = np.zeros(len(pq)); e[j] = 1.0
        z = lu.solve(e.astype(complex))
        m_cols[int(b)] = 2.0 * np.real(E * np.conj(z) / E[j])
    return dict(v_base=v_base, m_cols=m_cols, base_mva=base_mva, pq=pq, lookup=lookup)


def build_thermal(ctx, projects):
    """Same construction as M35's Ckt24 thermal model: requests are pure-P, so a
    branch's apparent-power limit collapses to two linear bounds on active flow.
    S_max is calibrated from each branch's OWN baseline flow and pandapower's own
    reported loading (pandapower rates by CURRENT at the ACTUAL voltage, so a
    nominal-voltage rating does not reconcile with `loading_percent`).

    M48 BUG FIX -- the spanning tree must cross CLOSED BUS-BUS SWITCHES.
    ------------------------------------------------------------------
    This adjacency was built from `net.line` and `net.trafo` alone. Ckt7 also has 9
    closed bus-bus switches (`net.switch`, et == "b"), and they are STRUCTURAL: without
    them the graph is severed at the substation, the walk from the root reached **6 of
    1,255 buses**, and `build_thermal` returned a model with **one branch row**. The
    Ckt7 MILP was running with essentially NO thermal constraints.

    It went unnoticed for five milestones because it never bound. Under the synthetic
    uniform[10,100] kW band Ckt7's optimum is genuinely pure-overvoltage-limited with
    thermal slack everywhere (M40, measured against the AC oracle; M42's fixed point is
    AC-feasible 6/6 with zero repair drops) -- a constraint that never binds can be
    missing from the model for free. Under the EMPIRICAL request distribution (M45) the
    MILP seats ~135-190 tiny systems instead of ~40 large ones, stacking export behind
    500 kVA service transformers, and the thermal limit binds hard: every iterate after
    the first overloaded a service transformer to 106-108% and was rejected by the AC
    oracle, so the loop never re-entered the feasible region. That is the whole of the
    "FCFS beats the certified optimum" anomaly (M46/M47).

    Ckt24 is unaffected: it has zero switches, its walk reaches all 6,058 buses, and its
    thermal model has 987 rows.

    A closed bus-bus switch fuses two buses and carries no rating of its own, so it is
    TRAVERSED but contributes no constraint row.
    """
    net = ctx.net
    adj = {}
    for i, r in net.line.iterrows():
        adj.setdefault(int(r.from_bus), []).append((int(r.to_bus), "line", i))
        adj.setdefault(int(r.to_bus), []).append((int(r.from_bus), "line", i))
    for i, r in net.trafo.iterrows():
        adj.setdefault(int(r.hv_bus), []).append((int(r.lv_bus), "trafo", i))
        adj.setdefault(int(r.lv_bus), []).append((int(r.hv_bus), "trafo", i))
    if len(net.switch):
        sw = net.switch[(net.switch.et == "b") & net.switch.closed.astype(bool)]
        for i, r in sw.iterrows():
            adj.setdefault(int(r.bus), []).append((int(r.element), "switch", i))
            adj.setdefault(int(r.element), []).append((int(r.bus), "switch", i))

    root = source_bus(net)      # the EMF bus has no line/trafo edge; rooting there finds nothing
    parent = {}
    seen = {root}
    stack = [root]
    while stack:
        u = stack.pop()
        for v, kind, idx in adj.get(u, []):
            if v not in seen:
                seen.add(v)
                parent[v] = (kind, idx, u)
                stack.append(v)

    excl_lines = set(ctx.excluded_line_idx)
    key_of, p_base, q_base, s_max, members = {}, [], [], [], []
    for r_i, proj in enumerate(projects):
        b = int(proj.bus)
        while b in parent:
            kind, idx, par = parent[b]
            if kind == "switch":       # zero-impedance, unrated: traverse, no row
                b = par; continue
            key = (kind, idx)
            if key not in key_of:
                if kind == "line" and idx in excl_lines:
                    b = par; continue
                if kind == "line":
                    row, res = net.line.loc[idx], net.res_line.loc[idx]
                    pb, qb = ((float(res.p_from_mw), float(res.q_from_mvar))
                              if int(row.from_bus) == par else
                              (float(res.p_to_mw), float(res.q_to_mvar)))
                    nominal = (math.sqrt(3) * float(net.bus.vn_kv[int(row.from_bus)])
                               * float(row.max_i_ka) * float(row.df) * float(row.parallel))
                else:
                    row, res = net.trafo.loc[idx], net.res_trafo.loc[idx]
                    pb, qb = ((float(res.p_hv_mw), float(res.q_hv_mvar))
                              if int(row.hv_bus) == par else
                              (float(res.p_lv_mw), float(res.q_lv_mvar)))
                    nominal = float(row.sn_mva) * float(row.parallel)
                load_pct = float(res.loading_percent)
                s_b = math.hypot(pb, qb)
                smax = (s_b / (load_pct / 100.0)) if load_pct > 1.0 else nominal
                if smax ** 2 <= qb ** 2:
                    b = par; continue
                key_of[key] = len(p_base)
                p_base.append(pb); q_base.append(qb); s_max.append(smax); members.append([])
            j = key_of.get(key)
            if j is not None:
                members[j].append(r_i)
            b = par

    p_base = np.array(p_base); q_base = np.array(q_base); s_max = np.array(s_max)
    r_lim = np.sqrt(np.maximum(s_max ** 2 - q_base ** 2, 0.0))
    return dict(p_base=p_base, r_lim=r_lim, members=members)


def gldf_milp(ctx, projects, g, th, v_margin_pu=0.0, time_limit=180.0):
    K = len(projects)
    cons = []
    A = np.zeros((len(g["pq"]), K))
    for r, p in enumerate(projects):
        col = g["m_cols"].get(int(p.bus))
        if col is None:
            continue
        A[:, r] = col * (-p.p_mw / g["base_mva"])

    hi = np.maximum((VMAX_PU - v_margin_pu) ** 2 - g["v_base"], 0.0)
    lo = np.minimum((VMIN_PU + v_margin_pu) ** 2 - g["v_base"], 0.0)
    keep = np.abs(A).max(axis=1) > 1e-12
    cons.append(LinearConstraint(A[keep], lo[keep], hi[keep]))

    if th is not None and len(th["p_base"]):
        A_t = np.zeros((len(th["p_base"]), K))
        for b, mem in enumerate(th["members"]):
            for r in mem:
                A_t[b, r] = -projects[r].p_mw
        cons.append(LinearConstraint(A_t, th["p_base"] - th["r_lim"], th["p_base"] + th["r_lim"]))

    c = np.array([-abs(p.p_mw) for p in projects])
    res = milp(c=c, constraints=cons, integrality=np.ones(K), bounds=Bounds(0, 1),
               options={"time_limit": time_limit, "mip_rel_gap": 1e-4, "presolve": True})
    if res.x is None:
        return set(), 0.0
    x = np.round(res.x).astype(int)
    return ({p.id for p, xi in zip(projects, x) if xi},
            float(sum(abs(p.p_mw) for p, xi in zip(projects, x) if xi)))


def gldf_vmax_pred(g, projects, ids) -> float:
    """GLDF's own predicted vmax (pu) for an accept-set -- the quantity whose error
    vs. the real AC vmax is the model-accuracy diagnostic."""
    v = g["v_base"].copy()
    by_id = {p.id: p for p in projects}
    for rid in ids:
        p = by_id[rid]
        col = g["m_cols"].get(int(p.bus))
        if col is not None:
            v = v + col * (-p.p_mw / g["base_mva"])
    return float(np.sqrt(np.maximum(v, 0.0)).max())


def ac_repair(ctx, projects, g, ids, max_drops=200):
    """Drop, ranked by GLDF voltage relief per kW at the worst bus -- ONE real AC
    solve per drop (an exhaustive per-step scan would be O(n) solves per drop and
    is not affordable at 150-300 requests)."""
    ids = set(ids)
    by_id = {p.id: p for p in projects}
    pos = {int(b): i for i, b in enumerate(g["pq"])}
    for _ in range(max_drops):
        ok, mw, _, d = evaluate(ctx, projects, ids)
        if ok:
            return ids, mw
        if not ids:
            return set(), 0.0
        row = pos.get(int(g["lookup"][int(d["vmax_bus"])])) if not d.get("diverged") else None
        best, best_score = None, -np.inf
        for rid in ids:
            p = by_id[rid]
            kw = abs(p.p_mw)
            if kw <= 0:
                continue
            if row is not None and d.get("over_v", 0.0) > 0.0:
                col = g["m_cols"].get(int(p.bus))
                relief = float(col[row]) * (-p.p_mw / g["base_mva"]) if col is not None else 0.0
            else:
                relief = kw       # thermal / divergence: shed the biggest
            s = relief / kw
            if s > best_score:
                best, best_score = rid, s
        if best is None:
            return set(), 0.0
        ids.discard(best)
    ok, mw, _, _ = evaluate(ctx, projects, ids)
    return (ids, mw) if ok else (set(), 0.0)


def ac_swap_climb(ctx, projects, ids, budget, seed):
    """exp62's randomized swap hill-climb on the REAL oracle, seeded from the
    repaired MILP set. Fixed, disclosed budget -- an exhaustive 1-swap polish is
    ~22,500 AC solves per instance here, which is not affordable."""
    rng = random.Random(seed)
    by_id = {p.id: p for p in projects}
    cur = set(ids)
    ok, cur_mw, _, _ = evaluate(ctx, projects, cur)
    if not ok:
        return cur, 0.0, 0
    evals = 0
    while evals < budget:
        acc = list(cur)
        rej = [p.id for p in projects if p.id not in cur]
        if rng.random() < 0.6 and acc and rej:
            trial = (cur - {rng.choice(acc)}) | {rng.choice(rej)}
        elif rej and rng.random() < 0.5:
            trial = cur | {rng.choice(rej)}
        elif acc:
            trial = cur - {rng.choice(acc)}
        else:
            break
        evals += 1
        ok_t, mw_t, _, _ = evaluate(ctx, projects, trial)
        if ok_t and mw_t > cur_mw:
            cur, cur_mw = trial, mw_t
    return cur, cur_mw, evals


def run_instance(ctx, scenario, seed, budget, replacement) -> dict:
    k = SCENARIOS[scenario]
    projects = build_projects(ctx, k, seed, replacement=replacement)
    n_distinct = len({p.bus for p in projects})

    t0 = time.time()
    fifo_ids, fifo_mw = skip_continue_baseline(ctx, projects)

    g = build_gldf(ctx, [p.bus for p in projects])
    th = build_thermal(ctx, projects)

    best_mw, best_ids, best_margin = 0.0, set(), float("nan")
    err_mpu, raw_kw, rep_kw = float("nan"), float("nan"), float("nan")
    for margin in (0.000, 0.005, 0.010):
        ids, _ = gldf_milp(ctx, projects, g, th, v_margin_pu=margin)
        if not ids:
            continue
        if margin == 0.000:
            # model-accuracy diagnostic, measured AT THE MILP'S OWN OPTIMUM (where
            # the optimizer has pushed hardest on the model, so the error is worst)
            _, mw_raw, _, d_raw = evaluate(ctx, projects, ids)
            err_mpu = (gldf_vmax_pred(g, projects, ids) - d_raw["vmax"]) * 1000
            raw_kw = sum(abs(p.p_mw) for p in projects if p.id in ids) * 1000
        ids_r, mw_r = ac_repair(ctx, projects, g, ids)
        if margin == 0.000:
            rep_kw = mw_r * 1000
        ids_c, mw_c, _ = ac_swap_climb(ctx, projects, ids_r, budget, seed)
        if mw_c > best_mw:
            best_mw, best_ids, best_margin = mw_c, ids_c, margin

    gain = (best_mw / fifo_mw - 1) * 100 if fifo_mw else float("nan")
    return dict(scenario=scenario, seed=seed, k=k, n_distinct_buses=n_distinct,
                replacement=replacement,
                fifo_kw=fifo_mw * 1000, fifo_n=len(fifo_ids),
                milp_kw=best_mw * 1000, milp_n=len(best_ids), milp_margin=best_margin,
                milp_gain_pct=gain,
                gldf_vmax_err_mpu=err_mpu, milp_raw_kw=raw_kw, repaired_kw=rep_kw,
                # M31/M32's reference gains are for the WITH-replacement instances.
                # Under --no-replacement the request sets (and hence the FCFS
                # baselines) are different, so those columns would not be comparable
                # -- emit NaN rather than a number that invites a false comparison.
                nsga2_m31_pct=(NSGA2_M31.get((scenario, seed), float("nan"))
                               if replacement else float("nan")),
                hillclimb_m32_pct=(HILLCLIMB_M32.get((scenario, seed), float("nan"))
                                   if replacement else float("nan")),
                dt=time.time() - t0)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--instances", default="MODERATE:0,MODERATE:1,MODERATE:2,"
                                            "STRESS:0,STRESS:1,STRESS:2")
    ap.add_argument("--budget", type=int, default=3000,
                     help="AC evals for the swap climb (exp62 used ~15,000 across 10 restarts)")
    ap.add_argument("--no-replacement", action="store_true",
                     help="DIAGNOSTIC: draw requests without replacement (changes the "
                          "instances -- NOT comparable to M31's headline)")
    ap.add_argument("--save", action="store_true")
    args = ap.parse_args()

    repl = not args.no_replacement
    instances = [(t.split(":")[0], int(t.split(":")[1])) for t in args.instances.split(",")]

    print("M39: Ckt7 headline margins, re-run with the GLDF-MILP\n")
    print(f"  request draw: {'WITH replacement (exp61 verbatim)' if repl else 'WITHOUT replacement (DIAGNOSTIC)'}")
    print(f"  swap-climb budget: {args.budget} AC evals/margin\n")
    ctx = build_network()
    ok0, _, _, _ = evaluate(ctx, [], set())
    assert ok0, "baseline infeasible after exclusion"

    n_cand = len([b for b in ctx.net.load.bus.unique() if b not in ctx.excluded_buses])
    print(f"  candidate pool: {n_cand} buses")
    if not repl:
        clamped = sorted({s for s, _ in instances if SCENARIOS[s] > n_cand})
        if clamped:
            print(f"  !! WARNING: {', '.join(clamped)} asks for more requests than there are "
                  f"candidate buses ({n_cand}); without replacement it is CLAMPED to {n_cand}.")
            print(f"     Those rows are NOT the same scenario as the headline -- fewer requests, "
                  f"so a lower FCFS baseline and a lower ceiling. Read them as a separate "
                  f"instance, not a correction.")
    print()

    rows = []
    for scenario, seed in instances:
        r = run_instance(ctx, scenario, seed, args.budget, repl)
        rows.append(r)
        n_req = r["k"] if repl else min(r["k"], n_cand)
        print(f"{scenario:8s} seed={seed}  FCFS {r['fifo_kw']:7.1f} kW ({r['fifo_n']:3d}/{n_req})"
              f"   [{r['n_distinct_buses']:3d} distinct buses of {n_req} requests]")
        print(f"    GLDF vmax err at MILP optimum {r['gldf_vmax_err_mpu']:+7.1f} mpu"
              f"   MILP raw {r['milp_raw_kw']:7.1f} kW -> AC-repaired {r['repaired_kw']:7.1f} kW"
              f" ({100 * r['repaired_kw'] / r['milp_raw_kw']:.0f}% survives)")
        if repl:
            print(f"    NSGA-II (M31) {r['nsga2_m31_pct']:+7.1f}%"
                  f"   hill-climb (M32) {r['hillclimb_m32_pct']:+7.1f}%"
                  f"   GLDF-MILP+climb {r['milp_gain_pct']:+7.1f}% "
                  f"({r['milp_n']:3d}/{n_req}, AC-verified)   [{r['dt']:.0f}s]\n")
        else:
            # no NSGA-II / hill-climb reference exists for THESE instances -- printing
            # M31/M32's numbers here would invite a comparison across different
            # request sets. vs-FCFS is the only sound comparison available.
            print(f"    GLDF-MILP+climb vs FCFS {r['milp_gain_pct']:+7.1f}% "
                  f"({r['milp_n']:3d}/{n_req}, AC-verified)   [{r['dt']:.0f}s]\n")

    import statistics as st
    if repl:
        print("=" * 88)
        print(f"  median gain vs FCFS:  NSGA-II (M31) "
              f"{st.median(r['nsga2_m31_pct'] for r in rows):+6.1f}%"
              f"   hill-climb (M32) {st.median(r['hillclimb_m32_pct'] for r in rows):+6.1f}%"
              f"   GLDF-MILP {st.median(r['milp_gain_pct'] for r in rows):+6.1f}%")
        w_n = sum(1 for r in rows if r["milp_gain_pct"] > r["nsga2_m31_pct"] + 0.05)
        w_h = sum(1 for r in rows if r["milp_gain_pct"] > r["hillclimb_m32_pct"] + 0.05)
        print(f"  GLDF-MILP beats NSGA-II on {w_n}/{len(rows)}, beats the hill-climber on {w_h}/{len(rows)}")

    if args.save and rows:
        out = REPO / "outputs"
        out.mkdir(exist_ok=True)
        path = out / ("m39_ckt7_gldf_headline.csv" if repl
                      else "m39_ckt7_gldf_norepl.csv")
        fields = sorted({k for r in rows for k in r})
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader(); w.writerows(rows)
        print(f"\nsaved {path}")


if __name__ == "__main__":
    main()
