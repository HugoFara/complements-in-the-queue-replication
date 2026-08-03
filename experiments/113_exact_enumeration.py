"""M87 -- THE RESIDUAL WITHOUT AN OPTIMIZER: EXACT ENUMERATION AT SMALL K

WHAT THIS SETTLES
-----------------
Every residual in the paper is a difference of two HEURISTIC lower bounds. GLDF-guided search
plus AC verification certifies feasibility, never optimality, so JOINT and max(sequential) are
each lower bounds on different optimization problems searched under different anchorings, and
their difference inherits no guarantee at all. A nonzero residual is, on that evidence alone,
observationally indistinguishable from "the joint search happened to find a better packing than
the sequential searches did". Both referee reports make this their first comment, and the paper
currently concedes it outright in Limitations.

The core cells are monodisperse at K=17. That is 2**17 = 131,072 subsets, each settled by one
AC power flow at ~25 ms. Enumerating all of them replaces every heuristic in Eq. (1) with the
exact optimum of the same problem, and the residual stops being a difference of bounds.

WHAT EXACTNESS MEANS FOR EACH TERM
----------------------------------
  JOINT           max over ALL feasible subsets of the mixed queue.
  gen / load track  max over feasible subsets of that sign alone -- a sub-cube of the same
                  enumeration, so it costs nothing extra.
  SEQUENTIAL      the first track generally has SEVERAL optimal accept-sets of equal capacity,
                  and which one a segregated process happens to pick changes what the second
                  track can seat, so we take the best over EVERY optimal first-track set. That
                  is the strongest possible segregated baseline, which makes the residual the
                  most conservative one available rather than an artifact of a lucky tie-break.
                  No extra power flows are needed: certifying a second-track set B against a
                  base case already holding the first track's A asks exactly whether A u B is
                  feasible, which the joint enumeration has answered.
  ALTERNATION     the same treatment applied to the fixed point of exp109: at every half-round,
                  branch over all capacity-optimal accept-sets of the studying track, again
                  reading feasibility off the enumeration. Bounded by --max-nodes; a capped
                  search can only understate the segregated baseline (hence overstate R*).

THE SECOND QUESTION THE SAME ENUMERATION ANSWERS
------------------------------------------------
Eq. (2) maximizes the UNWEIGHTED SUM of accepted generation and load kW. No utility values a
marginal kW of each identically, and the objective that sums across signs is exactly the one
that rewards cancelling pairs -- so the residual could be an artifact of the 1:1 weighting.
Once every feasible subset is enumerated, the full Pareto frontier over (accepted generation,
accepted load) is free, and so is the residual under any weight w:

    value_w(S) = w * gen_kw(S) + (1 - w) * load_kw(S)

The segregated tracks are NOT re-weighted. A generation queue maximizes generation because that
is the only sign it studies; w enters as the metric the two processes are compared under, not as
something a single-signed track could optimize. That is the faithful model of the counterfactual,
and it is why the sequential accept-sets are w-invariant while their scores are not.

WHAT IT DOES NOT SETTLE
-----------------------
The irreducible cells (co-located Ckt24, SMART-DS) run at K=24-44 and are far out of enumeration
range. Their claim never rested on an optimizer: it rests on explicit mixed pairs that exact
power flow accepts while rejecting each half (Section 5.3). This file certifies the MAGNITUDES on
the cells where the corpus's median residual lives; the certificate covers the rest.

Run:
  uv run python experiments/113_exact_enumeration.py --families ckt7 --seeds 0 1 2 3 --save
  uv run python experiments/113_exact_enumeration.py --families ckt7 --load-frac 0.3 --save
"""
from __future__ import annotations

import argparse
import multiprocessing as mp
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

_c = __import__("68_ckt7_gldf_headline")
_s = __import__("83_setpoint_sweep")
_alt = __import__("109_alternating_rounds")

OUT = Path(__file__).resolve().parents[1] / "outputs"

WEIGHTS = [0.0, 0.1, 0.25, 0.5, 0.75, 0.9, 1.0]

# Families solved by pandapower. The SMART-DS oracle drives a process-global OpenDSS engine
# that is not fork-safe, and its cells are out of enumeration range anyway.
ENUMERABLE = ("ckt7", "ckt24", "spur", "swiss55")

_W: dict = {}          # per-worker state, populated by fork


# --------------------------------------------------------------- the enumeration
def _init_worker(f, projects):
    _W["f"], _W["projects"] = f, projects
    _W["ids"] = [p.id for p in projects]
    f._proj = None
    f._staged(projects)                     # stage once; children fork a warm net


def _chunk(args):
    """AC-evaluate every subset whose integer mask lies in [lo, hi)."""
    lo, hi = args
    f, projects, ids = _W["f"], _W["projects"], _W["ids"]
    k = len(ids)
    out = np.zeros(hi - lo, dtype=np.uint8)
    for m in range(lo, hi):
        acc = {ids[i] for i in range(k) if m >> i & 1}
        ok, _mw, _vio, _info = f.evaluate(f.ctx, projects, acc)
        out[m - lo] = bool(ok)
    return lo, out


def enumerate_all(f, projects, n_jobs, chunk_size=2048, serial_below=64):
    """Feasibility of all 2**K subsets, by exact AC power flow. Returns a uint8 array.

    Small sub-cubes -- a five-request second track, an alternation half-round -- run in
    process: forking a pool costs more than the few hundred solves it would spread.
    """
    k = len(projects)
    n = 1 << k
    feas = np.zeros(n, dtype=np.uint8)
    if n <= serial_below:
        _init_worker(f, projects)
        _lo, out = _chunk((0, n))
        return out
    chunk_size = max(32, min(chunk_size, n // max(1, 4 * n_jobs)))
    bounds = [(lo, min(lo + chunk_size, n)) for lo in range(0, n, chunk_size)]
    ctx = mp.get_context("fork")
    with ctx.Pool(n_jobs, initializer=_init_worker, initargs=(f, projects)) as pool:
        done = 0
        for lo, out in pool.imap_unordered(_chunk, bounds):
            feas[lo:lo + len(out)] = out
            done += len(out)
            if done % (chunk_size * 16) == 0:
                print(f"    {done}/{n} subsets ({100*done/n:.0f}%)", end="\r", flush=True)
    print(" " * 60, end="\r")
    return feas


def _chunk_masks(masks):
    """AC-evaluate an explicit list of masks. Same worker state as `_chunk`."""
    f, projects, ids = _W["f"], _W["projects"], _W["ids"]
    k = len(ids)
    out = np.zeros(len(masks), dtype=np.uint8)
    for j, m in enumerate(masks):
        acc = {ids[i] for i in range(k) if int(m) >> i & 1}
        ok, _mw, _vio, _info = f.evaluate(f.ctx, projects, acc)
        out[j] = bool(ok)
    return masks, out


def enumerate_subset(f, projects, n_jobs, masks, chunk_size=256, serial_below=64):
    """Feasibility over an explicit set of masks; every other mask stays 0.

    Used when a cheaper oracle has already ruled subsets out and the expensive one only has to
    confirm the survivors. Returns a full-length array so every downstream reader is unchanged.
    """
    n = 1 << len(projects)
    feas = np.zeros(n, dtype=np.uint8)
    masks = np.asarray(masks, dtype=np.int64)
    if len(masks) == 0:
        return feas
    if len(masks) <= serial_below:
        _init_worker(f, projects)
        _m, out = _chunk_masks(masks)
        feas[masks] = out
        return feas
    chunk_size = max(16, min(chunk_size, len(masks) // max(1, 4 * n_jobs)))
    parts = [masks[i:i + chunk_size] for i in range(0, len(masks), chunk_size)]
    ctx = mp.get_context("fork")
    with ctx.Pool(n_jobs, initializer=_init_worker, initargs=(f, projects)) as pool:
        done = 0
        for ms, out in pool.imap_unordered(_chunk_masks, parts):
            feas[ms] = out
            done += len(ms)
            print(f"    {done}/{len(masks)} candidates ({100*done/len(masks):.0f}%)",
                  end="\r", flush=True)
    print(" " * 60, end="\r")
    return feas


def sign_totals(projects):
    """Per-subset accepted generation and load kW, as arrays over the whole mask space."""
    k = len(projects)
    n = 1 << k
    idx = np.arange(n, dtype=np.int64)
    gen = np.zeros(n)
    load = np.zeros(n)
    for i, p in enumerate(projects):
        bit = (idx >> i & 1).astype(np.float64)
        if p.p_mw < 0:
            gen += bit * abs(p.p_mw) * 1000.0
        else:
            load += bit * p.p_mw * 1000.0
    return gen, load


def pareto(gen, load, feas):
    """Non-dominated (gen_kw, load_kw) pairs among feasible subsets."""
    g, l = gen[feas], load[feas]
    order = np.lexsort((-l, -g))                    # gen desc, then load desc
    g, l = g[order], l[order]
    front, best = [], -np.inf
    for gi, li in zip(g, l):
        if li > best + 1e-9:
            front.append((float(gi), float(li)))
            best = li
    return front[::-1]


# ---------------------------------------------------- exact sequential + alternation
def _optimal_masks(gen, load, feas, restrict_bits, w=None):
    """Masks that are feasible, use only `restrict_bits`, and maximize capacity."""
    n = len(feas)
    idx = np.arange(n, dtype=np.int64)
    allowed = (idx & ~restrict_bits) == 0
    ok = feas.astype(bool) & allowed
    if not ok.any():
        return 0.0, [0]
    tot = gen + load if w is None else w * gen + (1 - w) * load
    best = tot[ok].max()
    return float(best), [int(m) for m in idx[ok & (tot >= best - 1e-9)]]


def _bits_of(projects, sign):
    b = 0
    for i, p in enumerate(projects):
        if (p.p_mw < 0) == (sign == "gen"):
            b |= 1 << i
    return b


def _subset_projects(projects, mask):
    return [p for i, p in enumerate(projects) if mask >> i & 1]


def _submasks(bits, n):
    """Every mask that selects only requests inside `bits`."""
    idx = np.arange(n, dtype=np.int64)
    return idx[(idx & ~bits) == 0]


def exact_sequential(fam, f, projects, gen, load, feas, n_jobs, first="gen", verbose=True):
    """Exact best value of a one-pass sequential study that studies `first` first.

    NO ADDITIONAL POWER FLOWS. Certifying a second-track set $B$ against a base case that
    already contains the first track's accept-set $A$ asks exactly whether $A \\cup B$ is
    AC-feasible against the pristine feeder -- the anchored feeders keep the base feeder's
    limits precisely so that this equivalence holds -- and the joint enumeration has already
    answered that for every pair. Reading the answer off `feas` is not an approximation of the
    anchored run; it is the same question, and it removes the only failure mode the anchored
    version had (a base case the solver could not converge from a flat start, which forced
    dropping candidates out of a maximum and so inflated the residual).

    Maximized over EVERY optimal accept-set of the first track, because a segregated process is
    free to land on any of them and we owe the comparison its best case.
    """
    n = len(feas)
    gbits, lbits = _bits_of(projects, "gen"), _bits_of(projects, "load")
    fbits, sbits = (gbits, lbits) if first == "gen" else (lbits, gbits)
    first_kw, anchors = _optimal_masks(gen, load, feas, fbits)
    sub = _submasks(sbits, n)
    if verbose:
        print(f"    seq {first}-first: track optimum {first_kw:.0f} kW over "
              f"{len(anchors)} tied accept-set(s) x {len(sub)} second-track subsets")

    ok_all = feas.astype(bool)
    tot = gen + load
    best_total, best_gen, best_load = -np.inf, 0.0, 0.0
    for a in anchors:
        cand = a | sub                              # first-track and second-track bits disjoint
        j = int(cand[np.argmax(np.where(ok_all[cand], tot[cand], -1.0))])
        if tot[j] > best_total:
            best_total = float(tot[j])
            best_gen, best_load = float(gen[j]), float(load[j])
    return best_total, best_gen, best_load, len(anchors), 0


def exact_alternation(fam, f, projects, gen, load, feas, n_jobs, start="gen", max_nodes=4096):
    """Exact fixed point of alternating single-signed re-studies, branching over tied optima.

    State is the set of accepted requests (a mask). Each half-round takes the studying track's
    PENDING requests, keeps every addition whose union with the accepted set is feasible --
    exactly what exp109's heuristic loop certifies against a base case holding the prior
    approvals -- and branches over every capacity-optimal outcome. Like `exact_sequential`,
    this reads feasibility off the joint enumeration and runs no further power flows.
    """
    n = len(feas)
    gbits, lbits = _bits_of(projects, "gen"), _bits_of(projects, "load")
    order = (gbits, lbits) if start == "gen" else (lbits, gbits)
    ok_all = feas.astype(bool)
    tot = gen + load

    seen: set = set()
    best = 0.0
    frontier = [(0, 0)]                        # (accepted mask, half-round index)
    nodes, capped = 0, False

    while frontier:
        acc, h = frontier.pop()
        if (acc, h % 2) in seen:
            continue
        seen.add((acc, h % 2))
        best = max(best, float(tot[acc]))
        if h >= 2 * _alt.MAX_ROUNDS:
            continue
        pending_bits = order[h % 2] & ~acc
        if not pending_bits:
            if order[(h + 1) % 2] & ~acc:
                frontier.append((acc, h + 1))
            continue
        if nodes >= max_nodes:
            capped = True
            continue
        nodes += 1

        cand = acc | _submasks(pending_bits, n)
        good = cand[ok_all[cand]]
        if not len(good):
            frontier.append((acc, h + 1))
            continue
        top = float(tot[good].max())
        if top <= float(tot[acc]) + 1e-9:          # this track can add nothing
            frontier.append((acc, h + 1))
            continue
        for child in good[tot[good] >= top - 1e-9]:
            frontier.append((int(child), h + 1))
    return best, nodes, capped


# ------------------------------------------------------------------------ driver
def run_cell(fam, f, projects, n_jobs, weights, max_nodes, do_alt=True,
             feas_path=None):
    k = len(projects)
    n_gen = sum(1 for p in projects if p.p_mw < 0)
    print(f"    K={k} ({n_gen} gen + {k - n_gen} load) -> {1 << k} subsets")

    t0 = time.time()
    feas = enumerate_all(f, projects, n_jobs)
    gen, load = sign_totals(projects)
    if feas_path is not None:                 # the enumeration is the only expensive thing
        np.savez_compressed(feas_path, feas=feas,
                            p_mw=np.array([p.p_mw for p in projects]),
                            bus=np.array([int(p.bus) for p in projects]))
    t_enum = time.time() - t0
    n_feas = int(feas.sum())

    gbits, lbits = _bits_of(projects, "gen"), _bits_of(projects, "load")
    joint_kw, _ = _optimal_masks(gen, load, feas, gbits | lbits)
    gen_kw, _ = _optimal_masks(gen, load, feas, gbits)
    load_kw, _ = _optimal_masks(gen, load, feas, lbits)

    sg1, sg1_g, sg1_l, n_anch_g, skip_g = exact_sequential(fam, f, projects, gen, load, feas,
                                                           n_jobs, "gen")
    sl1, sl1_g, sl1_l, n_anch_l, skip_l = exact_sequential(fam, f, projects, gen, load, feas,
                                                           n_jobs, "load")
    best_seq = max(sg1, sl1)

    # the heuristic the paper actually ran, on the same draw
    f._proj = None
    _hj_ids, hj_kw = fam.certify(f, projects, n_jobs)
    fam.reset(f, [p for p in projects if p.p_mw < 0])
    _hg, hg_kw = fam.certify(f, [p for p in projects if p.p_mw < 0], n_jobs)
    fam.reset(f, [p for p in projects if p.p_mw > 0])
    _hl, hl_kw = fam.certify(f, [p for p in projects if p.p_mw > 0], n_jobs)
    fam.reset(f, projects)

    alt_kw, alt_nodes, alt_capped = (float("nan"), 0, False)
    if do_alt:
        a_g, n_g, c_g = exact_alternation(fam, f, projects, gen, load, feas, n_jobs,
                                          "gen", max_nodes)
        a_l, n_l, c_l = exact_alternation(fam, f, projects, gen, load, feas, n_jobs,
                                          "load", max_nodes)
        alt_kw, alt_nodes, alt_capped = max(a_g, a_l), n_g + n_l, c_g or c_l

    front = pareto(gen, load, feas.astype(bool))

    row = dict(k=k, n_gen=n_gen, n_load=k - n_gen, n_subsets=1 << k, n_feasible=n_feas,
               exact_joint_kw=joint_kw, exact_gen_track_kw=gen_kw, exact_load_track_kw=load_kw,
               exact_seq_genfirst_kw=sg1, exact_seq_loadfirst_kw=sl1,
               exact_best_sequential_kw=best_seq,
               n_tied_gen_optima=n_anch_g, n_tied_load_optima=n_anch_l,
               n_anchors_dropped=skip_g + skip_l,
               exact_residual_kw=joint_kw - best_seq,
               exact_residual_pct=100 * (joint_kw - best_seq) / joint_kw if joint_kw else 0.0,
               exact_alternation_kw=alt_kw,
               exact_irreducible_kw=joint_kw - alt_kw,
               exact_irreducible_pct=(100 * (joint_kw - alt_kw) / joint_kw
                                      if joint_kw and np.isfinite(alt_kw) else float("nan")),
               alternation_nodes=alt_nodes, alternation_capped=alt_capped,
               heuristic_joint_kw=hj_kw, heuristic_gen_track_kw=hg_kw,
               heuristic_load_track_kw=hl_kw,
               joint_gap_kw=joint_kw - hj_kw,
               joint_gap_pct=100 * (joint_kw - hj_kw) / joint_kw if joint_kw else 0.0,
               pareto=";".join(f"{g:.0f}/{l:.0f}" for g, l in front),
               enum_seconds=t_enum)

    # the residual under a re-weighted comparison metric
    for w in weights:
        jw, _ = _optimal_masks(gen, load, feas, gbits | lbits, w=w)
        sw = max(w * sg1_g + (1 - w) * sg1_l, w * sl1_g + (1 - w) * sl1_l)
        row[f"joint_w{w:g}_kw"] = jw
        row[f"seq_w{w:g}_kw"] = sw
        row[f"residual_w{w:g}_pct"] = 100 * (jw - sw) / jw if jw > 0 else 0.0
    return row


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--families", nargs="+", default=["ckt7"])
    ap.add_argument("--k", type=int, default=17)
    ap.add_argument("--size-kw", type=float, default=100.0)
    ap.add_argument("--load-frac", type=float, default=None,
                    help="Ckt7 only; the family default is 0.5")
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2, 3])
    ap.add_argument("--placement", default="random")
    ap.add_argument("--weights", type=float, nargs="+", default=WEIGHTS)
    ap.add_argument("--max-nodes", type=int, default=4096)
    ap.add_argument("--no-alternation", action="store_true")
    ap.add_argument("--n-jobs", type=int, default=8)
    ap.add_argument("--mat", default=None)
    ap.add_argument("--tag", default="",
                    help="suffix for the per-cell .npz feasibility vectors; the name "
                         "carries no setpoint, so a run at a new one must set this or "
                         "it overwrites the old vectors")
    ap.add_argument("--out", default="m87_exact_enumeration.csv")
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()

    print("\n  M87 -- EXACT RESIDUALS BY ENUMERATION\n")
    print("  Every subset AC-solved. JOINT, both tracks and both sequential orders are exact")
    print("  optima, not heuristic lower bounds, so the residual between them is certified.\n")

    rows = []
    for fname in a.families:
        if fname not in ENUMERABLE:
            raise SystemExit(f"{fname} is not enumerable here (see ENUMERABLE)")
        fam = _alt.get_family(fname, a.n_jobs, mat=a.mat, placement=a.placement)
        print(f"  {fam.desc}   (K={a.k}, {a.size_kw:.0f} kW)\n")
        for seed in a.seeds:
            f = fam.make_feeder()
            if a.load_frac is not None and fname == "ckt7":
                projects = _s._build_mixed(f, a.k, seed, a.load_frac, a.size_kw)
            else:
                projects = fam.make_projects(f, seed, a.k, a.size_kw)
            print(f"  seed {seed}:")
            try:
                fp = (OUT / f"m87_feas_{fname}_lf{a.load_frac}_k{a.k}_s{seed}{a.tag}.npz"
                      if a.save else None)
                if fp is not None:
                    OUT.mkdir(exist_ok=True)
                r = run_cell(fam, f, projects, a.n_jobs, a.weights, a.max_nodes,
                             do_alt=not a.no_alternation, feas_path=fp)
            except Exception as exc:              # one bad cell must not lose the others
                print(f"    FAILED: {type(exc).__name__}: {exc}\n", flush=True)
                continue
            r.update(family=fname, seed=seed, size_kw=a.size_kw,
                     load_frac=a.load_frac if a.load_frac is not None else 0.5,
                     placement=a.placement)
            rows.append(r)
            print(f"    feasible subsets      {r['n_feasible']}/{r['n_subsets']} "
                  f"({100*r['n_feasible']/r['n_subsets']:.1f}%)   [{r['enum_seconds']:.0f}s]")
            print(f"    exact gen / load track  {r['exact_gen_track_kw']:.0f} / "
                  f"{r['exact_load_track_kw']:.0f} kW")
            print(f"    exact best sequential   {r['exact_best_sequential_kw']:.0f} kW "
                  f"(gen-first {r['exact_seq_genfirst_kw']:.0f}, "
                  f"load-first {r['exact_seq_loadfirst_kw']:.0f})")
            print(f"    exact JOINT             {r['exact_joint_kw']:.0f} kW")
            print(f"    EXACT RESIDUAL R1       {r['exact_residual_kw']:.0f} kW "
                  f"= {r['exact_residual_pct']:.1f}% of joint")
            if not a.no_alternation:
                print(f"    exact alternation       {r['exact_alternation_kw']:.0f} kW  -> "
                      f"R* = {r['exact_irreducible_pct']:.1f}%"
                      + ("   [node cap reached]" if r['alternation_capped'] else ""))
            print(f"    heuristic joint         {r['heuristic_joint_kw']:.0f} kW  -> "
                  f"optimality gap {r['joint_gap_kw']:.0f} kW "
                  f"({r['joint_gap_pct']:.1f}%)")
            print(f"    Pareto (gen/load kW)    {r['pareto']}")
            print()
            if a.save:
                OUT.mkdir(exist_ok=True)
                pd.DataFrame(rows).to_csv(OUT / a.out, index=False)

    d = pd.DataFrame(rows)
    print("=" * 78)
    print("\n  EXACT vs HEURISTIC, over all cells\n")
    print(f"  {'family':>8} {'cells':>6} {'exact R1 %':>12} {'heur R1 %':>11} "
          f"{'joint gap %':>12} {'exact R* %':>11}")
    print("  " + "-" * 66)
    for fname, g in d.groupby("family", sort=False):
        heur_r1 = 100 * (g.heuristic_joint_kw
                         - np.maximum(g.exact_seq_genfirst_kw, g.exact_seq_loadfirst_kw)) \
            / g.heuristic_joint_kw
        print(f"  {fname:>8} {len(g):>6d} "
              f"{g.exact_residual_pct.median():>11.1f} {heur_r1.median():>10.1f} "
              f"{g.joint_gap_pct.median():>11.1f} {g.exact_irreducible_pct.median():>10.1f}")

    print("\n  The residual under a re-weighted comparison metric (medians over cells)\n")
    print("  " + "  ".join(f"w={w:g}" for w in a.weights))
    print("  " + "  ".join(f"{d[f'residual_w{w:g}_pct'].median():5.1f}" for w in a.weights))
    print("\n  w is the weight on generation kW; w=0.5 is the paper's objective rescaled,")
    print("  w=1 values generation only and w=0 load only. The segregated tracks are not")
    print("  re-weighted -- a single-signed queue has nothing to trade off.\n")
    if a.save:
        print(f"  saved -> outputs/{a.out}\n")


if __name__ == "__main__":
    main()
