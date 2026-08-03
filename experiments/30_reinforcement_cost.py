"""Validates `allocation.reinforcement` -- the real, catalogue-priced upgrade
cost that replaces the `reinforcement_cost_proxy` placeholder as a genuine
lever a planner (or, eventually, the search) can choose to apply, rather than
only a violation to avoid (docs/paper.md Section 5/7, ROADMAP.md's "What
'cost' means"). This is M6 of docs/joint-allocation-scope.md.

`allocation.reinforcement.price_reinforcement` prices a thermal-only lever
(reconductor an overloaded line, or upsize an overloaded transformer, from a
small catalogue reusing `gridsynth.parameters`' own validated LV conductor
physics) and AC-reverifies every upgrade through the same `pandapower.runpp`
oracle the rest of this package trusts. It does not model a voltage-specific
lever (regulators, OLTC, capacitor banks) -- a pure over-/under-voltage
violation is reported as unresolved, honestly, not priced at zero.

That split matters here specifically because M5 (experiments/29) already
found EULV is voltage-limited and the M3 ENWL feeder is thermal-limited --
so this script is also a direct test of whether that split predicts where
the reinforcement lever helps: expected to do nothing for EULV, and to
recover real capacity on ENWL.

Run:  uv run python experiments/30_reinforcement_cost.py --network eulv
      uv run python experiments/30_reinforcement_cost.py --network enwl --skip-nsga2
"""

from __future__ import annotations

import argparse
import random
import time
from pathlib import Path

from gridsynth import import_opendss

from allocation import AllocationProblem, Request, compare_to_baseline, greedy_baseline, nsga2_search
from allocation.baseline import reinforced_greedy_baseline
from allocation.reinforcement import price_reinforcement

REPO = Path(__file__).resolve().parents[1]
CACHE = REPO / "cache" / "enwl_full"

PV_KW_LO, PV_KW_HI = 3.0, 10.0  # same band as experiments 24/25/26/28/29

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


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--network", choices=NETWORKS, default="eulv")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--k", type=int, default=None, help="override the default candidate-pool size")
    ap.add_argument("--skip-nsga2", action="store_true", help="skip the (slow) NSGA-II comparison")
    ap.add_argument("--marginal-requests", type=int, default=5,
                     help="how many of the greedy baseline's first rejected requests to price individually")
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

    print(f"Reinforcement-cost proxy vs. the placeholder -- {args.network} "
          f"({len(net.bus)} buses, {len(net.load)} loads), {k} candidate PV requests, seed={args.seed}\n")

    # 1. greedy baseline, then price the next few rejected requests one at a time --
    baseline = greedy_baseline(problem)
    print("== 1. what would it cost to accept the greedy baseline's first N rejected requests? ==")
    print(f"  greedy baseline: {baseline.accommodated_mw*1000:7.1f} kW "
          f"({len(baseline.accepted_ids)}/{k} accepted) -- "
          f"vmin={baseline.vmin_pu:.5f} vmax={baseline.vmax_pu:.5f} "
          f"line%={baseline.max_line_loading_percent:.2f} trafo%={baseline.max_trafo_loading_percent:.2f}")

    ordered = sorted(requests, key=lambda r: r.queue_order)
    accepted_ids = set(baseline.accepted_ids)
    rejected = [r for r in ordered if r.id not in accepted_ids]
    trial = set(accepted_ids)
    for r in rejected[: args.marginal_requests]:
        trial.add(r.id)
        plan = price_reinforcement(problem, trial)
        if plan.feasible_after_reinforcement:
            print(f"    + {r.id} ({r.p_mw*1000:+.1f} kW): resolvable, cost=EUR{plan.total_cost_eur:,.0f} "
                  f"({len(plan.line_upgrades)} line / {len(plan.trafo_upgrades)} trafo upgrades, "
                  f"{plan.n_iterations} round(s))")
        else:
            print(f"    + {r.id} ({r.p_mw*1000:+.1f} kW): NOT resolvable by this catalogue "
                  f"(voltage_violation_pu={plan.result_after_reinforcement.voltage_violation_pu:.5f} -- "
                  "no lever for a pure voltage violation)")
            trial.discard(r.id)  # don't compound an unresolved violation into the next iteration's trial
    print()

    # 2. the full reinforced-queue walk vs. the plain greedy baseline --------
    print("== 2. reinforced_greedy_baseline: reinforcement as a lever across the whole queue ==")
    t0 = time.time()
    reinforced = reinforced_greedy_baseline(problem)
    dt = time.time() - t0
    extra_kw = (reinforced.result.accommodated_mw - baseline.accommodated_mw) * 1000
    print(f"  plain greedy       : {baseline.accommodated_mw*1000:7.1f} kW "
          f"({len(baseline.accepted_ids)}/{k} accepted), EUR0")
    print(f"  reinforced greedy  : {reinforced.result.accommodated_mw*1000:7.1f} kW "
          f"({len(reinforced.result.accepted_ids)}/{k} accepted), "
          f"EUR{reinforced.total_cost_eur:,.0f} "
          f"({len(reinforced.line_upgrades)} line / {len(reinforced.trafo_upgrades)} trafo upgrades), "
          f"time={dt:.1f}s  ({extra_kw:+.1f} kW vs. plain greedy)")
    if reinforced.total_cost_eur > 0:
        print(f"    => EUR{reinforced.total_cost_eur/max(extra_kw, 1e-9):,.1f} per extra kW accommodated")
    print()

    # 3. where does that leave NSGA-II, which pays nothing but can't spend money to unlock more? --
    if not args.skip_nsga2:
        print("== 3. NSGA-II (zero-cost search ceiling) for context ==")
        t0 = time.time()
        results = nsga2_search(problem, pop_size=cfg["pop_size"], n_gen=cfg["n_gen"], seed=args.seed)
        nsga_dt = time.time() - t0
        cmp = compare_to_baseline(results, baseline)
        best = cmp.best_feasible_search
        print(f"  NSGA-II            : {best.accommodated_mw*1000:7.1f} kW "
              f"({len(best.accepted_ids)}/{k} accepted), EUR0 (no reinforcement lever), time={nsga_dt:.1f}s")
        if reinforced.result.accommodated_mw > best.accommodated_mw:
            print(f"    => reinforcement bought {(reinforced.result.accommodated_mw-best.accommodated_mw)*1000:.1f} "
                  f"kW beyond what a free (zero-cost) search alone can reach, for "
                  f"EUR{reinforced.total_cost_eur:,.0f}")
        else:
            print("    => on this network/seed, NSGA-II's free search already reaches at least as much MW as "
                  "paying for reinforcement did -- reinforcement's lever wasn't the binding opportunity here.")


if __name__ == "__main__":
    main()
