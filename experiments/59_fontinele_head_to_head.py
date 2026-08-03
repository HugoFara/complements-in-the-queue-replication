"""M29: a direct head-to-head against Fontinele et al. [13] on their own networks.

Fontinele, Torrez Caballero, Coelho Marques da Costa, "Connection Order Matters:
Path-Dependent Hosting Capacity in Distributed Generation Interconnection Queues"
(SSRN 6561541, submitted to Applied Energy, 11 Apr 2026) is the closest prior work
to this project's thesis. They optimize the CONNECTION ORDER of a fixed queue (a
permutation search over a sequential accept/reject walk); we search accept/reject
SUBSETS directly (no ordering is simulated -- a subset is jointly evaluated in one
shot). The paper differentiates the two as "a reordered serial queue is still
serial" vs. "the simultaneous-cluster framing FERC Order 2023 actually targets."
This experiment tests that claim directly, on their own benchmark networks, in
their own reported regime, rather than asserting it.

WHAT IS AND ISN'T A FAITHFUL REPLICATION. Reproduced exactly (verified against
their reported figures):
  - IEEE 33-bus (`pandapower.networks.case33bw()`): 33 buses, 37 lines, 32 loads,
    total load 3,715 kW + 2,300 kVAr, base Vmin = 0.9131 pu at (pandapower 0-indexed)
    bus 17 -- matches their Table/text exactly, confirming they report pandapower's
    own bus indexing.
  - IEEE 69-bus: NOT in pandapower.networks (checked: no case69 exists in this
    pandapower version). Rebuilt from MATPOWER's case69.m (github.com/MATPOWER/
    matpower, data/case69.m) -- the same Baran & Wu (1989) source Fontinele et al.
    cite, "implemented from published data". Verified: 69 buses, 68 lines, 48
    loads, total load 3,802.1 kW + 2,694.7 kVAr, Vmin = 0.9092 pu at (pandapower
    0-indexed) bus 64 -- matches their reported 3,802 kW + 2,695 kVAr / 0.909 pu
    at bus 64 exactly, including the bus index convention.
  - Their exact Algorithm 1 feasibility rule: Vmax <= 1.05 pu (all buses);
    Vmin >= 0.93 pu but ONLY for buses that were already >= 0.93 pu in a reference
    state (pre-existing undervoltage is tolerated, never re-triggered); line
    loading <= 100%; total network losses <= 15% of total load; PF convergence
    required. `sgen(P_i, bus_i)` = pure active-power injection, Q=0 (unity power
    factor DG, matching their notation).
  - Penetration scenarios I3 (50-320 kW/project, "saturation -- reordering adds
    value") and I4 (100-640 kW/project, "stress -- maximum impact") -- the two
    RESTRICTIVE scenarios their Table 5 reports gains on (I1/I2 are trivial for
    every method by their own finding: all 50 projects connect regardless of
    order below ~120% penetration, so they add nothing to this comparison).
    N=50 projects, sizes drawn uniformly within each scenario's range.

NOT reproducible and NOT attempted: their literal per-seed instances. The paper
gives no bus-assignment rule for the 50 projects ("connection bus b_i" is stated
as a project attribute, never how it's drawn) and states code/data are available
"upon reasonable request", not published. So this is a matched-METHODOLOGY
replication (same networks, same constraint model, same penetration ranges, same
N), not a matched-INSTANCE one -- we draw our own project sets (buses sampled
uniformly at random, WITH replacement since N=50 > n_load on both networks; sizes
uniform in-range) across several seeds and report the distribution, rather than
claiming to hit their exact 22.4%/9.7% means from 3 seeds.

ONE DOCUMENTED ADAPTATION: their "new undervoltage" rule compares each sequential
step's voltage to the *previous step's* voltage (a sequential-specific idea,
since a permutation has no other reference). A simultaneous subset evaluation has
no "previous step" -- so the reference state used here is the network's BASE
(no-DG) voltage snapshot, which is actually the more literal reading of their own
stated rationale ("pre-existing undervoltage in the BASE network is accepted...
if a connection worsens a pre-existing undervoltage, it is still accepted") --
that rationale is about the base network's inherent condition, not about
intermediate sequential states.

SEARCH BUDGET: `joint_search`'s generation 0 is warm-started with FIFO's own
accept-set (the M28 lesson, `nsga2_search_dss_seeded`) rather than pure
`BinaryRandomSampling`. On a 50-bit space, blind random search has no guarantee
of even matching FIFO within a modest budget -- confirmed by this script's own
early smoke test (unseeded, tiny budget: -11% to -40% vs. FIFO, a pure
search-budget artifact). Since NSGA-II is elitist, seeding means the reported
joint result can never score below FIFO; any gain over FIFO reported below is a
genuine gain, not a search-budget fluke in either direction.

ONE FLAGGED GAP (not resolved, following this project's own domain-correctness-
flag discipline): Fontinele et al. include a thermal-loading constraint but never
state the conductor ampacity values they assigned (the base network they cite,
`pandapower.networks.case33bw()`, ships with `max_i_ka=99999` i.e. thermally
unbounded, and their own discussion (Section 6.8) attributes the entire
order-dependence mechanism to voltage coupling, never to a thermal violation).
This experiment leaves lines at that same pandapower default (thermally
unbounded) on both networks -- consistent with the network exactly as they cite
using it, and with their own results being voltage-driven -- rather than
inventing ampacity numbers they never published.

Run: uv run python experiments/59_fontinele_head_to_head.py [--seeds 5] [--save]
"""

from __future__ import annotations

import argparse
import copy
import random
import statistics
import time
from dataclasses import dataclass

import numpy as np
import pandapower as pp
import pandapower.networks as pn
from pymoo.algorithms.moo.nsga2 import NSGA2
from pymoo.core.problem import ElementwiseProblem
from pymoo.operators.crossover.pntx import TwoPointCrossover
from pymoo.operators.mutation.bitflip import BitflipMutation
from pymoo.operators.sampling.rnd import BinaryRandomSampling
from pymoo.optimize import minimize

# IEEE 69-bus (Baran & Wu 1989), transcribed from MATPOWER's case69.m
# (github.com/MATPOWER/matpower/blob/master/data/case69.m) -- see module
# docstring for the verification against Fontinele et al.'s reported totals.
CASE69_BUS = [  # (bus_num, p_kw, q_kvar)
    (1, 0.0, 0.0), (2, 0.0, 0.0), (3, 0.0, 0.0), (4, 0.0, 0.0), (5, 0.0, 0.0),
    (6, 2.6, 2.2), (7, 40.4, 30.0), (8, 75.0, 54.0), (9, 30.0, 22.0), (10, 28.0, 19.0),
    (11, 145.0, 104.0), (12, 145.0, 104.0), (13, 8.0, 5.5), (14, 8.0, 5.5), (15, 0.0, 0.0),
    (16, 45.5, 30.0), (17, 60.0, 35.0), (18, 60.0, 35.0), (19, 0.0, 0.0), (20, 1.0, 0.6),
    (21, 114.0, 81.0), (22, 5.3, 3.5), (23, 0.0, 0.0), (24, 28.0, 20.0), (25, 0.0, 0.0),
    (26, 14.0, 10.0), (27, 14.0, 10.0), (28, 26.0, 18.6), (29, 26.0, 18.6), (30, 0.0, 0.0),
    (31, 0.0, 0.0), (32, 0.0, 0.0), (33, 14.0, 10.0), (34, 19.5, 14.0), (35, 6.0, 4.0),
    (36, 26.0, 18.6), (37, 26.0, 18.6), (38, 0.0, 0.0), (39, 24.0, 17.0), (40, 24.0, 17.0),
    (41, 1.2, 1.0), (42, 0.0, 0.0), (43, 6.0, 4.3), (44, 0.0, 0.0), (45, 39.2, 26.3),
    (46, 39.2, 26.3), (47, 0.0, 0.0), (48, 79.0, 56.4), (49, 384.7, 274.5), (50, 384.7, 274.5),
    (51, 40.5, 28.3), (52, 3.6, 2.7), (53, 4.3, 3.5), (54, 26.4, 19.0), (55, 24.0, 17.2),
    (56, 0.0, 0.0), (57, 0.0, 0.0), (58, 0.0, 0.0), (59, 100.0, 72.0), (60, 0.0, 0.0),
    (61, 1244.0, 888.0), (62, 32.0, 23.0), (63, 0.0, 0.0), (64, 227.0, 162.0), (65, 59.0, 42.0),
    (66, 18.0, 13.0), (67, 18.0, 13.0), (68, 28.0, 20.0), (69, 28.0, 20.0),
]
CASE69_BRANCH = [  # (from_bus, to_bus, r_ohm, x_ohm)
    (1, 2, 0.0005, 0.0012), (2, 3, 0.0005, 0.0012), (3, 4, 0.0015, 0.0036), (4, 5, 0.0251, 0.0294),
    (5, 6, 0.366, 0.1864), (6, 7, 0.381, 0.1941), (7, 8, 0.0922, 0.047), (8, 9, 0.0493, 0.0251),
    (9, 10, 0.819, 0.2707), (10, 11, 0.1872, 0.0619), (11, 12, 0.7114, 0.2351), (12, 13, 1.03, 0.34),
    (13, 14, 1.044, 0.34), (14, 15, 1.058, 0.3496), (15, 16, 0.1966, 0.065), (16, 17, 0.3744, 0.1238),
    (17, 18, 0.0047, 0.0016), (18, 19, 0.3276, 0.1083), (19, 20, 0.2106, 0.069), (20, 21, 0.3416, 0.1129),
    (21, 22, 0.014, 0.0046), (22, 23, 0.1591, 0.0526), (23, 24, 0.3463, 0.1145), (24, 25, 0.7488, 0.2475),
    (25, 26, 0.3089, 0.1021), (26, 27, 0.1732, 0.0572), (3, 28, 0.0044, 0.0108), (28, 29, 0.064, 0.1565),
    (29, 30, 0.3978, 0.1315), (30, 31, 0.0702, 0.0232), (31, 32, 0.351, 0.116), (32, 33, 0.839, 0.2816),
    (33, 34, 1.708, 0.5646), (34, 35, 1.474, 0.4873), (3, 36, 0.0044, 0.0108), (36, 37, 0.064, 0.1565),
    (37, 38, 0.1053, 0.123), (38, 39, 0.0304, 0.0355), (39, 40, 0.0018, 0.0021), (40, 41, 0.7283, 0.8509),
    (41, 42, 0.31, 0.3623), (42, 43, 0.041, 0.0478), (43, 44, 0.0092, 0.0116), (44, 45, 0.1089, 0.1373),
    (45, 46, 0.0009, 0.0012), (4, 47, 0.0034, 0.0084), (47, 48, 0.0851, 0.2083), (48, 49, 0.2898, 0.7091),
    (49, 50, 0.0822, 0.2011), (8, 51, 0.0928, 0.0473), (51, 52, 0.3319, 0.114), (9, 53, 0.174, 0.0886),
    (53, 54, 0.203, 0.1034), (54, 55, 0.2842, 0.1447), (55, 56, 0.2813, 0.1433), (56, 57, 1.59, 0.5337),
    (57, 58, 0.7837, 0.263), (58, 59, 0.3042, 0.1006), (59, 60, 0.3861, 0.1172), (60, 61, 0.5075, 0.2585),
    (61, 62, 0.0974, 0.0496), (62, 63, 0.145, 0.0738), (63, 64, 0.7105, 0.3619), (64, 65, 1.041, 0.5302),
    (11, 66, 0.2012, 0.0611), (66, 67, 0.0047, 0.0014), (12, 68, 0.7394, 0.2444), (68, 69, 0.0047, 0.0016),
]

# Fontinele et al. Table 3: restrictive scenarios only (I1/I2 are trivial for
# every method on both networks, per their own reported finding).
SCENARIOS = {"I3": (50.0, 320.0), "I4": (100.0, 640.0)}
V_MAX, V_MIN_PROTECTED, LOADING_MAX_PCT, LOSS_FRAC_MAX = 1.05, 0.93, 100.0, 0.15


def build_case69() -> "pp.pandapowerNet":
    net = pp.create_empty_network()
    bus_idx = {}
    for bus_num, _, _ in CASE69_BUS:
        bus_idx[bus_num] = pp.create_bus(net, vn_kv=12.66, name=str(bus_num))
    pp.create_ext_grid(net, bus=bus_idx[1], vm_pu=1.0)
    for bus_num, p_kw, q_kvar in CASE69_BUS:
        if p_kw != 0 or q_kvar != 0:
            pp.create_load(net, bus=bus_idx[bus_num], p_mw=p_kw / 1000, q_mvar=q_kvar / 1000)
    for fbus, tbus, r_ohm, x_ohm in CASE69_BRANCH:
        pp.create_line_from_parameters(
            net, from_bus=bus_idx[fbus], to_bus=bus_idx[tbus], length_km=1.0,
            r_ohm_per_km=r_ohm, x_ohm_per_km=x_ohm, c_nf_per_km=0.0, max_i_ka=99999.0)
    return net


@dataclass
class Project:
    id: str
    bus: int
    p_kw: float


def build_projects(net, k: int, lo: float, hi: float, seed: int) -> list[Project]:
    rng = random.Random(seed)
    load_buses = sorted(net.load.bus.unique())
    return [Project(id=f"p{i}", bus=rng.choice(load_buses), p_kw=rng.uniform(lo, hi))
            for i in range(k)]


def _feasible(net) -> tuple[bool, str]:
    try:
        pp.runpp(net)
    except Exception:
        return False, "no_converge"
    if net.res_bus.vm_pu.max() > V_MAX:
        return False, "overvoltage"
    return True, "ok"


def fontinele_fifo(net_base, projects: list[Project]) -> set[str]:
    """Their Algorithm 1, D_max = infinity (unconstrained -- matches Table 5's
    main-results FIFO column): sequential connect, skip-and-continue on any
    violation, dynamic per-step voltage reference."""
    net = copy.deepcopy(net_base)
    pp.runpp(net)
    v_prev = net.res_bus.vm_pu.copy()
    total_load_mw = net.load.p_mw.sum()
    accepted: set[str] = set()
    for proj in projects:
        sgen_idx = pp.create_sgen(net, bus=proj.bus, p_mw=proj.p_kw / 1000, q_mvar=0.0)
        ok, _ = _feasible(net)
        if ok:
            v = net.res_bus.vm_pu
            newly_bad = ((v_prev >= V_MIN_PROTECTED) & (v < V_MIN_PROTECTED)).any()
            over_loading = net.res_line.loading_percent.max() > LOADING_MAX_PCT
            over_losses = net.res_line.pl_mw.sum() > LOSS_FRAC_MAX * total_load_mw
            ok = not (newly_bad or over_loading or over_losses)
        if ok:
            accepted.add(proj.id)
            v_prev = net.res_bus.vm_pu.copy()
        else:
            net.sgen.drop(sgen_idx, inplace=True)
    return accepted


def joint_evaluate(net_base, projects: list[Project], v_base, total_load_mw: float,
                    accepted_ids) -> tuple[bool, float, float]:
    """Simultaneous subset evaluation: add every accepted project's sgen at once,
    ONE power flow, check the same constraint set against the network's BASE
    (no-DG) voltage as the undervoltage reference (see module docstring's
    "documented adaptation"). Returns (feasible, connected_kw, violation_magnitude)
    -- the latter for a soft-constraint search gradient, matching this project's
    own `_SubsetProblem` convention (src/allocation/search.py) rather than a hard
    infeasible-is-a-wall cutoff."""
    net = copy.deepcopy(net_base)
    kw = 0.0
    for proj in projects:
        if proj.id in accepted_ids:
            pp.create_sgen(net, bus=proj.bus, p_mw=proj.p_kw / 1000, q_mvar=0.0)
            kw += proj.p_kw
    try:
        pp.runpp(net)
    except Exception:
        return False, 0.0, 10.0
    v = net.res_bus.vm_pu
    over_v = max(0.0, v.max() - V_MAX)
    newly_bad_mask = (v_base >= V_MIN_PROTECTED) & (v < V_MIN_PROTECTED)
    under_v = max(0.0, (V_MIN_PROTECTED - v[newly_bad_mask]).max()) if newly_bad_mask.any() else 0.0
    over_load = max(0.0, net.res_line.loading_percent.max() - LOADING_MAX_PCT) / 100.0
    losses_frac = net.res_line.pl_mw.sum() / total_load_mw
    over_loss = max(0.0, losses_frac - LOSS_FRAC_MAX)
    violation = over_v + under_v + over_load + over_loss
    return violation == 0.0, kw, violation


class _FontineleSubsetProblem(ElementwiseProblem):
    def __init__(self, net_base, projects, v_base, total_load_mw):
        self.net_base, self.projects, self.v_base, self.total_load_mw = net_base, projects, v_base, total_load_mw
        super().__init__(n_var=len(projects), n_obj=2, xl=0, xu=1, vtype=bool)

    def _evaluate(self, x, out, *args, **kwargs):
        accepted = {p.id for p, bit in zip(self.projects, x) if bit}
        _, kw, violation = joint_evaluate(self.net_base, self.projects, self.v_base, self.total_load_mw, accepted)
        out["F"] = [-kw, violation]


def joint_search(net_base, projects, pop, gen, seed, seed_ids: set[str] | None = None) -> tuple[set[str], float]:
    """NSGA-II subset search. If `seed_ids` is given (e.g. FIFO's own accept-set),
    generation 0 is warm-started with it instead of pure `BinaryRandomSampling`
    -- exactly the M28 lesson (`nsga2_search_dss_seeded`): on a 50-bit space, a
    blind random population has no guarantee of even matching a known-good
    baseline within a modest budget, and elitism means seeding can only help,
    never hurt. Without this, a fair "does joint search unlock more than
    reordering" comparison is confounded by whether the search merely
    rediscovered FIFO -- exactly what happened in this experiment's own smoke
    test (unseeded, tiny budget: -11% to -40% vs. FIFO, a pure search-budget
    artifact, not a finding)."""
    pp.runpp(net_base)
    v_base = net_base.res_bus.vm_pu.copy()
    total_load_mw = net_base.load.p_mw.sum()
    problem = _FontineleSubsetProblem(net_base, projects, v_base, total_load_mw)
    n_var = len(projects)
    if seed_ids is not None:
        rng = np.random.default_rng(seed)
        x0 = rng.random((pop, n_var)) < 0.5
        x0[0] = [p.id in seed_ids for p in projects]
        sampling = x0
    else:
        sampling = BinaryRandomSampling()
    algorithm = NSGA2(pop_size=pop, sampling=sampling,
                       crossover=TwoPointCrossover(), mutation=BitflipMutation(),
                       eliminate_duplicates=True)
    res = minimize(problem, algorithm, ("n_gen", gen), seed=seed, verbose=False)
    best_kw, best_ids = 0.0, set()
    for x in res.pop.get("X"):
        accepted = {p.id for p, bit in zip(projects, x) if bit}
        ok, kw, _ = joint_evaluate(net_base, projects, v_base, total_load_mw, accepted)
        if ok and kw > best_kw:
            best_kw, best_ids = kw, accepted
    return best_ids, best_kw


def run_instance(net_base, network_name, scenario, seed, pop, gen, search_seeds) -> dict:
    lo, hi = SCENARIOS[scenario]
    projects = build_projects(net_base, 50, lo, hi, seed)
    t0 = time.time()
    fifo_ids = fontinele_fifo(net_base, projects)
    fifo_kw = sum(p.p_kw for p in projects if p.id in fifo_ids)

    best_kw = 0.0
    for s in range(search_seeds):
        _, kw = joint_search(net_base, projects, pop, gen, seed=s, seed_ids=fifo_ids)
        best_kw = max(best_kw, kw)

    gain_pct = (best_kw / fifo_kw - 1) * 100 if fifo_kw > 0 else float("nan")
    return dict(network=network_name, scenario=scenario, seed=seed,
                fifo_kw=fifo_kw, fifo_n=len(fifo_ids), joint_kw=best_kw,
                gain_pct=gain_pct, dt=time.time() - t0)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seeds", type=int, default=5, help="instance draws per network x scenario")
    ap.add_argument("--pop", type=int, default=40)
    ap.add_argument("--gen", type=int, default=60)
    ap.add_argument("--search-seeds", type=int, default=3, help="NSGA-II restarts per instance; best kept")
    ap.add_argument("--save", action="store_true")
    args = ap.parse_args()

    networks = {"33-bus": pn.case33bw(), "69-bus": build_case69()}
    fontinele_gain = {  # their Table 5 gains, for side-by-side printing only
        ("33-bus", "I3"): (5.6, 31.8), ("33-bus", "I4"): (24.3, 67.6),
        ("69-bus", "I3"): (0.0, 0.3), ("69-bus", "I4"): (3.2, 17.3),
    }

    print("M29: subset+sizing search (ours) vs. Fontinele et al.'s FIFO, on THEIR own networks\n")
    rows = []
    for net_name, net_base in networks.items():
        for scenario in SCENARIOS:
            gains = []
            for seed in range(args.seeds):
                row = run_instance(net_base, net_name, scenario, seed, args.pop, args.gen, args.search_seeds)
                rows.append(row)
                gains.append(row["gain_pct"])
                print(f"  {net_name:8s} {scenario}  seed={seed}  FIFO={row['fifo_kw']:7.1f}kW"
                      f"({row['fifo_n']:2d})  joint={row['joint_kw']:7.1f}kW  "
                      f"gain={row['gain_pct']:+6.1f}%  {row['dt']:.0f}s")
            lo, hi = fontinele_gain[(net_name, scenario)]
            print(f"    -> median {statistics.median(gains):+.1f}% (range {min(gains):+.1f}% to "
                  f"{max(gains):+.1f}%) vs. Fontinele et al.'s reported MA-vs-FIFO {lo:+.1f}% to {hi:+.1f}%\n")

    if args.save and rows:
        import csv
        from pathlib import Path
        out = Path(__file__).resolve().parents[1] / "outputs"
        out.mkdir(exist_ok=True)
        path = out / "m29_fontinele_head_to_head.csv"
        fields = sorted({k for r in rows for k in r.keys()})
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader(); w.writerows(rows)
        print(f"saved {path}")


if __name__ == "__main__":
    main()
