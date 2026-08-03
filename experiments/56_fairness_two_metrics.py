"""M25: is "fairness is usually free" robust to the fairness metric?

The note reports (Section 4.9/4.17) that demanding zero queue-jumps usually
costs nothing, measured by `queue_fairness_violations` -- the *total* count of
queue-order inversions (an L1 metric). But that is one of two natural
definitions of queue-order fairness, and they can disagree:

  * total inversions  (L1)  -- how many (earlier-rejected, later-accepted) pairs
  * max displacement  (Linf) -- how far the single most-displaced admitted
    request cut ahead in the queue; the quantity Fontinele et al. (SSRN 6561541)
    cap directly (their N/2 bound)

A schedule can be cheap on one and expensive on the other. This experiment
recomputes the capacity-vs-fairness front under BOTH metrics on the same
networks, so the "usually free" claim is checked for metric-robustness rather
than asserted under whichever metric was convenient. It also reports the
max-displacement of the honest skip-and-continue baseline (M22) -- which,
unlike stop-dead greedy, is NOT a queue prefix and so can itself displace
requests -- putting a number on the fairness the status quo already spends.

Run:  uv run python experiments/56_fairness_two_metrics.py [--network eulv|enwl] [--save]
"""

from __future__ import annotations

import argparse
import csv
import random
import time
from pathlib import Path

from gridsynth import import_opendss

from allocation import (
    AllocationProblem,
    Request,
    greedy_baseline,
    max_queue_displacement,
    nsga2_search_fair,
    queue_fairness_violations,
    skip_continue_baseline,
)

REPO = Path(__file__).resolve().parents[1]
CACHE = REPO / "cache" / "enwl_full"
PV_KW_LO, PV_KW_HI = 3.0, 10.0

NETWORKS = {
    "eulv": dict(master=REPO / "data" / "opendss" / "eulv" / "Master_snapshot.dss", k=40, pop=40, gen=40),
    "enwl": dict(master=CACHE / "network_15" / "Feeder_4" / "Master_snapshot.dss", k=100, pop=30, gen=30),
    "enwl_bound": dict(master=CACHE / "network_1" / "Feeder_4" / "Master_snapshot.dss", k=50, pop=40, gen=40),
}


def build_requests(load_buses, k, seed) -> list[Request]:
    rng = random.Random(seed)
    buses = rng.sample(load_buses, min(k, len(load_buses)))
    return [Request(id=f"pv{i}", bus=b, p_mw=-rng.uniform(PV_KW_LO, PV_KW_HI) / 1000.0, queue_order=i)
            for i, b in enumerate(buses)]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--network", choices=NETWORKS, default="eulv")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--save", action="store_true")
    args = ap.parse_args()
    cfg = NETWORKS[args.network]
    net = import_opendss(str(cfg["master"]))
    load_buses = list(net.load.bus.unique())
    requests = build_requests(load_buses, cfg["k"], args.seed)
    n = len(requests)
    problem = AllocationProblem(net=net, requests=requests)

    print(f"M25: two fairness metrics -- {args.network} ({n} requests, seed {args.seed})\n")

    # what the baselines already spend, under BOTH metrics
    stop = greedy_baseline(problem)
    skip = skip_continue_baseline(problem)
    for label, res in [("stop-dead greedy (prefix, fair by construction)", stop),
                       ("skip-continue (the honest FCFS baseline)", skip)]:
        inv = queue_fairness_violations(requests, res.accepted_ids)
        disp = max_queue_displacement(requests, res.accepted_ids)
        print(f"  {label:48s} {res.accommodated_mw*1000:7.1f} kW  "
              f"inversions={inv:4d}  max-displacement={disp:3d} (N/2={n//2})")

    # the fair search front, scored under both metrics
    print(f"\n  fair search front (nsga2_search_fair), each feasible point under both metrics:")
    t0 = time.time()
    fair = nsga2_search_fair(problem, pop_size=cfg["pop"], n_gen=cfg["gen"], seed=args.seed)
    feas = [r for r in fair if r.feasible]
    scored = []
    for r in feas:
        inv = queue_fairness_violations(requests, r.accepted_ids)
        disp = max_queue_displacement(requests, r.accepted_ids)
        scored.append((r, inv, disp))
    print(f"  {len(feas)} feasible points, {time.time()-t0:.0f}s")

    # capacity ceiling and the zero-cost point under EACH metric definition
    if feas:
        ceiling = max(r.accommodated_mw for r in feas) * 1000
        zero_inv = [r for r, inv, _ in scored if inv == 0]
        zero_disp = [r for r, _, disp in scored if disp == 0]
        best_zero_inv = max((r.accommodated_mw for r in zero_inv), default=None)
        best_zero_disp = max((r.accommodated_mw for r in zero_disp), default=None)
        print(f"\n  free-search ceiling (any fairness):          {ceiling:7.1f} kW")
        if best_zero_inv is not None:
            print(f"  best at zero INVERSIONS (L1 metric):         {best_zero_inv*1000:7.1f} kW  "
                  f"-> costs {ceiling - best_zero_inv*1000:5.1f} kW")
        else:
            print(f"  no zero-inversion point found in budget")
        if best_zero_disp is not None:
            print(f"  best at zero MAX-DISPLACEMENT (Linf metric):  {best_zero_disp*1000:7.1f} kW  "
                  f"-> costs {ceiling - best_zero_disp*1000:5.1f} kW")
        else:
            print(f"  no zero-displacement point found in budget")

        # Fontinele-style N/2 displacement cap: cheapest-fairness bound they use
        cap = n // 2
        under_cap = [r for r, _, disp in scored if disp <= cap]
        best_cap = max((r.accommodated_mw for r in under_cap), default=None)
        if best_cap is not None:
            print(f"  best under N/2 displacement cap (Fontinele):  {best_cap*1000:7.1f} kW  "
                  f"-> costs {ceiling - best_cap*1000:5.1f} kW")

        # the disagreement: is a point cheap on one metric expensive on the other?
        print(f"\n  metric (dis)agreement -- Pareto-ish sample (inv, disp, kW):")
        seen = set()
        for r, inv, disp in sorted(scored, key=lambda t: (t[1], -t[0].accommodated_mw)):
            key = inv
            if key in seen:
                continue
            seen.add(key)
            print(f"    inversions={inv:4d}  max-disp={disp:3d}  {r.accommodated_mw*1000:7.1f} kW")

    if args.save and feas:
        (REPO / "outputs").mkdir(exist_ok=True)
        path = REPO / "outputs" / f"m25_fairness_two_metrics_{args.network}.csv"
        with open(path, "w", newline="") as f:
            wr = csv.writer(f)
            wr.writerow(["accommodated_kw", "inversions", "max_displacement"])
            for r, inv, disp in scored:
                wr.writerow([r.accommodated_mw * 1000, inv, disp])
        print(f"\nsaved {path}")


if __name__ == "__main__":
    main()
