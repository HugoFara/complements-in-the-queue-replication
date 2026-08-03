"""M53 (E2): a MIXED queue KILLS our own certificate -- and hands us the real case for joint
allocation.

RESULT (Ckt7, 42 cells, 200 arrival orders each)
------------------------------------------------
    SUBSET-CLOSURE broken                            15 / 42 cells
    M51 CERTIFICATE false negatives                   7 / 42 cells
    certified optimum EXCEEDS the screen's reachable  11 / 42 cells   (Y > R)

**The M51 lemma does not hold on mixed queues, and the reviewer called it before we ran it.**
Its monotonicity hypothesis is not a technicality; it is load-bearing, and a real 2026 queue --
rooftop PV *and* EV chargers *and* storage -- violates it. That last line also kills M50's other
claim, that "effective pressure R/Y is >= 1.00 by construction": the construction assumed
monotonicity, so Y <= R. Here Y > R in 11 cells.

THE 100 kW BLOCK IS THE WHOLE STORY. On Ckt7 a 100 kW PV request is infeasible at EVERY bus on
its own (exp80: 0/17) -- the feeder's baseline vmax sits exactly on its 1.05 limit. So:

  load    screen says      certified      seated DESPITE      capacity      joint gain over
  frac    reachable        optimum        failing alone       spread        median order
    0%      0 / 17            0 kW              0             1.00x              --
   15%      3 / 17          500 kW             13 (1300 kW)   1.25x            0.0%
   30%      4 / 17          700 kW             12 (1200 kW)   1.33x            0.0%
   50%      7 / 17         1400 kW              9 ( 900 kW)   2.00x          +30.0%
   70%     11 / 17         1300 kW              5 ( 500 kW)   1.86x          +30.0%
  100%     16 / 17          300 kW              0             1.00x            0.0%

An all-PV queue of 100 kW requests can host **nothing**. Add 15% EV load and the feeder hosts
500 kW -- while the solo screen still insists only 300 kW is reachable. The loads absorb the
voltage rise that made each PV request impossible alone. **A request that is infeasible by
itself becomes feasible in company**, which is exactly the complementarity a joint allocator
exists to exploit and exactly what no screen, and no first-come-first-served process, can see.

THE ARGUMENT FOR JOINT ALLOCATION THAT EVERY EARLIER EXPERIMENT WAS BLIND TO
----------------------------------------------------------------------------
**FCFS cannot see a complement that has not arrived yet.** A PV application rejected today
would have been feasible if the EV charger three houses down had applied first. In the mixed
100 kW cells joint allocation beats the median arrival order by up to **+33%** -- and it
connects capacity that FCFS rejects *as physically impossible*, not merely as unlucky.

Every prior experiment in this project used a single-signed queue (all PV, or SMART-DS's all
EV), so none of them could produce this. It is the strongest case for the method in the whole
line of work, and we found it only by trying to break our own theorem.

AND THE MIX CUTS BOTH WAYS, DEPENDING ON GRANULARITY
----------------------------------------------------
At 20 kW (where PV is seatable alone) mixing simply RELIEVES congestion: achievable rises
940 -> 1,740 kW at 50/50 -- the entire queue fits -- the capacity lottery falls to 1.00x and
joint allocation's value goes to ZERO. At 100 kW (where PV is not seatable alone) mixing
ENABLES otherwise-impossible connections and CREATES a lottery (2.00x, +33%). Same feeder, same
mix, opposite conclusions, decided by granularity. Hosting capacity peaks at 50/50 in both.

WHY THIS IS THE EXPERIMENT THAT CAN BREAK OUR OWN RESULT
--------------------------------------------------------
Every queue in this project so far is monotone: all PV export (voltage only ever goes UP) or,
on SMART-DS, all EV load (voltage only ever goes DOWN). That monotonicity buys subset-closure
-- any subset of a feasible accept-set is feasible -- and subset-closure is the single
hypothesis behind M51's certificate:

    LEMMA. Let F be the requests that are feasible ON THEIR OWN. If F is feasible as a SET,
    then skip-continue FCFS accepts exactly F in EVERY arrival order, so the connected total
    is identical in all of them.  =>  A lottery requires a blocked request.

A real 2026 interconnection queue is not monotone. It contains rooftop PV *and* EV chargers
*and* storage, and they push the network in OPPOSITE directions. The reviewer named this as the
lemma's limit, and they are right. This experiment makes the failure happen on purpose.

CKT7 IS THE PERFECT PLACE TO DO IT. Its baseline vmax is **exactly 1.0500** -- the feeder sits
ON its own limit before a single request arrives (measured; see exp80). So:

    a PV request      raises voltage  ->  violates 1.05 immediately
    an EV load        lowers  voltage ->  RELIEVES the very constraint the PV violated

which means a PV request can be **infeasible alone and feasible in company**. That is exactly
the complementarity a joint allocator is supposed to exploit, and exactly what a solo screen
cannot see. Two things should follow, and both are measured here:

  1. **The screen under-counts.** Requests that fail alone get seated anyway, in some orders.
     Counted directly: `complementarity_witnesses` = requests with solo_feasible=False and a
     seat rate > 0.
  2. **The certificate produces FALSE NEGATIVES.** `n_blocked == 0` no longer implies a spread
     of 1.000, because the walk can seat requests that were never in F to begin with. Any such
     cell is a counterexample to the lemma -- and it is the honest limit of the whole M50-M51
     line, so it is reported as a headline number, not buried.

WHAT ELSE THIS ASKS
-------------------
  * Does the CAPACITY lottery widen? Complementary requests mean the ORDER in which the two
    kinds arrive decides how much headroom ever exists. Prediction: yes, and this is the
    strongest case for joint allocation in the whole project -- FCFS cannot see a complement
    that has not arrived yet, and a MILP can.
  * Does the IDENTITY lottery widen? (exp80: it is driven by smallness, and is already ~85%.)
  * Does joint allocation's margin over FCFS grow with the mix?

DESIGN
------
Queue total kW and K held fixed (exp80's discipline). Sweep the LOAD FRACTION f from 0 (all PV
export, the classic setting) to 1 (all EV load, SMART-DS's setting), through the mixed middle
that neither of them covers. Sizes are held constant at the mean so that only the MIX moves --
exp80 showed scale and dispersion each have their own large effect, and they must not be free
to vary here.

The objective is total |kW| connected: a queue serves both generation and demand, and the
network operator's question is how much of it can be accommodated at all.

Run:  uv run python experiments/81_mixed_queue.py --feeder ckt7 --save
"""

from __future__ import annotations

import argparse
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

LOAD_FRACTIONS = [0.0, 0.15, 0.3, 0.5, 0.7, 0.85, 1.0]


def build_mixed(feeder, k, seed, load_frac, size_kw):
    """K requests at random load buses, all the same size; `load_frac` of them are LOADS
    (positive p_mw, e.g. EV chargers) and the rest are PV EXPORT (negative p_mw).

    The bus draw is a separate RNG stream from the PV/load assignment, so changing the mix
    never moves a request -- only its sign."""
    rng_b = random.Random(seed)
    buses = [rng_b.choice(feeder.load_buses) for _ in range(k)]
    rng_s = random.Random(seed + 10_000)
    idx = list(range(k))
    rng_s.shuffle(idx)
    n_load = int(round(load_frac * k))
    is_load = set(idx[:n_load])
    out = []
    for i, b in enumerate(buses):
        mw = (size_kw / 1000.0) if i in is_load else (-size_kw / 1000.0)
        out.append(_c.Project(id=f"r{i}", bus=int(b), p_mw=mw))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--feeder", choices=["ckt7", "ckt24"], default="ckt7")
    ap.add_argument("--load-fractions", type=float, nargs="+", default=LOAD_FRACTIONS)
    # TWO SIZES, AND THE SECOND IS THE ONE THAT CAN BREAK THE LEMMA.
    #   20 kW  -- a PV request is feasible ALONE at every bus (exp80), so the solo screen sees
    #            everything and no complement is needed to rescue anything. Tests what the mix
    #            does to the LOTTERY.
    #  100 kW  -- a PV request is feasible alone at NO bus (exp80: 0/17). It can only ever be
    #            seated if a LOAD arrives to absorb its voltage rise. That is a request which is
    #            infeasible alone and feasible in company: subset-closure, and with it M51's
    #            certificate, must fail here or the lemma is stronger than we claimed.
    ap.add_argument("--sizes-kw", type=float, nargs="+", default=[20.0, 100.0])
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    ap.add_argument("--oversubscription", type=float, default=2.0)
    ap.add_argument("--permutations", type=int, default=200)
    ap.add_argument("--n-jobs", type=int, default=8)
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()

    if a.feeder == "ckt7":
        mod, rel = __import__("68_ckt7_gldf_headline"), __import__("70_ckt7_relinearize")
    else:
        mod, rel = __import__("64_ckt24_gldf_bound"), __import__("69_ckt24_relinearize")
    vmin, vmax, load = mod.VMIN_PU, mod.VMAX_PU, mod.LOADING_MAX_PCT
    ctx = mod.build_network()
    a_ref = {"ckt7": 866.5, "ckt24": 2113.0}[a.feeder]
    total_kw = a.oversubscription * a_ref

    print(f"M53 -- the MIXED queue on {a.feeder.upper()}")
    print(f"  baseline vmin {ctx.net.res_bus.vm_pu.min():.4f}  vmax "
          f"{ctx.net.res_bus.vm_pu.max():.4f}   limits {vmin}/{vmax}")
    print(f"  queue total held at {total_kw:.0f} kW ({a.oversubscription:.1f}x A_ref); "
          f"request sizes {a.sizes_kw}; only the PV/LOAD MIX varies")
    print(f"  {a.permutations} arrival orders x {len(a.seeds)} draws per cell\n")

    rows = []
    for size_kw in a.sizes_kw:
      k = max(4, int(round(total_kw / size_kw)))
      print(f"  --- {size_kw:.0f} kW requests, K={k}")
      for lf in a.load_fractions:
        for seed in a.seeds:
            t0 = time.time()
            f = _x.Feeder.from_ctx(a.feeder, ctx, None, vmin, vmax, load)
            projects = build_mixed(f, k, seed, lf, size_kw)
            ids_all = [p.id for p in projects]
            is_load = {p.id: p.p_mw > 0 for p in projects}

            feas = _x.solo_feasible(ctx, projects, f, a.n_jobs)
            reach_kw = sum(abs(p.p_mw) * 1000 for p in projects if p.id in feas)

            ref = _m.fcfs_accept_sets(ctx, projects, f, [_x.REF_WALK_SEED], a.n_jobs)[0]
            blocked = sorted(feas - set(ref))

            walks = _x.fcfs_walks(ctx, projects, f, a.permutations, a.n_jobs)
            kws = np.array([w[0] for w in walks])
            seat = np.stack([w[2] for w in walks])
            rate = seat.mean(axis=0)
            p5, p50, p95 = np.percentile(kws, [5, 50, 95])
            spread = p95 / p5 if p5 > 0 else (1.0 if p95 <= 0 else float("inf"))
            n_coinflip = int(((rate > 0.25) & (rate < 0.75)).sum())

            # ---- THE TWO THINGS THIS EXPERIMENT EXISTS FOR ----
            # 1. requests that FAIL ALONE and are seated anyway: the screen under-counts,
            #    because a complement arrived and made room for them.
            witnesses = [i for i, rid in enumerate(ids_all)
                         if rid not in feas and rate[i] > 0.0]
            # 2. the certificate's false negative: nothing blocked, yet the total still moves.
            false_negative = bool(len(blocked) == 0 and spread > 1.0001)

            ids, mw, _i = _m.certified_allocation(ctx, projects, f, f, n_jobs=a.n_jobs,
                                                  verbose=False)
            y_kw = mw * 1000

            rows.append(dict(
                feeder=a.feeder, load_frac=lf, size_kw=size_kw, k=k, seed=seed,
                requested_kw=sum(abs(p.p_mw) * 1000 for p in projects),
                reachable_kw=reach_kw, n_reachable=len(feas), yardstick_kw=y_kw,
                n_blocked=len(blocked),
                n_complementarity=len(witnesses),
                complementarity_kw=sum(abs(projects[i].p_mw) * 1000 for i in witnesses),
                subset_closure_broken=len(witnesses) > 0,
                certificate_false_negative=false_negative,
                fcfs_p5=p5, fcfs_median=p50, fcfs_p95=p95, spread=spread,
                median_pct=100 * p50 / y_kw if y_kw else np.nan,
                joint_gain_pct=100 * (y_kw / p50 - 1) if p50 > 0 else np.nan,
                coinflip=n_coinflip, frac_coinflip=n_coinflip / k,
                seated_loads=float(np.mean([sum(seat[w][i] for i in range(k)
                                                if is_load[ids_all[i]])
                                            for w in range(len(walks))])),
                dt=time.time() - t0))
            if a.save:
                OUT.mkdir(exist_ok=True)
                pd.DataFrame(rows).to_csv(OUT / f"m53_mixed_{a.feeder}.csv", index=False)

            r = rows[-1]
            flag = ""
            if r["subset_closure_broken"]:
                flag += f"  <-- {len(witnesses)} seated DESPITE failing alone"
            if false_negative:
                flag += "  <-- CERTIFICATE FALSE NEGATIVE"
            print(f"  load {100*lf:5.1f}%  s={seed}   reachable {len(feas):3d}/{k:<3d}  "
                  f"achievable {y_kw:7.1f} kW   blocked {len(blocked):3d}   "
                  f"spread {spread:5.2f}x  median {r['median_pct']:5.1f}%  "
                  f"joint +{r['joint_gain_pct']:5.1f}%  coinflip {n_coinflip:3d}"
                  f"  [{r['dt']:.0f}s]{flag}")

    d = pd.DataFrame(rows)
    print("\n" + "=" * 104)
    print("  DOES A MIXED QUEUE BREAK THE LEMMA?  (median over draws)\n")
    print(f"    {'size':>6s} {'load %':>7s} {'reachable':>10s} {'achiev.':>9s} | {'spread':>7s} "
          f"{'median':>7s} {'joint gain':>11s} | {'seated despite failing alone':>29s}")
    for (sz, lf), g in d.groupby(["size_kw", "load_frac"]):
        print(f"    {sz:5.0f}kW {100*lf:6.0f}% {g.n_reachable.median():9.0f}/{int(g.k.iloc[0]):<3d} "
              f"{g.yardstick_kw.median():8.0f}kW | {g.spread.median():6.2f}x "
              f"{g.median_pct.median():6.1f}% {g.joint_gain_pct.median():10.1f}% | "
              f"{g.n_complementarity.median():14.0f} requests "
              f"({g.complementarity_kw.median():6.0f} kW)")

    broke = d[d.subset_closure_broken]
    fn = d[d.certificate_false_negative]
    print(f"\n  SUBSET-CLOSURE broken in {len(broke)}/{len(d)} cells "
          f"(a request that fails ALONE is seated in company)")
    print(f"  CERTIFICATE false negatives: {len(fn)}/{len(d)} cells "
          f"(nothing blocked, yet the total still moved)")
    if len(fn):
        print("    -> the M51 lemma DOES NOT hold on mixed queues. Its monotonicity hypothesis")
        print("       is not a technicality; it is load-bearing, and real queues violate it.")
    else:
        print("    -> the lemma SURVIVED the mix. Worth understanding why before trusting it.")
    pure = d[(d.load_frac == 0) | (d.load_frac == 1)]
    mixed = d[(d.load_frac > 0) & (d.load_frac < 1)]
    if len(pure) and len(mixed):
        print(f"\n  joint allocation's gain over the median arrival order:")
        print(f"    PURE queues (all PV or all load): {pure.joint_gain_pct.median():+.1f}%")
        print(f"    MIXED queues:                     {mixed.joint_gain_pct.median():+.1f}%")
    if d.load_frac.nunique() > 2:
        rr = spearmanr(d.load_frac, d.spread)
        print(f"\n  Spearman(load fraction, capacity spread) = {rr.statistic:+.3f} "
              f"p={rr.pvalue:.4f}")
    if a.save:
        print(f"\n  saved -> outputs/m53_mixed_{a.feeder}.csv")


if __name__ == "__main__":
    main()
