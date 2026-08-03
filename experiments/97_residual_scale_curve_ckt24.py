"""M69 -- THE SEGREGATION RESIDUAL vs REQUEST GRANULARITY ON CKT24 (the thermal feeder).

exp94 (M66) mapped Ckt7's granularity axis: holding the queue's total requested fixed and
varying only the individual request SIZE, the residual (joint - best study order) rose from
~10% at 12-75 kW to +58% at 100 kW -- coarser requests, each able to flip feasibility on their
own, amplify the irreducible cost. exp96 (M68) then showed Ckt24's residual stays ~0 across the
OVERSUBSCRIPTION axis. This file completes the map: does coarse granularity, the amplifier on
Ckt7, produce any residual on the one-way thermal feeder?

A "flat 0 at every granularity" was expected -- and the sweep FALSIFIES it, in the same way
exp96 (the oversubscription axis) did, and for the same reason. Ckt24's binding constraint is
NOT fixed: at FINE granularity the substation transformer (thermal) binds, whose loading depends
only on AGGREGATE net PV, so max-PV = max-relief = max-enabled-load and a generation-first order
is optimal -- residual 0. At COARSE granularity PV becomes OVERVOLTAGE-limited (only 44% of
buses seat a 100 kW PV solo), and overvoltage coupling is TWO-WAY: a co-located load suppresses
voltage and admits PV that was infeasible alone, so joint co-study seats a PV+load combination
no order recovers. Coarsening the request therefore does the same thing here as raising
oversubscription -- it shifts the binding constraint from thermal (one-way) to voltage (two-way)
-- and the residual turns on.

The curve is a BAND, not a ramp, because two things must both hold for the residual: PV must be
voltage-limited (coarse enough), AND the queue must be dense enough to contain complementary
PV<->load pairs. At 150 kW the first holds but the second fails -- a 150 kW PV barely seats at
all (PV_solo collapses toward 0) and K=20 is too sparse for pairs -- so the residual falls back
to 0. The peak sits at 100 kW, where PV is voltage-limited yet K=30 still supplies pairs.

cost-vs-parallel is reported alongside -- it is the recoverable M60 cost. And each row's
oversubscription is printed, because holding total fixed does NOT hold it fixed on Ckt24: finer
PV packs closer to the (large) hosting ceiling, so PV_solo rises and oversubscription falls as
size falls -- which is exactly why the fine end sits in the thermal, residual-0 regime.

Total requested held ~constant, monodisperse size per row, load_frac 0.5, Ckt24 @1.05 (forced).
three_way reused from exp96; residual = joint - best sequential order.

Run:
  .venv/bin/python experiments/97_residual_scale_curve_ckt24.py --save
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

_seg = __import__("88_ckt24_segregation")          # Ckt24Feeder, build_mixed
_over = __import__("96_residual_oversubscription_ckt24")   # three_way

OUT = Path(__file__).resolve().parents[1] / "outputs"

SIZES_KW = [30, 50, 100, 150]
TOTAL_KW = 3000.0        # oversubscribes the PV track at the coarse end (K=20 @150 kW ~3x)
SETPOINT = 1.05          # FORCED -- Ckt24's only feasible setpoint (exp88)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--setpoint", type=float, default=SETPOINT)
    ap.add_argument("--load-frac", type=float, default=0.5)
    ap.add_argument("--sizes-kw", type=float, nargs="+", default=SIZES_KW)
    ap.add_argument("--total-kw", type=float, default=TOTAL_KW)
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2, 3])
    ap.add_argument("--n-jobs", type=int, default=8)
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()

    print("\n  M69 -- SEGREGATION RESIDUAL vs REQUEST SCALE  (Ckt24 @%.2f, load %.0f%%)\n"
          % (a.setpoint, 100 * a.load_frac))
    print(f"  total requested held ~= {a.total_kw:.0f} kW, K = total/size; monodisperse per row.")
    print("  residual ~0 while fine PV is thermal-limited; positive where coarse PV turns")
    print("  overvoltage-limited (two-way) with enough request density to form pairs.\n")

    f = _seg.Ckt24Feeder.at_setpoint(a.setpoint)

    hdr = (f"  {'size':>5} {'K':>4} | {'PV solo':>8} {'oversub':>8} | "
           f"{'vs parallel':>12} | {'residual %: med':>15} {'min':>6} {'max':>6}")
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))

    rows = []
    for s in a.sizes_kw:
        K = max(2, int(round(a.total_kw / s)))
        per = []
        for seed in a.seeds:
            t0 = time.time()
            projects = _seg.build_mixed(f, K, seed, a.load_frac, s)
            pv_req = sum(abs(p.p_mw) for p in projects if p.p_mw < 0) * 1000
            r = _over.three_way(f, projects, a.n_jobs)
            oversub = (pv_req / r["pv_alone_kw"]) if r["pv_alone_kw"] > 0 else float("inf")
            r.update(setpoint=a.setpoint, load_frac=a.load_frac, size_kw=s, k=K, seed=seed,
                     total_kw=a.total_kw, pv_requested_kw=pv_req, oversub_ratio=oversub,
                     dt=time.time() - t0)
            rows.append(r)
            per.append(r)
            if a.save:
                OUT.mkdir(exist_ok=True)
                pd.DataFrame(rows).to_csv(OUT / "m69_residual_scale_ckt24.csv", index=False)

        res = np.array([p["residual_vs_sequential_pct"] for p in per], float)
        res = res[~np.isnan(res)]
        parg = np.array([p["cost_vs_parallel_pct"] for p in per], float)
        parg = parg[~np.isnan(parg)]
        pv_solo_m = np.median([p["pv_alone_kw"] for p in per])
        ov = np.median([p["oversub_ratio"] for p in per if np.isfinite(p["oversub_ratio"])] or [np.nan])
        if len(res):
            pstr = (f"{np.median(parg):>+10.0f}%" if len(parg) else f"{'--':>11}")
            print(f"  {s:>4.0f} {K:>4} | {pv_solo_m:>8.0f} {ov:>7.2f}x | {pstr:>12} | "
                  f"{np.median(res):>+14.1f}% {np.min(res):>+5.1f} {np.max(res):>+5.1f}")

    d = pd.DataFrame(rows)
    if a.save:
        OUT.mkdir(exist_ok=True)
        d.to_csv(OUT / "m69_residual_scale_ckt24.csv", index=False)

    print("\n" + "=" * 78)
    print("  The residual as a function of request scale (Ckt24):")
    for s in a.sizes_kw:
        sub = d[d.size_kw == s].residual_vs_sequential_pct.replace([np.inf, -np.inf], np.nan).dropna()
        if len(sub):
            bar = "#" * int(round(max(0.0, sub.median()) / 2)) or "."
            print(f"    {s:>4.0f} kW : residual {sub.median():>+6.1f}%  {bar}")
    print("\n  Ckt7 (exp94) at the same knob: +10% (fine) rising monotonically to +58% at 100 kW.")
    print("  Ckt24 is a BAND: ~0 while fine PV is thermal-limited (one-way), a positive peak at")
    print("  100 kW where coarse PV turns overvoltage-limited (two-way) with enough pairs, back")
    print("  to ~0 at 150 kW where PV barely seats. Same knob; the residual tracks voltage-binding.\n")
    if a.save:
        print("  saved -> outputs/m69_residual_scale_ckt24.csv")


if __name__ == "__main__":
    main()
