"""M47: how much REAL request pressure does it take before the queue starts to bind?

WHY THIS EXPERIMENT EXISTS
--------------------------
M43/M44 found a large arrival-order lottery on both real feeders. M46 showed it was
largely an artifact: those runs drew request sizes from a synthetic uniform[10,100] kW
band, and the real distribution (M45: median 6.1 kW, 96.8% under 20 kW) is ~5x smaller.
Re-run with real sizes, the lottery on Ckt24 does not shrink -- it VANISHES (1.00x
spread; 149/150 seated in every one of 200 arrival orders; FCFS already optimal).

So "FCFS is a lottery" is not a fact about feeders. It is a fact about feeders AT A
GIVEN REQUEST PRESSURE. The honest question -- and the one this sweep answers -- is:

    at what request pressure does a real feeder's queue begin to bind,
    and what does arrival order cost once it does?

That is the regime map again, but with an empirically-grounded x-axis. It is also
forward-looking rather than deflationary: PV penetration is rising, so "how many
requests until this bites" is the question a DNO actually has.

THE PRIMARY METRIC IS MODEL-FREE
--------------------------------
The spread of FCFS realized capacity across random arrival orders (p95/p5) requires NO
power-flow model, NO optimum, NO MILP -- just the AC oracle and a queue walk. That
matters, because M46 showed the MILP yardstick is UNRELIABLE in the many-small-requests
regime (on Ckt7-empirical the luckiest arrival order BEAT the "optimum" by 1.4%: the
re-linearization loop lands AC-infeasible and the repair drops requests).

So: `spread` is the headline. `% of optimum` is reported alongside but is SOFT -- it is
a ratio to a certified-but-loose lower bound, and is flagged wherever FCFS exceeds it.

Request sizes are always empirical here. Buses are drawn as in exps 60-72.
"""

from __future__ import annotations

import argparse
import multiprocessing as mp
import random
import time
from pathlib import Path

import numpy as np
import pandas as pd

_e = __import__("73_empirical_request_distribution")

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "outputs"

_CTX = None
_PROJ = None
_MOD = None


def _run_chunk(perm_seeds):
    """One worker: a block of arrival orders over the fixed request set."""
    mod, ctx, projects = _MOD, _CTX, _PROJ
    ids = [p.id for p in projects]
    k = len(projects)
    cache: dict[frozenset, tuple[bool, float]] = {}

    def feasible(trial):
        hit = cache.get(trial)
        if hit is None:
            ok, mw, _, _ = mod.evaluate(ctx, projects, set(trial))
            hit = (ok, mw)
            cache[trial] = hit
        return hit

    out = []
    for ps in perm_seeds:
        rng = random.Random(ps)
        order = list(range(k))
        rng.shuffle(order)
        acc, mw = frozenset(), 0.0
        for i in order:
            trial = acc | {ids[i]}
            ok, m = feasible(trial)
            if ok:
                acc, mw = trial, m
        out.append((mw * 1000, len(acc)))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--feeder", choices=["ckt7", "ckt24"], required=True)
    ap.add_argument("--ks", type=int, nargs="+", default=None,
                    help="queue lengths to sweep")
    ap.add_argument("--permutations", type=int, default=200,
                    help="p95/p5 at n=40 is a 2nd-order statistic and swings "
                         "wildly; the first sweep was non-monotonic above the "
                         "threshold purely from that. 200+ for a usable tail.")
    ap.add_argument("--seed", type=int, default=0, help="fixes the request set at each K")
    ap.add_argument("--n-jobs", type=int, default=8)
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()

    global _CTX, _PROJ, _MOD

    if a.feeder == "ckt7":
        exp = __import__("71_arrival_order_lottery")
        mod = __import__("68_ckt7_gldf_headline")
        ks = a.ks or [25, 50, 100, 150, 250, 400, 600]
        mk = lambda ctx, k: exp.build_projects_x(ctx, k, a.seed, replacement=True,
                                                 empirical=True)
    else:
        exp = __import__("72_ckt24_arrival_order")
        mod = __import__("64_ckt24_gldf_bound")
        ks = a.ks or [150, 300, 600, 1000, 1500]
        mk = lambda ctx, k: exp.build_projects_x(ctx, k, a.seed, empirical=True)

    print(f"M47 -- request-pressure sweep on {a.feeder.upper()}, EMPIRICAL request sizes")
    print(f"  K = {ks};  {a.permutations} arrival orders per K\n")

    ctx = mod.build_network()
    _CTX, _MOD = ctx, mod

    rows = []
    for k in ks:
        t0 = time.time()
        projects = mk(ctx, k)
        _PROJ = projects
        req_kw = sum(abs(p.p_mw) for p in projects) * 1000

        opt_ids, opt_mw, _g = exp.relinearized_optimum(ctx, projects, verbose=False)
        opt_kw = opt_mw * 1000

        seeds = list(range(a.permutations))
        chunks = [seeds[i::a.n_jobs] for i in range(a.n_jobs)]
        with mp.get_context("fork").Pool(a.n_jobs) as pool:
            got = pool.map(_run_chunk, [c for c in chunks if c])
        walks = [w for chunk in got for w in chunk]
        kws = np.array([w[0] for w in walks])
        ns = np.array([w[1] for w in walks])

        p5, p25, p50, p75, p95 = np.percentile(kws, [5, 25, 50, 75, 95])
        spread = p95 / p5 if p5 > 0 else float("inf")
        # p95/p5 is noisy at modest n; the IQR ratio and CV are stable and are what the
        # paper should quote. Both are reported so the tail claim can be checked.
        iqr_ratio = p75 / p25 if p25 > 0 else float("inf")
        cv = float(kws.std() / kws.mean()) if kws.mean() else float("nan")
        # model-free: what fraction of the QUEUE gets seated (needs no optimum at all)
        seat_frac = ns / k
        soft = kws.max() > opt_kw          # FCFS beat the "optimum" -> yardstick is loose

        rows.append(dict(k=k, requested_kw=req_kw, opt_kw=opt_kw, opt_n=len(opt_ids),
                         pressure=req_kw / opt_kw if opt_kw else np.nan,
                         fcfs_p5=p5, fcfs_median=p50, fcfs_p95=p95,
                         fcfs_min=kws.min(), fcfs_max=kws.max(),
                         spread_p95_p5=spread, iqr_ratio_p75_p25=iqr_ratio,
                         cv=cv,
                         median_pct_of_opt=100 * p50 / opt_kw if opt_kw else np.nan,
                         seated_frac_median=float(np.median(seat_frac)),
                         yardstick_soft=bool(soft), dt=time.time() - t0))
        if a.save:
            # incremental: a long sweep must never lose a completed K to a kill
            OUT.mkdir(exist_ok=True)
            pd.DataFrame(rows).to_csv(OUT / f"m47_pressure_sweep_{a.feeder}.csv", index=False)
            pd.DataFrame({"k": k, "fcfs_kw": kws, "fcfs_n": ns}).to_csv(
                OUT / f"m47_walks_{a.feeder}_k{k}.csv", index=False)

        r = rows[-1]
        flag = "  <-- FCFS BEAT THE OPTIMUM: yardstick loose" if soft else ""
        print(f"  K={k:<5d} req {req_kw:7.0f} kW  opt {opt_kw:7.0f} kW ({len(opt_ids):4d})  "
              f"pressure {r['pressure']:4.2f}x   FCFS p5 {p5:7.1f} p95 {p95:7.1f}  "
              f"SPREAD {spread:5.2f}x   seated {100*r['seated_frac_median']:5.1f}%"
              f"   [{r['dt']:.0f}s]{flag}")

    d = pd.DataFrame(rows)
    print("\n  THE MODEL-FREE ANSWER (no optimum, no MILP -- just FCFS outcomes):")
    print("    K      requested   pressure   FCFS spread (p95/p5)   queue seated")
    for r in rows:
        bar = "#" * min(40, int((r["spread_p95_p5"] - 1) * 20))
        print(f"    {r['k']:<6d} {r['requested_kw']:8.0f} kW  {r['pressure']:5.2f}x   "
              f"{r['spread_p95_p5']:6.2f}x  {bar:<40s} {100*r['seated_frac_median']:5.1f}%")

    if a.save:
        print(f"\n  saved -> outputs/m47_pressure_sweep_{a.feeder}.csv "
              f"(+ per-K raw walks, m47_walks_{a.feeder}_k*.csv)")


if __name__ == "__main__":
    main()
