"""M20 of the joint-allocation roadmap: validate the exact-AC route on a real,
published meshed+unbalanced network, not a synthetic one.

M19 closed the unbalanced-and-meshed cell with a single normally-open tie
switch closed on IEEE123 -- a real feeder, but a loop *we* added, and its own
"Honest scope limits" flagged that as "one loop, one feeder," not a network
that ships meshed by design. The IEEE 342-Node Low Voltage Networked Test
System is that network: IEEE's own description calls it "the first
distribution test feeder developed by the IEEE that contains unbalanced
networked components," built specifically to be heavily meshed (network
protectors, a 120/208V grid network, multiple 277/480V spot networks) --
390 buses, 71 independent cycles from 71 transformers feeding multiple
points into a shared secondary grid, not one tie. `data/opendss/
lvtestcase_na/Master.dss` is the OpenDSS-native port (`LVTestCaseNorthAmerican`
in `dss-extensions/electricdss-tst`, confirmed by OpenDSS's own author, Roger
Dugan, as the canonical port); see PROVENANCE.md in that directory for what
was and wasn't vendored. It solves standalone with no changes needed --
unlike M19's IEEE123 loop, there was no latent topology bug to fix here.

This script checks, in order: (1) the network solves and is baseline-feasible
via `AllocationProblemDSS`; (2) `build_lindistflow_model_dss` refuses it (a
`ValueError`, not a wrong answer) -- checked directly, not assumed, because
it turns out to refuse for a genuinely different reason than M19's IEEE123
loop did. Every one of this network's 70 step-down transformers is
delta-connected on its primary side (`conns=[d,w]` or `[d,d]`), which
`dss_convex`'s per-phase decoupled tree cannot cross at all (a pre-existing,
documented limitation -- see that module's docstring). That means the
*empty-request* baseline model "builds" without error: the per-phase BFS
from `root_bus` never leaves the small cluster of primary buses reachable
without crossing a transformer, and never even reaches the genuinely meshed
secondary network to find a cycle. The refusal only actually fires once real
candidate requests on the secondary side are checked for reachability against
that (mostly-unreachable) tree -- so this script exercises that check with
real requests, not the zero-request baseline M19's own check used, precisely
because that gap would otherwise go uncaught here; (3) `nsga2_search_dss`
beats `greedy_baseline_dss`.

**The request kW band is screened for this network, not reused from IEEE123
(M15/M19's 10-150 kW).** This network mixes two secondary voltage classes --
residential 120/208V spot loads (median 15.6-36.1 kW, existing-load max
68-144 kW) and commercial 277/480V spot loads (median 304-565 kW, max up to
653 kW) -- so a single kW band sized for IEEE123's one 4.16kV feeder is not
meaningful here. 92% of load buses (576/624) are the smaller residential
class, so a band screened against *that* class dominates which points end up
feasible. Screened directly: at IEEE123's own 10-150 kW band, `greedy_
baseline_dss` accepts only 2/60 requests regardless of band width (78.2 kW
at 10-150, 24.6 kW at 5-40) -- the thermal headroom on this densely-loaded
(42.2 MW already committed) secondary grid is far tighter than IEEE123's,
not a bug. At 1-5 kW (chosen here), greedy accepts 37/60 -- a real,
non-degenerate accept/reject split close in spirit to M15's own 44/60,
leaving NSGA-II real room to improve on.

Run:  uv run python experiments/45_ieee342_validation.py
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
MASTER = REPO / "data" / "opendss" / "lvtestcase_na" / "Master.dss"
EXCLUDE_BUSES = {"p1"}  # the source bus (230kV Vsource)
K, KW_LO, KW_HI = 60, 1.0, 5.0  # screened for this network -- see module docstring
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
    that this network ships meshed by design, not just solvable."""
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
    print("== 0. graph check (independent of allocation/) ==")
    print(f"  {g.number_of_nodes()} buses, {g.number_of_edges()} edges, "
          f"connected={nx.is_connected(g)}, cycles={len(cycles)}")
    if len(cycles) < 2:
        raise SystemExit(f"expected this network to ship meshed by design (many cycles), found {len(cycles)}")
    print(f"  meshed by design (not one added tie) -- {len(cycles)} independent cycles\n")


def main() -> None:
    print("M20: the exact-AC route on a real, published meshed+unbalanced network -- "
          "IEEE 342-Node LV Networked Test System\n")

    check_genuinely_meshed(MASTER)

    # 1. baseline feasibility ---------------------------------------------
    base_problem = AllocationProblemDSS(master_path=str(MASTER), requests=[], max_loading_percent=1e6)
    base = base_problem.evaluate([])
    print("== 1. baseline (zero candidate requests, via AllocationProblemDSS) ==")
    print(f"  converged={base.converged}  feasible={base.feasible}  "
          f"vmin={base.vmin_pu:.4f}  vmax={base.vmax_pu:.4f} pu\n")
    if not base.feasible:
        raise SystemExit("baseline infeasible -- can't run a meaningful search-beats-baseline check")

    # 2. the MILP route must refuse this network ---------------------------
    # (against the *real* candidate requests, not the empty baseline -- see
    # module docstring: the empty-request model "succeeds" here for the
    # wrong reason, since delta-connected transformers make almost the whole
    # network unreachable from root_bus before a request ever gets checked)
    points = candidate_attach_points(MASTER, EXCLUDE_BUSES)
    k = min(K, len(points))
    requests = build_requests(points, k, SEED)
    problem = AllocationProblemDSS(master_path=str(MASTER), requests=requests, max_loading_percent=1e6)

    print("== 2. convex/MILP route (radial-only by construction) ==")
    try:
        build_lindistflow_model_dss(problem, root_bus="p1")
        print("  UNEXPECTED: build_lindistflow_model_dss did not raise on a meshed network\n")
    except ValueError as e:
        print(f"  build_lindistflow_model_dss correctly refused: {e}\n")

    # 3. search-beats-baseline on the exact-AC, non-radial-safe route -----
    print(f"== 3. nsga2_search_dss vs. greedy_baseline_dss ({k} single-phase-tap requests, "
          f"{KW_LO:.0f}-{KW_HI:.0f} kW band screened for this network) ==")
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
        print(f"\n  for comparison (different network, different kW band -- not a like-for-like "
              f"margin, only the same qualitative search-beats-baseline check): M15's radial "
              f"IEEE123 run found +33.3% (44/60 -> 53/60 accepted, 10-150 kW); M19's meshed "
              f"IEEE123 run (one added tie) found +120.6% (same band) -- see "
              f"docs/joint-allocation-scope.md.")
    else:
        print("  no feasible individual found")


if __name__ == "__main__":
    main()
