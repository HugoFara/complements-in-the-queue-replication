"""M9 of the joint-allocation roadmap: queue-order fairness as a genuine
Pareto objective.

ROADMAP.md's one-line target has always described the goal as a Pareto front
over {accommodated MW, upgrade cost, queue-order fairness} -- but no prior
milestone actually implemented the third term. `greedy_baseline` (M1) is
fairness-perfect by construction (it stops dead at the first violation, so
its accept-set is always a prefix of the queue), but the "smarter" baselines
built since (M6's `reinforced_greedy_baseline`, M7's
`continuous_greedy_baseline`) both *skip* a request that doesn't fit and
keep walking -- which can accept a later, smaller request over an earlier,
still-waiting one. That trade-off was never measured before this milestone.

`allocation.queue_fairness_violations(requests, accepted_ids)` counts queue-
order inversions (pairs where an earlier-queued request was rejected while a
later one was accepted) -- the "how many times did we jump the queue" cost of
joint optimization over the status-quo serial process. `nsga2_search_fair`
adds it as a genuine third NSGA-II objective, alongside -accommodated_mw and
reinforcement_cost_proxy, on top of the same binary accept/reject decision
`nsga2_search` (M1) already searches.

This experiment demonstrates the trade-off is real (not just theoretically
possible) on both existing validation networks: does *some* capacity get
left on the table by the free (M1) search once fairness is
also optimized for, or does the search find "free" fairness that costs
nothing? Both are reported, whichever is found.

Run:  uv run python experiments/33_fairness_pareto.py --network eulv
      uv run python experiments/33_fairness_pareto.py --network enwl --skip-nsga2-plain
"""

from __future__ import annotations

import argparse
import random
import time
from pathlib import Path

from gridsynth import import_opendss

from allocation import (
    AllocationProblem,
    Request,
    continuous_greedy_baseline,
    greedy_baseline,
    nsga2_search,
    nsga2_search_fair,
    queue_fairness_violations,
)
from allocation.baseline import reinforced_greedy_baseline

REPO = Path(__file__).resolve().parents[1]
CACHE = REPO / "cache" / "enwl_full"

PV_KW_LO, PV_KW_HI = 3.0, 10.0  # same band as experiments 24/25/26/28-31

NETWORKS = {
    "eulv": dict(master=REPO / "data" / "opendss" / "eulv" / "Master_snapshot.dss",
                 k=40, pop_size=40, n_gen=40),
    "enwl": dict(master=CACHE / "network_15" / "Feeder_4" / "Master_snapshot.dss",
                 k=100, pop_size=30, n_gen=30),
}


def build_requests(load_buses: list[int], k: int, seed: int) -> list[Request]:
    rng = random.Random(seed)
    buses = rng.sample(load_buses, k)
    return [Request(id=f"pv{i}", bus=bus, p_mw=-rng.uniform(PV_KW_LO, PV_KW_HI) / 1000.0, queue_order=i)
            for i, bus in enumerate(buses)]


def report(name: str, requests, result) -> None:
    v = queue_fairness_violations(requests, result.accepted_ids)
    print(f"  {name:32s} {len(result.accepted_ids):3d} accepted  "
          f"{result.accommodated_mw*1000:7.1f} kW  violations={v:4d}  "
          f"feasible={result.feasible}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--network", choices=NETWORKS, default="eulv")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--k", type=int, default=None)
    ap.add_argument("--skip-nsga2-plain", action="store_true",
                     help="skip the plain (non-fair) nsga2_search comparison")
    args = ap.parse_args()

    cfg = NETWORKS[args.network]
    master = cfg["master"]
    if not master.exists():
        raise SystemExit(f"{master} not found -- for enwl, run experiments/28_m3_scale_report.py first")
    k = args.k or cfg["k"]

    net = import_opendss(str(master))
    load_buses = net.load.bus.tolist()
    requests = build_requests(load_buses, k, args.seed)
    problem = AllocationProblem(net=net, requests=requests)

    print(f"M9: queue-order fairness -- {args.network} ({k} candidate requests, seed={args.seed})\n")

    print("== existing baselines/searches: measuring a fairness cost never reported before ==")
    baseline = greedy_baseline(problem)
    report("greedy baseline (queue order)", requests, baseline)

    reinforced = reinforced_greedy_baseline(problem).result
    report("reinforced greedy baseline (M6)", requests, reinforced)

    continuous = continuous_greedy_baseline(problem)
    report("continuous greedy baseline (M7)", requests, continuous)

    if not args.skip_nsga2_plain:
        t0 = time.time()
        plain_results = nsga2_search(problem, pop_size=cfg["pop_size"], n_gen=cfg["n_gen"], seed=args.seed)
        dt = time.time() - t0
        plain_feasible = [r for r in plain_results if r.feasible]
        plain_best = max(plain_feasible, key=lambda r: r.accommodated_mw) if plain_feasible else None
        if plain_best:
            report(f"nsga2_search (M1, 2-objective, {dt:.0f}s)", requests, plain_best)

    print("\n== nsga2_search_fair: a genuine 3rd objective ==")
    t0 = time.time()
    fair_results = nsga2_search_fair(problem, pop_size=cfg["pop_size"], n_gen=cfg["n_gen"], seed=args.seed)
    dt = time.time() - t0
    fair_feasible = [r for r in fair_results if r.feasible]
    print(f"  {len(fair_results)} distinct final individuals, {len(fair_feasible)} fully feasible, {dt:.0f}s")

    scored = sorted(
        ((r, queue_fairness_violations(requests, r.accepted_ids)) for r in fair_feasible),
        key=lambda t: (t[1], -t[0].accommodated_mw),
    )
    print("\n  Pareto front, sorted by violations (best accommodated MW at each violation count):")
    seen_v = set()
    for r, v in scored:
        if v in seen_v:
            continue
        seen_v.add(v)
        print(f"    violations={v:4d}  accepted={len(r.accepted_ids):3d}  "
              f"accommodated={r.accommodated_mw*1000:7.1f} kW")

    zero_violation = [r for r, v in scored if v == 0]
    if zero_violation:
        best_zero = max(zero_violation, key=lambda r: r.accommodated_mw)
        best_overall = max(fair_feasible, key=lambda r: r.accommodated_mw)
        gap = (best_overall.accommodated_mw - best_zero.accommodated_mw) * 1000
        print(f"\n  best zero-violation point found: {best_zero.accommodated_mw*1000:.1f} kW")
        print(f"  best point found (any fairness): {best_overall.accommodated_mw*1000:.1f} kW")
        print(f"  -> demanding zero queue-jumps costs {gap:.1f} kW on this search's front"
              if gap > 1e-6 else "\n  -> zero queue-jumps cost nothing on this search's front (surprising, reported as found)")
    else:
        print("\n  no zero-violation point found in this population/generation budget "
              "(greedy_baseline's own 0-violation point is always a safe fallback)")


if __name__ == "__main__":
    main()
