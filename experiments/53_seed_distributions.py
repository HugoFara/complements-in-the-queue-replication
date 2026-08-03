"""M24: are the headline margins real, or single-seed metaheuristic noise?

Every headline joint-vs-serial margin in the note is a single seed (+3.6% on
EULV, +1.2 kW on the ENWL feeder). Two of them (+0.3%, +1.2 kW) are small
enough to be within plausible run-to-run variance. This experiment replaces
each point estimate with a distribution and a paired significance test, and
separates the two independent sources of variance:

  1. **Request-draw variance** -- the joint-vs-skip-continue margin across many
     random request sets (different buses + sizes). This is the one that
     matters for "is the advantage real or a fluke of one draw." Measured with
     the deterministic AC-reverified convex route so the margin carries no
     search-stochasticity confound; paired Wilcoxon + t-test of joint vs.
     skip-continue across draws.

  2. **Metaheuristic variance** -- for a *single fixed* request set, NSGA-II's
     accommodated MW across many search seeds. This tests directly whether a
     claimed few-kW margin is smaller than NSGA-II's own seed-to-seed spread.

The skip-and-continue baseline (M22) is the comparator throughout, not
stop-dead greedy.

Run:  uv run python experiments/53_seed_distributions.py [--draws 30] [--search-seeds 15] [--save]
"""

from __future__ import annotations

import argparse
import csv
import random
import statistics
import time
from pathlib import Path

from gridsynth import import_opendss
from scipy import stats

from allocation import (
    AllocationProblem,
    Request,
    lindistflow_search_iterative,
    nsga2_search,
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


def request_draw_variance(name, cfg, draws) -> dict:
    """Joint-vs-skip margin across ``draws`` random request sets (convex route)."""
    net = import_opendss(str(cfg["master"]))
    load_buses = list(net.load.bus.unique())
    skips, joints, margins_kw, margins_pct, n_bound = [], [], [], [], 0
    for s in range(draws):
        requests = build_requests(load_buses, cfg["k"], 1000 + s)  # offset seeds off the note's own seed 0
        problem = AllocationProblem(net=net, requests=requests)
        skip = skip_continue_baseline(problem)
        try:
            joint = lindistflow_search_iterative(problem).result
        except Exception:
            continue
        if not joint.feasible:
            continue
        sk = skip.accommodated_mw * 1000
        jk = joint.accommodated_mw * 1000
        skips.append(sk); joints.append(jk)
        margins_kw.append(jk - sk)
        margins_pct.append((jk - sk) / sk * 100 if sk > 0 else 0.0)
        if len(skip.accepted_ids) < cfg["k"]:
            n_bound += 1
    # paired tests: does joint differ from skip across draws?
    try:
        w_stat, w_p = stats.wilcoxon(joints, skips, zero_method="zsplit")
    except ValueError:
        w_stat, w_p = float("nan"), float("nan")  # all-zero differences => no measurable effect
    t_stat, t_p = stats.ttest_rel(joints, skips)
    n_pos = sum(1 for m in margins_kw if m > 1e-6)
    return dict(name=name, n=len(margins_kw), n_bound=n_bound, n_joint_gt_skip=n_pos,
                margin_kw_mean=statistics.mean(margins_kw), margin_kw_sd=statistics.pstdev(margins_kw),
                margin_kw_med=statistics.median(margins_kw),
                margin_kw_min=min(margins_kw), margin_kw_max=max(margins_kw),
                margin_pct_mean=statistics.mean(margins_pct), margin_pct_max=max(margins_pct),
                wilcoxon_p=w_p, ttest_p=t_p)


def metaheuristic_variance(name, cfg, search_seeds) -> dict:
    """For a single fixed request set (seed 0, the note's own), NSGA-II
    accommodated MW across ``search_seeds`` search seeds -- the run-to-run
    spread the single-seed headline sits inside of."""
    net = import_opendss(str(cfg["master"]))
    load_buses = list(net.load.bus.unique())
    requests = build_requests(load_buses, cfg["k"], 0)  # the note's own seed-0 request set
    problem = AllocationProblem(net=net, requests=requests)
    skip = skip_continue_baseline(problem)
    sk = skip.accommodated_mw * 1000
    conv = lindistflow_search_iterative(problem).result.accommodated_mw * 1000
    joints = []
    for s in range(search_seeds):
        res = nsga2_search(problem, pop_size=cfg["pop"], n_gen=cfg["gen"], seed=s)
        feas = [r for r in res if r.feasible]
        if feas:
            joints.append(max(feas, key=lambda r: r.accommodated_mw).accommodated_mw * 1000)
    return dict(name=name, skip_kw=sk, convex_kw=conv,
                nsga2_mean=statistics.mean(joints), nsga2_sd=statistics.pstdev(joints),
                nsga2_min=min(joints), nsga2_max=max(joints), n=len(joints),
                margin_mean_kw=statistics.mean(joints) - sk)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--draws", type=int, default=30)
    ap.add_argument("--search-seeds", type=int, default=15)
    ap.add_argument("--networks", default="eulv,enwl,enwl_bound")
    ap.add_argument("--skip-metaheuristic", action="store_true")
    ap.add_argument("--save", action="store_true")
    args = ap.parse_args()
    nets = [n for n in args.networks.split(",") if n in NETWORKS]

    print(f"M24: seed distributions -- request-draw variance ({args.draws} draws, convex) "
          f"vs. skip-continue baseline\n")
    print(f"  {'network':12s} {'n':>3s} {'bound':>5s} {'J>S':>4s}  {'margin kW (mean+-sd)':>22s} "
          f"{'median':>7s} {'[min,max]':>16s}  {'mean%':>6s}  {'Wilcoxon p':>10s} {'t p':>9s}")
    rows = []
    for name in nets:
        t0 = time.time()
        r = request_draw_variance(name, NETWORKS[name], args.draws)
        rows.append(r)
        sig = "***" if r["wilcoxon_p"] < 0.001 else ("**" if r["wilcoxon_p"] < 0.01 else ("*" if r["wilcoxon_p"] < 0.05 else "n.s."))
        print(f"  {r['name']:12s} {r['n']:3d} {r['n_bound']:5d} {r['n_joint_gt_skip']:4d}  "
              f"{r['margin_kw_mean']:9.2f} +- {r['margin_kw_sd']:5.2f}  {r['margin_kw_med']:7.2f} "
              f"[{r['margin_kw_min']:6.1f},{r['margin_kw_max']:6.1f}]  {r['margin_pct_mean']:5.2f}  "
              f"{r['wilcoxon_p']:10.2e} {r['ttest_p']:9.2e} {sig}  ({time.time()-t0:.0f}s)")

    print("\n  Reading: a margin is only a real finding if it is both (a) practically")
    print("  non-trivial and (b) significant. 'n.s.' or a mean margin inside +-sd of 0")
    print("  should be downgraded from the headline.")

    meta_rows = []
    if not args.skip_metaheuristic:
        print(f"\nM24b: metaheuristic variance -- NSGA-II accommodated MW over {args.search_seeds} "
              f"search seeds, FIXED seed-0 request set")
        for name in nets:
            if name == "enwl_bound":
                continue  # eulv (+3.6%) and enwl (+1.2 kW) are the two headline claims
            t0 = time.time()
            r = metaheuristic_variance(name, NETWORKS[name], args.search_seeds)
            meta_rows.append(r)
            spread = r["nsga2_max"] - r["nsga2_min"]
            print(f"  {r['name']:12s} skip={r['skip_kw']:7.1f}  convex={r['convex_kw']:7.1f}  "
                  f"nsga2={r['nsga2_mean']:7.1f}+-{r['nsga2_sd']:.2f} kW "
                  f"[{r['nsga2_min']:.1f},{r['nsga2_max']:.1f}]  "
                  f"seed-spread={spread:.1f} kW vs margin-over-skip={r['margin_mean_kw']:.1f} kW  ({time.time()-t0:.0f}s)")
        print("  If seed-spread >= margin-over-skip, the single-seed headline is inside the noise.")

    if args.save:
        (REPO / "outputs").mkdir(exist_ok=True)
        with open(REPO / "outputs" / "m24_seed_distributions.csv", "w", newline="") as f:
            wr = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            wr.writeheader(); wr.writerows(rows)
        if meta_rows:
            with open(REPO / "outputs" / "m24_metaheuristic_variance.csv", "w", newline="") as f:
                wr = csv.DictWriter(f, fieldnames=list(meta_rows[0].keys()))
                wr.writeheader(); wr.writerows(meta_rows)
        print(f"\nsaved outputs/m24_seed_distributions.csv")


if __name__ == "__main__":
    main()
