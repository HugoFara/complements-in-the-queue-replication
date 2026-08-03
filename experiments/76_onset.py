"""M49: what actually switches the arrival-order lottery on? (It is not just queue length.)

WHERE THIS STARTED
------------------
M47 found the paper's central claim: the lottery is a THRESHOLD in request pressure, not a
property of a feeder. Below an onset, arrival order costs *nothing* (spread exactly 1.00x);
above it, the median queue gives up a large slice of achievable capacity. But M47 located
that threshold with **one request draw per feeder** and **one K value either side of it**,
so this experiment was written to do it properly: a fine K grid, several draws, more orders.

WHAT THE FIRST CELLS SAID, AND WHY THE EXPERIMENT CHANGED SHAPE
---------------------------------------------------------------
At **K = 50** -- far below M47's Ckt7 onset of ~100-125 -- two of three request draws showed
no lottery at all (spread 1.00x, every order identical). The third showed a **2.00x spread**.

Same feeder. Same queue length. Same distribution. The difference was one request:

    50 requests, 401 kW total, median 6.1 kW ... and ONE request of 74.2 kW (19% of all
    requested capacity). Its seat rate across 200 arrival orders: 32%.

    - arrives LATE  -> rejected; all 49 small projects fit;      total 326.3 kW
    - arrives EARLY -> seated; it eats the headroom and BLOCKS
                       the small ones behind it (seated count
                       falls as low as 13 of 50);                 total as low as 152.2 kW

**A single heavy-tailed request is a BLOCKER, and it manufactures a lottery at a queue
length where none otherwise exists.** That is not an edge case -- it is the direct
consequence of the real size distribution (M45: skew 14.7, the top 1% of requests carry 33%
of all requested kW). A queue of uniformly small requests is order-insensitive even when
oversubscribed; a queue containing one big one is a lottery even when barely subscribed.

Note the DIRECTION, which is the policy-relevant part: seating the big project makes the
feeder host LESS in total (326 -> 152 kW). The median outcome equals the BEST outcome; the
lottery is pure downside. **Serial FCFS hands the big developer a seat at the direct expense
of aggregate hosted capacity** -- which is also why M46 found largest-first to be the worst
sort key (23-58% of achievable), and why it matters that real queues are mildly
largest-first (M45: Spearman(arrival rank, size) = -0.21).

SO THIS EXPERIMENT MEASURES TWO AXES, NOT ONE
---------------------------------------------
  x1  QUEUE LENGTH K        -- request pressure, M47's axis
  x2  SIZE HETEROGENEITY    -- how much of the queue's kW sits in its largest request
                              (`top_share`), plus the Gini of the size distribution

and reports, for each (K, draw):

  n_contested        requests whose seat rate is in (5%, 95%)  -- ANY order dependence
  n_coinflip         requests whose seat rate is in (25%, 75%) -- a REAL coin flip
                     (both are needed: at K=50/seed 2, `n_contested` is 47 of 50 -- but 39
                      of those are seated 75-95% of the time and only ~8 are true coin
                      flips. The sensitive statistic detects the onset; the strict one
                      says how bad it is.)
  biggest_seat_rate  the seat rate of the single largest request -- the blocker probe
  size_seat_spearman does being BIG help or hurt you in a serial queue?

**Per-request seat rates are saved raw**, so any threshold can be re-derived later without
re-running the sweep. (M47 had to be re-run once already because only summary statistics
were kept.)

EVERYTHING THAT MATTERS HERE IS MODEL-FREE
------------------------------------------
Seat rates, contested counts, seated fractions and the capacity spread need NO power-flow
model, NO linearization and NO MILP -- only the AC oracle and a queue walk. That is not a
detail: M48 has just shown the MILP yardstick was silently broken on Ckt7 (an empty thermal
model), and the reason M47's onset SURVIVED that bug intact is precisely that it never used
the yardstick. The certified allocation is computed here too, but only to express the
MAGNITUDE of the loss, and it is reported separately -- with both M48 invariants asserted
(the yardstick must be AC-maximal, and no queue walk may beat it).

Run:  uv run python experiments/76_onset.py --feeder ckt7  --save
      uv run python experiments/76_onset.py --feeder ckt24 --save
"""

from __future__ import annotations

import argparse
import multiprocessing as mp
import os
import random
import time
from pathlib import Path

os.environ.setdefault("OMP_NUM_THREADS", "1")

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "outputs"

_CTX = None
_PROJ = None
_MOD = None

# Dense through M47's transition (Ckt7 100-125, Ckt24 150-300), and now also well BELOW it
# -- K=50 is where the blocker effect turned up, and M47 never looked at more than one draw.
KS = {"ckt7": [25, 50, 75, 100, 110, 120, 130, 140, 150],
      "ckt24": [50, 100, 150, 200, 250, 300]}


def _walk_chunk(perm_seeds):
    """One worker: a block of arrival orders over the fixed request set."""
    mod, ctx, projects = _MOD, _CTX, _PROJ
    ids = [p.id for p in projects]
    k = len(projects)
    cache: dict[frozenset, tuple[bool, float]] = {}
    out = []
    for ps in perm_seeds:
        rng = random.Random(ps)
        order = list(range(k))
        rng.shuffle(order)
        acc, mw = frozenset(), 0.0
        for i in order:
            trial = acc | {ids[i]}
            hit = cache.get(trial)
            if hit is None:
                ok, m, _, _ = mod.evaluate(ctx, projects, set(trial))
                hit = (ok, m)
                cache[trial] = hit
            if hit[0]:
                acc, mw = trial, hit[1]
        seated = np.fromiter((ids[i] in acc for i in range(k)), dtype=bool, count=k)
        out.append((mw * 1000, len(acc), seated))
    return out


def gini(x: np.ndarray) -> float:
    """Concentration of the queue's requested kW. 0 = all requests equal."""
    if x.sum() <= 0:
        return float("nan")
    s = np.sort(x)
    n = len(s)
    return float((2 * np.arange(1, n + 1) - n - 1).dot(s) / (n * s.sum()))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--feeder", choices=["ckt7", "ckt24"], required=True)
    ap.add_argument("--ks", type=int, nargs="+", default=None)
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2, 3, 4],
                    help="request DRAWS. M47 used ONE, and the draw turns out to matter as "
                         "much as K -- one heavy-tail request changes the answer.")
    ap.add_argument("--permutations", type=int, default=200)
    ap.add_argument("--n-jobs", type=int, default=8)
    ap.add_argument("--no-yardstick", action="store_true",
                    help="skip the MILP -- the onset itself does not need it")
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()

    if a.feeder == "ckt7":
        mod = __import__("68_ckt7_gldf_headline")
        rel = __import__("70_ckt7_relinearize")
        exp = __import__("71_arrival_order_lottery")
        mk = lambda ctx, k, s: exp.build_projects_x(ctx, k, s, replacement=True,
                                                    empirical=True)
    else:
        mod = __import__("64_ckt24_gldf_bound")
        rel = __import__("69_ckt24_relinearize")
        exp = __import__("72_ckt24_arrival_order")
        mk = lambda ctx, k, s: exp.build_projects_x(ctx, k, s, empirical=True)
    m48 = __import__("75_milp_maximality")

    ks = a.ks or KS[a.feeder]
    print(f"M49 -- what switches the lottery on?  {a.feeder.upper()}, EMPIRICAL sizes")
    print(f"  K = {ks}")
    print(f"  request draws = {a.seeds};  {a.permutations} arrival orders each\n")

    global _CTX, _PROJ, _MOD
    ctx = mod.build_network()
    _CTX, _MOD = ctx, mod

    rows, rates_rows = [], []
    for k in ks:
        for seed in a.seeds:
            t0 = time.time()
            projects = mk(ctx, k, seed)
            _PROJ = projects
            sizes = np.array([abs(p.p_mw) * 1000 for p in projects])
            req_kw = float(sizes.sum())

            seeds_p = list(range(a.permutations))
            chunks = [seeds_p[i::a.n_jobs] for i in range(a.n_jobs)]
            with mp.get_context("fork").Pool(a.n_jobs) as pool:
                got = pool.map(_walk_chunk, [c for c in chunks if c])
            walks = [w for chunk in got for w in chunk]
            kws = np.array([w[0] for w in walks])
            ns = np.array([w[1] for w in walks])
            seat = np.stack([w[2] for w in walks])          # (P, k) bool

            # ---- the model-free onset statistics ----
            rate = seat.mean(axis=0)
            n_contested = int(((rate > 0.05) & (rate < 0.95)).sum())
            n_coinflip = int(((rate > 0.25) & (rate < 0.75)).sum())
            big = int(sizes.argmax())
            sp = float(spearmanr(sizes, rate).statistic) if sizes.std() > 0 else float("nan")

            p5, p25, p50, p75, p95 = np.percentile(kws, [5, 25, 50, 75, 95])
            spread = p95 / p5 if p5 > 0 else float("inf")
            iqr = p75 / p25 if p25 > 0 else float("inf")

            # ---- the yardstick (MILP, fixed in M48) -- for MAGNITUDE only ----
            opt_kw, maximal, beaten = float("nan"), None, None
            if not a.no_yardstick:
                ids, mw, _i = m48.certified_allocation(ctx, projects, mod, rel,
                                                       n_jobs=a.n_jobs, verbose=False)
                opt_kw = mw * 1000
                maximal = not m48.maximality_probe(ctx, projects, mod, ids, a.n_jobs)
                beaten = bool(kws.max() > opt_kw + 1e-6)
                _CTX, _PROJ, _MOD = ctx, projects, mod   # the probe rebinds the globals

            rows.append(dict(
                feeder=a.feeder, k=k, seed=seed, requested_kw=req_kw,
                # size heterogeneity -- the second axis
                max_request_kw=float(sizes.max()), median_request_kw=float(np.median(sizes)),
                top_share=float(sizes.max() / req_kw) if req_kw else np.nan,
                gini=gini(sizes),
                # the lottery
                contested=n_contested, frac_contested=n_contested / k,
                coinflip=n_coinflip, frac_coinflip=n_coinflip / k,
                biggest_seat_rate=float(rate[big]),
                size_seat_spearman=sp,
                fcfs_min=float(kws.min()), fcfs_p5=p5, fcfs_median=p50,
                fcfs_p95=p95, fcfs_max=float(kws.max()),
                spread_p95_p5=spread, iqr_ratio=iqr,
                cv=float(kws.std() / kws.mean()) if kws.mean() else np.nan,
                seated_frac_median=float(np.median(ns / k)),
                yardstick_kw=opt_kw, yardstick_maximal=maximal, yardstick_beaten=beaten,
                median_pct_of_yardstick=100 * p50 / opt_kw if opt_kw == opt_kw else np.nan,
                dt=time.time() - t0))

            # raw seat rates, so any threshold can be re-derived without re-running
            for i, p in enumerate(projects):
                rates_rows.append(dict(k=k, seed=seed, project=p.id, bus=p.bus,
                                       kw=float(sizes[i]), seat_rate=float(rate[i])))

            if a.save:
                OUT.mkdir(exist_ok=True)
                pd.DataFrame(rows).to_csv(OUT / f"m49_onset_{a.feeder}.csv", index=False)
                pd.DataFrame(rates_rows).to_csv(
                    OUT / f"m49_seatrates_{a.feeder}.csv.gz", index=False)

            r = rows[-1]
            flag = "  <-- YARDSTICK BEATEN" if beaten else ""
            print(f"  K={k:<4d} s={seed}  {req_kw:6.0f} kW  biggest {sizes.max():5.1f} kW "
                  f"({100*r['top_share']:4.1f}% of queue, seated {100*r['biggest_seat_rate']:5.1f}%)"
                  f"   coinflip {n_coinflip:3d}  contested {n_contested:3d}/{k:<3d}"
                  f"   spread {spread:5.2f}x   median {r['median_pct_of_yardstick']:5.1f}% of ach."
                  f"  [{r['dt']:.0f}s]{flag}")

    d = pd.DataFrame(rows)
    print("\n" + "=" * 100)
    print("  IS THE LOTTERY DRIVEN BY QUEUE LENGTH, OR BY ONE BIG REQUEST?")
    print("  (model-free throughout: no power-flow model, no MILP)\n")
    print("    K     draw   top_share   biggest seated   coinflips   FCFS spread")
    for r in rows:
        bar = "#" * min(30, int((r["spread_p95_p5"] - 1) * 15))
        print(f"    {r['k']:<5d} {r['seed']:<5d}  {100*r['top_share']:6.1f}%    "
              f"{100*r['biggest_seat_rate']:6.1f}%       {r['coinflip']:4d}      "
              f"{r['spread_p95_p5']:5.2f}x {bar}")

    lot = d[d.spread_p95_p5 > 1.01]
    print(f"\n  cells WITH a lottery: {len(lot)}/{len(d)}")
    if len(d) > 2:
        for name, col in [("queue length K", d.k), ("top_share (size heterogeneity)", d.top_share),
                          ("requested kW", d.requested_kw)]:
            rho = spearmanr(col, d.spread_p95_p5).statistic
            print(f"    Spearman(spread, {name:<32s}) = {rho:+.3f}")
    print("\n  ONSET (smallest K with any contested request), by draw:")
    for seed in a.seeds:
        sub = d[(d.seed == seed) & (d.contested > 0)].sort_values("k")
        if len(sub):
            r = sub.iloc[0]
            print(f"    draw {seed}: K = {int(r.k):<4d}  (top request = "
                  f"{100*r.top_share:.1f}% of the queue's kW)")
        else:
            print(f"    draw {seed}: no lottery anywhere in the swept range")

    if a.save:
        print(f"\n  saved -> outputs/m49_onset_{a.feeder}.csv "
              f"(+ raw per-request seat rates, m49_seatrates_{a.feeder}.csv.gz)")


if __name__ == "__main__":
    main()
