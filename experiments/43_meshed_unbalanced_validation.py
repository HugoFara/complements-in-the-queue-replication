"""M19 of the joint-allocation roadmap: a network that is genuinely
*both* non-radial (meshed) and unbalanced (single-phase-lateral topology)
-- the one cell `docs/paper.md` Section 7 named as "untested on any route"
after M18. Section 4.13 checked non-radial alone (a synthetic loop added to
EULV, itself balanced); Sections 4.14/4.15 checked unbalanced alone
(IEEE13/123, both radial as vendored). Neither combined the two axes.

IEEE123 turns out to be the natural candidate for both at once: it is
already genuinely unbalanced (single-phase laterals, split-phase secondaries,
the same North-American topology Section 4.15 built `AllocationProblemDSS`
for), and the *published* test feeder itself specifies two normally-open
tie switches (Sw7, Sw8) for exactly this kind of reconfiguration study --
closing one turns the same feeder non-radial without changing anything else
about it. `data/opendss/ieee123/IEEE123Master_looped.dss` (first vendored
in M13, when IEEE123 was tried and abandoned for the *pandapower*-import
axis before this file was ever actually solved by anything) already closes
Sw7 -- but it had a real, previously-uncaught bug: the published master
defines `Sw7` as `Bus1=151 Bus2=300_OPEN`, a *dummy* bus that exists only so
a radial (all-ties-open) study can still report what voltage the open tie
point would see, without actually being connected to the rest of the
circuit. M13's version only closed the switch *contact*, which just
energizes that dummy bus as a dead-end stub off 151 -- checked directly
this milestone, not assumed correct from "it solves without error": bus
300 (fed from the opposite direction) and bus 300_OPEN landed on visibly
different voltages, and a graph check found the "closed" network still had
exactly buses-1 edges, i.e. still a tree. Fixed by re-pointing Sw7's own
Bus2 at the real bus 300 before closing it -- now a genuine single loop
(checked: 131 buses, 131 edges, one cycle spanning 27 buses; bus 300 and
bus 151 land on matching voltages).

A second, previously-unexercised gap was caught the same way: `allocation.
dss_convex`'s per-phase BFS never actually checked for a cycle -- it would
silently pick an arbitrary spanning tree of a meshed network rather than
raise, unlike `convex.py`'s own `_extract_tree`. Fixed there directly (see
`dss_convex._bfs_phase_tree`'s docstring) before this script was written,
not discovered by this script failing silently.

This script checks, in order: (1) the corrected looped network actually
solves and is baseline-feasible via `AllocationProblemDSS`; (2) `build_
lindistflow_model_dss` now cleanly refuses it (a `ValueError`, not a wrong
answer) -- the MILP route stays radial-only by construction, same
precedent Section 4.13 set for the pandapower route; (3) `nsga2_search_dss`
still beats `greedy_baseline_dss` on this genuinely unbalanced *and* meshed
network, at the same request band M15's own (radial) IEEE123 check used, so
the two margins are directly comparable.

Run:  uv run python experiments/43_meshed_unbalanced_validation.py
"""

from __future__ import annotations

import random
import time
from pathlib import Path

import networkx as nx
import opendssdirect as dss_base

from allocation import AllocationProblemDSS, DSSRequest, compare_to_baseline, greedy_baseline_dss, nsga2_search_dss
from allocation.dss_convex import build_lindistflow_model_dss

REPO = Path(__file__).resolve().parents[1]
MASTER = REPO / "data" / "opendss" / "ieee123" / "IEEE123Master_looped.dss"
EXCLUDE_BUSES = {"sourcebus", "150"}  # same exclusions as M15's own IEEE123 run
K, KW_LO, KW_HI = 60, 10.0, 150.0
POP_SIZE, N_GEN = 40, 40
SEED = 0


def candidate_attach_points(master_path: Path, exclude_buses: set[str]) -> list[tuple[str, int]]:
    ctx = dss_base.NewContext()
    ctx.Command("Clear")
    ctx.Command(f'Redirect "{master_path}"')
    ctx.Solution.Solve()
    points = []
    for name in ctx.Circuit.AllBusNames():
        if name in exclude_buses:
            continue
        ctx.Circuit.SetActiveBus(name)
        for node in ctx.Bus.Nodes():
            points.append((name, node))
    return points


def build_requests(points: list[tuple[str, int]], k: int, seed: int) -> list[DSSRequest]:
    rng = random.Random(seed)
    sample = rng.sample(points, k)
    return [DSSRequest(id=f"ev{i}", bus=bus, phase=phase, p_kw=rng.uniform(KW_LO, KW_HI), queue_order=i)
            for i, (bus, phase) in enumerate(sample)]


def check_genuinely_meshed(master_path: Path) -> None:
    """Independent of anything `allocation/` does -- a plain graph check
    that closing Sw7 actually created a cycle, not just a longer tree."""
    ctx = dss_base.NewContext()
    ctx.Command("Clear")
    ctx.Command(f'Redirect "{master_path}"')
    ctx.Solution.Solve()
    g = nx.Graph()
    i = ctx.Lines.First()
    while i:
        ctx.Circuit.SetActiveElement("Line." + ctx.Lines.Name())
        if not ctx.CktElement.IsOpen(1, 0):
            b1, b2 = ctx.Lines.Bus1().split(".")[0], ctx.Lines.Bus2().split(".")[0]
            g.add_edge(b1, b2)
        i = ctx.Lines.Next()
    i = ctx.Transformers.First()
    while i:
        ctx.Circuit.SetActiveElement("Transformer." + ctx.Transformers.Name())
        buses = [b.split(".")[0] for b in ctx.CktElement.BusNames()]
        g.add_edge(buses[0], buses[1])
        i = ctx.Transformers.Next()
    cycles = nx.cycle_basis(g)
    print(f"== 0. graph check (independent of allocation/) ==")
    print(f"  {g.number_of_nodes()} buses, {g.number_of_edges()} edges, "
          f"connected={nx.is_connected(g)}, cycles={len(cycles)}")
    if len(cycles) != 1:
        raise SystemExit(f"expected exactly 1 cycle from closing one tie switch, found {len(cycles)}")
    print(f"  cycle ({len(cycles[0])} buses): {cycles[0]}\n")


def main() -> None:
    print("M19: a network that is genuinely both unbalanced and meshed -- "
          "IEEE123 with tie switch Sw7 closed\n")

    check_genuinely_meshed(MASTER)

    # 1. baseline feasibility ---------------------------------------------
    base_problem = AllocationProblemDSS(master_path=str(MASTER), requests=[], max_loading_percent=1e6)
    base = base_problem.evaluate([])
    print("== 1. baseline (zero candidate requests, via AllocationProblemDSS) ==")
    print(f"  converged={base.converged}  feasible={base.feasible}  "
          f"vmin={base.vmin_pu:.4f}  vmax={base.vmax_pu:.4f} pu\n")
    if not base.feasible:
        raise SystemExit("baseline infeasible -- can't run a meaningful search-beats-baseline check")

    # 2. the MILP route must cleanly refuse this network -------------------
    print("== 2. convex/MILP route (radial-only by construction) ==")
    try:
        build_lindistflow_model_dss(base_problem, root_bus="150")
        print("  UNEXPECTED: build_lindistflow_model_dss did not raise on a meshed network\n")
    except ValueError as e:
        print(f"  build_lindistflow_model_dss correctly refused: {e}\n")

    # 3. search-beats-baseline on the exact-AC, non-radial-safe route -----
    points = candidate_attach_points(MASTER, EXCLUDE_BUSES)
    k = min(K, len(points))
    requests = build_requests(points, k, SEED)
    problem = AllocationProblemDSS(master_path=str(MASTER), requests=requests, max_loading_percent=1e6)

    print(f"== 3. nsga2_search_dss vs. greedy_baseline_dss ({k} single-phase-tap requests, "
          f"same band as M15's radial IEEE123 run) ==")
    baseline = greedy_baseline_dss(problem)
    print(f"  greedy baseline: {len(baseline.accepted_ids)}/{k} accepted, "
          f"{baseline.accommodated_mw*1000:.1f} kW, vmin={baseline.vmin_pu:.4f} pu")

    t0 = time.time()
    results = nsga2_search_dss(problem, pop_size=POP_SIZE, n_gen=N_GEN, seed=SEED)
    dt = time.time() - t0
    comparison = compare_to_baseline(results, baseline)
    best = comparison.best_feasible_search
    if best is not None:
        gain_pct = (best.accommodated_mw / baseline.accommodated_mw - 1) * 100 if baseline.accommodated_mw else float("nan")
        print(f"  nsga2_search_dss ({dt:.0f}s): {len(best.accepted_ids)}/{k} accepted, "
              f"{best.accommodated_mw*1000:.1f} kW, vmin={best.vmin_pu:.4f} pu -- "
              f"{'beats' if comparison.dominates else 'does NOT beat'} the baseline ({gain_pct:+.1f}%)")
        print(f"\n  for comparison: M15's *radial* IEEE123 run (same k=60, kw_lo/hi, pop/gen, seed) "
              f"found +33.3% (44/60 -> 53/60 accepted); the margin here is the meshed-vs-radial "
              f"delta on the same feeder, same request band -- see docs/joint-allocation-scope.md.")
    else:
        print("  no feasible individual found")


if __name__ == "__main__":
    main()
