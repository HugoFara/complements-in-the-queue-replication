"""Validates `allocation.convex` (LinDistFlow MILP) against `allocation.search`
(NSGA-II) and the real pandapower AC oracle -- does swapping the metaheuristic
for a convex relaxation actually work, and where does it differ?

Motivation: NSGA-II pays one `runpp` per individual (ROADMAP.md's Hard Parts
names this as the scaling bottleneck: pop_size*n_gen solves, ~100s on the M3
ENWL feeder for 900 solves). Every feeder this repo has imported is radial, so
LinDistFlow (Baran & Wu 1989) applies: dropping AC losses linearizes the
branch-flow physics, and since every request here is a fixed-size accept/
reject decision with q_mvar=0, the whole feasible region collapses to a single
MILP (`allocation.convex.lindistflow_search`) -- solved once with
`scipy.optimize.milp`, not searched.

This script does NOT trust the MILP's own accept-set: every result reported
below re-verifies through `AllocationProblem.evaluate` (the same pandapower
oracle NSGA-II uses), matching this repo's standing rule that a search result
is only as good as its AC-verified feasibility. It runs five checks per
network, in increasing order of what could go wrong:

  1. **Baseline parity** -- with zero new requests, does the relaxed model's
     own voltage/loading prediction match pandapower's real `runpp`? Isolates
     the physics-modeling gap from anything about the search.
  2. **Accept-set parity** -- forward-evaluate the (real, AC-verified) greedy
     baseline's accept-set through the relaxed model. Tells us whether the
     relaxation's belief agrees with ground truth right at a near-boundary
     accept-set, not just at an empty/lightly-loaded one.
  3. **Head-to-head** -- solve time and AC-verified accommodated MW: greedy
     baseline, the single-shot LinDistFlow MILP, the loss-corrected iterative
     MILP (`lindistflow_search_iterative`), and NSGA-II, same network/seed.
     The single-shot MILP *underperformed the greedy baseline* on the M3 ENWL
     feeder the first time this was run -- traced to pure LinDistFlow
     dropping every edge's own I^2R/I^2X loss, which is negligible edge-by-
     edge but compounds across the ~4,768 downstream edges a near-source
     trunk line carries current for (see `lindistflow_search_iterative`'s
     docstring for the exact numbers). The iterative variant fixes this with
     a fixed-point loss correction, verified AC-feasible every round.
  4. **Octagon sensitivity** -- does a finer thermal-limit polygon (more
     sides) matter, or is polygon coarseness a red herring next to the
     compounding-loss mechanism above? (It's a red herring: this sweep is
     what ruled out polygon coarseness as the dominant error in the first
     place, before the real mechanism was traced.)

Run:  uv run python experiments/29_convex_relaxation.py --network eulv
      uv run python experiments/29_convex_relaxation.py --network enwl --skip-nsga2
"""

from __future__ import annotations

import argparse
import random
import time
from pathlib import Path

from gridsynth import import_opendss

from allocation import AllocationProblem, Request, compare_to_baseline, greedy_baseline, nsga2_search
from allocation.convex import (
    build_lindistflow_model,
    evaluate_relaxed,
    lindistflow_search,
    lindistflow_search_iterative,
)

REPO = Path(__file__).resolve().parents[1]
CACHE = REPO / "cache" / "enwl_full"

PV_KW_LO, PV_KW_HI = 3.0, 10.0  # same band as experiments 24/25/26/28

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


def fmt(r) -> str:
    return (f"vmin={r.vmin_pu:.5f} vmax={r.vmax_pu:.5f} "
            f"line%={r.max_line_loading_percent:6.2f} trafo%={r.max_trafo_loading_percent:6.2f}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--network", choices=NETWORKS, default="eulv")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--k", type=int, default=None, help="override the default candidate-pool size")
    ap.add_argument("--skip-nsga2", action="store_true", help="skip the (slow) NSGA-II comparison")
    ap.add_argument("--octagon-sweep", action="store_true",
                     help="also solve at 8/16/32/64 octagon sides, to separate polygon "
                          "coarseness from the nominal-voltage thermal-rating approximation")
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

    print(f"LinDistFlow MILP vs. NSGA-II -- {args.network} "
          f"({len(net.bus)} buses, {len(net.load)} loads), {k} candidate PV requests, seed={args.seed}\n")

    model = build_lindistflow_model(problem)
    print(f"tree: {len(model.nonroot_buses)} edges (radial, checked)\n")

    # 1. baseline parity ------------------------------------------------------
    empty_relaxed = evaluate_relaxed(model, [])
    empty_actual = problem.evaluate([])
    print("== 1. baseline parity (empty subset: relaxed model vs. real runpp) ==")
    print(f"  relaxed : {fmt(empty_relaxed)}")
    print(f"  actual  : {fmt(empty_actual)}")
    print()

    # 2. accept-set parity at the greedy baseline's near-boundary point ------
    baseline = greedy_baseline(problem)
    baseline_relaxed = evaluate_relaxed(model, baseline.accepted_ids)
    print("== 2. accept-set parity (greedy baseline's accept-set: relaxed vs. real) ==")
    print(f"  relaxed : {fmt(baseline_relaxed)}")
    print(f"  actual  : {fmt(baseline)}  accommodated={baseline.accommodated_mw*1000:.1f} kW  "
          f"feasible={baseline.feasible}")
    print()

    # 3. head-to-head -----------------------------------------------------------
    print("== 3. head-to-head (AC-verified accommodated MW, wall time) ==")
    print(f"  greedy baseline      : {baseline.accommodated_mw*1000:7.1f} kW  "
          f"({len(baseline.accepted_ids)}/{k} accepted)")

    t0 = time.time()
    conv = lindistflow_search(problem)
    conv_dt = time.time() - t0
    gap_vs_baseline = (conv.result.accommodated_mw - baseline.accommodated_mw) * 1000
    print(f"  LinDistFlow MILP     : {conv.result.accommodated_mw*1000:7.1f} kW  "
          f"({len(conv.result.accepted_ids)}/{k} accepted)  "
          f"feasible={conv.result.feasible}  time={conv_dt:.2f}s  mip_gap={conv.mip_gap:.4f}  "
          f"({gap_vs_baseline:+.1f} kW vs. greedy)")
    print(f"    relaxed model's own belief about its chosen set: {fmt(conv.relaxed)}")
    print(f"    AC-verified reality for that same set           : {fmt(conv.result)}")

    t0 = time.time()
    it_conv = lindistflow_search_iterative(problem)
    it_dt = time.time() - t0
    gap_it_vs_baseline = (it_conv.result.accommodated_mw - baseline.accommodated_mw) * 1000
    print(f"  LinDistFlow iterative: {it_conv.result.accommodated_mw*1000:7.1f} kW  "
          f"({len(it_conv.result.accepted_ids)}/{k} accepted)  "
          f"feasible={it_conv.result.feasible}  time={it_dt:.2f}s  "
          f"n_iter={it_conv.n_iterations}  converged={it_conv.converged}  "
          f"({gap_it_vs_baseline:+.1f} kW vs. greedy, "
          f"{(it_conv.result.accommodated_mw-conv.result.accommodated_mw)*1000:+.1f} kW vs. single-shot)")
    print(f"    loss-corrected accommodated-MW trace across rounds: "
          f"{[round(x, 1) for x in it_conv.accommodated_mw_trace]}")

    if not args.skip_nsga2:
        t0 = time.time()
        results = nsga2_search(problem, pop_size=cfg["pop_size"], n_gen=cfg["n_gen"], seed=args.seed)
        nsga_dt = time.time() - t0
        cmp = compare_to_baseline(results, baseline)
        best = cmp.best_feasible_search
        speedup = nsga_dt / it_dt if it_dt > 0 else float("inf")
        print(f"  NSGA-II              : {best.accommodated_mw*1000:7.1f} kW  "
              f"({len(best.accepted_ids)}/{k} accepted)  "
              f"feasible={best.feasible}  time={nsga_dt:.2f}s  dominates_baseline={cmp.dominates}")
        print(f"\n  LinDistFlow iterative MILP was {speedup:.0f}x faster than NSGA-II "
              f"({it_dt:.2f}s vs {nsga_dt:.2f}s), reaching "
              f"{it_conv.result.accommodated_mw/best.accommodated_mw*100:.1f}% of NSGA-II's accommodated MW "
              f"(single-shot reached {conv.result.accommodated_mw/best.accommodated_mw*100:.1f}%).")
    print()

    # 4. octagon sensitivity ----------------------------------------------------
    if args.octagon_sweep:
        print("== 4. octagon-side sweep (isolating polygon coarseness from the "
              "nominal-voltage thermal approximation) ==")
        for sides in (8, 16, 32, 64):
            t0 = time.time()
            c = lindistflow_search(problem, octagon_sides=sides)
            dt = time.time() - t0
            print(f"  sides={sides:3d}  time={dt:5.2f}s  "
                  f"accommodated={c.result.accommodated_mw*1000:7.1f} kW  "
                  f"n={len(c.result.accepted_ids)}  feasible={c.result.feasible}  "
                  f"relaxed_line%={c.relaxed.max_line_loading_percent:6.2f}  "
                  f"actual_line%={c.result.max_line_loading_percent:6.2f}")


if __name__ == "__main__":
    main()
