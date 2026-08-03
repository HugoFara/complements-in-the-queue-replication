"""M66 -- THE SEGREGATION RESIDUAL AS A FUNCTION OF REQUEST SCALE  (Ckt7).

M63 measured a +20% residual (joint - best study order) at a uniform 100 kW request size,
K=17. M65 then found it VANISHES (median 0%) once request sizes are drawn from the CA Rule 21
empirical distribution (median ~6 kW): a queue of tiny requests is near-matroid, so greedy,
parallel, sequential and joint all pack the same set and there is nothing for co-study to
recover. The two results are not a contradiction -- they are two points on one curve, and this
file measures the curve.

THE CONTROL. The confound the reviewer flagged is `total = size x K`. Here we hold the queue's
OVERSUBSCRIPTION roughly constant -- total requested kW ~= a fixed multiple of the feeder's
hosting capacity -- and vary only the individual request SIZE, setting K = round(TOTAL / size)
for each. So every cell is the same feeder under the same pressure, differing only in the
GRANULARITY of the requests. The residual then isolates the effect of request scale.

Requests are monodisperse at each size (exp83's `_build_mixed`, load_frac 0.5, the peak mix),
so "size" is unambiguous; the three-way test (parallel / sequential both orders / joint) is
M63's, reused verbatim from exp91/exp93. Ckt7 @1.04 (the honest setpoint).

THE PREDICTION, stated before running: the residual is ~0 for requests that are a small
fraction of hosting capacity (granular, near-matroid), rises as each request becomes able to
individually flip feasibility, and is the number to report as the paper's magnitude -- a curve
over request scale, not a single percentage. Where it peaks, and how high, is the result.

Run:
  .venv/bin/python experiments/94_residual_scale_curve.py --save
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

_c = __import__("68_ckt7_gldf_headline")
_s = __import__("83_setpoint_sweep")              # SlackFreeFeeder, _build_mixed (monodisperse)
_h = __import__("93_hetero_residual_ckt7")        # three_way (M63's residual, one queue)

OUT = Path(__file__).resolve().parents[1] / "outputs"

# request as ~% of Ckt7 hosting (~1.3 MW): 12->0.9%, 30->2.3%, 50->3.8%, 100->7.7%, 150->11.5%
SIZES_KW = [12, 20, 30, 50, 75, 100, 150]
TOTAL_KW = 2000.0        # fixed oversubscription: ~1.3-1.5x Ckt7's mixed-queue hosting


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--setpoint", type=float, default=1.04)
    ap.add_argument("--load-frac", type=float, default=0.5)
    ap.add_argument("--sizes-kw", type=float, nargs="+", default=SIZES_KW)
    ap.add_argument("--total-kw", type=float, default=TOTAL_KW)
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2, 3, 4, 5])
    ap.add_argument("--n-jobs", type=int, default=8)
    ap.add_argument("--out", default="m66_residual_scale_ckt7.csv",
                    help="output CSV; the default holds the published 1.04 curve, so a "
                         "run at another setpoint must name its own")
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()

    print("\n  M66 -- SEGREGATION RESIDUAL vs REQUEST SCALE  (Ckt7 @%.2f, load %.0f%%)\n"
          % (a.setpoint, 100 * a.load_frac))
    print(f"  oversubscription held ~constant: total requested ~= {a.total_kw:.0f} kW, K = total/size.")
    print(f"  monodisperse size per row; {len(a.seeds)} seeds each; residual = joint - best order.\n")

    f = _s.SlackFreeFeeder.at_setpoint(a.setpoint, _c.VMIN_PU, _c.VMAX_PU, _c.LOADING_MAX_PCT)

    hdr = (f"  {'size':>5} {'K':>4} | {'joint kW':>9} {'size/joint':>10} | "
           f"{'parallel gap':>13} | {'residual %: med':>15} {'min':>6} {'max':>6}")
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))

    rows = []
    for s in a.sizes_kw:
        K = max(2, int(round(a.total_kw / s)))
        per = []
        for seed in a.seeds:
            t0 = time.time()
            projects = _s._build_mixed(f, K, seed, a.load_frac, s)
            r = _h.three_way(f, projects, a.n_jobs)
            r.update(setpoint=a.setpoint, load_frac=a.load_frac, size_kw=s, k=K, seed=seed,
                     requested_kw=sum(abs(p.p_mw) for p in projects) * 1000, dt=time.time() - t0)
            rows.append(r)
            per.append(r)
            if a.save:
                OUT.mkdir(exist_ok=True)
                pd.DataFrame(rows).to_csv(OUT / a.out, index=False)

        res = np.array([p["residual_vs_sequential_pct"] for p in per], float)
        res = res[~np.isnan(res)]
        parg = np.array([p["cost_vs_parallel_pct"] for p in per], float)
        parg = parg[~np.isnan(parg)]
        jm = np.median([p["joint_kw"] for p in per])
        size_frac = 100 * s / jm if jm > 0 else float("nan")
        if len(res):
            print(f"  {s:>4.0f} {K:>4} | {jm:>8.0f}  {size_frac:>9.1f}% | "
                  f"{np.median(parg):>+7.0f}% (max{np.max(parg):>+4.0f}) | "
                  f"{np.median(res):>+14.1f}% {np.min(res):>+5.1f} {np.max(res):>+5.1f}")

    d = pd.DataFrame(rows)
    if a.save:
        OUT.mkdir(exist_ok=True)
        d.to_csv(OUT / a.out, index=False)

    print("\n" + "=" * 82)
    print("  The residual as a function of request scale (Ckt7):")
    for s in a.sizes_kw:
        sub = d[d.size_kw == s].residual_vs_sequential_pct.replace([np.inf, -np.inf], np.nan).dropna()
        if len(sub):
            bar = "#" * int(round(max(0.0, sub.median()) / 2))
            print(f"    {s:>4.0f} kW : {sub.median():>+6.1f}%  {bar}")
    print("\n  This is the magnitude-bearing statement: the segregation cost co-study recovers")
    print("  is a function of how large each request is relative to hosting capacity, not a")
    print("  single percentage. Residential-scale queues (M65) sit at the flat left end.\n")
    if a.save:
        print(f"  saved -> outputs/{a.out}")


if __name__ == "__main__":
    main()
