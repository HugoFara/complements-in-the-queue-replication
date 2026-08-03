"""Validates continuous request sizing (M7): extending every search from a
binary accept/reject decision per request to a continuous fraction (0-1] of
its full requested size -- `docs/paper.md`'s third concrete next step
("extend the binary accept/reject decision to continuous request sizing").

`AllocationProblem.evaluate_sizes` is the new core primitive (`evaluate`
becomes its fraction=1.0 special case); three searches are built on top,
mirroring the binary ones each already has:

  - `continuous_greedy_baseline` vs. `greedy_baseline` -- bisects the largest
    feasible fraction of a request that doesn't fit at full size, instead of
    skipping it outright.
  - `nsga2_search_continuous` vs. `nsga2_search` -- real-valued genes (SBX
    crossover, polynomial mutation) instead of a binary bitstring.
  - `lindistflow_search(continuous=True)` vs. the binary MILP (M5) -- the
    same LinDistFlow LP, just with the accept variables' integrality
    relaxed; mathematically a pure simplification (fewer constraints), not a
    new model.

Since {0, 1} is a strict subset of [0, 1], every continuous variant's
achievable optimum can never be *worse* than its binary counterpart's on the
same network/requests -- checked directly below, not just assumed. Every
result reported is still AC-verified through `AllocationProblem.evaluate_sizes`
(the same pandapower oracle every other search in this package uses),
following the standing rule since M5: never trust a relaxed model's own
belief about feasibility.

Run:  uv run python experiments/31_continuous_sizing.py --network eulv
      uv run python experiments/31_continuous_sizing.py --network enwl --skip-nsga2
"""

from __future__ import annotations

import argparse
import random
import time
from pathlib import Path

from gridsynth import import_opendss

from allocation import AllocationProblem, Request, compare_to_baseline, greedy_baseline, nsga2_search
from allocation.baseline import continuous_greedy_baseline
from allocation.convex import lindistflow_search
from allocation.search import nsga2_search_continuous

REPO = Path(__file__).resolve().parents[1]
CACHE = REPO / "cache" / "enwl_full"

PV_KW_LO, PV_KW_HI = 3.0, 10.0  # same band as experiments 24/25/26/28/29/30

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


def n_partial(sizes, lo=0.01, hi=0.99) -> int:
    return sum(1 for frac in sizes.values() if lo < frac < hi)


def report(label: str, binary_mw: float, continuous_mw: float, continuous_feasible: bool,
           continuous_sizes: dict, k: int, dt: float | None = None) -> None:
    extra_kw = (continuous_mw - binary_mw) * 1000
    time_str = f"  time={dt:.1f}s" if dt is not None else ""
    print(f"  {label:22s}: binary={binary_mw*1000:7.1f} kW  continuous={continuous_mw*1000:7.1f} kW "
          f"({extra_kw:+.1f} kW, {n_partial(continuous_sizes)}/{k} requests partially granted, "
          f"feasible={continuous_feasible}){time_str}")
    if continuous_mw < binary_mw - 1e-9:
        print(f"    !! continuous UNDERPERFORMED binary on {label} -- this should be impossible "
              f"({{0,1}} subset of [0,1]); investigate before trusting this result.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--network", choices=NETWORKS, default="eulv")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--k", type=int, default=None, help="override the default candidate-pool size")
    ap.add_argument("--skip-nsga2", action="store_true", help="skip the (slow) NSGA-II comparison")
    args = ap.parse_args()

    cfg = NETWORKS[args.network]
    master = cfg["master"]
    if not master.exists():
        raise SystemExit(f"{master} not found -- for enwl, run experiments/28_m3_scale_report.py "
                          "first to populate the cache (or point --network at eulv)")
    k = args.k or cfg["k"]

    net = import_opendss(str(master))
    load_buses = net.load.bus.tolist()
    requests = build_requests(load_buses, k, args.seed)
    problem = AllocationProblem(net=net, requests=requests)

    print(f"Continuous request sizing vs. binary accept/reject -- {args.network} "
          f"({len(net.bus)} buses, {len(net.load)} loads), {k} candidate PV requests, seed={args.seed}\n")

    # 1. greedy baseline -------------------------------------------------------
    print("== 1. greedy baseline: binary accept/reject vs. bisected partial awards ==")
    binary_base = greedy_baseline(problem)
    t0 = time.time()
    continuous_base = continuous_greedy_baseline(problem)
    dt = time.time() - t0
    report("greedy baseline", binary_base.accommodated_mw, continuous_base.accommodated_mw,
           continuous_base.feasible, continuous_base.sizes, k, dt)
    print()

    # 2. NSGA-II ----------------------------------------------------------------
    if not args.skip_nsga2:
        print("== 2. NSGA-II: binary bitstring vs. continuous real-valued genes ==")
        t0 = time.time()
        binary_results = nsga2_search(problem, pop_size=cfg["pop_size"], n_gen=cfg["n_gen"], seed=args.seed)
        binary_dt = time.time() - t0
        binary_best = compare_to_baseline(binary_results, binary_base).best_feasible_search

        t0 = time.time()
        continuous_results = nsga2_search_continuous(problem, pop_size=cfg["pop_size"], n_gen=cfg["n_gen"],
                                                       seed=args.seed)
        continuous_dt = time.time() - t0
        continuous_best = compare_to_baseline(continuous_results, continuous_base).best_feasible_search

        if continuous_best is None:
            print("    continuous NSGA-II found no feasible individual in its final population -- unexpected.")
        else:
            report("NSGA-II", binary_best.accommodated_mw, continuous_best.accommodated_mw,
                   continuous_best.feasible, continuous_best.sizes, k)
            print(f"    time: binary={binary_dt:.1f}s  continuous={continuous_dt:.1f}s")
        print()

    # 3. convex LinDistFlow relaxation ------------------------------------------
    print("== 3. LinDistFlow relaxation (M5): binary MILP vs. continuous LP ==")
    t0 = time.time()
    binary_conv = lindistflow_search(problem)
    binary_conv_dt = time.time() - t0
    t0 = time.time()
    continuous_conv = lindistflow_search(problem, continuous=True)
    continuous_conv_dt = time.time() - t0
    report("LinDistFlow", binary_conv.result.accommodated_mw, continuous_conv.result.accommodated_mw,
           continuous_conv.result.feasible, continuous_conv.relaxed.sizes, k)
    print(f"    time: binary={binary_conv_dt:.2f}s  continuous={continuous_conv_dt:.2f}s")
    if not continuous_conv.result.feasible:
        print(f"    continuous LP's own optimum was AC-infeasible as found (vmin={continuous_conv.result.vmin_pu:.5f} "
              f"vmax={continuous_conv.result.vmax_pu:.5f} line%={continuous_conv.result.max_line_loading_percent:.2f}) "
              "-- retrying with a small thermal margin (thermal_margin_pct=2.0):")
        margined = lindistflow_search(problem, continuous=True, thermal_margin_pct=2.0)
        print(f"      margined continuous: {margined.result.accommodated_mw*1000:7.1f} kW  "
              f"feasible={margined.result.feasible}")


if __name__ == "__main__":
    main()
