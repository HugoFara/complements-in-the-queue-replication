"""M13 of the joint-allocation roadmap: does the search still work on a
non-radial (meshed) network?

Every result validated so far (M0-M12) used strictly radial feeders: EULV and
ENWL's own topology, and the LinDistFlow MILP (`lindistflow_search*`) is
architecturally radial-only by construction (`convex.py`'s `_extract_tree`
raises if the network isn't a single loop-free tree). But `nsga2_search` and
its variants never assumed a tree -- they call `pandapower.runpp`, a general
Newton-Raphson AC solver that meshed networks are its ordinary case, not an
edge case. That's an implicit, never-checked claim. This milestone checks it
directly, on the cheapest possible axis: still-balanced (positive-sequence),
just non-radial -- the unbalanced axis is a separate, larger milestone
(a new oracle variant reading `runpp_3ph`'s per-phase result tables), scoped
out of this one deliberately (see docs/joint-allocation-scope.md's M13).

The network: EULV's own feeder (the exact 40-request problem experiments
24/25 first validated M0/M1 on), plus one added tie line joining two leaves
165 hops apart in the radial tree -- the same kind of loop a real LV network
reconfiguration switch would create, just synthetic since EULV as vendored
has none. Everything else (buses, requests, seed, search budget) is
identical to experiment 25/33, so any behavior change is attributable to the
one thing that changed: the network is no longer a tree.

Two things are checked, not one: (1) `nsga2_search`/`nsga2_search_fair`
still return a feasible, baseline-beating result on the meshed network --
the general route surviving contact with the case it was never tested on.
(2) `lindistflow_search` on the *same* meshed network raises the expected,
documented error rather than silently returning a wrong answer -- the radial-
only guard actually firing, not just present in the source.

Run:  uv run python experiments/37_nonradial_validation.py
"""

from __future__ import annotations

import random
import time
from pathlib import Path

import networkx as nx
import pandapower as pp
import pandapower.topology as top

from gridsynth import import_opendss

from allocation import (
    AllocationProblem,
    Request,
    compare_to_baseline,
    greedy_baseline,
    lindistflow_search,
    nsga2_search,
    nsga2_search_fair,
    queue_fairness_violations,
)

REPO = Path(__file__).resolve().parents[1]
MASTER = REPO / "data" / "opendss" / "eulv" / "Master_snapshot.dss"

PV_KW_LO, PV_KW_HI = 3.0, 10.0  # same band as experiments 24/25/33
PV_KW_HI_STRESSED = 14.0  # see main(): the tie line relieves enough headroom that
# the original band no longer binds at all -- this restores a comparable
# binding regime (35/40 baseline acceptance, matching M1's own 35/40 almost
# exactly) so the search-vs-baseline comparison is actually exercised.
K, POP_SIZE, N_GEN, SEED = 40, 40, 40, 0  # same budget as experiment 25/33's eulv run

# The backbone conductor already present in EULV (lowest r/km of the ten
# std types in this feeder, i.e. the largest/cheapest-to-lose-capacity-on
# conductor) -- a plausible choice for a loop-reinforcement tie cable.
TIE_R_OHM_PER_KM = 0.089
TIE_X_OHM_PER_KM = 0.0675
TIE_MAX_I_KA = 0.4
TIE_LENGTH_KM = 0.05  # 50 m, a short LV interconnect


def build_requests(load_buses: list[int], k: int, seed: int, kw_hi: float = PV_KW_HI) -> list[Request]:
    """Identical construction to experiments 24/25/33 at kw_hi=PV_KW_HI, so
    that comparison is apples-to-apples with M1's own already-published
    radial numbers. kw_hi is overridable (see PV_KW_HI_STRESSED)."""
    rng = random.Random(seed)
    buses = rng.sample(load_buses, k)
    return [Request(id=f"pv{i}", bus=bus, p_mw=-rng.uniform(PV_KW_LO, kw_hi) / 1000.0, queue_order=i)
            for i, bus in enumerate(buses)]


def add_loop_closing_tie(net: pp.pandapowerNet) -> tuple[int, int]:
    """Add one tie line between the two leaves furthest apart (by hop count)
    in EULV's radial tree, turning it into a single-loop meshed network.
    Returns the (from_bus, to_bus) pair joined."""
    graph = top.create_nxgraph(net)
    root = int(net.ext_grid.bus.iloc[0])
    assert nx.is_tree(graph), "EULV is expected radial before the tie is added"

    leaves = [n for n in graph.nodes if graph.degree(n) == 1 and n != root]
    best = max(
        ((nx.shortest_path_length(graph, a, b), a, b) for i, a in enumerate(leaves) for b in leaves[i + 1:]),
        key=lambda t: t[0],
    )
    _, bus_a, bus_b = best
    pp.create_line_from_parameters(
        net, from_bus=int(bus_a), to_bus=int(bus_b), length_km=TIE_LENGTH_KM,
        r_ohm_per_km=TIE_R_OHM_PER_KM, x_ohm_per_km=TIE_X_OHM_PER_KM, c_nf_per_km=0.0,
        max_i_ka=TIE_MAX_I_KA, name="tie_M13_loop")
    return int(bus_a), int(bus_b)


def main() -> None:
    net = import_opendss(str(MASTER))
    load_buses = net.load.bus.tolist()

    bus_a, bus_b = add_loop_closing_tie(net)
    graph = top.create_nxgraph(net)
    print(f"M13: non-radial validation -- EULV + one tie line ({bus_a} <-> {bus_b})\n")
    print(f"  buses={len(net.bus)}  lines={len(net.line)}  is_tree={nx.is_tree(graph)}  "
          f"connected={nx.is_connected(graph)}  cycles={len(nx.cycle_basis(nx.Graph(graph)))}")

    pp.runpp(net)
    print(f"  base-case (no requests) runpp converged={net['converged']}, "
          f"vmin={net.res_bus.vm_pu.min():.4f} vmax={net.res_bus.vm_pu.max():.4f} pu\n")

    original_band_requests = build_requests(load_buses, K, SEED, kw_hi=PV_KW_HI)
    original_band_problem = AllocationProblem(net=net, requests=original_band_requests)
    original_baseline = greedy_baseline(original_band_problem)
    print(f"== M1's own request band (3-{PV_KW_HI:.0f} kWp) on the meshed network ==")
    print(f"  greedy baseline: {len(original_baseline.accepted_ids)}/{K} accepted, "
          f"{original_baseline.accommodated_mw*1000:.1f} kW, vmax={original_baseline.vmax_pu:.4f} pu "
          f"(M1's radial baseline was 35/40, 236.8 kW, vmax=1.0998 -- the tie line's redundant path\n"
          f"  lowers the effective impedance to the stressed leaf enough that this band no longer\n"
          f"  binds at all; restressing with a wider band below to get a real comparison)\n")

    requests = build_requests(load_buses, K, SEED, kw_hi=PV_KW_HI_STRESSED)
    problem = AllocationProblem(net=net, requests=requests)

    print(f"== (1) nsga2_search / nsga2_search_fair on the meshed network, restressed band (3-{PV_KW_HI_STRESSED:.0f} kWp) ==")
    baseline = greedy_baseline(problem)
    print(f"  greedy baseline: {len(baseline.accepted_ids)}/{K} accepted, "
          f"{baseline.accommodated_mw*1000:.1f} kW, vmax={baseline.vmax_pu:.4f} pu, "
          f"cost={baseline.reinforcement_cost_proxy:.3f}")

    t0 = time.time()
    results = nsga2_search(problem, pop_size=POP_SIZE, n_gen=N_GEN, seed=SEED)
    dt = time.time() - t0
    feasible = [r for r in results if r.feasible]
    comparison = compare_to_baseline(results, baseline)
    best = comparison.best_feasible_search
    print(f"  nsga2_search ({dt:.0f}s): {len(feasible)}/{len(results)} feasible individuals")
    if best is not None:
        print(f"  best feasible: {len(best.accepted_ids)}/{K} accepted, "
              f"{best.accommodated_mw*1000:.1f} kW, vmax={best.vmax_pu:.4f} pu -- "
              f"{'beats' if comparison.dominates else 'does NOT beat'} the baseline "
              f"({baseline.accommodated_mw*1000:.1f} kW)")
    else:
        print("  no feasible individual found")

    t0 = time.time()
    fair_results = nsga2_search_fair(problem, pop_size=POP_SIZE, n_gen=N_GEN, seed=SEED)
    dt = time.time() - t0
    fair_feasible = [r for r in fair_results if r.feasible]
    print(f"\n  nsga2_search_fair ({dt:.0f}s): {len(fair_feasible)}/{len(fair_results)} feasible individuals")
    if fair_feasible:
        best_fair = max(fair_feasible, key=lambda r: r.accommodated_mw)
        v = queue_fairness_violations(problem.requests, best_fair.accepted_ids)
        print(f"  best feasible: {best_fair.accommodated_mw*1000:.1f} kW, violations={v} -- "
              f"the fairness objective also functions unmodified on a meshed network")

    print("\n== (2) lindistflow_search on the same meshed network ==")
    try:
        lindistflow_search(problem)
        print("  UNEXPECTED: the MILP solved a non-radial network without complaint")
    except ValueError as e:
        print(f"  raised ValueError as expected, radial-only guard fired correctly:\n    {e}")


if __name__ == "__main__":
    main()
