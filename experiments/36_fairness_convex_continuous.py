"""M12 of the joint-allocation roadmap: extend queue-order fairness to the
convex-MILP and continuous-sizing search variants.

M9 added `queue_fairness_violations` as a genuine third objective, but only
for `nsga2_search_fair` -- the binary, evolutionary search. Two faster/other
routes had no fairness awareness at all: `lindistflow_search` (M5's MILP,
55-86x faster than NSGA-II) and `nsga2_search_continuous` (M7's continuous
request sizing). This milestone closes both gaps:

- `lindistflow_search_fair` caps `queue_fairness_violations` on the MILP's
  chosen accept-set via an exact linearization (see `allocation.convex`'s
  `_solve` docstring), traced as a front by an epsilon-constraint sweep
  (solve once per violation-count threshold) rather than NSGA-II's
  evolutionary Pareto front, since the MILP's own objective is single
  (maximize accommodated MW).
- `nsga2_search_continuous_fair` is the direct continuous-sizing analogue of
  `nsga2_search_fair` -- real-valued genes, "accepted" meaning a nonzero
  granted fraction.

Why this milestone, not one of the other candidates on ROADMAP.md's open
list: DOE i2X (the funding target `docs/paper.md` frames this project
around) names "fairer interconnection" and "fair procedures" explicitly in
its own mission; FERC issued six show-cause orders in June 2026 specifically
about interconnection cost-allocation fairness; and until this milestone,
the *fast* production-shaped search route (the MILP) had no fairness
awareness at all -- only the slow evolutionary one did.

Run:  uv run python experiments/36_fairness_convex_continuous.py --network eulv
      uv run python experiments/36_fairness_convex_continuous.py --network enwl
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
    lindistflow_search,
    lindistflow_search_fair,
    nsga2_search_continuous,
    nsga2_search_continuous_fair,
    queue_fairness_violations,
)

REPO = Path(__file__).resolve().parents[1]
CACHE = REPO / "cache" / "enwl_full"

PV_KW_LO, PV_KW_HI = 3.0, 10.0  # same band as experiments 24/25/26/28-34

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


def sweep_convex(problem: AllocationProblem, k: int) -> None:
    print("== lindistflow_search_fair: epsilon-constraint sweep ==")
    plain = lindistflow_search(problem)
    plain_v = queue_fairness_violations(problem.requests, plain.result.accepted_ids)
    print(f"  lindistflow_search (unconstrained): {plain.result.accommodated_mw*1000:7.1f} kW  "
          f"violations={plain_v}  feasible={plain.result.feasible}  {plain.solve_time_s:.2f}s")

    caps = sorted({0, 1, 2, 5, 10, 20, min(plain_v, k), k * (k - 1) // 2})
    t0 = time.time()
    seen_mw = set()
    for cap in caps:
        fair = lindistflow_search_fair(problem, max_violations=cap)
        actual_v = queue_fairness_violations(problem.requests, fair.result.accepted_ids)
        mw = round(fair.result.accommodated_mw * 1000, 1)
        marker = "" if mw in seen_mw else "  <- new point on the front"
        seen_mw.add(mw)
        print(f"  max_violations={cap:4d}: accommodated={mw:7.1f} kW  actual_violations={actual_v:4d}  "
              f"feasible={fair.result.feasible}{marker}")
    print(f"  sweep of {len(caps)} thresholds took {time.time() - t0:.1f}s total")


def compare_continuous(problem: AllocationProblem, pop_size: int, n_gen: int, seed: int) -> None:
    print("\n== nsga2_search_continuous_fair vs. nsga2_search_continuous ==")
    # Separate AllocationProblem instances (same net/requests, fresh M10 cache
    # each) -- run back-to-back on the *same* instance, the second call's
    # timing is misleadingly fast: `FloatRandomSampling`'s initial population
    # is seed-determined independent of the objective, so generation 0's
    # genotypes -- and, empirically, often much more than just generation 0
    # -- are exact repeats of what the first call already cached. That's a
    # real, interesting consequence of M10's cache (not a bug), but reporting
    # it here without separate instances would misstate the fair search's
    # own standalone cost.
    plain_problem = AllocationProblem(net=problem.net, requests=problem.requests)
    fair_problem = AllocationProblem(net=problem.net, requests=problem.requests)

    t0 = time.time()
    plain = nsga2_search_continuous(plain_problem, pop_size=pop_size, n_gen=n_gen, seed=seed)
    dt_plain = time.time() - t0
    plain_feasible = [r for r in plain if r.feasible]
    if plain_feasible:
        best_plain = max(plain_feasible, key=lambda r: r.accommodated_mw)
        v = queue_fairness_violations(problem.requests, best_plain.accepted_ids)
        print(f"  nsga2_search_continuous (M7, {dt_plain:.0f}s): best={best_plain.accommodated_mw*1000:7.1f} kW  "
              f"violations={v}")

    t0 = time.time()
    fair = nsga2_search_continuous_fair(fair_problem, pop_size=pop_size, n_gen=n_gen, seed=seed)
    dt_fair = time.time() - t0
    fair_feasible = [r for r in fair if r.feasible]
    print(f"  nsga2_search_continuous_fair (M12, {dt_fair:.0f}s): {len(fair)} individuals, "
          f"{len(fair_feasible)} feasible")

    scored = sorted(
        ((r, queue_fairness_violations(problem.requests, r.accepted_ids)) for r in fair_feasible),
        key=lambda t: (t[1], -t[0].accommodated_mw),
    )
    seen_v = set()
    for r, v in scored:
        if v in seen_v:
            continue
        seen_v.add(v)
        print(f"    violations={v:4d}  accommodated={r.accommodated_mw*1000:7.1f} kW")

    zero = [r for r, v in scored if v == 0]
    if zero:
        best_zero = max(zero, key=lambda r: r.accommodated_mw)
        best_overall = max(fair_feasible, key=lambda r: r.accommodated_mw) if fair_feasible else None
        if best_overall:
            gap = (best_overall.accommodated_mw - best_zero.accommodated_mw) * 1000
            print(f"  demanding zero queue-jumps costs {gap:.1f} kW on this search's front"
                  if gap > 1e-6 else "  zero queue-jumps cost nothing here (free fairness)")
    else:
        print("  no zero-violation point found in this population/generation budget")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--network", choices=NETWORKS, default="eulv")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--k", type=int, default=None)
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

    print(f"M12: fairness on the convex and continuous search routes -- "
          f"{args.network} ({k} candidate requests, seed={args.seed})\n")

    sweep_convex(problem, k)
    compare_continuous(problem, cfg["pop_size"], cfg["n_gen"], args.seed)


if __name__ == "__main__":
    main()
