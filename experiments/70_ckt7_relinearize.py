"""M42: the re-linearization loop on Ckt7 -- one method for both utility feeders.

M41 (exp69) fixed the MILP on Ckt24: re-linearize GLDF at the incumbent solution
instead of forever at the empty queue, iterate to a fixed point, and the 0.002 pu
safety margin and the greedy AC repair both become UNNECESSARY (AC-feasible at
margin 0 on 6/6, zero drops). Ckt7 was left behind on the old pipeline
(margin + repair + a 3,000-eval swap climb, exp68/M39), so the paper currently
reports two different methods for its two real utility-scale feeders. This closes
that.

WHY THIS SHOULD ALSO FIX CKT7'S MODEL ERROR -- the two findings are the same one.
M40 traced Ckt7's terrible GLDF error (+18 to +136 mpu) to the REQUEST SAMPLER:
150-300 requests drawn WITH replacement from a 199-bus pool stack 228-313 kW onto
single buses, which is exactly "evaluate the affine model a very long way from
where it was pinned". That is ANCHOR error under another name -- the same disease
M41 cured on Ckt24. If so, re-linearizing should largely repair GLDF's accuracy on
the WITH-replacement instances too, WITHOUT changing the request model, and Ckt7
would no longer need the LP to merely *propose*.

So this runs BOTH protocols:
  * WITH replacement (default) -- exp61's draw verbatim, so the FCFS baselines and
    the M31/M32/M39 reference gains all still match, and this becomes a
    like-for-like replacement for the Section 7.9 headline.
  * WITHOUT replacement (--no-replacement) -- M40's instances, where GLDF is
    already essentially exact (<=0.2 mpu). A control: the loop should have little
    left to do there.

COMPARISON IS ISOLATED. M39 = MILP(empty anchor) + AC repair + swap climb(3,000).
This = MILP(re-linearized to a fixed point) + swap climb(3,000), same move set,
same budget, same seed, same oracle. The ONLY thing that changes is where the model
is linearized, so any difference is attributable to that and not to the climb. The
loop's own fixed point is reported SEPARATELY (`loop_kw`, before any climb) so the
re-linearization can be judged on its own.

Ckt7 is the easier network for this loop in one respect (no substation
non-worsening rule, no overloaded transformers -- see exp68) and the harder one in
another: its optimum is PURE OVERVOLTAGE-limited, sitting exactly on vmax = 1.0500
(M40), so there is no thermal slack to hide model error in.

SAME MEASUREMENT TRAP AS M41, SAME GUARD. Ckt7's slack bus also sits at 1.0500 pu
and the oracle's vmax is a max over ALL buses, so it masks the true PQ maximum
whenever the network is within limits. The model only ever speaks about PQ buses.
Every voltage compared here is a PQ-only max, and an assertion checks that the
anchored model reproduces the anchor's AC voltages exactly.

RESULT (2026-07-12) -- and it RETRACTS M40's central claim
--------------------------------------------------------------------------------
The loop is **AC-feasible at margin 0 on 6/6 with ZERO repair drops**, reaches a
fixed point in 4-8 iterations, and beats M39 on 6/6:

| instance   | FCFS    | M39 (margin+repair+climb) | **re-linearized** | vs M39 |
|------------|--------:|--------------------------:|------------------:|-------:|
| MODERATE 0 |  336.5  |                    1129.3 |        **1130.3** | +0.08% |
| MODERATE 1 |  780.3  |                    1118.1 |        **1118.8** | +0.07% |
| MODERATE 2 |  351.6  |                    1070.6 |        **1089.0** | +1.71% |
| STRESS 0   |  413.2  |                    1504.5 |        **1519.1** | +0.97% |
| STRESS 1   |  798.5  |                    1446.2 |        **1457.2** | +0.76% |
| STRESS 2   |  497.6  |                    1612.9 |        **1626.1** | +0.82% |

Median gain over honest FCFS rises +214.3% -> **+218.3%**. Both utility feeders now
run the identical method, with neither a safety margin nor a repair step.

**THE SWAP CLIMB NOW CONTRIBUTES NOTHING.** Seeded from the fixed point it changes
the answer on **1 of 6** instances, by 0.16 kW. The local search that M32/M39
credited with "doing the deciding" was only ever compensating for a badly anchored
model. Remove the bad anchor and it has no work left to do.

**AND THE PREMISE OF THIS WHOLE FILE WAS WRONG -- M40 IS RETRACTED.** The header
above predicted the loop would "largely repair GLDF's accuracy on the WITH-replacement
instances". It did not need to: **GLDF's error at the MILP's optimum, with replacement,
at iteration 0 -- i.e. BEFORE any re-linearization -- is already +0.01 to +0.77 mpu.**
The "+18 to +136 mpu" that motivated M39's propose-don't-decide design and M40's
sampler diagnosis was measured on **RANDOM accept-sets** (25-50% of the queue = 1.9-8.7
MW of PV on a feeder hosting ~1.1-1.5 MW), 3-8x outside the feasible region, where the
AC solve mostly diverges. **No optimizer ever goes there.** M40 then compared that
random-set error (with replacement) against an at-the-optimum error (without
replacement) and reported a "collapse" -- an invalid comparison between two different
quantities. There was no collapse; there was nothing to collapse.

Mechanism, mundane in hindsight: **the MILP accepts only 35-55 of the 150-300
requests**, and concentrating export on one bus is expensive in voltage, so the
optimizer AVOIDS the stacking that breaks the linearization. 228-313 kW of stacked
export is a property of the DRAWN QUEUE, never of an ACCEPTED set (max 82-135 kW at
the optimum).

**The transferable lesson: validate a surrogate on the trajectory the optimizer
actually follows -- at its optimum and at the incumbents it passes through -- never on
random samples of the decision space.** The random-set statistic is easy to compute,
was misleading for five consecutive experiments, and cost us a repair heuristic and a
local search that the program never needed. Note the complementary trap is equally
real (M35): at its OWN optimum the optimizer hunts for exactly the accept-sets where
the model is optimistic, so error measured there is WORSE than random-set error, not
better. Neither statistic substitutes for the other; only the second decides whether
the answer is correct.

Run:  uv run python experiments/70_ckt7_relinearize.py [--save]
      uv run python experiments/70_ckt7_relinearize.py --no-replacement [--save]
"""

from __future__ import annotations

import argparse
import copy
import os
import time

# THIS LOOP IS NOT REPRODUCIBLE, AND PINNING THE SOLVER DOES NOT MAKE IT SO (M48).
#
# The MILP is massively degenerate -- many accept-sets tie on total kW -- and HiGHS
# (scipy's `milp`) does not return the same one every run. The SLP loop then AMPLIFIES the
# tie-break, because the tied set it happens to return becomes the next linearization
# anchor. Measured on MODERATE:0: iteration 0 is bit-identical every time (1117.0 kW, 40
# accepted), then iteration 1 returns 38, 39 or 41 accepted requests -- all at ~1130.2 kW --
# and the trajectories diverge from there. Final answers observed: **1130.2 to 1135.2 kW,
# a 0.4% spread**, on the identical command and seed.
#
# `OMP_NUM_THREADS=1` is set anyway (it keeps the solver from grabbing all 16 cores when
# this runs alongside a worker pool), but it does NOT make the result deterministic -- an
# earlier claim to the contrary in this file was wrong, and was itself a coincidence of two
# runs landing on the same branch.
#
# The real mitigation lives in exp75's `certified_allocation`: run the loop from SEVERAL
# anchors and keep the best AC-feasible set. Every iterate is AC-verified, so a best-of can
# only raise the certified lower bound, and it is far more stable (on Ckt7 K=150 all four
# anchors converge to the same 866.5 kW). Quote these numbers to ~0.5%, not to 0.1 kW.
os.environ.setdefault("OMP_NUM_THREADS", "1")

import numpy as np
import pandas as pd
from scipy.optimize import Bounds, LinearConstraint, milp

_c = __import__("68_ckt7_gldf_headline")

VMIN_PU, VMAX_PU = _c.VMIN_PU, _c.VMAX_PU
SCENARIOS = _c.SCENARIOS

# exp68/M39's AC-verified results on the SAME (with-replacement) instances -- MILP at
# the empty anchor + AC repair + 3,000-eval swap climb. Read from the CSV rather than
# transcribed, so the reference cannot silently drift from what was actually run.
_m39 = pd.read_csv("outputs/m39_ckt7_gldf_headline.csv")
M39_KW = {(r.scenario, int(r.seed)): float(r.milp_kw) for r in _m39.itertuples()}
M39_GAIN = {(r.scenario, int(r.seed)): float(r.milp_gain_pct) for r in _m39.itertuples()}


def anchored_context(ctx, projects, anchor_ids):
    net = copy.deepcopy(ctx.net)
    by_id = {p.id: p for p in projects}
    for rid in anchor_ids:
        p = by_id[rid]
        _c.pp.create_load(net, bus=p.bus, p_mw=p.p_mw, q_mvar=0.0, name=rid)
    _c.pp.runpp(net, **_c.RUNPP_KWARGS)
    return _c.NetworkContext(net=net, excluded_line_idx=ctx.excluded_line_idx,
                             excluded_buses=ctx.excluded_buses)


def milp_anchored(projects, A_v, th, A_t, v_base_eff, p_base_eff, cuts, time_limit):
    K = len(projects)
    cons = []
    v_hi = np.maximum(VMAX_PU ** 2 - v_base_eff, 0.0)
    v_lo = np.minimum(VMIN_PU ** 2 - v_base_eff, 0.0)
    keep = np.abs(A_v).max(axis=1) > 1e-12
    cons.append(LinearConstraint(A_v[keep], v_lo[keep], v_hi[keep]))
    if th is not None and len(th["p_base"]):
        cons.append(LinearConstraint(A_t, p_base_eff - th["r_lim"], p_base_eff + th["r_lim"]))
    for S in cuts:
        row = np.where(np.isin([p.id for p in projects], list(S)), -1.0, 1.0)
        cons.append(LinearConstraint(row.reshape(1, -1), 1.0 - len(S), np.inf))

    c = np.array([-abs(p.p_mw) for p in projects])
    res = milp(c=c, constraints=cons, integrality=np.ones(K), bounds=Bounds(0, 1),
               options={"time_limit": time_limit, "mip_rel_gap": 1e-4, "presolve": True})
    if res.x is None:
        return None, 0.0
    x = np.round(res.x).astype(int)
    return ({p.id for p, xi in zip(projects, x) if xi},
            float(sum(abs(p.p_mw) for p, xi in zip(projects, x) if xi)))


def run_instance(ctx, scenario, seed, budget, replacement, max_iter, time_limit,
                 verbose=True):
    k = SCENARIOS[scenario]
    projects = _c.build_projects(ctx, k, seed, replacement=replacement)
    ids_all = [p.id for p in projects]
    t0 = time.time()

    fifo_ids, fifo_mw = _c.skip_continue_baseline(ctx, projects)

    # M is operating-point invariant (M41: pandapower keeps PQ loads in Sbus, not
    # Ybus), so build it once and reuse at every anchor; only v_base is rebuilt.
    g = _c.build_gldf(ctx, [p.bus for p in projects])
    A_v = np.zeros((len(g["pq"]), len(projects)))
    for r, p in enumerate(projects):
        col = g["m_cols"].get(int(p.bus))
        if col is not None:
            A_v[:, r] = col * (-p.p_mw / g["base_mva"])

    inv = {int(g["lookup"][b]): b for b in ctx.net.bus.index}
    pq_pp = [inv[int(i)] for i in g["pq"]]

    anchor: set[str] = set()
    ctx_a = anchored_context(ctx, projects, anchor)
    cuts: list[set[str]] = []
    best_ids, best_mw = set(), 0.0
    first_err = final_err = float("nan")
    n_iter = 0

    for it in range(max_iter):
        n_iter = it + 1
        v_anchor = np.array([ctx_a.net.res_bus.vm_pu[b] for b in pq_pp]) ** 2
        th = _c.build_thermal(ctx_a, projects)
        A_t = np.zeros((len(th["p_base"]), len(projects)))
        for bi, mem in enumerate(th["members"]):
            for r in mem:
                A_t[bi, r] = -projects[r].p_mw

        a = np.array([1.0 if i in anchor else 0.0 for i in ids_all])
        v_base_eff = v_anchor - A_v @ a
        p_base_eff = th["p_base"] + (A_t @ a if len(th["p_base"]) else 0.0)

        vmax_model_at_anchor = float(np.sqrt(np.maximum(v_base_eff + A_v @ a, 0.0)).max())
        vmax_ac_at_anchor = float(ctx_a.net.res_bus.vm_pu.loc[pq_pp].max())
        assert abs(vmax_model_at_anchor - vmax_ac_at_anchor) < 1e-6, (
            f"anchored model does not reproduce the anchor: "
            f"{vmax_model_at_anchor:.6f} vs {vmax_ac_at_anchor:.6f}")

        x, raw_mw = milp_anchored(projects, A_v, th, A_t, v_base_eff, p_base_eff,
                                  cuts, time_limit)
        if x is None:
            break

        xv = np.array([1.0 if i in x else 0.0 for i in ids_all])
        pred = float(np.sqrt(np.maximum(v_base_eff + A_v @ xv, 0.0)).max())
        ok, mw, viol, d = _c.evaluate(ctx, projects, x)
        ctx_x = anchored_context(ctx, projects, x)
        ac_pq = float(ctx_x.net.res_bus.vm_pu.loc[pq_pp].max())
        err = (pred - ac_pq) * 1000 if not d.get("diverged") else float("nan")
        if it == 0:
            first_err = err
        final_err = err

        if verbose:
            print(f"    it{it}  anchor={len(anchor):3d}  MILP {raw_mw * 1000:7.1f} kW "
                  f"({len(x):3d})   pred {pred:.4f}  AC {ac_pq:.4f}  err {err:+8.2f} mpu   "
                  f"{'AC-FEASIBLE' if ok else f'viol {viol:.5f}'}")

        if ok and mw > best_mw:
            best_ids, best_mw = set(x), mw
        if x == anchor:
            break
        if not ok:
            cuts.append(set(x))
        anchor, ctx_a = set(x), ctx_x

    # Fall back to the old repair only if the loop never produced a feasible iterate.
    repair_drops = 0
    if not best_ids:
        rep_ids, rep_mw = _c.ac_repair(ctx, projects, g, set(anchor))
        repair_drops = len(anchor) - len(rep_ids)
        best_ids, best_mw = rep_ids, rep_mw

    loop_mw = best_mw
    # Same climb, same budget, same seed as M39 -- so the ONLY difference vs M39 is
    # where the model was linearized.
    climb_ids, climb_mw, _ = _c.ac_swap_climb(ctx, projects, set(best_ids), budget, seed)

    m39 = M39_KW.get((scenario, seed), float("nan")) if replacement else float("nan")
    return dict(scenario=scenario, seed=seed, k=k, replacement=replacement,
                n_distinct_buses=len({p.bus for p in projects}),
                fifo_kw=fifo_mw * 1000, fifo_n=len(fifo_ids),
                iters=n_iter, repair_drops=repair_drops,
                first_err_mpu=first_err, final_err_mpu=final_err,
                loop_kw=loop_mw * 1000, loop_n=len(best_ids),
                final_kw=climb_mw * 1000, final_n=len(climb_ids),
                gain_pct=(climb_mw / fifo_mw - 1) * 100 if fifo_mw else float("nan"),
                loop_gain_pct=(loop_mw / fifo_mw - 1) * 100 if fifo_mw else float("nan"),
                m39_kw=m39, m39_gain_pct=M39_GAIN.get((scenario, seed), float("nan"))
                if replacement else float("nan"),
                vs_m39_pct=(climb_mw * 1000 / m39 - 1) * 100 if m39 == m39 else float("nan"),
                dt=time.time() - t0)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--instances", default="MODERATE:0,MODERATE:1,MODERATE:2,"
                                           "STRESS:0,STRESS:1,STRESS:2")
    ap.add_argument("--budget", type=int, default=3000)
    ap.add_argument("--max-iter", type=int, default=8)
    ap.add_argument("--time-limit", type=float, default=180.0)
    ap.add_argument("--no-replacement", action="store_true")
    ap.add_argument("--save", action="store_true")
    args = ap.parse_args()

    repl = not args.no_replacement
    print("M42: Ckt7 re-linearization loop -- one method for both utility feeders\n")
    print(f"  request draw: {'WITH replacement (exp61 verbatim)' if repl else 'WITHOUT replacement (M40 instances)'}")
    print(f"  swap-climb budget: {args.budget} AC evals (same as M39)\n")

    ctx = _c.build_network()
    ok0, _, _, _ = _c.evaluate(ctx, [], set())
    assert ok0, "baseline infeasible after exclusion"

    rows = []
    for tok in args.instances.split(","):
        scenario, seed = tok.split(":")[0], int(tok.split(":")[1])
        print(f"{scenario} seed={seed}")
        r = run_instance(ctx, scenario, seed, args.budget, repl,
                         args.max_iter, args.time_limit)
        rows.append(r)
        tail = (f"   M39 {r['m39_kw']:7.1f} kW   vs M39 {r['vs_m39_pct']:+6.2f}%"
                if repl else "")
        print(f"  -> loop {r['loop_kw']:7.1f} kW  (+climb {r['final_kw']:7.1f} kW, "
              f"{r['gain_pct']:+7.1f}% vs FCFS){tail}   "
              f"[{r['iters']} iters, {r['repair_drops']} repair drops, {r['dt']:.0f}s]\n")

    df = pd.DataFrame(rows)
    print("=" * 96)
    print(f"  GLDF error at the MILP optimum: iter 0 "
          f"{df.first_err_mpu.min():+.2f}..{df.first_err_mpu.max():+.2f} mpu"
          f"   ->   at the fixed point {df.final_err_mpu.min():+.2f}..{df.final_err_mpu.max():+.2f} mpu")
    print(f"  feasible at margin 0 with no repair: {int((df.repair_drops == 0).sum())}/{len(df)}")
    if repl:
        print(f"  vs M39 (margin+repair+climb): {df.vs_m39_pct.min():+.2f}%..{df.vs_m39_pct.max():+.2f}%, "
              f"median {df.vs_m39_pct.median():+.2f}%   (beats it on {(df.vs_m39_pct > 0.05).sum()}/{len(df)})")
        print(f"  gain vs FCFS: median {df.gain_pct.median():+.1f}%  (M39 median "
              f"{df.m39_gain_pct.median():+.1f}%)")
    else:
        print(f"  gain vs FCFS: median {df.gain_pct.median():+.1f}%")

    if args.save:
        out = ("outputs/m42_ckt7_relinearize.csv" if repl
               else "outputs/m42_ckt7_relinearize_norepl.csv")
        df.to_csv(out, index=False)
        print(f"\nsaved {out}")


if __name__ == "__main__":
    main()
