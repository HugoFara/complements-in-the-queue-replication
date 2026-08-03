"""M1 of the joint-allocation roadmap: does a search beat the serial baseline?

ROADMAP.md's M1 is done when an NSGA-II search over accept/reject subsets
returns a Pareto front that dominates a greedy/queue-order baseline on total
accommodated MW at equal or lower reinforcement cost, on the M0 network, seed-
reproducible. `allocation.greedy_baseline` and `allocation.nsga2_search`
(src/allocation/baseline.py, search.py) are those two pieces; this experiment
runs both on the exact same network + request pool experiment 24 used to prove
the M0 oracle binds (40 synthetic rooftop-PV export requests on the EULV
feeder, seed 0), so the comparison is apples-to-apples.

Why a search can beat FCFS here: the serial process (accept in queue order
until the first violation) commits to *whichever* prefix the arrival order
happens to produce, even if a later, smaller request would have fit in the
headroom a stalled larger one is blocking. NSGA-II searches the combination
directly, so it can find a differently-shaped accepted set -- not just a
longer prefix -- that packs more total MW into the same feasible envelope.

Run:  uv run python experiments/25_joint_allocation_search.py
"""

from __future__ import annotations

import argparse
import random
import time
from pathlib import Path

from gridsynth import import_opendss

from allocation import AllocationProblem, Request, compare_to_baseline, greedy_baseline, nsga2_search

REPO = Path(__file__).resolve().parents[1]
MASTER = REPO / "data" / "opendss" / "eulv" / "Master_snapshot.dss"

PV_KW_LO, PV_KW_HI = 3.0, 10.0  # same band as experiment 24


def build_requests(load_buses: list[int], k: int, seed: int) -> list[Request]:
    """Identical construction to experiment 24, so the two experiments compare
    the same problem instance."""
    rng = random.Random(seed)
    buses = rng.sample(load_buses, k)
    requests = []
    for i, bus in enumerate(buses):
        kw = rng.uniform(PV_KW_LO, PV_KW_HI)
        requests.append(Request(id=f"pv{i}", bus=bus, p_mw=-kw / 1000.0, queue_order=i))
    return requests


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--k", type=int, default=40)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--pop-size", type=int, default=40)
    ap.add_argument("--n-gen", type=int, default=40)
    args = ap.parse_args()

    net = import_opendss(str(MASTER))
    load_buses = net.load.bus.tolist()
    requests = build_requests(load_buses, args.k, args.seed)
    problem = AllocationProblem(net=net, requests=requests)

    print(f"Joint allocation M1: greedy baseline vs. NSGA-II on the same {args.k}-request "
          f"EULV problem experiment 24 used (seed={args.seed})\n")

    baseline = greedy_baseline(problem)
    print(f"greedy baseline (queue order, stop at first violation): "
          f"{len(baseline.accepted_ids)}/{args.k} accepted, "
          f"{baseline.accommodated_mw*1000:.1f} kW accommodated, "
          f"vmax={baseline.vmax_pu:.4f} pu, cost={baseline.reinforcement_cost_proxy:.3f}")

    t0 = time.time()
    results = nsga2_search(problem, pop_size=args.pop_size, n_gen=args.n_gen, seed=args.seed)
    dt = time.time() - t0
    print(f"\nNSGA-II: pop_size={args.pop_size}, n_gen={args.n_gen} "
          f"({args.pop_size * args.n_gen} solves, {dt:.0f}s) -> {len(results)} distinct final individuals")

    feasible = sorted((r for r in results if r.feasible), key=lambda r: -r.accommodated_mw)
    print(f"  {len(feasible)} fully feasible (cost == 0) among them")
    print(f"\n  {'accepted':>8s} {'accommodated (kW)':>18s} {'vmax (pu)':>10s} {'cost':>8s}")
    for r in feasible[:8]:
        print(f"  {len(r.accepted_ids):>8d} {r.accommodated_mw*1000:>18.1f} "
              f"{r.vmax_pu:>10.4f} {r.reinforcement_cost_proxy:>8.3f}")

    comparison = compare_to_baseline(results, baseline)
    best = comparison.best_feasible_search
    print(f"\nbest feasible search result: {len(best.accepted_ids)}/{args.k} accepted, "
          f"{best.accommodated_mw*1000:.1f} kW accommodated, cost={best.reinforcement_cost_proxy:.3f}")
    print(f"baseline:                    {len(baseline.accepted_ids)}/{args.k} accepted, "
          f"{baseline.accommodated_mw*1000:.1f} kW accommodated, cost={baseline.reinforcement_cost_proxy:.3f}")
    print(f"\nM1 'done when' bar (>= accommodated MW at <= cost, seed-reproducible): "
          f"{'MET' if comparison.dominates else 'NOT MET'}")
    if comparison.dominates and best.accommodated_mw > baseline.accommodated_mw:
        gain_kw = (best.accommodated_mw - baseline.accommodated_mw) * 1000
        print(f"search accommodates {gain_kw:.1f} kW more than the serial baseline, "
              "at equal (zero) reinforcement cost -- by accepting a different-shaped subset, "
              "not just a longer arrival-order prefix.")


if __name__ == "__main__":
    main()
