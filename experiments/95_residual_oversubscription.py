"""M67 -- THE SEGREGATION RESIDUAL vs OVERSUBSCRIPTION (fixed granularity, Ckt7).

M66 held the queue's total requested constant and varied request size, and found the residual
(joint - best study order) rises with coarseness. But it also overturned M65: at controlled
oversubscription the residual is +8-12% even at 12 kW, so M65's "it vanishes for small
requests" was an artifact of M65's LOW total (fixed K x tiny sizes), not the size itself.

This isolates the other variable. Fix the request size at 30 kW (fine enough that granularity
is not the story) and sweep the queue's TOTAL requested -- i.e. how far the generation track is
oversubscribed beyond what the feeder can host solo. The prediction the M65/M66 reconciliation
implies: the residual is ~0 while the PV track fits under its own solo limit, and turns on and
grows once the track is oversubscribed, because only then does the sequential PV-first study
reject generation that a co-located load would have supported.

If that is the shape, the two-variable story is complete and honest:
    the residual is real, and its magnitude is a function of (oversubscription x granularity) --
    not a single percentage, and not an artifact of the 100 kW unit.

Ckt7 @1.04, load_frac 0.5, monodisperse 30 kW; three-way test reused from exp91/exp93.
Oversubscription is reported as (PV requested) / (PV certified solo) per cell, self-contained.

Run:
  .venv/bin/python experiments/95_residual_oversubscription.py --save
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
_s = __import__("83_setpoint_sweep")
_h = __import__("93_hetero_residual_ckt7")        # three_way

OUT = Path(__file__).resolve().parents[1] / "outputs"

SIZE_KW = 30.0
TOTALS_KW = [700, 900, 1300, 1700, 2100, 2500]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--setpoint", type=float, default=1.04)
    ap.add_argument("--load-frac", type=float, default=0.5)
    ap.add_argument("--size-kw", type=float, default=SIZE_KW)
    ap.add_argument("--totals-kw", type=float, nargs="+", default=TOTALS_KW)
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2, 3, 4, 5])
    ap.add_argument("--n-jobs", type=int, default=8)
    ap.add_argument("--out", default="m67_residual_oversub_ckt7.csv",
                    help="output CSV; the default holds the published 1.04 curve, so a "
                         "run at another setpoint must name its own")
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()

    print("\n  M67 -- RESIDUAL vs OVERSUBSCRIPTION  (Ckt7 @%.2f, %.0f kW requests, load %.0f%%)\n"
          % (a.setpoint, a.size_kw, 100 * a.load_frac))
    print("  fixed granularity, sweep total requested. oversubscription = PV requested / PV solo.\n")

    f = _s.SlackFreeFeeder.at_setpoint(a.setpoint, _c.VMIN_PU, _c.VMAX_PU, _c.LOADING_MAX_PCT)

    hdr = (f"  {'total':>6} {'K':>4} | {'PV req':>7} {'PV solo':>8} {'oversub':>8} | "
           f"{'parallel':>9} | {'residual %: med':>15} {'min':>6} {'max':>6}")
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))

    rows = []
    for total in a.totals_kw:
        K = max(2, int(round(total / a.size_kw)))
        per = []
        for seed in a.seeds:
            t0 = time.time()
            projects = _s._build_mixed(f, K, seed, a.load_frac, a.size_kw)
            pv_req = sum(abs(p.p_mw) for p in projects if p.p_mw < 0) * 1000
            r = _h.three_way(f, projects, a.n_jobs)
            oversub = (pv_req / r["pv_alone_kw"]) if r["pv_alone_kw"] > 0 else float("inf")
            r.update(setpoint=a.setpoint, load_frac=a.load_frac, size_kw=a.size_kw, k=K,
                     seed=seed, total_kw=total, pv_requested_kw=pv_req, oversub_ratio=oversub,
                     dt=time.time() - t0)
            rows.append(r)
            per.append(r)
            if a.save:
                OUT.mkdir(exist_ok=True)
                pd.DataFrame(rows).to_csv(OUT / a.out, index=False)

        res = np.array([p["residual_vs_sequential_pct"] for p in per], float)
        res = res[~np.isnan(res)]
        parg = np.array([p["cost_vs_parallel_pct"] for p in per], float)
        parg = parg[~np.isnan(parg)]
        pv_req_m = np.median([p["pv_requested_kw"] for p in per])
        pv_solo_m = np.median([p["pv_alone_kw"] for p in per])
        ov = np.median([p["oversub_ratio"] for p in per if np.isfinite(p["oversub_ratio"])] or [np.nan])
        if len(res):
            print(f"  {total:>6.0f} {K:>4} | {pv_req_m:>7.0f} {pv_solo_m:>8.0f} {ov:>7.2f}x | "
                  f"{np.median(parg):>+8.0f}% | {np.median(res):>+14.1f}% "
                  f"{np.min(res):>+5.1f} {np.max(res):>+5.1f}")

    d = pd.DataFrame(rows)
    if a.save:
        OUT.mkdir(exist_ok=True)
        d.to_csv(OUT / a.out, index=False)

    print("\n" + "=" * 80)
    print("  Residual vs oversubscription (Ckt7, 30 kW requests):")
    for total in a.totals_kw:
        sub = d[d.total_kw == total]
        r = sub.residual_vs_sequential_pct.replace([np.inf, -np.inf], np.nan).dropna()
        ov = sub.oversub_ratio.replace([np.inf, -np.inf], np.nan).dropna()
        if len(r):
            bar = "#" * int(round(max(0.0, r.median()) / 2))
            print(f"    oversub {ov.median():>4.2f}x (total {total:>4.0f} kW): {r.median():>+6.1f}%  {bar}")
    print("\n  If the residual is ~0 at oversub <= 1 and grows above it, the driver is confirmed:")
    print("  co-study only recovers capacity once the generation track is oversubscribed.\n")
    if a.save:
        print(f"  saved -> outputs/{a.out}")


if __name__ == "__main__":
    main()
