"""M50: what actually predicts the lottery -- and a cheap, a-priori test for it.

THE CLAIM THIS KILLS (our own, from M47)
----------------------------------------
M47: *"the arrival-order lottery is a threshold in REQUEST PRESSURE"*, where pressure =
(kW requested) / (kW achievable). M49's Ckt24 sweep falsifies that as stated:

    K=300, Ckt24        requested   achievable   PRESSURE   lottery?
      draw 0             3,589 kW      2,113 kW     1.70x    YES  (1.70x spread, 262 coin-flips)
      draw 1             3,918         2,054        1.91x    NO   (1.00x -- every order identical)
      draw 2             4,282         2,030        2.11x    NO   (1.00x)

**The two MOST over-subscribed queues have no lottery at all; the least over-subscribed one
gambles.** Across Ckt24's 18 cells, Spearman(spread, pressure) = **-0.07**. Pressure does not
predict the lottery. It barely correlates with it.

WHY -- AND THE STATISTIC THAT DOES WORK
---------------------------------------
Draws 1 and 2 seat **98% of their requests** while realizing only ~50% of requested kW. Their
queues are BIMODAL: ~294 tiny requests that obviously all fit, plus a handful of 674-793 kW
monsters that **never** fit (seat rate 0.000 in every cell, every order). Nothing sits near
the feasibility boundary, so **physics decides everything -- at 2x pressure.**

A request that can never be seated inflates the queue's kW but contributes no contest. It is
rejected in every arrival order, by physics, not by luck. So discount it:

    EFFECTIVE PRESSURE := (kW of requests that CAN be seated at all) / (kW achievable)

where "can be seated at all" is tested **one request at a time, alone, on the base feeder** --
K AC solves, no MILP, no permutations, no linearization. That is the whole test.

RESULT (across all 63 cells of M49, both feeders)
-------------------------------------------------
| predictor                      | Ckt7  | Ckt24  | pooled |
|--------------------------------|-------|--------|--------|
| M47's raw pressure             | +0.60 | **-0.07** | +0.36 |
| queue length K                 | +0.70 | +0.55  | +0.33  |
| **effective pressure**         | **+0.81** | **+0.85** | **+0.90** |

(Spearman rho vs the FCFS capacity spread. The pooled +0.90 is across two feeders that differ
by 5x in bus count and bind on different constraints.)

And unlike raw pressure it has a THRESHOLD:

    effective pressure <= 1.1  ->  median spread 1.00x, median order gets 100% of achievable
    effective pressure  > 1.1  ->  a lottery in 22 of 22 cells

**THE LAW.** *A connection queue becomes a lottery when the requests that CAN be seated
collectively exceed what the feeder can host.* Requests that can never be seated are noise:
they inflate the queue and they are decided by physics.

This also SUBSUMES M49's blocker finding. A "marginally feasible" large request is exactly
one that counts toward the reachable mass and pushes it past the ceiling; a request too big to
ever fit does not count, however large -- which is why a 794 kW request (62% of its queue)
produces no lottery at all, and a 74 kW one (18% of its queue) produces a 2x lottery.

WHY IT MATTERS PRACTICALLY
--------------------------
Every other diagnostic in this project needs either a power-flow model (GLDF/MILP -- and M48
is a cautionary tale about trusting one) or hundreds of AC queue walks. **This needs K AC
solves and nothing else.** A DNO can run it on a live queue in seconds and learn whether their
FCFS process is currently a lottery or merely a formality.

Run:  uv run python experiments/77_effective_pressure.py --feeder ckt7  --save
      uv run python experiments/77_effective_pressure.py --feeder ckt24 --save
"""

from __future__ import annotations

import argparse
import multiprocessing as mp
import os
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


def _solo(rid):
    """Can this request be seated AT ALL -- alone, on the base feeder?"""
    ok, _, _, _ = _MOD.evaluate(_CTX, _PROJ, {rid})
    return rid, bool(ok)


def solo_feasible(ctx, projects, mod, n_jobs=8):
    """The a-priori screen: one AC solve per request. No model, no permutations."""
    global _CTX, _PROJ, _MOD
    _CTX, _PROJ, _MOD = ctx, projects, mod
    with mp.get_context("fork").Pool(n_jobs) as pool:
        got = pool.map(_solo, [p.id for p in projects])
    return {rid for rid, ok in got if ok}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--feeder", choices=["ckt7", "ckt24"], required=True)
    ap.add_argument("--n-jobs", type=int, default=8)
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()

    if a.feeder == "ckt7":
        mod = __import__("68_ckt7_gldf_headline")
        exp = __import__("71_arrival_order_lottery")
        mk = lambda ctx, k, s: exp.build_projects_x(ctx, k, s, replacement=True,
                                                    empirical=True)
    else:
        mod = __import__("64_ckt24_gldf_bound")
        exp = __import__("72_ckt24_arrival_order")
        mk = lambda ctx, k, s: exp.build_projects_x(ctx, k, s, empirical=True)

    # Re-use M49's grid and its already-measured spreads/yardsticks; this experiment adds
    # ONLY the a-priori screen, so the two are directly comparable cell for cell.
    m49 = pd.read_csv(OUT / f"m49_onset_{a.feeder}.csv")
    print(f"M50 -- the a-priori lottery screen on {a.feeder.upper()}")
    print(f"  {len(m49)} cells from M49; adding one AC solve per request\n")

    ctx = mod.build_network()
    rows = []
    for r in m49.itertuples():
        projects = mk(ctx, int(r.k), int(r.seed))
        by_id = {p.id: abs(p.p_mw) * 1000 for p in projects}
        feas = solo_feasible(ctx, projects, mod, a.n_jobs)

        reach_kw = sum(by_id[i] for i in feas)
        n_never = len(projects) - len(feas)
        never_kw = r.requested_kw - reach_kw
        rows.append(dict(
            feeder=a.feeder, k=int(r.k), seed=int(r.seed),
            requested_kw=r.requested_kw, yardstick_kw=r.yardstick_kw,
            reachable_kw=reach_kw, n_never_feasible=n_never, never_feasible_kw=never_kw,
            pressure=r.requested_kw / r.yardstick_kw,
            eff_pressure=reach_kw / r.yardstick_kw,
            spread=r.spread_p95_p5, coinflip=r.coinflip,
            median_pct=r.median_pct_of_yardstick,
            lottery=bool(r.spread_p95_p5 > 1.01)))
        x = rows[-1]
        print(f"  K={r.k:<4d} s={r.seed}  requested {r.requested_kw:7.0f} kW  "
              f"({n_never:3d} requests / {never_kw:7.0f} kW can NEVER be seated)   "
              f"pressure {x['pressure']:5.2f}x -> EFF {x['eff_pressure']:5.2f}x   "
              f"spread {r.spread_p95_p5:5.2f}x  {'LOTTERY' if x['lottery'] else '--'}")

    d = pd.DataFrame(rows)
    print("\n" + "=" * 78)
    print("  WHAT PREDICTS THE LOTTERY?   (Spearman rho vs the FCFS capacity spread)")
    for name, col in [("M47's raw pressure", d.pressure), ("queue length K", d.k),
                      ("EFFECTIVE pressure", d.eff_pressure)]:
        rr = spearmanr(col, d.spread)
        print(f"    {name:<22s} rho = {rr.statistic:+.3f}   p = {rr.pvalue:.5f}")

    print("\n  THE THRESHOLD (effective pressure):")
    for lo, hi in [(0, 1.0), (1.0, 1.1), (1.1, 1.3), (1.3, 9)]:
        s = d[(d.eff_pressure >= lo) & (d.eff_pressure < hi)]
        if len(s):
            print(f"    {lo:4.1f}-{hi:4.1f}x : {len(s):2d} cells, lottery in {s.lottery.sum():2d} "
                  f"({100*s.lottery.mean():3.0f}%), median spread {s.spread.median():.2f}x, "
                  f"median order gets {s.median_pct.median():.0f}% of achievable")

    if a.save:
        OUT.mkdir(exist_ok=True)
        d.to_csv(OUT / f"m50_eff_pressure_{a.feeder}.csv", index=False)
        print(f"\n  saved -> outputs/m50_eff_pressure_{a.feeder}.csv")


if __name__ == "__main__":
    main()
