"""M89 -- DISPERSION: WHAT THE RESIDUALS LOOK LIKE OVER TWENTY SEEDS, NOT FOUR

THE OBJECTION
-------------
Four seeds, six draws at some cells, two-cell points labelled "indicative", median-maximum
ranges, and no uncertainty quantification anywhere. Both referee reports call the empirical
reporting below the bar, and one adds the obvious retort: the authors say compute is cheap, so
run enough seeds to put an interval on every number.

WHAT THIS FILE IS
-----------------
Not a new measurement. exp109 already computes both residuals for every family; this reads its
output at twenty seeds per cell and reports what four seeds could not support:

  median with a bootstrap 95% interval   -- the interval on the statistic the paper quotes
  interquartile range                    -- spread, which a median-maximum range hides
  share of draws with a positive residual -- the claim "positive in 8 of 9 cells" as a rate
  sign test against zero                  -- is the residual distinguishable from zero at all

The medians are the paper's numbers, so a wide interval does not falsify anything; it bounds
what may be claimed. Two things would matter. A family whose interval straddles zero cannot
carry a "the residual is nonzero here" sentence. And a family whose R* interval excludes zero
is one where re-study demonstrably does not recover everything -- the paper's whole positive
result, stated with dispersion for the first time.

Run (after exp109 has been re-run at twenty seeds):
  uv run python experiments/115_dispersion.py --inputs m80_alternating_rounds.csv \
      m82_colocated_alternating.csv --save
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

OUT = Path(__file__).resolve().parents[1] / "outputs"


def boot_ci(x, stat=np.median, n_boot=20000, alpha=0.05, seed=0):
    """Percentile bootstrap interval. Reported to two decimals only because the underlying
    quantity is quantized to one request (100 kW), which is coarser than the interval."""
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    if len(x) == 0:
        return float("nan"), float("nan"), float("nan")
    if len(x) == 1:
        return float(stat(x)), float(x[0]), float(x[0])
    rng = np.random.default_rng(seed)
    draws = rng.choice(x, size=(n_boot, len(x)), replace=True)
    s = stat(draws, axis=1)
    return float(stat(x)), float(np.percentile(s, 100 * alpha / 2)), \
        float(np.percentile(s, 100 * (1 - alpha / 2)))


def sign_p(x, tol=1e-9):
    """Two-sided exact sign test of 'the residual is zero', over draws that are not ties.

    Every residual is non-negative by construction (the joint optimum dominates any segregated
    one), so this is a one-sided question written two-sidedly: with no negative draws possible,
    p is simply the chance that all n non-tied draws land positive, 2^-n * 2.
    """
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    nz = int((x > tol).sum())
    n = len(x)
    if n == 0 or nz == 0:
        return n, 0, float("nan")
    return n, nz, float(min(1.0, 2.0 ** (-(nz)) * 2))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--inputs", nargs="+", default=["m80_alternating_rounds.csv",
                                                    "m82_colocated_alternating.csv"])
    ap.add_argument("--out", default="m89_dispersion.csv")
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()

    frames = []
    for fn in a.inputs:
        p = OUT / fn
        if not p.exists():
            print(f"  (skipping missing {fn})")
            continue
        d = pd.read_csv(p)
        if "placement" not in d.columns:
            d["placement"] = "random"
        # The SMART-DS families encode placement in the family name and are built co-located
        # whatever --placement said, so the column would otherwise mislabel them.
        d.loc[d.family.str.endswith("_colocated"), "placement"] = "colocated"
        d.loc[d.family.str.endswith("_random"), "placement"] = "random"
        d.loc[d.family == "spur", "placement"] = "colocated"
        d["source"] = fn
        frames.append(d)
    if not frames:
        raise SystemExit("no inputs found")
    d = pd.concat(frames, ignore_index=True)
    # A cell is identified by feeder, placement, queue size and seed. Re-runs (a family split
    # across files, or a repeated invocation) must not be counted twice; the last file listed
    # wins, so pass the authoritative one last.
    before = len(d)
    d = d.drop_duplicates(["family", "placement", "k", "seed"], keep="last")
    if len(d) < before:
        print(f"  (dropped {before - len(d)} duplicate cells across inputs)")

    print("\n  M89 -- RESIDUAL DISPERSION OVER SEEDS\n")
    print("  R1 = joint - best single-pass sequential order;  R* = joint - alternation fixed")
    print("  point. Intervals are percentile bootstrap on the median, 20,000 resamples.\n")
    hdr = (f"  {'family':>18} {'place':>9} {'n':>3} | {'R1 % median [95% CI]':>26} "
           f"{'IQR':>11} | {'R* % median [95% CI]':>26} {'R*>0':>7}")
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))

    rows = []
    for (fam, place), g in d.groupby(["family", "placement"], sort=False):
        r1 = g.residual_singlepass_pct.values
        rs = g.residual_alt_pct.values
        m1, lo1, hi1 = boot_ci(r1)
        ms, los, his = boot_ci(rs)
        q1, q3 = np.percentile(r1[np.isfinite(r1)], [25, 75])
        n1, pos1, p1 = sign_p(r1)
        ns, poss, ps = sign_p(rs)
        rows.append(dict(family=fam, placement=place, n=len(g),
                         r1_median=m1, r1_lo=lo1, r1_hi=hi1, r1_iqr_lo=q1, r1_iqr_hi=q3,
                         r1_positive=pos1, r1_sign_p=p1,
                         rstar_median=ms, rstar_lo=los, rstar_hi=his,
                         rstar_positive=poss, rstar_sign_p=ps,
                         joint_kw_median=float(np.median(g.joint_kw))))
        print(f"  {fam:>18} {place:>9} {len(g):>3d} | "
              f"{m1:>8.1f}  [{lo1:>5.1f}, {hi1:>5.1f}]     "
              f"[{q1:>4.1f},{q3:>5.1f}] | "
              f"{ms:>8.1f}  [{los:>5.1f}, {his:>5.1f}]     {poss:>2d}/{ns:<2d}")

    out = pd.DataFrame(rows)
    print("\n  Families whose R* interval excludes zero (re-study provably leaves capacity):")
    hit = out[(out.rstar_lo > 0) & np.isfinite(out.rstar_lo)]
    if len(hit):
        for _, r in hit.iterrows():
            print(f"    {r.family} ({r.placement}): R* = {r.rstar_median:.1f}% "
                  f"[{r.rstar_lo:.1f}, {r.rstar_hi:.1f}], {int(r.rstar_positive)} of {r.n} draws")
    else:
        print("    none")
    print("\n  Families whose R1 interval straddles zero (no residual claim is supportable):")
    miss = out[(out.r1_lo <= 0) | ~np.isfinite(out.r1_lo)]
    print("    " + (", ".join(f"{r.family} ({r.placement})" for _, r in miss.iterrows())
                    if len(miss) else "none"))
    print()
    if a.save:
        OUT.mkdir(exist_ok=True)
        out.to_csv(OUT / a.out, index=False)
        print(f"  saved -> outputs/{a.out}\n")


if __name__ == "__main__":
    main()
