"""M44: the arrival-order lottery on EPRI Ckt24 -- does M43 generalize?

M43 (experiments/71, Ckt7) held the request set FIXED and permuted only the queue,
and found: FCFS realizes 14-81% of a certified optimum depending purely on arrival
order (p95/p5 = 3.0x); a trivial smallest-first sort key recovers ~90%; the
knapsack value-density rule -- which the roadmap DERIVED should be optimal --
reaches only 31-57%; and P(seated) is 30x higher in the first queue decile than the
last.

Ckt7 is 1,255 buses and purely overvoltage-limited. Ckt24 is 6,058 buses, meshed,
with shunt capacitors, line charging, a structurally-overloaded substation
transformer, and BOTH voltage and thermal constraints binding. If the lottery is a
property of serial allocation rather than of one feeder's quirks, it must show up
here too -- and the mechanism behind smallest-first (spreading injection beats
concentrating it, because the constraint is per-bus rather than a single budget)
predicts it should.

This is the same three experiments, same code path, different feeder:
  E1  the lottery      -- N arrival orders on ONE fixed request set
  E2  the sort key     -- can a cheap ordering rule recover the loss?
  E3  position value   -- P(seated) as a function of queue slot

The optimum is exp69/M41's re-linearized fixed point: AC-verified at margin zero,
no repair step. It is a CERTIFIED allocation, hence a LOWER bound on the true
optimum -- so every "% of optimum" here is an UPPER bound on that rule's true
share. The direction is conservative.
"""

from __future__ import annotations

import argparse
import multiprocessing as mp
import random
import time
from pathlib import Path

import numpy as np
import pandas as pd

_b = __import__("64_ckt24_gldf_bound")
_q = __import__("69_ckt24_relinearize")
_e = __import__("73_empirical_request_distribution")

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "outputs"

_CTX = None
EMPIRICAL = False  # set in main(); inherited by forked workers


def build_projects_x(ctx, k, seed, empirical=False):
    """Same bus draw; `empirical=True` swaps ONLY the size distribution
    (uniform[10,100] kW -> the CA interconnection queue, M45), so any change is
    attributable to the size distribution alone."""
    base = _b.build_projects(ctx, k, seed)
    if not empirical:
        return base
    rng = random.Random(seed + 10_000)
    kws = _e.sample_kw(rng, len(base))
    return [_b.Project(id=p.id, bus=p.bus, p_mw=-float(kw) / 1000.0)
            for p, kw in zip(base, kws)]


def make_walker(ctx, projects):
    """skip-continue FCFS, memoized on the accept-set (`_b.evaluate` depends only on
    the set of accepted ids)."""
    cache: dict[frozenset, tuple[bool, float]] = {}
    ids = [p.id for p in projects]

    def feasible(trial: frozenset):
        hit = cache.get(trial)
        if hit is None:
            ok, mw, _, _ = _b.evaluate(ctx, projects, set(trial))
            hit = (ok, mw)
            cache[trial] = hit
        return hit

    def walk(order):
        acc, mw = frozenset(), 0.0
        for i in order:
            trial = acc | {ids[i]}
            ok, m = feasible(trial)
            if ok:
                acc, mw = trial, m
        return acc, mw

    return walk, cache


# The workers must NOT rebuild the network. Ckt24's substation has a regulator, and
# importing its OpenDSS model makes the engine write `REG_subxfmr_regulator.csv` into
# the CWD -- so N workers racing to create the same file fail with OpenDSS error #303
# ("Bad file number"). They do not need OpenDSS at all: `_b.evaluate` only runs
# `pp.runpp` on an already-imported pandapower net. So the parent builds `ctx` once and
# the fork start method (Linux default) shares it copy-on-write. (Ckt7/exp71 never hit
# this because it has no regulator to write.)
def _run_chunk(args):
    scenario, seed, perm_seeds = args
    ctx = _CTX
    projects = build_projects_x(ctx, _b.SCENARIOS[scenario], seed,
                                empirical=EMPIRICAL)
    walk, cache = make_walker(ctx, projects)
    k = len(projects)

    rows = []
    for ps in perm_seeds:
        rng = random.Random(ps)
        order = list(range(k))
        rng.shuffle(order)
        acc, mw = walk(order)
        pos = np.empty(k, dtype=int)
        for slot, i in enumerate(order):
            pos[i] = slot
        seated = np.array([projects[i].id in acc for i in range(k)])
        rows.append((ps, mw * 1000, len(acc), pos, seated))
    return rows, len(cache)


def relinearized_optimum(ctx, projects, max_iter=10, time_limit_s=180.0, mip_gap=1e-4,
                         verbose=True):
    """exp69/M41's loop, returning the accept-set."""
    ids_all = [p.id for p in projects]
    g = _b.build_gldf(ctx, [p.bus for p in projects])
    A_v = np.zeros((len(g.pq), len(projects)))
    for r, p in enumerate(projects):
        col = g.m_cols.get(int(p.bus))
        if col is not None:
            A_v[:, r] = col * (-p.p_mw / g.base_mva)

    anchor: set[str] = set()
    ctx_a = _q.anchored_context(ctx, projects, anchor)
    cuts: list[set[str]] = []
    best_ids, best_mw = set(), 0.0

    for it in range(max_iter):
        v_anchor = _q.v_at(ctx_a, g.pq, g.lookup)
        th = _b.build_thermal(ctx_a, projects)
        A_t = np.zeros((len(th.p_base), len(projects)))
        for bi, mem in enumerate(th.members):
            for r in mem:
                A_t[bi, r] = -projects[r].p_mw

        a = np.array([1.0 if i in anchor else 0.0 for i in ids_all])
        v_base_eff = v_anchor - A_v @ a
        p_base_eff = th.p_base + (A_t @ a if len(th.p_base) else 0.0)

        x, _ = _q.milp_anchored(projects, g, A_v, th, A_t, v_base_eff, p_base_eff,
                                cuts, time_limit_s, mip_gap)
        if x is None:
            break
        ok, mw, _, _ = _b.evaluate(ctx, projects, x)
        if verbose:
            print(f"    milp it{it}  {mw * 1000:7.1f} kW ({len(x):3d})  "
                  f"{'AC-FEASIBLE' if ok else 'infeasible'}")
        if ok and mw > best_mw:
            best_ids, best_mw = set(x), mw
        if x == anchor:
            break
        if not ok:
            cuts.append(set(x))
        anchor = set(x)
        ctx_a = _q.anchored_context(ctx, projects, anchor)

    # See exp71: under the empirical distribution the fixed point can be AC-infeasible
    # (many small accepted requests accumulate linearization error), so M42's retired
    # repair step is needed again as a fallback.
    if not best_ids and anchor:
        rep_ids, rep_mw = _b.repair_to_ac_feasible(ctx, projects, g, set(anchor))
        if verbose:
            print(f"    fixed point AC-infeasible -> repair dropped "
                  f"{len(anchor) - len(rep_ids)} request(s) -> {rep_mw * 1000:.1f} kW")
        best_ids, best_mw = rep_ids, rep_mw

    return best_ids, best_mw, g


def binding_bus_row(ctx, projects, g, opt_ids):
    """Index (into g.pq) of the bus that BINDS at the optimum -- the row of M the
    knapsack weight should actually be read from. Requires the solution."""
    inv = {int(g.lookup[b]): b for b in ctx.net.bus.index}
    pq_pp = [inv[int(i)] for i in g.pq]
    ctx_o = _q.anchored_context(ctx, projects, opt_ids)
    return int(np.argmax(ctx_o.net.res_bus.vm_pu.loc[pq_pp].values))


def sort_keys(ctx, projects, g, crit_row=None):
    """Same rules as M43. The knapsack value-density ratio is exactly 1/s(bus) --
    independent of request size -- so it cannot separate two requests at the same bus;
    ties are broken smallest-first (principled) in `value_density` and by ARRIVAL order
    in `least_sensitive_first`, and the gap between them measures the tie lottery."""
    k = len(projects)
    sens = np.zeros(k)
    for i, p in enumerate(projects):
        col = g.m_cols.get(int(p.bus))
        sens[i] = float(np.max(col)) / g.base_mva if col is not None else 0.0

    size = np.array([abs(p.p_mw) for p in projects])
    with np.errstate(divide="ignore"):
        density = np.where(sens > 0, 1.0 / sens, np.inf)

    keys = {
        "smallest_first": list(np.argsort(size, kind="stable")),
        "largest_first": list(np.argsort(-size, kind="stable")),
        "value_density": list(np.lexsort((size, sens))),
        "least_sensitive_first": list(np.argsort(sens, kind="stable")),
    }
    # The weight the knapsack framing ACTUALLY calls for: how much a request loads the
    # bus that BINDS -- not the request's own bus sensitivity. On Ckt7 the two are
    # uncorrelated (r = -0.03). Starred because it is NOT deployable: it needs the
    # binding bus, which you only learn by solving the problem you wanted to avoid.
    if crit_row is not None:
        crit = np.array([float(g.m_cols[int(p.bus)][crit_row])
                         if g.m_cols.get(int(p.bus)) is not None else 0.0
                         for p in projects])
        keys["binding_bus_density*"] = list(np.lexsort((size, crit)))
    return keys, sens, density


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--scenario", default="MODERATE", choices=list(_b.SCENARIOS))
    ap.add_argument("--seed", type=int, default=0, help="fixes the REQUEST SET")
    ap.add_argument("--permutations", type=int, default=1000)
    ap.add_argument("--n-jobs", type=int, default=8)
    ap.add_argument("--empirical", action="store_true",
                    help="draw request SIZES from the CA interconnection queue (M45)")
    ap.add_argument("--sortkeys-only", action="store_true")
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()
    global EMPIRICAL
    EMPIRICAL = a.empirical

    print("M44 -- arrival-order lottery on EPRI Ckt24 (6,058 buses)")
    print(f"  request set FIXED: {a.scenario} seed={a.seed}; "
          f"{a.permutations} arrival orders\n")

    ctx = _b.build_network()
    projects = build_projects_x(ctx, _b.SCENARIOS[a.scenario], a.seed,
                                empirical=a.empirical)
    k = len(projects)
    total_kw = sum(abs(p.p_mw) for p in projects) * 1000
    print(f"  {k} requests, {len({p.bus for p in projects})} distinct buses, "
          f"{total_kw:.0f} kW requested\n")

    print("  MILP optimum (re-linearized fixed point, AC-verified):")
    t0 = time.time()
    opt_ids, opt_mw, g = relinearized_optimum(ctx, projects)
    opt_kw = opt_mw * 1000
    print(f"  -> optimum {opt_kw:.1f} kW ({len(opt_ids)} seated)  [{time.time()-t0:.0f}s]\n")

    crit = binding_bus_row(ctx, projects, g, opt_ids)
    keys, sens, density = sort_keys(ctx, projects, g, crit)

    if a.sortkeys_only:
        walk, _ = make_walker(ctx, projects)
        print("  E2: does a better sort key recover the loss?")
        for name, order in keys.items():
            _, mw = walk(order)
            print(f"    {name:<22s} {mw*1000:7.1f} kW  "
                  f"({100*mw*1000/opt_kw:5.1f}% of optimum)")
        print(f"    {'MILP optimum':<22s} {opt_kw:7.1f} kW  (100.0%)")
        return

    print(f"  E1: walking {a.permutations} arrival orders on {a.n_jobs} workers...")
    t0 = time.time()
    global _CTX
    _CTX = ctx                     # inherited by the forked workers; see _run_chunk
    perm_seeds = list(range(a.permutations))
    chunks = [perm_seeds[i::a.n_jobs] for i in range(a.n_jobs)]
    args = [(a.scenario, a.seed, c) for c in chunks if c]
    with mp.get_context("fork").Pool(a.n_jobs) as pool:
        out = pool.map(_run_chunk, args)

    rows = [r for chunk, _ in out for r in chunk]
    kws = np.array([r[1] for r in rows])
    ns = np.array([r[2] for r in rows])
    print(f"  -> {len(rows)} walks in {time.time()-t0:.0f}s\n")

    lo, hi = kws.min(), kws.max()
    p5, p50, p95 = np.percentile(kws, [5, 50, 95])
    print("  E1 RESULT -- same feeder, same requests, only the order differs:")
    print(f"    FCFS realized : {lo:7.1f} - {hi:7.1f} kW  (median {p50:.1f}, "
          f"p5 {p5:.1f}, p95 {p95:.1f})")
    print(f"    spread        : {hi/lo:.2f}x full range, {p95/p5:.2f}x p95/p5")
    print(f"    MILP optimum  : {opt_kw:7.1f} kW  (certified, AC-verified)")
    print(f"    FCFS as % of optimum: {100*lo/opt_kw:.1f}% (worst) .. "
          f"{100*hi/opt_kw:.1f}% (luckiest), median {100*p50/opt_kw:.1f}%")
    print(f"    projects seated: {ns.min()}-{ns.max()} of {k}\n")

    print("  E2: does a better sort key recover the loss?")
    walk, _ = make_walker(ctx, projects)
    e2 = {}
    for name, order in keys.items():
        _, mw = walk(order)
        e2[name] = mw * 1000
        rank = 100.0 * (kws < mw * 1000).mean()
        print(f"    {name:<22s} {mw*1000:7.1f} kW  ({100*mw*1000/opt_kw:5.1f}% of "
              f"optimum, beats {rank:5.1f}% of random orders)")
    print(f"    {'arrival (random) mean':<22s} {kws.mean():7.1f} kW  "
          f"({100*kws.mean()/opt_kw:5.1f}% of optimum)")
    print(f"    {'MILP optimum':<22s} {opt_kw:7.1f} kW  (100.0%)\n")

    pos = np.stack([r[3] for r in rows])
    seat = np.stack([r[4] for r in rows])
    decile = np.minimum((pos * 10) // k, 9)
    rates = [100 * seat[decile == d].mean() for d in range(10)]
    print("  E3: P(seated) by queue position:")
    print("    decile:  " + " ".join(f"{d:5d}" for d in range(10)))
    print("    P(seat): " + " ".join(f"{r:5.1f}" for r in rates))
    print(f"    -> first {rates[0]:.1f}%  vs last {rates[-1]:.1f}%  "
          f"({rates[0]/rates[-1] if rates[-1] else float('inf'):.1f}x)\n")

    per_proj = seat.mean(axis=0)
    print(f"    always seated (>95% of orders): {(per_proj >= 0.95).sum()}")
    print(f"    never  seated (<5%  of orders): {(per_proj <= 0.05).sum()}")
    print(f"    COIN FLIPS (5-95%):             "
          f"{((per_proj > 0.05) & (per_proj < 0.95)).sum()}\n")

    if a.save:
        OUT.mkdir(exist_ok=True)
        tag = f"{a.scenario.lower()}{a.seed}"
        pd.DataFrame({"perm_seed": [r[0] for r in rows], "fcfs_kw": kws,
                      "fcfs_n": ns, "opt_kw": opt_kw,
                      "pct_of_opt": 100 * kws / opt_kw}).to_csv(
            OUT / f"m44_lottery_ckt24_{tag}.csv", index=False)
        pd.DataFrame({"rule": list(e2) + ["arrival_mean", "milp_optimum"],
                      "kw": [e2[n] for n in e2] + [kws.mean(), opt_kw],
                      "pct_of_opt": [100 * e2[n] / opt_kw for n in e2]
                                    + [100 * kws.mean() / opt_kw, 100.0]}).to_csv(
            OUT / f"m44_sortkeys_ckt24_{tag}.csv", index=False)
        pd.DataFrame({"project": [p.id for p in projects],
                      "bus": [p.bus for p in projects],
                      "kw": [abs(p.p_mw) * 1000 for p in projects],
                      "sensitivity": sens, "seat_rate": per_proj,
                      "in_milp_optimum": [p.id in opt_ids for p in projects]}).to_csv(
            OUT / f"m44_projects_ckt24_{tag}.csv", index=False)
        print(f"  saved -> outputs/m44_*_ckt24_{tag}.csv")


if __name__ == "__main__":
    main()
