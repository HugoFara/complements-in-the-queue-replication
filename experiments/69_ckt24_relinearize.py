"""M41: can the MILP be *fixed* instead of AC-repaired? A re-linearization loop.

[[allocation-honest-baseline-pivot]] (docs/logs/gldf-milp.md, M35): the GLDF-MILP's
zero-margin optimum is AC-INFEASIBLE on 6/6 Ckt24 instances (vmax 1.0519-1.0535
against a 1.0500 limit -- the model *under*-predicts vmax by 1.9-3.5 mpu at its
own optimum, because the optimizer actively hunts for accept-sets where the
linearization is optimistic). M35 handles that downstream, with a 0.002 pu safety
margin plus a greedy AC repair (0-15 drops). Both are blunt: the margin costs
capacity everywhere to cure an error that only bites in a few places, and the
repair only ever *removes* requests. The published achievable numbers land at
74.0-90.7% of the MILP's own ceiling estimate. This experiment asks whether that
9-26% gap is real physics or just our linearization point.

THE IDEA. GLDF is defined at an ARBITRARY linearization point -- that is exactly
what "generalized" buys over Baran-Wu. We have been throwing that away: every
solve so far linearizes at the EMPTY QUEUE, then asks the MILP to make a decision
several MW of PV away from it. So: solve, run the real AC power flow on the
answer, RE-LINEARIZE GLDF THERE, and re-solve. Iterate to a fixed point. This is
successive linear programming, and it targets the pathology directly -- the model
becomes exact exactly where the optimizer wants to go.

WHAT ACTUALLY MOVES WHEN YOU RE-LINEARIZE -- and it is less than it sounds.
Read the construction (exp64 `build_gldf`):

    E := -Y_LL^-1 Y_LS V_S                      <- depends on Y and V_S only
    M := 2 Re( diag(E) conj(Z) diag(E)^-1 )     <- depends on E and Z only
    v  = v_anchor + M (p - p_anchor)

`Y` is the bus-admittance matrix; pandapower carries constant-power loads in
`Sbus`, NOT in `Ybus`, so ADDING REQUESTS DOES NOT CHANGE Y. Therefore E, Z and M
are all invariant to the operating point, and re-linearizing GLDF moves **only the
affine anchor `v_anchor`** -- the SLOPE is fixed by the network, not by the
loading. (Asserted numerically below, not assumed: `--check-invariance` rebuilds
M at a loaded anchor and compares it to M at the baseline.)

So this loop is a re-ANCHORING, not a re-slope. That is a weaker instrument than
"successive linearization" normally implies, and it is worth being honest that the
first-order sensitivities never improve. What it does fix is the part of the error
that comes from evaluating an affine model 4-8 MW away from where it was pinned:
after re-anchoring, the model reproduces the AC voltages EXACTLY at the incumbent
accept-set, and is first-order accurate in the neighbourhood the optimizer is
actually searching. Whether that is enough is the empirical question here.

THE LOOP (per instance):

    anchor <- {}                        (empty queue: M35's linearization point)
    repeat:
        solve AC at `anchor`            -> v_anchor, branch flows at anchor
        rebuild the GLDF + thermal model ANCHORED THERE
        x <- MILP(model, no-good cuts)  (margin 0 -- no safety fudge at all)
        AC-verify x                     -> feasible? predicted vs actual vmax?
        if x == anchor: FIXED POINT, stop
        if x is AC-infeasible: add a no-good cut forbidding exactly x
        anchor <- x                     (an infeasible x is still a perfectly
                                         valid *operating point* to linearize at)

Anchoring the model at a non-empty accept-set changes the MILP's constants but not
its columns. With `a` the incumbent's indicator vector:

    voltage:  v(x) = v_anchor + A_v (x - a)   =>  v_base_eff := v_anchor - A_v a
    thermal:  P_b(x) = P_anchor,b - (A_t (x - a))_b
                                              =>  p_base_eff := p_anchor + A_t a

Both reduce to exp64's own constraint rows with the constants swapped, so the
MILP itself is unchanged. Self-check enforced every iteration: evaluating the
anchored model AT x = a must reproduce the anchor's AC vmax to <1e-6 pu (if it
does not, the shift algebra above is wrong) -- asserted, not eyeballed.

MEASURING THE ERROR HONESTLY -- a trap the self-check above caught on iteration 0.
Ckt24's SLACK bus sits at exactly 1.0500 pu, and the oracle's `vmax` is a max over
ALL buses. So whenever every PQ bus is below the limit, the oracle reports the
SLACK's 1.0500 and masks the true PQ maximum (1.0457 at baseline). The model, by
construction, only ever speaks about PQ buses -- the slack row/col is removed from
Y_LL and no request can move its voltage. Comparing model-vs-oracle directly would
therefore corrupt exactly the error measurement this experiment exists to make: a
loop that successfully pushed voltages *below* 1.05 would still read "error =
pred - 1.0500". Every voltage compared below is a max over the PQ buses only.
(This does NOT affect M35's reported +1.9..+3.5 mpu: those optima all sit ABOVE
1.05, where the all-bus max IS the PQ max. It would only have bitten here.)

NO-GOOD CUTS. sum_{i in S}(1-x_i) + sum_{i not in S} x_i >= 1 forbids exactly the
vertex S and nothing else. Always valid, no monotonicity assumption. Their only
job is to stop the loop cycling between two accept-sets; they are NOT doing the
optimizing. (The stronger cut -- sum_{i in S} x_i <= |S|-1, which also kills every
superset of S -- is valid only if adding PV can never *fix* a violation. That is
true for a pure-overvoltage feeder but NOT obviously true here, where reverse flow
through a branch can cut its loading. Not used.)

WHAT THIS CAN AND CANNOT SHOW. If the loop reaches an AC-feasible fixed point at
margin 0 with no repair drops, the MILP is "fixed" in the sense asked: the
0.002 pu margin and the greedy repair both become unnecessary, and the reported
capacity should RISE toward the ceiling. If instead the error at the optimum stays
~2-3 mpu no matter where we anchor, that is also a real finding -- it would say
the residual is a SLOPE error (M is the no-load sensitivity and the true dV/dP
stiffens under load), which re-anchoring provably cannot fix, and that the honest
routes are a true convex relaxation (MISOCP) or an AC-sensitivity model. Both
outcomes are reported as found.

NOTE ON "CEILING". Re-anchoring changes the model, so the MILP's optimum changes
too -- the iterated bound is NOT comparable to M35's `gldf_bound_kw` as a ceiling.
The only quantity comparable across the two is the AC-VERIFIED accepted kW, which
is certified by the real oracle in both cases. That is what the result table
compares, and the ceiling column is reported for orientation only.

RESULT (2026-07-12) -- the MILP CAN be fixed. The margin and the repair both go.
--------------------------------------------------------------------------------
The loop reaches a fixed point in 3-6 iterations (15-58s per instance) and is
**AC-FEASIBLE AT MARGIN 0 ON 6/6, WITH ZERO REPAIR DROPS**. M35's 0.002 pu safety
margin and its greedy AC repair (which had to drop up to 15 requests) are both now
unnecessary -- not tuned down, *unnecessary*.

| instance   | FCFS    | M35 (margin+repair) | drops | **re-linearized** | vs M35 | iters |
|------------|--------:|--------------------:|------:|------------------:|-------:|------:|
| MODERATE 0 | 1765.7  |              4055.6 |     0 |        **4057.5** |  +0.0% |     3 |
| MODERATE 1 | 1077.4  |              4430.4 |     1 |        **4493.6** |  +1.4% |     3 |
| MODERATE 2 | 1723.7  |              4761.5 |     0 |        **4761.5** |  +0.0% |     5 |
| STRESS 0   | 1765.7  |              6127.6 |    15 |        **6277.9** |  +2.5% |     5 |
| STRESS 1   | 1093.0  |              6482.0 |    12 |        **6764.5** |  +4.4% |     5 |
| STRESS 2   | 1723.7  |              6446.3 |    13 |        **6844.6** |  +6.2% |     6 |

Capacity never decreases: beats M35 on 4/6, ties on 2/6. Gains over honest FCFS
become **+129.8% to +518.9%** (M35 reported +129.7% to +493.1%). The biggest wins
are exactly where the repair was doing the most damage (STRESS, 12-15 drops) --
which is the mechanism working as predicted.

**THE RESIDUAL WAS ANCHOR ERROR, NOT SLOPE ERROR.** The open question above is
settled in the first direction: re-anchoring alone is enough, even though it
provably cannot touch the sensitivities. No MISOCP or AC-sensitivity model needed.

READ THE ERROR COLUMN CAREFULLY -- one number in it is tautological.
At the fixed point x == anchor, so the model reproduces the AC solution *by
construction* and the reported error is exactly 0.00 mpu. That is the DEFINITION
of convergence, not a discovery, and it must not be quoted as "GLDF is now exact".
The three non-circular pieces of evidence are:
  1. **Feasibility**: the fixed point AC-verifies at margin 0 with no repair, 6/6.
     Nothing about that is definitional -- the oracle could have rejected it.
  2. **One-step-out error**: the model anchored at iteration 0's answer predicts
     iteration 1's *different* optimum to within **+0.08 to +0.67 mpu**, against
     -1.87 to -3.62 mpu when anchored at the empty queue. A 5-30x reduction on a
     genuinely out-of-sample point.
  3. **Capacity rose** (+0.0 to +6.2%). A model that had merely learned to agree
     with itself would not also seat more kW through the *unchanged* AC oracle.

WHAT THIS DOES TO THE "% OF CEILING" NUMBERS -- an epistemic upgrade, and the most
valuable consequence. M35's ceiling (`gldf_bound_kw`, 4,473-8,281 kW) is the
zero-margin MILP at the empty anchor -- and it is AC-INFEASIBLE on 6/6. It is an
*over-estimate* produced by model error, so "NSGA-II reaches 22.8-61.3% of the
ceiling" leaned on an inflated denominator. The converged fixed point is instead a
**certified AC-feasible allocation**, hence a genuine LOWER bound on the true
optimum. Dividing by it therefore yields an UPPER bound on any method's true
fraction of achievable capacity -- rigorous, and in the conservative direction:

    NSGA-II attains AT MOST 27.4-68.2% of achievable capacity on Ckt24
    (vs. the 22.8-61.3% "of estimated ceiling" reported in M35)

Both numerator and denominator are now AC-verified allocations, so this statement
requires trusting no power-flow model at all. That is strictly stronger than what
the paper claims today, even though the percentages went *up*.

SCOPE / HONESTY.
  - The fixed point is a self-consistent local optimum. It is NOT proven globally
    optimal, and this does not make GLDF a convex relaxation. "Provably optimal"
    stays unclaimable.
  - Convergence is empirical (6/6 in <=6 iterations), not guaranteed. The no-good
    cuts bound the loop but do not prove termination.
  - The oracle tests `violation == 0.0` by exact float equality, so a ~1e-9
    overshoot counts as infeasible and triggers a cut (visible on STRESS 2, it3).
    Harmless and conservative.
  - Ckt7 has NOT been re-run with this loop; it still uses margin + repair + swap
    climb (exp68).

Run:  uv run python experiments/69_ckt24_relinearize.py [--save]
      uv run python experiments/69_ckt24_relinearize.py --check-invariance
"""

from __future__ import annotations

import argparse
import copy
import time

import numpy as np
import pandas as pd
from scipy.optimize import Bounds, LinearConstraint, milp

_b = __import__("64_ckt24_gldf_bound")

VMIN_PU, VMAX_PU = _b.VMIN_PU, _b.VMAX_PU
SCENARIOS = _b.SCENARIOS

# M35's published achievable numbers (margin 0.002 + greedy repair), from
# outputs/m35_ckt24_gldf_bound.csv -- printed for reference, not recomputed.
M35_ACHIEVABLE_KW = {("MODERATE", 0): 4055.6, ("MODERATE", 1): 4430.4, ("MODERATE", 2): 4761.5,
                     ("STRESS", 0): 6127.6, ("STRESS", 1): 6482.0, ("STRESS", 2): 6446.3}
M35_REPAIR_DROPS = {("MODERATE", 0): 0, ("MODERATE", 1): 1, ("MODERATE", 2): 0,
                    ("STRESS", 0): 15, ("STRESS", 1): 12, ("STRESS", 2): 13}


def anchored_context(ctx, projects, anchor_ids):
    """ctx.net + the anchor's accepted requests, solved. Everything else (the
    exclusions, the substation's non-worsening baseline) is carried over
    UNCHANGED -- sub_baseline_pct in particular must stay the *original* baseline,
    not be recomputed at the anchor, or the non-worsening rule would drift."""
    net = copy.deepcopy(ctx.net)
    by_id = {p.id: p for p in projects}
    for rid in anchor_ids:
        p = by_id[rid]
        _b.pp.create_load(net, bus=p.bus, p_mw=p.p_mw, q_mvar=0.0, name=rid)
    _b.pp.runpp(net, **_b.RUNPP_KWARGS)
    return _b.NetworkContext(net=net, sub_idx=ctx.sub_idx,
                             sub_baseline_pct=ctx.sub_baseline_pct,
                             bad_trafo_idx=ctx.bad_trafo_idx,
                             excluded_line_idx=ctx.excluded_line_idx,
                             excluded_buses=ctx.excluded_buses)


def v_at(ctx_a, pq, lookup) -> np.ndarray:
    """squared |V| at the PQ buses of a solved (anchored) net, in ppc order."""
    inv = {int(lookup[b]): b for b in ctx_a.net.bus.index}
    return np.array([ctx_a.net.res_bus.vm_pu[inv[int(i)]] for i in pq]) ** 2


def milp_anchored(projects, g, A_v, th, A_t, v_base_eff, p_base_eff, cuts,
                  time_limit_s, mip_gap):
    """exp64's MILP with the anchored constants and the accumulated no-good cuts."""
    K = len(projects)
    cons = []

    # same non-worsening clamp as exp64: a bus already at/above the ceiling may not
    # be pushed higher, but is not required to come down.
    v_hi = np.maximum(VMAX_PU ** 2 - v_base_eff, 0.0)
    v_lo = np.minimum(VMIN_PU ** 2 - v_base_eff, 0.0)
    keep = np.abs(A_v).max(axis=1) > 1e-12
    cons.append(LinearConstraint(A_v[keep], v_lo[keep], v_hi[keep]))

    if th is not None and len(th.p_base):
        cons.append(LinearConstraint(A_t, p_base_eff - th.r_lim, p_base_eff + th.r_lim))

    for S in cuts:
        row = np.where(np.isin([p.id for p in projects], list(S)), -1.0, 1.0)
        cons.append(LinearConstraint(row.reshape(1, -1), 1.0 - len(S), np.inf))

    c = np.array([-abs(p.p_mw) for p in projects])
    res = milp(c=c, constraints=cons, integrality=np.ones(K), bounds=Bounds(0, 1),
               options={"time_limit": time_limit_s, "mip_rel_gap": mip_gap,
                        "presolve": True})
    if res.x is None:
        return None, 0.0
    x = np.round(res.x).astype(int)
    return ({p.id for p, xi in zip(projects, x) if xi},
            float(sum(abs(p.p_mw) for p, xi in zip(projects, x) if xi)))


def run_instance(ctx, scenario, seed, max_iter, time_limit_s, mip_gap, verbose=True):
    projects = _b.build_projects(ctx, SCENARIOS[scenario], seed)
    ids_all = [p.id for p in projects]
    t0 = time.time()

    fifo_ids, fifo_mw = _b.skip_continue_baseline(ctx, projects)

    # M is operating-point invariant (Y carries no constant-power load), so the
    # expensive part -- one sparse solve per request bus on a 6,057-bus system --
    # is done ONCE and reused at every anchor. Only v_anchor is rebuilt.
    g = _b.build_gldf(ctx, [p.bus for p in projects])
    A_v = np.zeros((len(g.pq), len(projects)))
    for r, p in enumerate(projects):
        col = g.m_cols.get(int(p.bus))
        if col is not None:
            A_v[:, r] = col * (-p.p_mw / g.base_mva)

    # The SLACK bus sits at exactly 1.0500 pu on Ckt24, and the oracle's `vmax` is a
    # max over ALL buses -- so it reads the slack whenever every PQ bus is below the
    # limit, masking the true PQ maximum. The model, by construction, only ever
    # speaks about PQ buses (the slack row/col is removed from Y_LL; its voltage is
    # fixed and no request can move it). Comparing the two directly would corrupt
    # exactly the error measurement this experiment exists to make, so every voltage
    # compared below is a max over the PQ buses only.
    inv = {int(g.lookup[b]): b for b in ctx.net.bus.index}
    pq_pp = [inv[int(i)] for i in g.pq]

    anchor: set[str] = set()
    ctx_a = anchored_context(ctx, projects, anchor)
    cuts: list[set[str]] = []
    best_ids, best_mw = set(), 0.0
    trace = []

    for it in range(max_iter):
        v_anchor = v_at(ctx_a, g.pq, g.lookup)
        th = _b.build_thermal(ctx_a, projects)
        A_t = np.zeros((len(th.p_base), len(projects)))
        for bi, mem in enumerate(th.members):
            for r in mem:
                A_t[bi, r] = -projects[r].p_mw

        a = np.array([1.0 if i in anchor else 0.0 for i in ids_all])
        v_base_eff = v_anchor - A_v @ a
        p_base_eff = th.p_base + (A_t @ a if len(th.p_base) else 0.0)

        # self-check: the anchored model, evaluated AT the anchor, must reproduce
        # the anchor's own AC voltages exactly. Catches any sign error in the shift.
        vmax_model_at_anchor = float(np.sqrt(np.maximum(v_base_eff + A_v @ a, 0.0)).max())
        vmax_ac_at_anchor = float(ctx_a.net.res_bus.vm_pu.loc[pq_pp].max())
        assert abs(vmax_model_at_anchor - vmax_ac_at_anchor) < 1e-6, (
            f"anchored model does not reproduce the anchor: "
            f"{vmax_model_at_anchor:.6f} vs {vmax_ac_at_anchor:.6f}")

        x, raw_mw = milp_anchored(projects, g, A_v, th, A_t, v_base_eff, p_base_eff,
                                  cuts, time_limit_s, mip_gap)
        if x is None:
            break

        xv = np.array([1.0 if i in x else 0.0 for i in ids_all])
        pred_vmax = float(np.sqrt(np.maximum(v_base_eff + A_v @ xv, 0.0)).max())

        # feasibility comes from exp64's oracle VERBATIM (so the accepted-kW numbers
        # stay comparable with M35); the PQ-restricted vmax, for the error measurement,
        # comes from the solved net -- which doubles as the next anchor, so it is not
        # an extra solve except on the final iterate.
        ok, mw, viol, d = _b.evaluate(ctx, projects, x)
        ctx_x = anchored_context(ctx, projects, x)
        ac_vmax_pq = float(ctx_x.net.res_bus.vm_pu.loc[pq_pp].max())
        err_mpu = (pred_vmax - ac_vmax_pq) * 1000 if not d.get("diverged") else float("nan")

        trace.append(dict(it=it, anchor_n=len(anchor), raw_kw=raw_mw * 1000, n=len(x),
                          pred_vmax=pred_vmax, ac_vmax_pq=ac_vmax_pq,
                          ac_vmax_all=d.get("vmax", float("nan")),
                          err_mpu=err_mpu, ac_feasible=ok, violation=viol))
        if verbose:
            print(f"    it{it}  anchor={len(anchor):3d}  MILP {raw_mw * 1000:7.1f} kW "
                  f"({len(x):3d})   pred vmax {pred_vmax:.4f}  AC {ac_vmax_pq:.4f}"
                  f"  err {err_mpu:+6.2f} mpu   {'AC-FEASIBLE' if ok else f'viol {viol:.5f}'}")

        if ok and mw > best_mw:
            best_ids, best_mw = set(x), mw
        if x == anchor:
            break                      # fixed point
        if not ok:
            cuts.append(set(x))        # forbid exactly this vertex; stops cycling
        anchor, ctx_a = set(x), ctx_x

    # If the loop never produced an AC-feasible iterate, fall back to M35's greedy
    # repair on the last one, so the row is still an honest AC-verified number.
    repair_drops = 0
    if not best_ids and trace:
        rep_ids, rep_mw, repair_drops = _b.repair_to_ac_feasible(ctx, projects, g, set(anchor))
        best_ids, best_mw = rep_ids, rep_mw

    m35 = M35_ACHIEVABLE_KW[(scenario, seed)]
    return dict(scenario=scenario, seed=seed, k=SCENARIOS[scenario],
                fifo_kw=fifo_mw * 1000,
                iters=len(trace),
                converged=bool(trace and trace[-1]["it"] < max_iter - 1
                               and len(trace) < max_iter),
                final_err_mpu=trace[-1]["err_mpu"] if trace else float("nan"),
                first_err_mpu=trace[0]["err_mpu"] if trace else float("nan"),
                any_feasible=bool(best_ids),
                repair_drops=repair_drops,
                loop_kw=best_mw * 1000, loop_n=len(best_ids),
                loop_over_fifo_pct=(best_mw * 1000 / (fifo_mw * 1000) - 1) * 100 if fifo_mw else float("nan"),
                m35_kw=m35, m35_repair_drops=M35_REPAIR_DROPS[(scenario, seed)],
                vs_m35_pct=(best_mw * 1000 / m35 - 1) * 100,
                dt=time.time() - t0), trace


def check_invariance(ctx):
    """Assert, don't assume: M really is operating-point invariant. Rebuild it at a
    heavily loaded anchor and compare against the empty-queue one."""
    projects = _b.build_projects(ctx, SCENARIOS["STRESS"], 0)
    buses = [p.bus for p in projects]
    g0 = _b.build_gldf(ctx, buses)
    ctx_a = anchored_context(ctx, projects, {p.id for p in projects[:150]})
    g1 = _b.build_gldf(ctx_a, buses)

    dm = max(float(np.abs(g0.m_cols[b] - g1.m_cols[b]).max()) for b in set(buses))
    dv = float(np.abs(g0.v_base - g1.v_base).max())
    print(f"  max |M_baseline - M_anchored|  = {dm:.3e}   <- expect ~0 (Y carries no PQ load)")
    print(f"  max |v_baseline - v_anchored|  = {dv:.3e}   <- expect LARGE (the anchor moved)")
    print(f"\n  => re-linearizing moves the ANCHOR only; the slope M is fixed by the network."
          if dm < 1e-9 else "\n  => M DID move -- the invariance claim is wrong, revisit.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--instances", default="MODERATE:0,MODERATE:1,MODERATE:2,"
                                           "STRESS:0,STRESS:1,STRESS:2")
    ap.add_argument("--max-iter", type=int, default=8)
    ap.add_argument("--time-limit", type=float, default=180.0)
    ap.add_argument("--mip-gap", type=float, default=1e-4)
    ap.add_argument("--check-invariance", action="store_true")
    ap.add_argument("--save", action="store_true")
    args = ap.parse_args()

    print("M41: re-linearization loop -- can the MILP be fixed instead of repaired?\n")
    ctx = _b.build_network()

    if args.check_invariance:
        check_invariance(ctx)
        return

    rows = []
    for tok in args.instances.split(","):
        scenario, seed = tok.split(":")[0], int(tok.split(":")[1])
        print(f"{scenario} seed={seed}")
        r, _ = run_instance(ctx, scenario, int(seed), args.max_iter,
                            args.time_limit, args.mip_gap)
        rows.append(r)
        print(f"  -> loop {r['loop_kw']:7.1f} kW ({r['loop_n']:3d})   "
              f"M35 {r['m35_kw']:7.1f} kW ({r['m35_repair_drops']} repair drops)   "
              f"vs M35 {r['vs_m35_pct']:+6.2f}%   vs FCFS {r['loop_over_fifo_pct']:+7.1f}%   "
              f"[{r['iters']} iters, {r['dt']:.0f}s]\n")

    df = pd.DataFrame(rows)
    print("=" * 96)
    print(f"  AC-feasible at margin 0 without any repair: "
          f"{int((df.repair_drops == 0).sum() & df.any_feasible.sum())}/{len(df)}")
    print(f"  model error at the optimum: first iter "
          f"{df.first_err_mpu.min():+.2f}..{df.first_err_mpu.max():+.2f} mpu   ->   "
          f"final {df.final_err_mpu.min():+.2f}..{df.final_err_mpu.max():+.2f} mpu")
    print(f"  vs M35 (margin 0.002 + greedy repair): "
          f"{df.vs_m35_pct.min():+.2f}% .. {df.vs_m35_pct.max():+.2f}%, "
          f"median {df.vs_m35_pct.median():+.2f}%   "
          f"(beats it on {(df.vs_m35_pct > 0.05).sum()}/{len(df)})")

    if args.save:
        out = "outputs/m41_ckt24_relinearize.csv"
        df.to_csv(out, index=False)
        print(f"\nsaved {out}")


if __name__ == "__main__":
    main()
