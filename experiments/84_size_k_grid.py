"""M56 -- E1a WAS CONFOUNDED: SIZE AND K WERE THE SAME VARIABLE. DISENTANGLE THEM.

A reviewer's catch, and it is airtight on logic alone -- no experiment needed to CONCEDE it,
only to repair it.

exp80's scale sweep (E1a) holds the queue's TOTAL requested kW fixed and sweeps the request
size. But then

        K = total / size            exactly.

So K fell 289 -> 17 across that sweep, and size and count are PERFECTLY anti-correlated by
construction. Which means

        Spearman(size, spread) = -0.455        and
        Spearman(K,    spread) = +0.455

are not two findings. They are one number wearing two labels, and "lumpiness destroys the
lottery" is observationally indistinguishable from "fewer requests, less lottery" -- which is
close to trivial (at K=17 there are few distinct feasible subsets, and the p95/p5 of 200 draws
from a tiny outcome space compresses to 1.00x mechanically). E1a did not identify a causal
variable. It relabelled K.

THE REPAIR. A 2-D grid: sweep size and K INDEPENDENTLY and let the total float. Then

    * reading DOWN a column  (K fixed, size varies)   isolates SIZE.
    * reading ACROSS a row   (size fixed, K varies)   isolates COUNT.

If the size effect survives at fixed K, E1a's claim is real and was merely badly measured. If
it vanishes, the claim was K all along and E1b -- which holds total, mean AND K fixed and varies
only the inequality (CV) -- is the only clean granularity result we have, and it becomes the
spine.

ALSO REPAIRED HERE: "coin flip" was a binary indicator on `0 < seat rate < 1`, which counts an
applicant seated in 197 of 200 orders as a coin flip. That number was load-bearing and it was
soft. This file reports the seat-rate DISTRIBUTION -- deciles, the fraction in [0.2, 0.8], and
the mean binary entropy H(rate) -- and the headline metric is now mean entropy, which is 0 for
a fate the grid decides and 1 for a true coin flip.

Usage:
  python experiments/84_size_k_grid.py --save
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

_m = __import__("75_milp_maximality")
_x = __import__("78_corpus_law")
_g = __import__("80_granularity")

OUT = Path(__file__).resolve().parents[1] / "outputs"

# Sizes stay inside the band where a request is seatable ALONE on Ckt7 (exp82: 199/199 at
# 20 kW, 184/199 at 75 kW, 7/199 at 100 kW). Above ~75 kW the cell degenerates to "nothing is
# seatable", which is the U's right limb -- a real regime, but not one that can inform a
# size-vs-count contrast, because there the count cannot matter either.
SIZES_KW = [6.0, 12.0, 20.0, 30.0, 50.0, 75.0]
KS = [30, 60, 120]


def binary_entropy(p):
    """H(p) in bits. 0 when the grid decides the fate, 1 at a true coin flip."""
    p = np.clip(np.asarray(p, dtype=float), 1e-12, 1 - 1e-12)
    return float(np.mean(-p * np.log2(p) - (1 - p) * np.log2(1 - p)))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--feeder", choices=["ckt7", "ckt24"], default="ckt7")
    ap.add_argument("--sizes", type=float, nargs="+", default=SIZES_KW)
    ap.add_argument("--ks", type=int, nargs="+", default=KS)
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1])
    ap.add_argument("--permutations", type=int, default=100)
    ap.add_argument("--n-jobs", type=int, default=8)
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()

    if a.feeder == "ckt7":
        mod = __import__("68_ckt7_gldf_headline")
    else:
        mod = __import__("64_ckt24_gldf_bound")
    vmin, vmax, load = mod.VMIN_PU, mod.VMAX_PU, mod.LOADING_MAX_PCT
    ctx = mod.build_network()

    print(f"\nM56 -- the (size, K) GRID on {a.feeder.upper()}: total kW FLOATS, so size and")
    print("       count are finally independent variables.\n")
    print(f"  sizes {a.sizes} kW  x  K {a.ks}  x  seeds {a.seeds}")
    print(f"  {a.permutations} arrival orders per cell\n")

    rows = []
    for s in a.sizes:
        for k in a.ks:
            for seed in a.seeds:
                t0 = time.time()
                f = _x.Feeder.from_ctx(a.feeder, ctx, _g.const_sizer(s), vmin, vmax, load)
                projects = _x.build_projects(f, k, seed)
                by_id = {p.id: abs(p.p_mw) * 1000 for p in projects}

                feas = _x.solo_feasible(ctx, projects, f, a.n_jobs)
                reach_kw = sum(by_id[i] for i in feas)

                ref = _m.fcfs_accept_sets(ctx, projects, f, [_x.REF_WALK_SEED], a.n_jobs)[0]
                blocked = sorted(feas - set(ref))

                walks = _x.fcfs_walks(ctx, projects, f, a.permutations, a.n_jobs)
                kws = np.array([w[0] for w in walks])
                p5, p50, p95 = np.percentile(kws, [5, 50, 95])

                seat = np.stack([w[2] for w in walks])          # (P, K) bool
                rate = seat.mean(axis=0)

                ids, mw, _i = _m.certified_allocation(ctx, projects, f, f,
                                                      n_jobs=a.n_jobs, verbose=False)
                y_kw = mw * 1000

                if p95 <= 0:
                    spread, med_pct = 1.0, 100.0
                else:
                    spread = p95 / p5 if p5 > 0 else float("inf")
                    med_pct = 100 * p50 / y_kw if y_kw else np.nan

                rows.append(dict(
                    feeder=a.feeder, size_kw=s, k=k, seed=seed,
                    requested_kw=float(sum(by_id.values())),
                    reachable_kw=reach_kw, n_reachable=len(feas), yardstick_kw=y_kw,
                    n_blocked=len(blocked),
                    fcfs_p5=p5, fcfs_median=p50, fcfs_p95=p95,
                    spread=spread, median_pct=med_pct,
                    # --- the IDENTITY lottery, reported honestly ---
                    entropy=binary_entropy(rate),               # <-- the new headline
                    frac_20_80=float(((rate >= 0.2) & (rate <= 0.8)).mean()),
                    frac_open=float(((rate > 0.0) & (rate < 1.0)).mean()),   # the OLD metric
                    rate_always=float((rate >= 0.999).mean()),
                    rate_never=float((rate <= 0.001).mean()),
                    dt=time.time() - t0))
                r = rows[-1]
                print(f"  size {s:5.0f} kW  K={k:<4d} s={seed}  reach {len(feas):3d}/{k:<3d}"
                      f"  achiev {y_kw:7.1f} kW  blocked {r['n_blocked']:3d}"
                      f" | spread {spread:5.2f}x  med {med_pct:5.1f}%"
                      f" | H {r['entropy']:.3f}  in[.2,.8] {100*r['frac_20_80']:5.1f}%"
                      f"  (old 'coinflip' {100*r['frac_open']:5.1f}%)  [{r['dt']:.0f}s]")

    d = pd.DataFrame(rows)
    if a.save:
        OUT.mkdir(exist_ok=True)
        d.to_csv(OUT / f"m56_size_k_{a.feeder}.csv", index=False)

    # ----------------------------------------------------------------- the contrast
    print("\n" + "=" * 96)
    print("  DOES SIZE STILL MATTER ONCE K IS HELD FIXED?\n")
    med = d.groupby(["size_kw", "k"]).median(numeric_only=True).reset_index()

    print(f"  {'':>10}" + "".join(f"{'K=' + str(k):>22}" for k in a.ks))
    print(f"  {'size':>10}" + "".join(f"{'spread   H':>22}" for _ in a.ks))
    print("  " + "-" * (10 + 22 * len(a.ks)))
    for s in a.sizes:
        cells = []
        for k in a.ks:
            r = med[(med.size_kw == s) & (med.k == k)]
            cells.append(f"{r.spread.iloc[0]:9.2f}x {r.entropy.iloc[0]:8.3f}"
                         if len(r) else " " * 20)
        print(f"  {s:>7.0f}kW" + "".join(f"{c:>22}" for c in cells))

    print("\n  WITHIN-COLUMN (K fixed -> the SIZE effect, uncontaminated):")
    for k in a.ks:
        sub = d[d.k == k]
        if len(sub) > 3 and sub.spread.replace([np.inf], np.nan).notna().sum() > 3:
            ss = sub.replace([np.inf], np.nan).dropna(subset=["spread"])
            r1, p1 = spearmanr(ss.size_kw, ss.spread)
            r2, p2 = spearmanr(sub.size_kw, sub.entropy)
            print(f"    K={k:<4d}  Spearman(size, spread) = {r1:+.3f} p={p1:.4f}"
                  f"   |   Spearman(size, H) = {r2:+.3f} p={p2:.4f}")

    print("\n  WITHIN-ROW (size fixed -> the COUNT effect, uncontaminated):")
    for s in a.sizes:
        sub = d[d.size_kw == s]
        if len(sub) > 3:
            ss = sub.replace([np.inf], np.nan).dropna(subset=["spread"])
            if len(ss) > 3:
                r1, p1 = spearmanr(ss.k, ss.spread)
                r2, p2 = spearmanr(sub.k, sub.entropy)
                print(f"    {s:>5.0f}kW  Spearman(K, spread) = {r1:+.3f} p={p1:.4f}"
                      f"   |   Spearman(K, H) = {r2:+.3f} p={p2:.4f}")

    print("\n  If the within-column size effect is null and the within-row K effect is not,")
    print("  E1a measured K, and only E1b (CV at fixed total/mean/K) survives.\n")

    if a.save:
        print(f"  saved -> outputs/m56_size_k_{a.feeder}.csv")


if __name__ == "__main__":
    main()
