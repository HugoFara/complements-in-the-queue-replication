"""M52 (E1): it is INEQUALITY, not lumpiness -- and there are TWO lotteries, not one.

RESULT FIRST, BECAUSE IT REFUTES BOTH THE REVIEWER'S PREDICTION AND OUR OWN
---------------------------------------------------------------------------
Ckt7, queue total held FIXED at 1,733 kW, constant request size swept 6 -> 800 kW, 200
arrival orders x 3 draws per cell:

    size   K    reachable  achievable   HOW-MUCH spread   median order   WHO coin-flips
      6   289      100%       960 kW         1.21x            89.6%          86.9%
     12   144      100%       924            1.18x            94.6%          86.1%
     20    87      100%       920            1.31x            76.1%          86.2%
     30    58      100%       840            1.65x           100.0%          84.5%
     40    43      100%       680            1.31x           100.0%          83.7%
     50    35      100%       600            1.00x           100.0%          31.4%
     60    29      100%       480            1.00x           100.0%           0.0%
     75    23       96%       300            1.00x           100.0%           0.0%
    100+   17        0%         0            1.00x               --            0.0%

Among the cells whose requests are still seatable:
    Spearman(size, capacity lottery)  = **-0.455**  (p=0.026)
    Spearman(size, identity lottery)  = **-0.801**  (p<0.001)

**BOTH NEGATIVE.** Lumpier requests produce LESS arrival-order dependence, not more. The
knapsack story ("small items -> greedy is near-optimal -> order cannot matter") predicts the
exact opposite, and it is wrong here -- not by a little, and not because the queue got less
contested: over-subscription RISES from 1.81x to 5.50x across this sweep while both lotteries
FALL. The prediction we wrote into the first version of this file -- an inverted U, peaking
where request size ~ headroom -- is **retracted**. Only its right limb survives.

WHY. As requests get lumpier, LOCATION becomes decisive. Only requests at electrically strong
buses can be seated at all, and those are seated in nearly every arrival order (seat rates go
to 0 or 1, coin-flips -> 0). The grid decides, not the queue. The lottery is MAXIMAL for
small, homogeneous requests, where many different subsets are feasible and arrival order picks
among them arbitrarily.

TWO LOTTERIES, AND THE CAPACITY ONE IS THE LESS IMPORTANT
----------------------------------------------------------
The 50 kW row is the finding this experiment nearly missed. Capacity spread **1.00x** -- every
one of the 200 arrival orders connects exactly 550.0 kW, and the median order attains 100% of
what is achievable. By the metric this project has used since M43, that queue is *perfectly
fair*. And yet 24 seatable requests are blocked, and 10-17 of 35 applicants have their fate
decided by a coin flip.

With equal-sized requests the feeder fits the same NUMBER of them regardless of order, so the
TOTAL is invariant while the IDENTITY of the winners is not. Capacity spread is structurally
blind to the lottery a rejected developer actually experiences. So both are now measured:

    spread     does arrival order change HOW MUCH gets connected?
    coinflip   does it change WHO gets connected?

At 6 kW: the median order is capacity-OPTIMAL (100% of achievable) and **84% of applicants are
coin-flips.** Efficiency and fairness are orthogonal, and every capacity-spread number this
project has reported -- including M49's 4.02x -- is a LOWER bound on the unfairness, because it
can only see contests that happen to move the total.

WHAT ACTUALLY DRIVES THE CAPACITY LOTTERY: INEQUALITY  (--dispersion, measured)
-------------------------------------------------------------------------------
Not scale. The sweep above holds size CONSTANT, which holds dispersion at zero -- and the
capacity spread never exceeds 1.65x. But M49's queues, drawn from the real CA distribution
(skew 14.7) at comparable K, reach 4.02x. The difference is not how big the requests are; it
is how UNEQUAL they are. So `--dispersion` fixes the total (1,733 kW), the mean (20 kW) AND K
(87), and varies only the spread of the size distribution:

     CV   biggest   achievable   HOW-MUCH spread   median order   WHO coin-flips
    0.00     20 kW     916 kW         1.31x            76.1%          86.2%
    0.25     35        907           1.55x            71.6%          90.8%
    0.50     55        853           1.74x            63.3%          67.8%
    1.00    102        831           2.58x            50.6%          71.3%
    1.50    146        822           2.75x            52.7%          81.6%
    2.00    183        757           2.67x            51.0%          86.2%   (one draw: 4.05x)
    3.00    239        678           2.68x            59.2%          86.2%
    4.00    278        538           2.62x            59.2%          83.9%

    Spearman(CV, capacity lottery) = **+0.753**  (p<0.001)
    Spearman(CV, identity lottery) = -0.211  (n.s.)

**THE DECOMPOSITION.** Two lotteries, two orthogonal drivers -- and each sweep is blind to the
other's:

                          capacity lottery      identity lottery
    SCALE      (E1a)          -0.455                -0.801
    DISPERSION (E1b)          **+0.753**             -0.211  (n.s.)

**Inequality drives WHAT gets connected; smallness drives WHO does.** At CV 2.0 a draw reaches
**4.05x** -- M49's real-distribution 4.02x, reproduced at identical K, mean and total. The real
CA queue is not remarkable for having small requests or big ones; it is remarkable for having
BOTH (skew 14.7), and that is the whole of the capacity lottery.

The median order's loss deepens with inequality too (76% -> 51% of achievable), so this is also
where joint allocation earns its keep. And achievable capacity itself falls 916 -> 538 kW as the
same total demand, in the same number of requests, becomes more unequal.

AND HOSTING CAPACITY IS NOT A SCALAR (now un-confounded)
--------------------------------------------------------
Achievable capacity falls **960 -> 300 kW** across this sweep with the queue's total demand
held fixed. The same feeder hosts 3x more when the same energy arrives as 6 kW units instead
of 75 kW units. The reviewer's fair objection to our earlier 6x claim -- that it raised
connection count and total kW together -- does not apply here: only granularity moves.

WHERE THIS CAME FROM
--------------------
A reviewer read the brief and found the contradiction we had left in it:

  * M46 retracted "the lottery is large" because it had been measured with a SYNTHETIC
    uniform[10,100] kW band; with real CA Rule 21 sizes (median 6.1 kW) the lottery
    VANISHED on Ckt24.
  * M49 then reported spreads up to 4.02x -- and the brief never said which distribution
    those came from.

The answer, from the CSVs: **M49 is empirical too.** Nothing contradicts. M46 measured ONE
queue length (K=150), and Ckt24 at K=150 really does show 1.00x in all three draws; the
lottery returns at K=200-300 with the same real sizes. What actually changed between the two
was not the distribution but **how much seatable demand the queue carried**.

The reviewer's reframing is right, and better than ours: the variable is not congestion but
**granularity -- request size relative to available headroom.** It is a knapsack: when items
are small next to capacity the integral optimum approaches the fractional one, any greedy is
near-optimal, and arrival order cannot cost much because nothing is indivisible enough to
block anything. As items get lumpy, greedy degrades and order becomes decisive.

WHERE THE REVIEWER'S METRIC BREAKS, AND WHAT REPLACES IT
--------------------------------------------------------
Their proposed measure is the size of the biggest request. On our own Ckt24 cells that is a
**negatively** signed predictor -- Spearman(spread, top_share) = **-0.334**:

  K=300, draw 1:  biggest 793.5 kW (20.3% of queue), seat rate 0.0%  ->  spread 1.00x, NO lottery
  K=300, draw 0:  biggest 770.2 kW (21.5% of queue), seat rate 0.0%  ->  spread 1.70x, 262 coin-flips

Same lumpiness, opposite outcome. Those monsters are seated **0% of the time in every arrival
order**: they are lumpy but **INERT** -- rejected by physics, not by luck. They inflate every
granularity statistic while contributing no contest at all. An item that cannot fit is not an
item in the knapsack; it is not in the instance.

Screen them out (M50's one-AC-solve-per-request test) and we predicted the knapsack logic would
give an inverted U:

    request size << headroom   ->  integral ~ fractional, greedy near-optimal   NO lottery
    request size ~  headroom   ->  indivisible, blocking, order decisive        MAX lottery
    request size >> headroom   ->  cannot be seated in ANY order                NO lottery

**Measured: only the right limb is real.** The left limb does not rise -- it is where the
lottery is WORST. See the result table at the top. The prediction was half wrong, and the half
that failed is the half the reviewer and we agreed on.

THE DESIGN
----------
Hold the queue's TOTAL requested kW fixed (this also answers the reviewer's fair complaint
that our 6x hosting-capacity number confounded connection count with total demand -- here
total demand does not move), and sweep the mean request size across three orders of
magnitude, from rooftop PV up through EV depots to data-centre scale:

    T := over_subscription x A_ref   (fixed)      s := mean request size      K := T / s

At every s: the screen (K AC solves), the certified optimum (exp75), and `--permutations`
FCFS walks. Reported against s / (per-request headroom):

    spread p95/p5     the lottery
    median_pct        what the typical arrival order gives up
    reachable_frac    how much of the queue physics rejects outright (the U's right limb)
    n_blocked         the M51 certificate: is any SEATABLE request being blocked?

Run:  uv run python experiments/80_granularity.py --feeder ckt7 --save
"""

from __future__ import annotations

import argparse
import math
import os
import random
import time
from pathlib import Path

os.environ.setdefault("OMP_NUM_THREADS", "1")

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

_c = __import__("68_ckt7_gldf_headline")
_m = __import__("75_milp_maximality")
_x = __import__("78_corpus_law")

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "outputs"

# Rooftop PV (the CA median is 6.1 kW) -> commercial -> EV depot -> data-centre scale.
# Dense through 40-100 kW: on Ckt7 that is where the per-request headroom knee sits. Measured
# against BOTH oracles (staged and exp68's, agreeing 12/12): a 50 kW request is seatable at
# every bus tried, a 100 kW request at NONE of them (it takes vmax from an already-maxed-out
# 1.0500 baseline to 1.0538). The U's right limb is not hypothetical on this feeder.
SIZES_KW = [6, 12, 20, 30, 40, 50, 60, 75, 100, 150, 300, 800]
K_MIN = 4


def const_sizer(kw):
    """Every request the same size. The cleanest possible read of the knapsack axis: only
    granularity moves, and the queue's total is held fixed by construction."""
    return lambda rng, n: np.full(n, float(kw))


def lognormal_sizer(total_kw, cv):
    """Sizes with a given coefficient of variation, rescaled so the queue's TOTAL is exactly
    `total_kw`. cv=0 reproduces `const_sizer`.

    THIS IS THE AXIS THE SCALE SWEEP COULD NOT SEE. Holding size CONSTANT holds dispersion at
    zero, and the scale sweep then found both lotteries *falling* as requests get lumpier
    (Spearman -0.455 and -0.801) -- the opposite of what the knapsack story predicts. Yet
    M49's queues, drawn from the real CA distribution (skew 14.7) at comparable K, reach
    4.02x. The difference between those queues and these is not how big the requests are; it
    is how UNEQUAL they are. So: fix the total, fix the mean, fix K, and vary only the spread.
    """
    sigma = math.sqrt(math.log(1.0 + cv ** 2)) if cv > 0 else 0.0

    def sizer(rng, n):
        if sigma == 0:
            x = np.ones(n)
        else:
            x = np.array([math.exp(sigma * rng.gauss(0.0, 1.0)) for _ in range(n)])
        return x * (total_kw / x.sum())        # the queue's total never moves

    return sizer


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--feeder", choices=["ckt7", "ckt24"], default="ckt7")
    ap.add_argument("--sizes", type=float, nargs="+", default=SIZES_KW)
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    ap.add_argument("--oversubscription", type=float, default=2.0,
                    help="queue's total requested kW, as a multiple of the reference capacity")
    ap.add_argument("--permutations", type=int, default=200)
    ap.add_argument("--n-jobs", type=int, default=8)
    ap.add_argument("--dispersion", action="store_true",
                    help="sweep the size distribution's SPREAD instead of its scale: total kW, "
                         "mean size and K all held fixed, only the inequality varies")
    ap.add_argument("--cvs", type=float, nargs="+",
                    default=[0.0, 0.25, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0])
    ap.add_argument("--mean-kw", type=float, default=20.0,
                    help="dispersion mode: the mean request size held fixed across the sweep")
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()

    if a.feeder == "ckt7":
        mod, rel = __import__("68_ckt7_gldf_headline"), __import__("70_ckt7_relinearize")
        vmin, vmax, load = mod.VMIN_PU, mod.VMAX_PU, mod.LOADING_MAX_PCT
    else:
        mod, rel = __import__("64_ckt24_gldf_bound"), __import__("69_ckt24_relinearize")
        vmin, vmax, load = mod.VMIN_PU, mod.VMAX_PU, mod.LOADING_MAX_PCT
    ctx = mod.build_network()

    # The reference capacity A_ref fixes the queue's total kW. It is M42/M49's own certified
    # number for this feeder -- deliberately NOT re-derived here, so that "2x over-subscribed"
    # means the same thing it meant in every earlier experiment.
    a_ref = {"ckt7": 866.5, "ckt24": 2113.0}[a.feeder]
    total_kw = a.oversubscription * a_ref

    print(f"M52 -- {'DISPERSION' if a.dispersion else 'granularity'} sweep on {a.feeder.upper()}")
    print(f"  reference capacity A_ref = {a_ref:.0f} kW (M42/M49's certified allocation)")
    print(f"  queue total held FIXED at {total_kw:.0f} kW ({a.oversubscription:.1f}x A_ref)")
    if a.dispersion:
        k_fixed = max(K_MIN, int(round(total_kw / a.mean_kw)))
        print(f"  mean request FIXED at {a.mean_kw:.0f} kW, K FIXED at {k_fixed}; "
              f"only the size distribution's SPREAD varies (CV {a.cvs})")
    else:
        print(f"  request size swept {a.sizes[0]:.0f} -> {a.sizes[-1]:.0f} kW")
    print(f"  {a.permutations} arrival orders per cell; draws {a.seeds}\n")

    axis = a.cvs if a.dispersion else a.sizes
    rows = []
    for s in axis:
        k = (max(K_MIN, int(round(total_kw / a.mean_kw))) if a.dispersion
             else max(K_MIN, int(round(total_kw / s))))
        for seed in a.seeds:
            t0 = time.time()
            sizer = (lognormal_sizer(total_kw, s) if a.dispersion else const_sizer(s))
            f = _x.Feeder.from_ctx(a.feeder, ctx, sizer, vmin, vmax, load)
            projects = _x.build_projects(f, k, seed)
            sizes = np.array([abs(p.p_mw) * 1000 for p in projects])
            req_kw = float(sizes.sum())

            feas = _x.solo_feasible(ctx, projects, f, a.n_jobs)
            by_id = {p.id: abs(p.p_mw) * 1000 for p in projects}
            reach_kw = sum(by_id[i] for i in feas)

            ref = _m.fcfs_accept_sets(ctx, projects, f, [_x.REF_WALK_SEED], a.n_jobs)[0]
            blocked = sorted(feas - set(ref))

            walks = _x.fcfs_walks(ctx, projects, f, a.permutations, a.n_jobs)
            kws = np.array([w[0] for w in walks])
            p5, p50, p95 = np.percentile(kws, [5, 50, 95])

            # TWO LOTTERIES, AND CONSTANT SIZES SEPARATE THEM CLEANLY.
            # Measured on the first pass of this sweep: at 50 kW, 24 reachable requests were
            # BLOCKED and yet every one of the 200 arrival orders connected exactly 550.0 kW
            # -- spread 1.00x. With equal-sized requests the feeder fits the same NUMBER of
            # them regardless of order, so the capacity total is invariant while the identity
            # of the winners is anything but. Capacity spread is therefore blind to the
            # lottery that a rejected developer actually experiences. Report both:
            #   spread     -- does arrival order change HOW MUCH gets connected?
            #   coinflip   -- does it change WHO gets connected?
            seat = np.stack([w[2] for w in walks])            # (P, K) bool
            rate = seat.mean(axis=0)
            n_contested = int(((rate > 0.05) & (rate < 0.95)).sum())
            n_coinflip = int(((rate > 0.25) & (rate < 0.75)).sum())
            n_seated_std = float(np.array([w[1] for w in walks]).std())

            ids, mw, _i = _m.certified_allocation(ctx, projects, f, f, n_jobs=a.n_jobs,
                                                  verbose=False)
            y_kw = mw * 1000

            # THE U's RIGHT LIMB, encoded honestly. When no request can be seated at all,
            # every arrival order connects 0 kW: the variance is ZERO and FCFS is (trivially)
            # optimal. That is spread 1.00 and 100% of achievable -- NOT a divide-by-zero.
            # Reading it as `inf` would invert the very finding this experiment is here for.
            if p95 <= 0:
                spread, med_pct, worst_pct = 1.0, 100.0, 100.0
            else:
                spread = p95 / p5 if p5 > 0 else float("inf")
                med_pct = 100 * p50 / y_kw if y_kw else np.nan
                worst_pct = 100 * p5 / y_kw if y_kw else np.nan

            rows.append(dict(
                feeder=a.feeder, size_kw=s, k=k, seed=seed, requested_kw=req_kw,
                cv=float(sizes.std() / sizes.mean()) if sizes.mean() else np.nan,
                max_request_kw=float(sizes.max()), mean_request_kw=float(sizes.mean()),
                reachable_kw=reach_kw, n_reachable=len(feas),
                reachable_frac=len(feas) / k, yardstick_kw=y_kw,
                size_over_capacity=s / a_ref, size_over_achievable=s / y_kw if y_kw else np.nan,
                eff_pressure=reach_kw / y_kw if y_kw else np.nan,
                n_blocked=len(blocked),
                # the CAPACITY lottery (how much) and the IDENTITY lottery (who)
                fcfs_p5=p5, fcfs_median=p50, fcfs_p95=p95, spread=spread,
                median_pct=med_pct, worst_pct=worst_pct,
                contested=n_contested, coinflip=n_coinflip,
                frac_contested=n_contested / k, frac_coinflip=n_coinflip / k,
                n_seated_std=n_seated_std,
                dt=time.time() - t0))
            if a.save:
                OUT.mkdir(exist_ok=True)
                pd.DataFrame(rows).to_csv(OUT / f"m52_{'dispersion' if a.dispersion else 'granularity'}_{a.feeder}.csv", index=False)
            r = rows[-1]
            lab = (f"CV {s:4.2f} (max {r['max_request_kw']:6.1f} kW)" if a.dispersion
                   else f"size {s:7.0f} kW")
            print(f"  {lab}  K={k:<4d} s={seed}   reachable {len(feas):3d}/{k:<3d} "
                  f"({100*r['reachable_frac']:5.1f}%)  achievable {y_kw:7.1f} kW"
                  f"   blocked {r['n_blocked']:3d}   HOW-MUCH spread {spread:5.2f}x"
                  f"  (median {r['median_pct']:5.1f}%)   WHO coinflip {n_coinflip:3d}"
                  f"/{k:<3d}  [{r['dt']:.0f}s]")

    d = pd.DataFrame(rows)
    if a.save:
        OUT.mkdir(exist_ok=True)
        tag = "dispersion" if a.dispersion else "granularity"
        d.to_csv(OUT / f"m52_{tag}_{a.feeder}.csv", index=False)

    print("\n" + "=" * 108)
    print(f"  {'DISPERSION' if a.dispersion else 'GRANULARITY'} vs THE TWO LOTTERIES "
          f"(median over draws)")
    print("    HOW MUCH gets connected  (capacity spread p95/p5)")
    print("    WHO gets connected       (share of requests whose seat rate is a coin flip)\n")
    xlab = "CV" if a.dispersion else "size"
    print(f"    {xlab:>7s} {'biggest':>8s} {'K':>5s} {'reachable':>10s} {'achiev.':>9s} "
          f"{'blocked':>8s} | {'HOW-MUCH':>9s} {'median':>7s} | {'WHO':>7s}")
    for s, g in d.groupby("size_kw"):
        sp, cf = g.spread.median(), g.frac_coinflip.median()
        bar = "#" * min(30, int(cf * 40))
        xv = f"{s:7.2f}" if a.dispersion else f"{s:7.0f}"
        print(f"    {xv} {g.max_request_kw.median():7.0f}kW {int(g.k.iloc[0]):5d} "
              f"{100*g.reachable_frac.median():9.1f}% {g.yardstick_kw.median():8.0f}kW "
              f"{g.n_blocked.median():8.0f} | {sp:8.2f}x {g.median_pct.median():6.1f}% | "
              f"{100*cf:6.1f}% {bar}")

    # The x-axis IS the swept quantity: request scale, or size INEQUALITY. Correlating against
    # `size_over_capacity` in dispersion mode would be nonsense -- there, scale never moves.
    live = d[d.reachable_frac > 0.5]
    xcol = live.cv if a.dispersion else live.size_over_capacity
    if len(live) > 2:
        for lab, col in [("HOW MUCH (capacity spread)", live.spread),
                         ("WHO (frac coin-flip)", live.frac_coinflip)]:
            rr = spearmanr(xcol, col)
            print(f"    among still-SEATABLE cells: Spearman({xlab:<4s}, {lab:<26s}) = "
                  f"{rr.statistic:+.3f}  p={rr.pvalue:.4f}")
    dead = d[d.reachable_frac <= 0.5]
    if len(dead):
        print(f"  the U's RIGHT limb -- {len(dead)} cells where most requests can never be "
              f"seated: median spread {dead.spread.median():.2f}x, coinflip "
              f"{100*dead.frac_coinflip.median():.1f}% "
              f"(lumpy, but INERT: physics rejects them in every order)")

    # Capacity invariance is NOT fairness. Count the cells where the total never moves and
    # yet the winners do -- the reviewer's mechanism-design point, measured.
    quiet = d[(d.spread <= 1.01) & (d.coinflip > 0)]
    if len(quiet):
        print(f"\n  {len(quiet)} cells connect the SAME kW in every arrival order while the "
              f"WINNERS change (median {quiet.coinflip.median():.0f} coin-flip requests).")
        print("  Capacity invariance is not fairness: an optimal total says nothing about who is in it.")
    if a.save:
        print(f"\n  saved -> outputs/m52_{tag}_{a.feeder}.csv")


if __name__ == "__main__":
    main()
