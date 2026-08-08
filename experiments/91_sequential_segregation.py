"""M63 -- IS THE SEGREGATION COST REAL, OR DOES A STUDY ORDER RECOVER IT?  (Ckt7)

M58 (exp86) costed segregation as PARALLEL study: certify PV against the baseline feeder,
certify load against the baseline feeder, union the two. A reviewer is right that this is the
WORST CASE for segregation, hence an UPPER BOUND on its cost, not the cost of the process a
utility actually runs. Real segregated interconnection is SEQUENTIAL: one queue is studied,
approved, and ADDED TO THE BASE CASE, and the other queue is then studied against
baseline-plus-the-first-track. Each track updates the network the next track sees, so the
second track gets to exploit the voltage the first track moved -- exactly this paper's
mechanism, working FOR segregation.

So the honest experiment is three-way, on ONE fixed mixed queue:

    JOINT          certify all requests together.
    SEG-parallel   certify(PV | base)  u  certify(load | base)            [M58, the upper bound]
    SEG-seq PV1st   A = certify(PV | base);  certify(load | base + A)
    SEG-seq ld1st   B = certify(load | base);  certify(PV | base + B)

    RESIDUAL := JOINT - max(seq_pvfirst, seq_ldfirst)

The residual is the number the paper should headline: the capacity that NO sequential study
order can recover, because it comes from requests that are only jointly feasible -- a PV that
needs a co-located load to be seatable will never clear a PV-first track (studied against the
bare baseline), and vice versa. That mutual dependence is the irreducible cost of not
co-studying.

  * If the residual is still tens of percent, M58's headline is bulletproof and the
    "no re-ordering fixes this" claim finally has its matching experiment.
  * If it collapses to ~0, the finding is the weaker "study order matters and there is a right
    order" -- and we would say so.

A SECOND, QUALITATIVE ASYMMETRY, free from the same runs: the two sequential unions are
AC-FEASIBLE BY CONSTRUCTION (the second track is certified against a base case that already
contains the first), whereas M58 showed the PARALLEL union need not be -- each parallel track
certifies against a baseline the other invalidates. So sequential study is both safer and
weakly higher-capacity than parallel; the comparison that matters is JOINT vs the BEST
sequential order, and that is what RESIDUAL measures.

Setpoints 1.05 (EPRI's pin, 0.0012 pu headroom) and 1.04 (the honest one, real headroom); the
number to quote is 1.04, as in M58.

Usage:
  .venv/bin/python experiments/91_sequential_segregation.py --save
  (the pinned pandapower is sufficient -- Ckt7 has no regulator, so nothing here needs the
  tap-control capability experiments/90_regulator_gate.py asks for.)
"""
from __future__ import annotations

import argparse
import copy
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pandapower as pp

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

_c = __import__("68_ckt7_gldf_headline")
_m = __import__("75_milp_maximality")
_s = __import__("83_setpoint_sweep")

OUT = Path(__file__).resolve().parents[1] / "outputs"

SETPOINTS = [1.05, 1.04]
LOAD_FRACTIONS = [0.3, 0.5, 0.7]


def certify(f, projects, n_jobs):
    if not projects:
        return set(), 0.0
    ids, mw, _ = _m.certified_allocation(f.ctx, projects, f, f, n_jobs=n_jobs, verbose=False)
    return set(ids), mw * 1000


def feeder_with_anchor(base_f, anchor_projects, anchor_ids):
    """A copy of `base_f` whose BASE CASE already contains the first track's accepted set as
    permanent in-service loads. Certifying the second queue against it is the sequential study:
    the GLDF re-linearizes at base+anchor, the oracle checks the second queue on top of it, and
    any set it certifies is AC-feasible TOGETHER with the anchor by construction."""
    ctx = copy.deepcopy(base_f.ctx)
    net = ctx.net
    by = {p.id: p for p in anchor_projects}
    for rid in anchor_ids:
        p = by[rid]
        pp.create_load(net, bus=int(p.bus), p_mw=float(p.p_mw), q_mvar=0.0,
                       name="anchor_" + rid, in_service=True)
    pp.runpp(net, **_c.RUNPP_KWARGS)
    f2 = _s.SlackFreeFeeder.from_ctx(base_f.name + "+anchor", ctx, None,
                                     base_f.VMIN_PU, base_f.VMAX_PU, base_f.LOADING_MAX_PCT)
    f2.slack = int(net.ext_grid.bus.iloc[0])
    f2.setpoint = base_f.setpoint
    f2._proj = None
    return f2


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--setpoints", type=float, nargs="+", default=SETPOINTS)
    ap.add_argument("--load-fractions", type=float, nargs="+", default=LOAD_FRACTIONS)
    ap.add_argument("--size-kw", type=float, default=100.0)
    ap.add_argument("--k", type=int, default=17)
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    ap.add_argument("--n-jobs", type=int, default=8)
    ap.add_argument("--out", default="m63_sequential_ckt7.csv",
                    help="output CSV; the default is the PUBLISHED 1.04/1.05 file, so a "
                         "run at another setpoint must name its own or it overwrites it")
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()

    print("\n  M63 -- SEGREGATION: PARALLEL (M58) vs SEQUENTIAL vs JOINT  (Ckt7)\n")
    print(f"  one fixed queue of K={a.k} at {a.size_kw:.0f} kW; median over seeds {a.seeds}.")
    print("  RESIDUAL = joint - best sequential order = capacity NO study order recovers.\n")

    hdr = (f"  {'src':>5} {'ld%':>4} | {'SEG-par':>8} {'SEQ-pv1':>8} {'SEQ-ld1':>8} "
           f"{'bestSEQ':>8} {'JOINT':>7} | {'vs par':>7} {'vs SEQ':>7}  {'par-safe':>8}")
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))

    rows = []
    for sp in a.setpoints:
        f = _s.SlackFreeFeeder.at_setpoint(sp, _c.VMIN_PU, _c.VMAX_PU, _c.LOADING_MAX_PCT)
        for lf in a.load_fractions:
            per_seed = []
            for seed in a.seeds:
                t0 = time.time()
                projects = _s._build_mixed(f, a.k, seed, lf, a.size_kw)
                pv = [p for p in projects if p.p_mw < 0]
                ld = [p for p in projects if p.p_mw > 0]

                # --- each track vs the BARE baseline (shared by parallel + the seq 1st stages)
                f._proj = None
                pv_ids, pv_kw = certify(f, pv, a.n_jobs)
                f._proj = None
                ld_ids, ld_kw = certify(f, ld, a.n_jobs)
                seg_par_kw = pv_kw + ld_kw
                f._proj = None
                par_union_ok, _, _, _ = f.evaluate(f.ctx, projects, pv_ids | ld_ids)

                # --- JOINT
                f._proj = None
                _jids, joint_kw = certify(f, projects, a.n_jobs)

                # --- SEQUENTIAL, both orders (2nd track vs base + accepted-1st-track)
                f_pv = feeder_with_anchor(f, pv, pv_ids)          # base + accepted PV
                _l2, ld2_kw = certify(f_pv, ld, a.n_jobs)
                seq_pv1_kw = pv_kw + ld2_kw

                f_ld = feeder_with_anchor(f, ld, ld_ids)          # base + accepted load
                _p2, pv2_kw = certify(f_ld, pv, a.n_jobs)
                seq_ld1_kw = ld_kw + pv2_kw

                best_seq = max(seq_pv1_kw, seq_ld1_kw)
                per_seed.append((seg_par_kw, seq_pv1_kw, seq_ld1_kw, best_seq, joint_kw,
                                 par_union_ok))
                rows.append(dict(setpoint=sp, load_frac=lf, seed=seed, k=a.k,
                                 size_kw=a.size_kw, n_pv=len(pv), n_load=len(ld),
                                 pv_alone_kw=pv_kw, load_alone_kw=ld_kw,
                                 seg_parallel_kw=seg_par_kw, par_union_ac_feasible=bool(par_union_ok),
                                 seq_pvfirst_kw=seq_pv1_kw, seq_loadfirst_kw=seq_ld1_kw,
                                 best_sequential_kw=best_seq, joint_kw=joint_kw,
                                 cost_vs_parallel_pct=(100 * (joint_kw / seg_par_kw - 1)
                                                       if seg_par_kw > 0 else float("nan")),
                                 residual_vs_sequential_pct=(100 * (joint_kw / best_seq - 1)
                                                             if best_seq > 0 else float("nan")),
                                 dt=time.time() - t0))
                if a.save:
                    OUT.mkdir(exist_ok=True)
                    pd.DataFrame(rows).to_csv(OUT / a.out, index=False)

            arr = np.array([[p[0], p[1], p[2], p[3], p[4]] for p in per_seed])
            med = np.median(arr, axis=0)
            all_safe = all(p[5] for p in per_seed)
            any_safe = any(p[5] for p in per_seed)
            vs_par = (f"{100*(med[4]/med[0]-1):+.0f}%" if med[0] > 0 else "inf")
            vs_seq = (f"{100*(med[4]/med[3]-1):+.0f}%" if med[3] > 0 else "inf")
            safe = "yes" if all_safe else ("some" if any_safe else "NO")
            print(f"  {sp:>5.2f} {100*lf:>3.0f}% | {med[0]:>7.0f} {med[1]:>8.0f} {med[2]:>8.0f} "
                  f"{med[3]:>8.0f} {med[4]:>7.0f} | {vs_par:>7} {vs_seq:>7}  {safe:>8}")

    d = pd.DataFrame(rows)
    if a.save:
        OUT.mkdir(exist_ok=True)
        d.to_csv(OUT / a.out, index=False)

    print("\n" + "=" * 88)
    for sp in a.setpoints:
        sub = d[d.setpoint == sp]
        par = sub.cost_vs_parallel_pct.replace([np.inf, -np.inf], np.nan).dropna()
        seq = sub.residual_vs_sequential_pct.replace([np.inf, -np.inf], np.nan).dropna()
        print(f"  source {sp:.2f}:")
        print(f"    joint vs PARALLEL   (M58's number): {par.min():+.0f}% to {par.max():+.0f}% "
              f"(median {par.median():+.0f}%)")
        print(f"    joint vs BEST SEQ   (the RESIDUAL) : {seq.min():+.0f}% to {seq.max():+.0f}% "
              f"(median {seq.median():+.0f}%)   <- irreducible, no order recovers it")
        print(f"    parallel union AC-safe: {int(sub.par_union_ac_feasible.sum())}/{len(sub)} "
              f"(sequential unions are AC-safe by construction)")
    print("\n  If the RESIDUAL stays tens of %, segregation costs real capacity no order fixes.")
    print("  If it collapses toward 0, the finding is 'there is a right study order'.\n")
    if a.save:
        print(f"  saved -> outputs/{a.out}")


if __name__ == "__main__":
    main()
