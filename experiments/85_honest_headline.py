"""M57 -- THE HEADLINE GAIN WAS MEASURED TWICE OVER AGAINST A STRAW BASELINE.

A reviewer noticed that two of our own numbers cannot both be true:

    §9  joint allocation is worth  +129.8% to +518.9%  (Ckt24)  /  +43.4% to +267.6%  (Ckt7)
    §4  the MEDIAN arrival order already attains  72-100%  of achievable

72% of achievable caps the gain at +39%, not +519%. They describe the same feeder and the
same queue, so one of them is wrong. Both are -- §9 is, twice:

  INFLATOR 1: THE SIZE DISTRIBUTION. §9 comes from exp68/exp64, whose `build_projects` draws
  `rng.uniform(0.010, 0.100)` MW -- the uniform[10,100] kW sampler we RETRACTED in M45, and
  which we showed piles 228-313 kW onto single buses. §4 uses the CA Rule 21 empirical draw
  (70,665 real requests, median 6.1 kW). We changed the distribution everywhere we measured
  arrival order, and nowhere we measured the headline.

  INFLATOR 2: THE BASELINE IS ONE ARRIVAL ORDER. `gain_pct` in m42/m41 is computed against
  `fifo_kw` -- the natural queue order, a SINGLE draw. But M49 measured FCFS on Ckt7 swinging
  247-570 kW across orders. So the denominator is one sample from a distribution whose spread
  we ourselves published, and Ckt7 MODERATE/seed 0 drew 336.5 kW out of that range. Comparing
  an optimum against an unlucky draw is not a measurement of what optimisation buys; it is a
  measurement of how unlucky the draw was.

  The honest denominator is the arrival-order distribution: report the gain over the MEDIAN
  order (what a typical queue actually delivers), and over p95 (what a LUCKY queue delivers,
  i.e. the gain that survives even against a well-ordered queue).

THIS FILE re-runs both utility-scale headlines with the empirical sizes and reports the gain
against p5 / median / p95 of the arrival-order distribution, so the same table answers "what
does joint allocation buy over a typical queue" and "does it buy anything over the best queue".

Usage:
  python experiments/85_honest_headline.py --feeder ckt7 --save
  python experiments/85_honest_headline.py --feeder ckt24 --save
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

_m = __import__("75_milp_maximality")
_x = __import__("78_corpus_law")
_e = __import__("73_empirical_request_distribution")

OUT = Path(__file__).resolve().parents[1] / "outputs"

# exp68/exp64's own scenarios, so the queue is the one the headline was measured on -- only
# the SIZE distribution and the BASELINE change.
SCENARIOS = {"MODERATE": 150, "STRESS": 300}


def build_empirical(mod, ctx, k, seed):
    """exp68/64's bus draw VERBATIM (same RNG, same order, same replacement), with only the
    size distribution swapped to the CA Rule 21 empirical draw. Any change is therefore
    attributable to the distribution alone -- exp71/72's protocol."""
    import random
    base = mod.build_projects(ctx, k, seed)
    rng = random.Random(seed + 10_000)
    kws = _e.sample_kw(rng, len(base))
    return [mod.Project(id=p.id, bus=p.bus, p_mw=-float(kw) / 1000.0)
            for p, kw in zip(base, kws)]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--feeder", choices=["ckt7", "ckt24"], default="ckt7")
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    ap.add_argument("--permutations", type=int, default=200)
    ap.add_argument("--n-jobs", type=int, default=8)
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()

    if a.feeder == "ckt7":
        mod = __import__("68_ckt7_gldf_headline")
        old = {("MODERATE", 0): 237.3, ("MODERATE", 1): 41.8, ("MODERATE", 2): 209.7,
               ("STRESS", 0): 267.6, ("STRESS", 1): 82.5, ("STRESS", 2): 226.8}
    else:
        mod = __import__("64_ckt24_gldf_bound")
        old = {("MODERATE", 0): 129.8, ("MODERATE", 1): 317.1, ("MODERATE", 2): 176.2,
               ("STRESS", 0): 255.5, ("STRESS", 1): 518.9, ("STRESS", 2): 297.1}
    vmin, vmax, load = mod.VMIN_PU, mod.VMAX_PU, mod.LOADING_MAX_PCT
    ctx = mod.build_network()

    print(f"\nM57 -- {a.feeder.upper()}'s headline, re-measured honestly\n")
    print("  size distribution : CA Rule 21 empirical (M45), NOT uniform[10,100]")
    print(f"  FCFS baseline     : the DISTRIBUTION over {a.permutations} arrival orders,")
    print("                      NOT the single natural-order draw §9 used\n")

    hdr = (f"  {'scen':<9}{'seed':<5}{'K':<5}{'achiev':>9}{'FCFS p5':>9}{'median':>9}{'p95':>9}"
           f" | {'gain/med':>9}{'gain/p95':>9} | {'§9 said':>9}")
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))

    rows = []
    for scen, k in SCENARIOS.items():
        for seed in a.seeds:
            t0 = time.time()
            f = _x.Feeder.from_ctx(a.feeder, ctx, None, vmin, vmax, load)
            projects = build_empirical(mod, ctx, k, seed)

            walks = _x.fcfs_walks(ctx, projects, f, a.permutations, a.n_jobs)
            kws = np.array([w[0] for w in walks])
            p5, p50, p95 = np.percentile(kws, [5, 50, 95])

            _ids, mw, _i = _m.certified_allocation(ctx, projects, f, f,
                                                   n_jobs=a.n_jobs, verbose=False)
            y_kw = mw * 1000

            g_med = 100 * (y_kw / p50 - 1) if p50 > 0 else float("nan")
            g_p95 = 100 * (y_kw / p95 - 1) if p95 > 0 else float("nan")
            was = old[(scen, seed)]

            rows.append(dict(feeder=a.feeder, scenario=scen, seed=seed, k=k,
                             requested_kw=sum(abs(p.p_mw) * 1000 for p in projects),
                             yardstick_kw=y_kw, fcfs_p5=p5, fcfs_median=p50, fcfs_p95=p95,
                             spread=p95 / p5 if p5 > 0 else float("inf"),
                             gain_over_median_pct=g_med, gain_over_p95_pct=g_p95,
                             median_pct_of_achievable=100 * p50 / y_kw if y_kw else np.nan,
                             old_gain_pct=was, dt=time.time() - t0))
            print(f"  {scen:<9}{seed:<5}{k:<5}{y_kw:>9.0f}{p5:>9.0f}{p50:>9.0f}{p95:>9.0f}"
                  f" | {g_med:>+8.1f}%{g_p95:>+8.1f}% | {was:>+8.1f}%")

    d = pd.DataFrame(rows)
    if a.save:
        OUT.mkdir(exist_ok=True)
        d.to_csv(OUT / f"m57_honest_headline_{a.feeder}.csv", index=False)

    print("\n" + "=" * 88)
    print(f"  gain over the MEDIAN arrival order : "
          f"{d.gain_over_median_pct.min():+.1f}% to {d.gain_over_median_pct.max():+.1f}%")
    print(f"  gain over the p95 (LUCKY) order    : "
          f"{d.gain_over_p95_pct.min():+.1f}% to {d.gain_over_p95_pct.max():+.1f}%")
    print(f"  §9 claimed                         : "
          f"{d.old_gain_pct.min():+.1f}% to {d.old_gain_pct.max():+.1f}%")
    print(f"\n  the median order already attains   : "
          f"{d.median_pct_of_achievable.min():.1f}% to "
          f"{d.median_pct_of_achievable.max():.1f}% of achievable")
    print("\n  If the honest gain lands in the tens of percent, the regime map changes shape")
    print("  and the paper is more deflationary than §9 implied. Better we find that.\n")
    if a.save:
        print(f"  saved -> outputs/m57_honest_headline_{a.feeder}.csv")


if __name__ == "__main__":
    main()
