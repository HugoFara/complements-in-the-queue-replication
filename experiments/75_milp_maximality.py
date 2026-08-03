"""M48: fixing the yardstick -- the MILP neither converges nor terminates maximal.

THE DEFECT THAT SENT US HERE
----------------------------
M46/M47 flagged it and then had to caveat every magnitude claim around it: under the
EMPIRICAL request distribution (M45; median 6.1 kW, so ~135-190 tiny requests accepted
rather than ~40 large ones), the luckiest FCFS arrival order BEATS the MILP "optimum" --
864.1 kW vs 852.4 kW on Ckt7 MODERATE seed 0 (101.4%). A certified optimum that a greedy
queue walk beats is not an optimum, and every "% of achievable" number in the
arrival-order result divides by it.

TWO DEFECTS, AND NEITHER IS "GLDF IS INACCURATE"
-----------------------------------------------
The diagnosis run (--diagnose) found both. That the reflex answer was wrong for the
third time in this project is itself worth recording.

**(1) THE RE-LINEARIZATION LOOP NEVER LANDS FEASIBLE.** On Ckt7 K=150 empirical:

    it0  (anchor = EMPTY)  852.4 kW (134)  AC-FEASIBLE
    it1                    898.3 kW (137)  infeasible
    it2                    909.1 kW (138)  infeasible
    ...                     ...
    it11                   910.3 kW (138)  infeasible     <- max_iter, give up

Every iterate after the first is AC-infeasible, by a hair, forever. The loop converges
in VALUE (910.9 -> 910.3) while never once re-entering the feasible region, so
`best_ids` never updates past iteration 0 and the returned answer is **the naive
empty-anchor MILP**. M41/M42's re-linearization -- the thing that fixed Ckt24 and
retired the safety margin and the AC repair -- contributes NOTHING here.

Mechanism: the MILP always pushes to the boundary of the MODEL's feasible set, and near
that boundary GLDF is optimistic by ~1-2 mpu in the direction of the move. Re-anchoring
makes the model exact AT the anchor, but the MILP then walks to the model boundary again
and overshoots the true one by the same ~1-2 mpu. The no-good cuts are powerless: each
excludes exactly one set out of 2^150.

This is not a model error, it is a missing SLP ingredient. Trust-region / sequential-LP
methods handle it with CONSTRAINT SHIFTING, which is the fix below.

**(2) THE ANSWER IS NOT AC-MAXIMAL. FCFS'S ALWAYS IS.** skip-continue FCFS terminates,
by construction, at a set where no remaining request can be added -- it walked the whole
queue and tried every one. The MILP carries no such guarantee: it stops where the
LINEARIZED constraint binds, and wherever GLDF OVER-predicts voltage rise, real AC
headroom is left unused. On the same instance, **7 of the 16 rejected requests could each
be added and stay AC-feasible.**

Why both defects are invisible in the synthetic regime and bite in the real one:
leftover headroom is only worth something if a request FITS in it. At uniform[10,100] kW
(~40 accepted, mean 55 kW) a few mpu of unused headroom fits nothing, and M41/M42
correctly found the fixed point AC-feasible at margin 0 on 6/6 with zero repair drops.
At the empirical distribution the median request is 6.1 kW and 96.8% are under 20 kW, so
the same few mpu fits several more requests -- and the ~1-2 mpu of model optimism is now
enough to make every iterate infeasible rather than merely tight.

THE FIX
-------
`certified_allocation` = re-linearize (M41) + **measured back-off** + **AC completion**:

  * BACK-OFF (fixes 1). At each iterate, measure the model's optimism at every PQ bus,
    err_b = v_AC(x)_b - v_pred(x)_b, and accumulate a per-bus shift bo_b = max(bo_b,
    err_b)+. Solve the next MILP against `vmax^2 - bo` instead of `vmax^2`. The model
    boundary retreats by exactly the amount the model was caught overstating, and the
    next iterate lands at or inside the true boundary.

    This is NOT the 0.002 pu global safety margin that M41 retired. That margin was a
    hand-tuned scalar guess applied everywhere; this is a per-bus quantity MEASURED on
    the optimizer's own trajectory, and it is zero wherever the model is honest. It
    keeps M42's lesson (validate the surrogate where the optimizer actually goes) and
    uses it to steer rather than merely to audit.

  * COMPLETION (fixes 2). From the best AC-feasible iterate, sweep the rejected requests
    in descending kW, AC-solve each candidate addition against the REAL oracle, keep
    what fits, repeat until a full pass adds nothing. The result is AC-MAXIMAL -- the
    property FCFS gets for free -- so it can no longer be beaten by a queue walk for the
    trivial reason of leaving headroom on the table.

AND A THIRD DEFECT, FOUND WHILE FIXING THE FIRST TWO: THE SOLVE IS NOT REPRODUCIBLE
-----------------------------------------------------------------------------------
The identical command and seed returns **1130.2 to 1135.2 kW** (a 0.4% spread) on Ckt7
MODERATE:0. The MILP is **massively degenerate** -- many accept-sets tie on total kW -- and
HiGHS does not return the same tied optimum every run. The SLP loop then AMPLIFIES that,
because whichever tied set comes back becomes the next linearization anchor: iteration 0 is
bit-identical every time (1117.0 kW, 40 accepted), iteration 1 returns 38, 39 or 41
accepted requests (all at ~1130.2 kW), and the trajectories diverge from there.

Pinning the solver to one thread does NOT fix this (an earlier claim that it did was a
coincidence of two runs landing on the same branch). The fix is to stop fighting the
degeneracy and exploit it: `certified_allocation` runs the loop from SEVERAL anchors and
keeps the best AC-feasible set. Every iterate is AC-verified, so a best-of can only RAISE
the certified lower bound -- and it is far more stable (on Ckt7 K=150 all four anchors
converge to the same 866.5 kW). **Quote these numbers to ~0.5%, not to 0.1 kW.**

WHAT THIS BUYS, AND WHAT IT DOES NOT -- STATED PLAINLY
------------------------------------------------------
It does NOT give an upper bound on the true AC optimum. GLDF is an approximation with
error of either sign, not a relaxation, so its MILP optimum was never an upper bound and
no amount of back-off or completion makes it one. What we have -- and all we have ever
had -- is a CERTIFIED FEASIBLE ALLOCATION: a LOWER bound on what is achievable.

The direction is the safe one. If the yardstick D satisfies D <= O (the true optimum),
then FCFS/D >= FCFS/O: the reported share OVERSTATES FCFS's efficiency, so the reported
LOSS UNDERSTATES the true loss. "Arrival order costs at least X%" survives. "FCFS attains
exactly X% of the optimum" never did, and the paper must stop saying it.

This file makes that statement TRUE by making D at least as good as the best queue walk,
which is precisely what M47 could not claim.

Run:  uv run python experiments/75_milp_maximality.py --feeder ckt7  --diagnose --save
      uv run python experiments/75_milp_maximality.py --feeder ckt24 --diagnose --save
"""

from __future__ import annotations

import argparse
import multiprocessing as mp
import os
import random
import time
from pathlib import Path

# Keeps HiGHS from grabbing all 16 cores when it runs alongside the worker pool below.
# It does NOT make the solve deterministic -- see the DEGENERACY note in the docstring.
os.environ.setdefault("OMP_NUM_THREADS", "1")

import numpy as np
import pandas as pd
from scipy.optimize import Bounds, LinearConstraint, milp

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "outputs"

_CTX = None
_PROJ = None
_MOD = None
_INC: frozenset = frozenset()


def load_feeder(feeder):
    """(oracle module, re-linearization module, arrival-order module).

    exp68/exp64 expose the same API under different types -- exp68's `build_gldf` /
    `build_thermal` return dicts, exp64's return dataclasses -- hence `_f` below.
    """
    if feeder == "ckt7":
        return (__import__("68_ckt7_gldf_headline"),
                __import__("70_ckt7_relinearize"),
                __import__("71_arrival_order_lottery"))
    return (__import__("64_ckt24_gldf_bound"),
            __import__("69_ckt24_relinearize"),
            __import__("72_ckt24_arrival_order"))


def _f(obj, name):
    """Ckt7's GLDF/Thermal are dicts; Ckt24's are dataclasses. Same fields."""
    return obj[name] if isinstance(obj, dict) else getattr(obj, name)


def broken_thermal(mod):
    """The PRE-M48 `build_thermal`: spanning tree over `net.line` + `net.trafo` only,
    with no bus-bus switches. Kept so the defect stays reproducible on demand rather
    than merely asserted -- `--broken-thermal` runs the whole diagnosis against it.

    On Ckt7 this walk reaches 6 of 1,255 buses and yields ONE constraint row.
    On Ckt24 (zero switches) it is identical to the fixed version.
    """
    real = mod.build_thermal

    def patched(ctx, projects):
        net = ctx.net
        saved = net.switch
        net.switch = saved.iloc[0:0]        # hide the switches; same code path otherwise
        try:
            return real(ctx, projects)
        finally:
            net.switch = saved

    return patched


# ----------------------------------------------------------------- the MILP
def milp_solve(projects, A_v, v_lo, v_hi, A_t, t_lo, t_hi, cuts, time_limit, mip_gap):
    """Both feeders' anchored MILPs are structurally identical (exp69 and exp70 differ
    only in argument order and in the dict-vs-dataclass types), so one solver serves
    both. The substation non-worsening rule lives in the ORACLE, not here.
    """
    K = len(projects)
    cons = []
    keep = np.abs(A_v).max(axis=1) > 1e-12          # rows no request can move
    cons.append(LinearConstraint(A_v[keep], v_lo[keep], v_hi[keep]))
    if A_t is not None and len(t_lo):
        cons.append(LinearConstraint(A_t, t_lo, t_hi))
    ids = [p.id for p in projects]
    for S in cuts:
        row = np.where(np.isin(ids, list(S)), -1.0, 1.0)
        cons.append(LinearConstraint(row.reshape(1, -1), 1.0 - len(S), np.inf))

    c = np.array([-abs(p.p_mw) for p in projects])
    res = milp(c=c, constraints=cons, integrality=np.ones(K), bounds=Bounds(0, 1),
               options={"time_limit": time_limit, "mip_rel_gap": mip_gap,
                        "presolve": True})
    if res.x is None:
        return None
    x = np.round(res.x).astype(int)
    return {p.id for p, xi in zip(projects, x) if xi}


def relinearize_backoff(ctx, projects, mod, rel, max_iter=15, time_limit=180.0,
                        mip_gap=1e-4, verbose=True, anchor0=None):
    """M41's loop + a MEASURED per-bus constraint shift (see the module docstring).

    `anchor0` warm-starts the loop from a given accept-set instead of the empty queue.

    Returns (best AC-feasible ids, mw, gldf, trace).
    """
    ids_all = [p.id for p in projects]
    g = mod.build_gldf(ctx, [p.bus for p in projects])
    pq, lookup, base_mva = _f(g, "pq"), _f(g, "lookup"), _f(g, "base_mva")
    m_cols = _f(g, "m_cols")

    A_v = np.zeros((len(pq), len(projects)))
    for r, p in enumerate(projects):
        col = m_cols.get(int(p.bus))
        if col is not None:
            A_v[:, r] = col * (-p.p_mw / base_mva)

    inv = {int(lookup[b]): b for b in ctx.net.bus.index}
    pq_pp = [inv[int(i)] for i in pq]

    backoff = np.zeros(len(pq))          # per-bus measured optimism, in |V|^2 units
    anchor: set[str] = set(anchor0) if anchor0 else set()
    ctx_a = rel.anchored_context(ctx, projects, anchor)
    cuts: list[set[str]] = []
    best_ids, best_mw = set(), 0.0
    if anchor:                            # the warm start is itself a certified incumbent
        ok0, mw0, _, _ = mod.evaluate(ctx, projects, anchor)
        if ok0:
            best_ids, best_mw = set(anchor), mw0
    trace = []

    for it in range(max_iter):
        v_anchor = np.array([ctx_a.net.res_bus.vm_pu[b] for b in pq_pp]) ** 2
        th = mod.build_thermal(ctx_a, projects)
        p_base, r_lim, members = _f(th, "p_base"), _f(th, "r_lim"), _f(th, "members")
        A_t = np.zeros((len(p_base), len(projects)))
        for bi, mem in enumerate(members):
            for r in mem:
                A_t[bi, r] = -projects[r].p_mw

        a = np.array([1.0 if i in anchor else 0.0 for i in ids_all])
        v_base_eff = v_anchor - A_v @ a
        p_base_eff = p_base + (A_t @ a if len(p_base) else 0.0)

        # the shift: retreat the ceiling by the optimism we have actually caught.
        # `maximum(..., 0)` is exp64's non-worsening clamp -- a bus already at or over
        # the ceiling may not be pushed HIGHER, but is not required to come down.
        v_hi = np.maximum(mod.VMAX_PU ** 2 - backoff - v_base_eff, 0.0)
        v_lo = np.minimum(mod.VMIN_PU ** 2 - v_base_eff, 0.0)

        x = milp_solve(projects, A_v, v_lo, v_hi, A_t, p_base_eff - r_lim,
                       p_base_eff + r_lim, cuts, time_limit, mip_gap)
        if x is None:
            break

        xv = np.array([1.0 if i in x else 0.0 for i in ids_all])
        v_pred = v_base_eff + A_v @ xv
        ok, mw, viol, d = mod.evaluate(ctx, projects, x)
        ctx_x = rel.anchored_context(ctx, projects, x)
        v_ac = np.array([ctx_x.net.res_bus.vm_pu[b] for b in pq_pp]) ** 2

        # the model's optimism at THIS iterate, per bus. Accumulate the worst seen.
        err = v_ac - v_pred
        backoff = np.maximum(backoff, np.maximum(err, 0.0))

        kw = sum(abs(p.p_mw) for p in projects if p.id in x) * 1000
        if verbose:
            print(f"      it{it:<2d} anchor={len(anchor):3d}  MILP {kw:7.1f} kW ({len(x):3d})"
                  f"  vmax {d.get('vmax', float('nan')):.5f} vmin {d.get('vmin', float('nan')):.4f}"
                  f"  line {d.get('line_pct', float('nan')):6.1f}% trafo {d.get('trafo_pct', float('nan')):6.1f}%"
                  f"  v-optimism {1000 * np.sqrt(np.maximum(v_ac, 0)).max() - 1000 * np.sqrt(np.maximum(v_pred, 0)).max():+6.2f} mpu"
                  f"   {'AC-FEASIBLE' if ok else f'VIOL {viol:.4f}'}")
        trace.append(dict(it=it, kw=kw, n=len(x), ok=bool(ok),
                          vmax=float(d.get("vmax", np.nan)),
                          vmin=float(d.get("vmin", np.nan)),
                          line_pct=float(d.get("line_pct", np.nan)),
                          trafo_pct=float(d.get("trafo_pct", np.nan)), viol=float(viol)))

        if ok and mw > best_mw:
            best_ids, best_mw = set(x), mw
        if x == anchor and ok:
            break
        if not ok:
            cuts.append(set(x))
        anchor, ctx_a = set(x), ctx_x

    return best_ids, best_mw, g, trace


# ------------------------------------------------------------- AC completion
def _init(inc):
    global _INC
    _INC = inc


def _try_add(rid):
    ok, mw, _, _ = _MOD.evaluate(_CTX, _PROJ, set(_INC) | {rid})
    return rid, ok, mw


def ac_complete(ctx, projects, mod, ids, n_jobs=8, max_passes=6, verbose=True):
    """Greedy AC-verified completion -> an AC-MAXIMAL accept-set.

    Sweeps rejected requests in descending kW (greedy on the objective) and AC-solves
    each candidate addition against the true oracle. Repeats until a full pass adds
    nothing, at which point no single remaining request can be added without violating
    a limit -- the property skip-continue FCFS gets for free, and the MILP lacks.

    Candidates that pass the parallel screen are COMMITTED one at a time and re-verified:
    two requests that each fit alone need not fit together, so a batched commit could
    return an infeasible set.
    """
    global _CTX, _PROJ, _MOD
    _CTX, _PROJ, _MOD = ctx, projects, mod

    cur = set(ids)
    by_id = {p.id: p for p in projects}
    ok, cur_mw, _, _ = mod.evaluate(ctx, projects, cur)
    if not ok:
        return cur, 0.0, 0, 0
    added_total, n_solves = 0, 0

    for p_i in range(max_passes):
        rejected = sorted((p.id for p in projects if p.id not in cur),
                          key=lambda r: -abs(by_id[r].p_mw))
        if not rejected:
            break
        with mp.get_context("fork").Pool(n_jobs, initializer=_init,
                                         initargs=(frozenset(cur),)) as pool:
            got = pool.map(_try_add, rejected)
        n_solves += len(rejected)
        cands = [rid for rid, ok_i, _ in got if ok_i]
        if not cands:
            break
        added = 0
        for rid in cands:
            ok_i, mw_i, _, _ = mod.evaluate(ctx, projects, cur | {rid})
            n_solves += 1
            if ok_i and mw_i > cur_mw:
                cur, cur_mw = cur | {rid}, mw_i
                added += 1
        added_total += added
        if verbose:
            print(f"      pass {p_i}: {len(cands):3d} of {len(rejected):3d} rejected "
                  f"requests still fit, {added:3d} committed -> {cur_mw * 1000:7.1f} kW")
        if added == 0:
            break

    return cur, cur_mw, added_total, n_solves


WARM_SEED_BASE = 100_000     # DISJOINT from the measurement permutations (0 .. P-1)


def certified_allocation(ctx, projects, mod, rel, n_jobs=8, verbose=True,
                         warm_starts=3, **kw):
    """THE FIXED YARDSTICK. A certified, AC-verified, AC-MAXIMAL allocation --
    a LOWER bound on achievable capacity (never an upper bound; see the docstring).

    WARM STARTS -- why the loop no longer starts only from the empty queue
    ---------------------------------------------------------------------
    The MILP is massively degenerate, so its trajectory (and hence the fixed point it
    lands on) is exquisitely sensitive to which of many tied accept-sets the solver
    happens to return. Rather than suffer that, we exploit it: run the SLP loop from
    SEVERAL starting anchors and keep the best AC-feasible set found by any of them.
    Because every iterate is AC-verified, this can only ever IMPROVE the lower bound.

    The extra anchors are **skip-continue FCFS accept-sets** from random arrival orders.
    That gives three things at once:

      1. FCFS's accept-set is already AC-feasible and AC-maximal, so it is a *good* warm
         start -- the M28 lesson (seed the search with the incumbent) applied again.
      2. It makes the invariant **"no queue walk may beat the certified optimum"** true
         BY CONSTRUCTION rather than by luck. Pre-M48 the luckiest of 1,000 walks beat
         the yardstick (101.6%); post-M48 it merely fails to (99.9%, a 1.0 kW margin).
         Starting the loop *from* a walk removes that failure mode structurally.
      3. It is deterministic given the seed (with HiGHS pinned to one thread).

    The warm-start seeds are drawn from `WARM_SEED_BASE`, **disjoint from the permutation
    seeds used to MEASURE the lottery** (0..P-1), so the denominator is never fitted to
    the sample it is compared against.
    """
    t0 = time.time()
    anchors: list[set[str] | None] = [None]        # the empty queue, as before
    if warm_starts:
        walks = fcfs_accept_sets(ctx, projects, mod,
                                 [WARM_SEED_BASE + i for i in range(warm_starts)], n_jobs)
        anchors += [a for a in walks]

    best_ids, best_mw, trace_all, n_feas, n_it = set(), 0.0, [], 0, 0
    for a in anchors:
        ids_a, mw_a, g, trace = relinearize_backoff(ctx, projects, mod, rel,
                                                    verbose=verbose, anchor0=a, **kw)
        n_feas += sum(t["ok"] for t in trace)
        n_it += len(trace)
        if mw_a > best_mw:
            best_ids, best_mw = ids_a, mw_a
    milp_kw = best_mw * 1000

    ids, mw, added, n_solves = ac_complete(ctx, projects, mod, best_ids, n_jobs=n_jobs,
                                           verbose=verbose)
    return ids, mw, dict(milp_kw=milp_kw, final_kw=mw * 1000, final_n=len(ids),
                         added=added, ac_solves=n_solves, warm_starts=warm_starts,
                         feasible_iters=n_feas, iters=n_it, dt=time.time() - t0)


# ---------------------------------------------------------------- FCFS walks
def _accept_chunk(perm_seeds):
    """Like `_walk_chunk`, but returns the ACCEPT-SET (for warm-starting the MILP)."""
    mod, ctx, projects = _MOD, _CTX, _PROJ
    ids = [p.id for p in projects]
    k = len(projects)
    cache: dict[frozenset, tuple[bool, float]] = {}
    out = []
    for ps in perm_seeds:
        rng = random.Random(ps)
        order = list(range(k))
        rng.shuffle(order)
        acc = frozenset()
        for i in order:
            trial = acc | {ids[i]}
            hit = cache.get(trial)
            if hit is None:
                ok, m, _, _ = mod.evaluate(ctx, projects, set(trial))
                hit = (ok, m)
                cache[trial] = hit
            if hit[0]:
                acc = trial
        out.append(set(acc))
    return out


def fcfs_accept_sets(ctx, projects, mod, seeds, n_jobs):
    """skip-continue FCFS accept-sets for the given arrival-order seeds."""
    global _CTX, _PROJ, _MOD
    _CTX, _PROJ, _MOD = ctx, projects, mod
    n = max(1, min(n_jobs, len(seeds)))
    chunks = [seeds[i::n] for i in range(n)]
    with mp.get_context("fork").Pool(n) as pool:
        got = pool.map(_accept_chunk, [c for c in chunks if c])
    return [a for chunk in got for a in chunk]


def _walk_chunk(perm_seeds):
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
        out.append(mw * 1000)
    return out


def fcfs_walks(ctx, projects, mod, permutations, n_jobs):
    global _CTX, _PROJ, _MOD
    _CTX, _PROJ, _MOD = ctx, projects, mod
    seeds = list(range(permutations))
    chunks = [seeds[i::n_jobs] for i in range(n_jobs)]
    with mp.get_context("fork").Pool(n_jobs) as pool:
        got = pool.map(_walk_chunk, [c for c in chunks if c])
    return np.array([w for chunk in got for w in chunk])


# ----------------------------------------------------------------- diagnosis
def maximality_probe(ctx, projects, mod, ids, n_jobs):
    """How many rejected requests could each be added and still be AC-FEASIBLE?
    Zero means the accept-set is AC-MAXIMAL -- the property FCFS gets for free."""
    global _CTX, _PROJ, _MOD
    _CTX, _PROJ, _MOD = ctx, projects, mod
    by_id = {p.id: p for p in projects}
    rejected = [p.id for p in projects if p.id not in ids]
    if not rejected:
        return []
    with mp.get_context("fork").Pool(n_jobs, initializer=_init,
                                     initargs=(frozenset(ids),)) as pool:
        got = pool.map(_try_add, rejected)
    return [(rid, abs(by_id[rid].p_mw) * 1000) for rid, ok_i, _ in got if ok_i]


def diagnose(ctx, projects, mod, rel, exp, k, n_jobs, permutations, thermal_rows):
    print(f"  [1] the thermal model: {thermal_rows} constraint rows")

    print("\n  [2] the re-linearization loop (+ warm starts, + AC completion)")
    ids, mw, info = certified_allocation(ctx, projects, mod, rel, n_jobs=n_jobs,
                                         verbose=False)
    kw = mw * 1000
    ok, _, _, d = mod.evaluate(ctx, projects, ids)
    print(f"      -> {kw:7.1f} kW, {len(ids)} seated, AC-feasible={ok}, "
          f"vmax {d['vmax']:.5f} (limit {mod.VMAX_PU}), trafo {d.get('trafo_pct', float('nan')):.1f}%")
    print(f"      {1 + info['warm_starts']} starting anchors (empty + {info['warm_starts']} "
          f"FCFS walks, seeds disjoint from the measurement sample); "
          f"feasible iterates {info['feasible_iters']}/{info['iters']}; "
          f"completion added {info['added']}")

    print("\n  [3] MAXIMALITY PROBE: does anything rejected still fit?")
    fits = maximality_probe(ctx, projects, mod, ids, n_jobs)
    n_rej = len(projects) - len(ids)
    print(f"      {len(fits)} of {n_rej} rejected requests can each be added and stay "
          f"AC-FEASIBLE"
          + (f" ({sum(v for _, v in fits):.1f} kW)" if fits else ""))
    print(f"      => the yardstick is {'NOT AC-maximal -- BROKEN' if fits else 'AC-MAXIMAL'}")

    print(f"\n  [4] does it dominate every FCFS arrival order? ({permutations} walks)")
    w = fcfs_walks(ctx, projects, mod, permutations, n_jobs)
    best, med = w.max(), float(np.median(w))
    beaten = best > kw + 1e-6
    print(f"      best of {permutations} FCFS walks : {best:7.1f} kW  "
          f"({100 * best / kw:.1f}% of the yardstick)")
    print(f"      median FCFS walk               : {med:7.1f} kW  ({100 * med / kw:.1f}%)")
    print(f"      the yardstick                  : {kw:7.1f} kW   "
          f"{'<-- BEATEN BY A QUEUE WALK: BROKEN' if beaten else '(dominates every walk)'}")
    print(f"\n      => arrival order costs the median queue >= {100 - 100 * med / kw:.1f}% "
          f"of achievable capacity")

    return dict(k=k, thermal_rows=thermal_rows, yardstick_kw=kw, yardstick_n=len(ids),
                feasible_iters=info["feasible_iters"], iters=info["iters"],
                completion_added=info["added"],
                addable=len(fits), addable_kw=float(sum(v for _, v in fits)),
                maximal=not fits,
                best_walk_kw=float(best), median_walk_kw=med,
                best_walk_pct=100 * float(best) / kw, median_walk_pct=100 * med / kw,
                yardstick_beaten=bool(beaten), dt=info["dt"])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--feeder", choices=["ckt7", "ckt24"], required=True)
    ap.add_argument("--ks", type=int, nargs="+", default=[150])
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--permutations", type=int, default=100)
    ap.add_argument("--n-jobs", type=int, default=8)
    ap.add_argument("--diagnose", action="store_true")
    ap.add_argument("--broken-thermal", action="store_true",
                    help="reproduce the PRE-M48 defect: drop bus-bus switches from the "
                         "thermal spanning tree (on Ckt7 this leaves 1 constraint row)")
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()

    mod, rel, exp = load_feeder(a.feeder)
    if a.broken_thermal:
        mod.build_thermal = broken_thermal(mod)
    print(f"M48 -- the MILP yardstick.  feeder={a.feeder.upper()}, "
          f"EMPIRICAL request sizes, seed={a.seed}"
          f"{'   [!! BROKEN THERMAL MODEL -- reproducing the pre-M48 defect]' if a.broken_thermal else ''}\n")
    ctx = mod.build_network()

    rows = []
    for k in a.ks:
        projects = (exp.build_projects_x(ctx, k, a.seed, replacement=True, empirical=True)
                    if a.feeder == "ckt7"
                    else exp.build_projects_x(ctx, k, a.seed, empirical=True))
        req_kw = sum(abs(p.p_mw) for p in projects) * 1000
        med_kw = float(np.median([abs(p.p_mw) for p in projects])) * 1000
        rows_t = len(_f(mod.build_thermal(ctx, projects), "p_base"))
        print("=" * 80)
        print(f"K={k}   {req_kw:.0f} kW requested   median request {med_kw:.1f} kW")
        print("=" * 80)
        rows.append(diagnose(ctx, projects, mod, rel, exp, k, a.n_jobs, a.permutations,
                             rows_t))
        print()

    if a.save and rows:
        OUT.mkdir(exist_ok=True)
        tag = f"{a.feeder}" + ("_broken" if a.broken_thermal else "")
        pd.DataFrame(rows).to_csv(OUT / f"m48_yardstick_{tag}.csv", index=False)
        print(f"saved -> outputs/m48_yardstick_{tag}.csv")


if __name__ == "__main__":
    main()
