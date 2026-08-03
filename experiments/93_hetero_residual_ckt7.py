"""M65 -- THE RESIDUAL WITH HETEROGENEOUS REQUEST SIZES AT LARGER K  (Ckt7).

A reviewer's coarseness objection to §2: every headline cell is integer multiples of 100 kW
(400/300/700/1500) at K=17, because every request is 100 kW -- so the "capacity" is a count of
seated requests times 100, and every percentage is a ratio of small integers. That instrument
can establish the SIGN and EXISTENCE of the segregation residual, but not a magnitude.

This file re-measures the residual (JOINT - best sequential order = capacity no study order
recovers, M63) with the coarseness removed two ways:

  * HETEROGENEOUS SIZES. Each request draws its kW from the CA Rule 21 empirical distribution
    (exp73's `sample_kw`: real interconnection request sizes, median ~6 kW, mean ~12, a heavy
    tail to ~1 MW), not a fixed 100 kW. Certified capacities are then continuous, not multiples
    of a unit, and the residual is a real number rather than a ratio of counts.
  * LARGER K, and REPORTED AS A DISTRIBUTION. With a ~12 kW mean request, K must be ~100+ for a
    queue to saturate Ckt7 (~1-1.5 MW hostable) and make the knapsack bite -- below that, tiny
    requests all fit and there is nothing to contest (exp84's hump). We sweep many seeds at K
    in the saturating range and report the residual's DISTRIBUTION, not a single median.

Everything else is M63's three-way test on Ckt7 at the honest setpoint 1.04, reusing exp91's
`certify` and `feeder_with_anchor` verbatim (sequential = approve one queue, add it to the base
case, study the other; both orders; residual against the better one). The question: does the
+20%-median residual survive de-quantization, or was it an artifact of the 100 kW grid?

Run:
  .venv/bin/python experiments/93_hetero_residual_ckt7.py --save
"""
from __future__ import annotations

import argparse
import random
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

_c = __import__("68_ckt7_gldf_headline")          # Project, VMIN/VMAX/LOADING
_s = __import__("83_setpoint_sweep")              # SlackFreeFeeder
_e = __import__("73_empirical_request_distribution")   # sample_kw (CA Rule 21 empirical)
_seq = __import__("91_sequential_segregation")    # certify, feeder_with_anchor (M63)

OUT = Path(__file__).resolve().parents[1] / "outputs"


def build_hetero(f, k, seed, load_frac):
    """K requests: buses uniform over the candidate pool, SIZES from the CA empirical draw,
    signs set by load_frac. Three disjoint RNG streams (bus / size / sign) so the mix and the
    size draw never move a request's bus -- only its sign or magnitude."""
    rng_b = random.Random(seed)
    buses = [rng_b.choice(f.load_buses) for _ in range(k)]
    rng_sz = random.Random(seed + 20_000)
    sizes_kw = _e.sample_kw(rng_sz, k)
    rng_s = random.Random(seed + 10_000)
    idx = list(range(k))
    rng_s.shuffle(idx)
    is_load = set(idx[:int(round(load_frac * k))])
    return [_c.Project(id=f"r{i}", bus=int(b),
                       p_mw=(float(sizes_kw[i]) if i in is_load else -float(sizes_kw[i])) / 1000.0)
            for i, b in enumerate(buses)]


def three_way(f, projects, n_jobs):
    """M63's residual on one queue: parallel, sequential (both orders), joint."""
    pv = [p for p in projects if p.p_mw < 0]
    ld = [p for p in projects if p.p_mw > 0]

    f._proj = None
    pv_ids, pv_kw = _seq.certify(f, pv, n_jobs)
    f._proj = None
    ld_ids, ld_kw = _seq.certify(f, ld, n_jobs)
    seg_par = pv_kw + ld_kw

    f._proj = None
    _j, joint = _seq.certify(f, projects, n_jobs)

    f_pv = _seq.feeder_with_anchor(f, pv, pv_ids)
    _l2, ld2 = _seq.certify(f_pv, ld, n_jobs)
    seq_pv1 = pv_kw + ld2

    f_ld = _seq.feeder_with_anchor(f, ld, ld_ids)
    _p2, pv2 = _seq.certify(f_ld, pv, n_jobs)
    seq_ld1 = ld_kw + pv2

    best_seq = max(seq_pv1, seq_ld1)
    return dict(pv_alone_kw=pv_kw, load_alone_kw=ld_kw, seg_parallel_kw=seg_par,
                seq_pvfirst_kw=seq_pv1, seq_loadfirst_kw=seq_ld1, best_sequential_kw=best_seq,
                joint_kw=joint,
                cost_vs_parallel_pct=(100 * (joint / seg_par - 1) if seg_par > 0 else np.nan),
                residual_vs_sequential_pct=(100 * (joint / best_seq - 1) if best_seq > 0 else np.nan))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--setpoint", type=float, default=1.04)
    ap.add_argument("--load-frac", type=float, default=0.5)
    ap.add_argument("--ks", type=int, nargs="+", default=[80, 120])
    ap.add_argument("--seeds", type=int, nargs="+", default=list(range(16)))
    ap.add_argument("--n-jobs", type=int, default=8)
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()

    print("\n  M65 -- THE RESIDUAL WITH HETEROGENEOUS SIZES, LARGER K  (Ckt7 @%.2f, load %.0f%%)\n"
          % (a.setpoint, 100 * a.load_frac))
    print("  sizes: CA Rule 21 empirical (median ~6 kW, mean ~12, tail to ~1 MW), NOT 100 kW.")
    print(f"  {len(a.seeds)} seeds per K; residual = joint - best sequential order.\n")

    f = _s.SlackFreeFeeder.at_setpoint(a.setpoint, _c.VMIN_PU, _c.VMAX_PU, _c.LOADING_MAX_PCT)

    hdr = (f"  {'K':>4} | {'requested':>10} {'joint kW':>10} {'par-gap%':>9} "
           f"| {'residual %: min':>16} {'p25':>6} {'med':>6} {'p75':>6} {'max':>6} {'>0':>6}")
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))

    rows = []
    for K in a.ks:
        per = []
        for seed in a.seeds:
            t0 = time.time()
            projects = build_hetero(f, K, seed, a.load_frac)
            req_kw = sum(abs(p.p_mw) for p in projects) * 1000
            r = three_way(f, projects, a.n_jobs)
            r.update(setpoint=a.setpoint, load_frac=a.load_frac, k=K, seed=seed,
                     requested_kw=req_kw, dt=time.time() - t0)
            rows.append(r)
            per.append(r)
            if a.save:
                OUT.mkdir(exist_ok=True)
                pd.DataFrame(rows).to_csv(OUT / "m65_hetero_residual_ckt7.csv", index=False)

        res = np.array([p["residual_vs_sequential_pct"] for p in per], float)
        res = res[~np.isnan(res)]
        parg = np.array([p["cost_vs_parallel_pct"] for p in per], float)
        parg = parg[~np.isnan(parg)]
        med_req = np.median([p["requested_kw"] for p in per])
        med_joint = np.median([p["joint_kw"] for p in per])
        if len(res):
            q = np.percentile(res, [0, 25, 50, 75, 100])
            frac_pos = 100 * (res > 1e-6).mean()
            print(f"  {K:>4} | {med_req:>9.0f}  {med_joint:>9.0f}  "
                  f"{np.median(parg):>+8.0f}% | {q[0]:>+15.1f}% {q[1]:>+5.1f} {q[2]:>+5.1f} "
                  f"{q[3]:>+5.1f} {q[4]:>+5.1f} {frac_pos:>5.0f}%")

    d = pd.DataFrame(rows)
    if a.save:
        OUT.mkdir(exist_ok=True)
        d.to_csv(OUT / "m65_hetero_residual_ckt7.csv", index=False)

    print("\n" + "=" * 96)
    r = d.residual_vs_sequential_pct.replace([np.inf, -np.inf], np.nan).dropna()
    if len(r):
        print(f"  RESIDUAL over all {len(r)} heterogeneous draws: "
              f"median {r.median():+.1f}%, IQR [{r.quantile(.25):+.1f}, {r.quantile(.75):+.1f}]%, "
              f"max {r.max():+.1f}%, positive in {100*(r>1e-6).mean():.0f}%.")
        print(f"  (M63's 100 kW-grid result at K=17: median +20%. If this survives de-quantization,")
        print(f"   the residual is a magnitude, not an artifact of the unit.)")
    if a.save:
        print("\n  saved -> outputs/m65_hetero_residual_ckt7.csv")


if __name__ == "__main__":
    main()
