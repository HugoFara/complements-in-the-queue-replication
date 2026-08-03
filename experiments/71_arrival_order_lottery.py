"""M43: the price of arrival order -- what does FCFS cost, and is it luck?

THE CLAIM THIS EXPERIMENT EXISTS TO TEST (and the one it replaces)
-----------------------------------------------------------------
M42 observed that on Ckt7, across 3 request draws at fixed penetration, the
ACHIEVABLE total barely moved (1089-1130 kW at MODERATE) while FCFS's realized
total swung 337-780 kW. That got summarized as "only the arrival order differs."

**That summary was WRONG and is retracted here.** Three seeds = three different
REQUEST SETS, each with one arrival order. Both varied. The observation is
suggestive but it does NOT isolate arrival order, and no claim about queue
position can rest on it.

This experiment isolates it properly:

  E1 (the lottery).  ONE feeder. ONE request set, held fixed. Sample many
      arrival ORDERS. Run skip-continue FCFS on each. The only thing that
      varies is luck of the draw in the queue.
      -> a distribution of realized capacity, against a certified optimum.

  E2 (the sort key). Same feeder, same fixed request set, one FCFS walk per
      ordering rule: arrival (random), smallest-first, largest-first,
      value-density (the knapsack greedy), MILP optimum.
      -> is the loss intrinsic to SERIAL allocation, or just to sorting by
         arrival date? Both answers are publishable; that is the point.

  E3 (position value). Across the E1 permutations, P(project seated) as a
      function of its queue position, holding the project's physics fixed.
      -> two electrically identical projects, different slots, different fates.

A NOTE ON THE KNAPSACK ANALOGY (E2), because it collapses in a way worth saying
------------------------------------------------------------------------------
Under GLDF the voltage rise a request causes at the critical bus is linear in
its size: weight_i = s(bus_i) * |p_i|, where s(bus) is the bus's voltage
sensitivity. Its value is the capacity it seats: value_i = |p_i|. So the
classic knapsack value-density ratio is

    value_i / weight_i = |p_i| / (s(bus_i) * |p_i|) = 1 / s(bus_i)

which is INDEPENDENT OF REQUEST SIZE. The knapsack-optimal greedy key is
therefore "least voltage-sensitive bus first" -- a purely LOCATIONAL rule.
Sorting by size (either direction) is not it. That is a testable, falsifiable
prediction of the analogy, and E2 tests it.

SCOPE / HONEST LIMITS
---------------------
- A uniform random permutation is a MODEL of arrival order, and a crude one:
  real queues correlate with developer size, project size and location. E2's
  structured orders (largest-first ~ "big developers move first") are reported
  alongside so the reader can see where a realistic order lands inside the
  lottery distribution.
- Single snapshot, constant-power PV, no smart-inverter Volt/VAr -- unchanged
  from the rest of this project, and it bites here too.
- The MILP optimum is the re-linearized fixed point of M41/M42: AC-verified,
  no safety margin, no repair step. It is a CERTIFIED allocation, hence a lower
  bound on the true optimum -- so every "% of optimum" below is conservative.
"""

from __future__ import annotations

import argparse
import multiprocessing as mp
import random
import time
from pathlib import Path

import numpy as np
import pandas as pd

_c = __import__("68_ckt7_gldf_headline")
_r = __import__("70_ckt7_relinearize")
_e = __import__("73_empirical_request_distribution")

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "outputs"

_CTX = None       # per-worker network context
EMPIRICAL = False  # set in main(); inherited by forked workers


def build_projects_x(ctx, k, seed, replacement=True, empirical=False):
    """The request set. `empirical=True` keeps the IDENTICAL bus draw and swaps ONLY the
    size distribution -- uniform[10,100] kW -> the CA interconnection queue (M45) -- so
    any change in the result is attributable to the size distribution alone.

    The real queue is ~97% residential rooftop (median 6 kW) with a thin heavy tail to
    1 MW; the synthetic band contained under a fifth of real requests. See
    data/interconnection/PROVENANCE.md.
    """
    base = _c.build_projects(ctx, k, seed, replacement=replacement)
    if not empirical:
        return base
    rng = random.Random(seed + 10_000)          # independent stream: buses stay fixed
    kws = _e.sample_kw(rng, len(base))
    return [_c.Project(id=p.id, bus=p.bus, p_mw=-float(kw) / 1000.0)
            for p, kw in zip(base, kws)]


# ------------------------------------------------------------------ the FCFS walk
def make_walker(ctx, projects):
    """skip-continue FCFS, memoized on the accept-set.

    `_c.evaluate` depends ONLY on the set of accepted ids, so it is safe to cache
    on `frozenset(trial)`. Across many permutations the early, small accept-sets
    recur constantly -- this is what makes 1,000 queue walks affordable.
    """
    cache: dict[frozenset, tuple[bool, float]] = {}
    by_idx = [p.id for p in projects]

    def feasible(trial: frozenset) -> tuple[bool, float]:
        hit = cache.get(trial)
        if hit is None:
            ok, mw, _, _ = _c.evaluate(ctx, projects, set(trial))
            hit = (ok, mw)
            cache[trial] = hit
        return hit

    def walk(order: list[int]) -> tuple[frozenset, float]:
        acc, mw = frozenset(), 0.0
        for i in order:
            trial = acc | {by_idx[i]}
            ok, m = feasible(trial)
            if ok:
                acc, mw = trial, m
        return acc, mw

    return walk, cache


def _init_worker(scenario, seed, replacement):
    global _CTX
    _CTX = _c.build_network()


def _run_chunk(args):
    """One worker: a block of permutations, its own accept-set cache."""
    scenario, seed, replacement, perm_seeds = args
    ctx = _CTX
    projects = build_projects_x(ctx, _c.SCENARIOS[scenario], seed,
                                replacement=replacement, empirical=EMPIRICAL)
    walk, cache = make_walker(ctx, projects)
    k = len(projects)

    rows = []
    for ps in perm_seeds:
        rng = random.Random(ps)
        order = list(range(k))
        rng.shuffle(order)
        acc, mw = walk(order)
        # E3 bookkeeping: position of every project in THIS queue, and its fate.
        pos = np.empty(k, dtype=int)
        for slot, i in enumerate(order):
            pos[i] = slot
        seated = np.array([projects[i].id in acc for i in range(k)])
        rows.append((ps, mw * 1000, len(acc), pos, seated))
    return rows, len(cache)


# ------------------------------------------------------------------ the optimum
def relinearized_optimum(ctx, projects, max_iter=12, time_limit=180.0, verbose=True):
    """M41/M42's loop, returning the accept-set: solve -> AC-solve -> re-anchor there
    -> re-solve, to a fixed point. AC-verified, margin 0, no repair."""
    ids_all = [p.id for p in projects]
    g = _c.build_gldf(ctx, [p.bus for p in projects])
    A_v = np.zeros((len(g["pq"]), len(projects)))
    for r, p in enumerate(projects):
        col = g["m_cols"].get(int(p.bus))
        if col is not None:
            A_v[:, r] = col * (-p.p_mw / g["base_mva"])

    inv = {int(g["lookup"][b]): b for b in ctx.net.bus.index}
    pq_pp = [inv[int(i)] for i in g["pq"]]

    anchor: set[str] = set()
    ctx_a = _r.anchored_context(ctx, projects, anchor)
    cuts: list[set[str]] = []
    best_ids, best_mw = set(), 0.0

    for it in range(max_iter):
        v_anchor = np.array([ctx_a.net.res_bus.vm_pu[b] for b in pq_pp]) ** 2
        th = _c.build_thermal(ctx_a, projects)
        A_t = np.zeros((len(th["p_base"]), len(projects)))
        for bi, mem in enumerate(th["members"]):
            for r in mem:
                A_t[bi, r] = -projects[r].p_mw

        a = np.array([1.0 if i in anchor else 0.0 for i in ids_all])
        v_base_eff = v_anchor - A_v @ a
        p_base_eff = th["p_base"] + (A_t @ a if len(th["p_base"]) else 0.0)

        x, _ = _r.milp_anchored(projects, A_v, th, A_t, v_base_eff, p_base_eff,
                                cuts, time_limit)
        if x is None:
            break
        ok, mw, _, _ = _c.evaluate(ctx, projects, x)
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
        ctx_a = _r.anchored_context(ctx, projects, anchor)

    # M41/M42 found the fixed point AC-feasible at margin 0 on 6/6 -- but that was on the
    # SYNTHETIC uniform[10,100] band (~40 accepted requests). Under the EMPIRICAL
    # distribution the MILP accepts ~190 tiny requests, the linearization error
    # accumulates across them, and the fixed point can land just outside the AC limit.
    # So the repair step (which M42 retired) is needed again. Without this fallback the
    # loop silently returns 0 kW.
    if not best_ids and anchor:
        rep_ids, rep_mw = _c.ac_repair(ctx, projects, g, set(anchor))
        if verbose:
            print(f"    fixed point AC-infeasible -> repair dropped "
                  f"{len(anchor) - len(rep_ids)} request(s) -> {rep_mw * 1000:.1f} kW")
        best_ids, best_mw = rep_ids, rep_mw

    return best_ids, best_mw, g


# ------------------------------------------------------------------ E2 sort keys
def binding_bus_row(ctx, projects, g, opt_ids):
    """Index (into g["pq"]) of the bus that BINDS at the optimum -- i.e. the row of M
    that the knapsack weight should actually be read from. Requires the solution."""
    inv = {int(g["lookup"][b]): b for b in ctx.net.bus.index}
    pq_pp = [inv[int(i)] for i in g["pq"]]
    ctx_o = _r.anchored_context(ctx, projects, opt_ids)
    return int(np.argmax(ctx_o.net.res_bus.vm_pu.loc[pq_pp].values))


def sort_keys(ctx, projects, g, crit_row=None):
    """Orderings to walk.

    The knapsack value-density ratio is EXACTLY `1 / s(bus)` (see the module
    docstring): value_i / weight_i = |p_i| / (s(bus_i)*|p_i|). It is independent of
    request size, so it cannot discriminate between two requests at the SAME bus --
    they are exact ties.

    That tie is not a detail. With replacement, 150 requests land on 100 buses, and
    breaking those ties differently moved the result by 16% in the first pilot. So we
    report the density rule under two tie-breaks, and the gap between them is itself a
    finding:
      value_density         -- ties broken smallest-first (the principled secondary key:
                               among equal-ratio items, small ones pack better)
      least_sensitive_first -- ties broken by ARRIVAL order (i.e. the lottery, again,
                               at a finer grain)

    (A first version computed `density = size / (sens*size)` rather than `1/sens`.
    Float rounding then turned the exact ties into pseudo-random tie-breaks, which is
    how the tie-sensitivity was noticed at all.)
    """
    k = len(projects)
    # dv/dP at each request's own bus, worst case over PQ buses (v = |V|^2 units)
    sens = np.zeros(k)
    for i, p in enumerate(projects):
        col = g["m_cols"].get(int(p.bus))
        sens[i] = float(np.max(col)) / g["base_mva"] if col is not None else 0.0

    size = np.array([abs(p.p_mw) for p in projects])
    with np.errstate(divide="ignore"):
        density = np.where(sens > 0, 1.0 / sens, np.inf)   # the exact knapsack ratio

    keys = {
        "smallest_first": list(np.argsort(size, kind="stable")),
        "largest_first": list(np.argsort(-size, kind="stable")),
        # lexicographic: ascending sensitivity, then ascending size
        "value_density": list(np.lexsort((size, sens))),
        # ascending sensitivity, ties keep arrival order (stable)
        "least_sensitive_first": list(np.argsort(sens, kind="stable")),
    }

    # The knapsack weight is NOT a request's own voltage sensitivity -- it is how much
    # the request loads the constraint that actually BINDS. Those are different, and on
    # Ckt7 they are uncorrelated (r = -0.03). This key uses the correct weight. It is
    # reported as an upper bound on what the knapsack framing can do, NOT as a
    # deployable rule: it needs the binding bus, which you only learn by solving the
    # problem you were trying to avoid solving.
    if crit_row is not None:
        crit = np.array([float(g["m_cols"][int(p.bus)][crit_row])
                         if g["m_cols"].get(int(p.bus)) is not None else 0.0
                         for p in projects])
        keys["binding_bus_density*"] = list(np.lexsort((size, crit)))

    return keys, sens, density


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--scenario", default="MODERATE", choices=list(_c.SCENARIOS))
    ap.add_argument("--seed", type=int, default=0, help="fixes the REQUEST SET")
    ap.add_argument("--permutations", type=int, default=1000)
    ap.add_argument("--no-replacement", action="store_true")
    ap.add_argument("--n-jobs", type=int, default=6)
    ap.add_argument("--empirical", action="store_true",
                    help="draw request SIZES from the CA interconnection queue (M45) "
                         "instead of uniform[10,100] kW; buses unchanged")
    ap.add_argument("--sortkeys-only", action="store_true",
                    help="E2 only: skip the permutation lottery (E1/E3)")
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()
    replacement = not a.no_replacement
    global EMPIRICAL
    EMPIRICAL = a.empirical

    print(f"M43 -- arrival-order lottery on EPRI Ckt7")
    print(f"  request set FIXED: {a.scenario} seed={a.seed} "
          f"(replacement={replacement}); {a.permutations} arrival orders\n")

    ctx = _c.build_network()
    projects = build_projects_x(ctx, _c.SCENARIOS[a.scenario], a.seed,
                                replacement=replacement, empirical=a.empirical)
    k = len(projects)
    total_kw = sum(abs(p.p_mw) for p in projects) * 1000
    print(f"  {k} requests, {len({p.bus for p in projects})} distinct buses, "
          f"{total_kw:.0f} kW requested\n")

    # --- the certified optimum (the yardstick) -----------------------------
    print("  MILP optimum (re-linearized fixed point, AC-verified):")
    t0 = time.time()
    opt_ids, opt_mw, g = relinearized_optimum(ctx, projects)
    opt_kw = opt_mw * 1000
    print(f"  -> optimum {opt_kw:.1f} kW ({len(opt_ids)} seated)  [{time.time()-t0:.0f}s]\n")

    # --- E2 only? ----------------------------------------------------------
    if a.sortkeys_only:
        crit = binding_bus_row(ctx, projects, g, opt_ids)
        keys, sens, density = sort_keys(ctx, projects, g, crit)
        walk, _ = make_walker(ctx, projects)
        print("  E2: does a better sort key recover the loss?")
        for name, order in keys.items():
            _, mw = walk(order)
            print(f"    {name:<22s} {mw*1000:7.1f} kW  "
                  f"({100*mw*1000/opt_kw:5.1f}% of optimum)")
        print(f"    {'MILP optimum':<22s} {opt_kw:7.1f} kW  (100.0%)")
        ties = len(projects) - len({p.bus for p in projects})
        print(f"\n    ({ties} of {k} requests share a bus with another -- the "
              f"knapsack ratio cannot separate them; the tie-break decides.)")
        if a.save:
            OUT.mkdir(exist_ok=True)
            tag = f"{a.scenario.lower()}{a.seed}" + ("" if replacement else "_norepl")
            rows = [(n, walk(o)[1] * 1000) for n, o in keys.items()]
            pd.DataFrame({"rule": [r for r, _ in rows] + ["milp_optimum"],
                          "kw": [v for _, v in rows] + [opt_kw],
                          "pct_of_opt": [100 * v / opt_kw for _, v in rows] + [100.0]
                          }).to_csv(OUT / f"m43_sortkeys_{tag}.csv", index=False)
            print(f"  saved -> outputs/m43_sortkeys_{tag}.csv")
        return

    # --- E1: the lottery ---------------------------------------------------
    print(f"  E1: walking {a.permutations} arrival orders on {a.n_jobs} workers...")
    t0 = time.time()
    perm_seeds = list(range(a.permutations))
    chunks = [perm_seeds[i::a.n_jobs] for i in range(a.n_jobs)]
    args = [(a.scenario, a.seed, replacement, c) for c in chunks if c]
    with mp.Pool(a.n_jobs, initializer=_init_worker,
                 initargs=(a.scenario, a.seed, replacement)) as pool:
        out = pool.map(_run_chunk, args)

    rows = [r for chunk, _ in out for r in chunk]
    n_cached = sum(nc for _, nc in out)
    kws = np.array([r[1] for r in rows])
    ns = np.array([r[2] for r in rows])
    print(f"  -> {len(rows)} walks in {time.time()-t0:.0f}s "
          f"({n_cached} distinct accept-sets evaluated)\n")

    lo, hi = kws.min(), kws.max()
    print("  E1 RESULT -- the same feeder, the same requests, only the order differs:")
    print(f"    FCFS realized : {lo:7.1f} - {hi:7.1f} kW   "
          f"(mean {kws.mean():.1f}, sd {kws.std():.1f}, spread {hi/lo:.2f}x)")
    print(f"    MILP optimum  : {opt_kw:7.1f} kW  (certified, AC-verified)")
    print(f"    FCFS as % of optimum: {100*lo/opt_kw:.1f}% (worst order) "
          f"to {100*hi/opt_kw:.1f}% (luckiest order), mean {100*kws.mean()/opt_kw:.1f}%")
    print(f"    projects seated: {ns.min()}-{ns.max()} of {k}\n")

    # --- E2: the sort key --------------------------------------------------
    print("  E2: does a better sort key recover the loss?")
    crit = binding_bus_row(ctx, projects, g, opt_ids)
    keys, sens, density = sort_keys(ctx, projects, g, crit)
    walk, _ = make_walker(ctx, projects)
    e2 = {}
    for name, order in keys.items():
        _, mw = walk(order)
        e2[name] = mw * 1000
        pct = 100 * mw * 1000 / opt_kw
        # where does this deterministic rule land inside the lottery?
        rank = 100.0 * (kws < mw * 1000).mean()
        print(f"    {name:<22s} {mw*1000:7.1f} kW  ({pct:5.1f}% of optimum, "
              f"beats {rank:5.1f}% of random orders)")
    print(f"    {'arrival (random) mean':<22s} {kws.mean():7.1f} kW  "
          f"({100*kws.mean()/opt_kw:5.1f}% of optimum)")
    print(f"    {'MILP optimum':<22s} {opt_kw:7.1f} kW  (100.0%)\n")

    # --- E3: position value ------------------------------------------------
    pos = np.stack([r[3] for r in rows])       # (P, k)
    seat = np.stack([r[4] for r in rows])      # (P, k) bool
    decile = np.minimum((pos * 10) // k, 9)
    print("  E3: P(seated) by queue position, over all projects x permutations:")
    print("    decile:  " + " ".join(f"{d:5d}" for d in range(10)))
    rates = [100 * seat[decile == d].mean() for d in range(10)]
    print("    P(seat): " + " ".join(f"{r:5.1f}" for r in rates))
    first, last = rates[0], rates[-1]
    print(f"    -> first decile {first:.1f}%  vs  last decile {last:.1f}%   "
          f"({first/last if last else float('inf'):.1f}x)\n")

    # per-project: how much does a project's fate depend on ITS OWN physics vs luck?
    per_proj = seat.mean(axis=0)
    lottery = ((per_proj > 0.05) & (per_proj < 0.95)).sum()
    print(f"    projects seated in >95% of orders (physics decides): "
          f"{(per_proj >= 0.95).sum()}")
    print(f"    projects seated in <5%  of orders (physics decides): "
          f"{(per_proj <= 0.05).sum()}")
    print(f"    projects whose fate is a COIN FLIP (5-95%):          {lottery}"
          f"   <- the queue lottery\n")

    if a.save:
        OUT.mkdir(exist_ok=True)
        tag = f"{a.scenario.lower()}{a.seed}" + ("" if replacement else "_norepl")
        pd.DataFrame({"perm_seed": [r[0] for r in rows], "fcfs_kw": kws,
                      "fcfs_n": ns, "opt_kw": opt_kw,
                      "pct_of_opt": 100 * kws / opt_kw}).to_csv(
            OUT / f"m43_lottery_{tag}.csv", index=False)
        pd.DataFrame({"rule": list(e2) + ["arrival_mean", "milp_optimum"],
                      "kw": [e2[n] for n in e2] + [kws.mean(), opt_kw],
                      "pct_of_opt": [100 * e2[n] / opt_kw for n in e2]
                                    + [100 * kws.mean() / opt_kw, 100.0]}).to_csv(
            OUT / f"m43_sortkeys_{tag}.csv", index=False)
        pd.DataFrame({"project": [p.id for p in projects],
                      "bus": [p.bus for p in projects],
                      "kw": [abs(p.p_mw) * 1000 for p in projects],
                      "sensitivity": sens, "value_density": density,
                      "seat_rate": per_proj,
                      "in_milp_optimum": [p.id in opt_ids for p in projects]}).to_csv(
            OUT / f"m43_projects_{tag}.csv", index=False)
        print(f"  saved -> outputs/m43_*_{tag}.csv")


if __name__ == "__main__":
    main()
