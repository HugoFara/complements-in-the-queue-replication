"""M15 of the joint-allocation roadmap: does the engine work on genuine
North-American single-phase-lateral topology?

M14 closed the achievable part of the unbalanced axis (unbalanced *loading*
on a symmetric 3-phase topology) but found true North-American topology
(single-phase laterals, split-phase secondaries -- IEEE13/123, SMART-DS)
blocked on pandapower issue #873: `runpp_3ph` is a symmetrical-component
solver that cannot correctly represent single-phase-connected elements at
all, no workaround. Fixing that inside pandapower would mean rewriting its
3ph solver architecture from scratch -- essentially duplicating what
OpenDSS's own engine already does. Instead, `AllocationProblemDSS`
(src/allocation/dss_problem.py) drives OpenDSS directly via
`opendssdirect.NewContext()` -- an isolated engine instance per evaluation,
the analogue of `copy.deepcopy(net)` -- sidestepping pandapower's solver
entirely rather than fixing it.

Two things are checked, not one: (1) IEEE13 and IEEE123 -- the literature-
standard single-phase-lateral test feeders, one of which (123) pandapower's
own balanced approximation couldn't even converge on (Section 4.13) --
solve correctly and fast via this route. (2) `nsga2_search_dss`/
`greedy_baseline_dss` replicate M1's search-beats-baseline bar on real
single-phase-lateral topology, the actual target this whole non-radial/
unbalanced thread has been building toward since M13.

A genuine data-quality finding surfaced and fixed along the way, not
smoothed over: IEEE13's vendored master file declares no line ampacities
(OpenDSS defaults every conductor to a uniform 400 A), so thermal loading
isn't a meaningful constraint on this network as published -- this
validation is scoped to the voltage axis instead, the same choice M0 made
for EULV. A second, more serious bug was caught and fixed before it ever
produced a number: comparing raw transformer current against
`NormalAmps` is wrong whenever the two windings sit at different voltage
levels (same power, different current) -- it first reported IEEE13's own
substation transformer as 2263% "overloaded" when its real loading is
~79%. Fixed by switching to apparent-power-vs-rated-kVA for transformers
(current-vs-ampacity stays correct for lines, both ends being the same
voltage).

Run:  uv run python experiments/39_opendss_direct_validation.py --network ieee13
      uv run python experiments/39_opendss_direct_validation.py --network ieee123
"""

from __future__ import annotations

import argparse
import random
import time
from pathlib import Path

import opendssdirect as dss_base

from allocation import (
    AllocationProblemDSS,
    DSSRequest,
    compare_to_baseline,
    greedy_baseline_dss,
    nsga2_search_dss,
)

REPO = Path(__file__).resolve().parents[1]

NETWORKS = {
    "ieee13": dict(master=REPO / "data" / "opendss" / "ieee13" / "IEEE13Nodeckt.dss",
                   exclude_buses={"sourcebus"}, k=38, kw_lo=50.0, kw_hi=150.0,
                   pop_size=40, n_gen=40),
    "ieee123": dict(master=REPO / "data" / "opendss" / "ieee123" / "IEEE123Master.dss",
                     exclude_buses={"sourcebus", "150"}, k=60, kw_lo=10.0, kw_hi=150.0,
                     pop_size=40, n_gen=40),
}
SEED = 0


def candidate_attach_points(master_path: Path, exclude_buses: set[str]) -> list[tuple[str, int]]:
    """Every (bus, phase) pair actually present on the solved circuit --
    OpenDSS's own `Bus.Nodes()` naturally handles a network where different
    buses have different phase counts, unlike a fixed 3-column table."""
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


def build_requests(points: list[tuple[str, int]], k: int, kw_lo: float, kw_hi: float,
                    seed: int) -> list[DSSRequest]:
    rng = random.Random(seed)
    sample = rng.sample(points, k)
    return [DSSRequest(id=f"ev{i}", bus=bus, phase=phase, p_kw=rng.uniform(kw_lo, kw_hi), queue_order=i)
            for i, (bus, phase) in enumerate(sample)]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--network", choices=NETWORKS, default="ieee13")
    ap.add_argument("--seed", type=int, default=SEED)
    args = ap.parse_args()
    cfg = NETWORKS[args.network]
    master = cfg["master"]

    print(f"M15: OpenDSS-direct validation -- {args.network}\n")

    base_problem = AllocationProblemDSS(master_path=str(master), requests=[], max_loading_percent=1e6)
    base = base_problem.evaluate([])
    print(f"base case (no candidate requests): converged={base.converged}  feasible={base.feasible}  "
          f"vmin={base.vmin_pu:.4f}  vmax={base.vmax_pu:.4f}  pu")
    print("  (thermal loading excluded -- see this file's own docstring: neither vendored master\n"
          "   declares line ampacities, an uncalibrated uniform default, not a real constraint)\n")

    points = candidate_attach_points(master, cfg["exclude_buses"])
    k = min(cfg["k"], len(points))
    requests = build_requests(points, k, cfg["kw_lo"], cfg["kw_hi"], args.seed)
    problem = AllocationProblemDSS(master_path=str(master), requests=requests, max_loading_percent=1e6)

    print(f"== nsga2_search_dss vs. greedy_baseline_dss ({k} single-phase-tap EV/demand requests) ==")
    baseline = greedy_baseline_dss(problem)
    print(f"  greedy baseline: {len(baseline.accepted_ids)}/{k} accepted, "
          f"{baseline.accommodated_mw*1000:.1f} kW, vmin={baseline.vmin_pu:.4f} pu")

    t0 = time.time()
    results = nsga2_search_dss(problem, pop_size=cfg["pop_size"], n_gen=cfg["n_gen"], seed=args.seed)
    dt = time.time() - t0
    feasible = [r for r in results if r.feasible]
    comparison = compare_to_baseline(results, baseline)
    best = comparison.best_feasible_search
    print(f"  nsga2_search_dss ({dt:.0f}s): {len(feasible)}/{len(results)} feasible individuals")
    if best is not None:
        gain_pct = (best.accommodated_mw / baseline.accommodated_mw - 1) * 100 if baseline.accommodated_mw else float("nan")
        print(f"  best feasible: {len(best.accepted_ids)}/{k} accepted, "
              f"{best.accommodated_mw*1000:.1f} kW, vmin={best.vmin_pu:.4f} pu -- "
              f"{'beats' if comparison.dominates else 'does NOT beat'} the baseline "
              f"({gain_pct:+.1f}%)")
    else:
        print("  no feasible individual found")


if __name__ == "__main__":
    main()
