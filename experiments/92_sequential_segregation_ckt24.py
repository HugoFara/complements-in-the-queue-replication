"""M64 -- THE SEQUENTIAL COUNTERFACTUAL ON CKT24 (the thermal feeder).

M63 (exp91) ran the three-way test -- JOINT vs SEGREGATED-parallel vs SEGREGATED-sequential
(both orders) -- on Ckt7, and found a residual (joint - best sequential order) of +0-45%
(median +20%): capacity NO study order recovers. The reviewer's second feeder is the obvious
completion, and it is the more revealing one, because Ckt24 is THERMAL-limited and its
mechanism inverts:

    On Ckt24, LOAD alone hosts ZERO (a new load worsens the very transformer loading that
    already sits at its non-worsening cap). PV export RELIEVES that transformer. So the
    sequential order that matters is PV-FIRST: approve the export, which drops the substation
    loading, then study the load queue against the relieved base case. Load-first recovers
    nothing (load alone is 0, so base+load == base and the PV track is unchanged).

    The residual on Ckt24 therefore asks: once PV-first has created thermal headroom and let
    some load in, how much MORE does JOINT co-study seat -- by choosing the PV set that best
    ENABLES load rather than the PV set that maximises PV alone?

THE ONE MODELLING SUBTLETY, and it is load-bearing. Ckt24's 45 MVA substation transformer
sits at its non-worsening cap at the SHIPPED baseline (an EPRI allocation-factor artifact;
exp64). That cap -- "do not load the transformer past where the feeder ships it" -- is a
property of the FEEDER's protection, not of any one study track. So when PV-first is baked
into the base case and the load queue is studied against base+PV, the transformer's limit is
STILL the shipped level, NOT the relieved base+PV loading. Re-reading the cap at base+PV would
turn the approved PV's relief into a new floor, forbid the load track from using the headroom
back up to the shipped level, and INFLATE the residual in JOINT's favour. So
`ckt24_with_anchor` keeps `sub_base_pct` at the shipped value while re-running everything else
(the GLDF anchor, the thermal p_base, the voltage/line baselines) at base+anchor. Both JOINT
and every sequential track are then capped at the same shipped transformer level, and the
comparison is fair.

Sequential unions are AC-feasible by construction (the second track is certified against a
base that already contains the first), as on Ckt7. Ckt24 is forced to source 1.05 (its only
feasible setpoint; exp88).

Run:
  .venv/bin/python experiments/92_sequential_segregation_ckt24.py --save
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

_c = __import__("64_ckt24_gldf_bound")
_seg = __import__("88_ckt24_segregation")

OUT = Path(__file__).resolve().parents[1] / "outputs"

SETPOINTS = [1.05]              # FORCED -- Ckt24's only feasible setpoint (exp88)
LOAD_FRACTIONS = [0.3, 0.5, 0.7]


def ckt24_with_anchor(base_f, anchor_projects, anchor_ids):
    """A Ckt24 feeder whose BASE CASE already contains the first track's accepted set as
    permanent in-service loads. Everything is re-run at base+anchor (GLDF anchor, thermal
    p_base, voltage/line baselines) EXCEPT the substation transformer's non-worsening cap,
    which stays at the SHIPPED value -- see the module docstring."""
    f2 = _seg.Ckt24Feeder.__new__(_seg.Ckt24Feeder)
    ctx = copy.deepcopy(base_f.ctx)
    net = ctx.net
    by = {p.id: p for p in anchor_projects}
    for rid in anchor_ids:
        p = by[rid]
        pp.create_load(net, bus=int(p.bus), p_mw=float(p.p_mw), q_mvar=0.0,
                       name="anchor_" + rid, in_service=True)
    pp.runpp(net, **_c.RUNPP_KWARGS)

    f2.name = base_f.name + "+anchor"
    f2.sizer = None
    f2.ctx = ctx
    f2.VMIN_PU, f2.VMAX_PU, f2.LOADING_MAX_PCT = (base_f.VMIN_PU, base_f.VMAX_PU,
                                                  base_f.LOADING_MAX_PCT)
    f2.excl_lines = list(base_f.excl_lines)
    f2.slack = int(net.ext_grid.bus.iloc[0])
    f2.setpoint = base_f.setpoint
    f2.sub_idx = ctx.sub_idx
    f2.bad_trafo_idx = list(base_f.bad_trafo_idx)
    f2.sub_base_pct = base_f.sub_base_pct        # SHIPPED cap, NOT re-read at base+anchor
    f2.load_buses = list(base_f.load_buses)
    f2._proj = f2._enet = f2._rows = None
    return f2


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--setpoints", type=float, nargs="+", default=SETPOINTS)
    ap.add_argument("--load-fractions", type=float, nargs="+", default=LOAD_FRACTIONS)
    ap.add_argument("--size-kw", type=float, default=100.0)
    ap.add_argument("--k", type=int, default=24)
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    ap.add_argument("--n-jobs", type=int, default=8)
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()

    print("\n  M64 -- SEGREGATION: PARALLEL vs SEQUENTIAL vs JOINT  (Ckt24, thermal-limited)\n")
    print(f"  one fixed queue of K={a.k} at {a.size_kw:.0f} kW; median over seeds {a.seeds}.")
    print("  Ckt24: load alone hosts 0, so PV-first is the order that matters (PV relieves the")
    print("  transformer, then load fits). RESIDUAL = joint - best sequential order.\n")

    hdr = (f"  {'src':>5} {'ld%':>4} | {'SEG-par':>8} {'SEQ-pv1':>8} {'SEQ-ld1':>8} "
           f"{'bestSEQ':>8} {'JOINT':>7} | {'vs par':>7} {'vs SEQ':>7}  {'par-safe':>8}")
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))

    rows = []
    for sp in a.setpoints:
        f = _seg.Ckt24Feeder.at_setpoint(sp)
        for lf in a.load_fractions:
            per_seed = []
            for seed in a.seeds:
                t0 = time.time()
                projects = _seg.build_mixed(f, a.k, seed, lf, a.size_kw)
                pv = [p for p in projects if p.p_mw < 0]
                ld = [p for p in projects if p.p_mw > 0]

                # each track vs the shipped baseline (shared by parallel + the seq 1st stages)
                f._proj = None
                pv_ids, pv_kw = _seg.certify(f, pv, a.n_jobs)
                f._proj = None
                ld_ids, ld_kw = _seg.certify(f, ld, a.n_jobs)
                seg_par_kw = pv_kw + ld_kw
                f._proj = None
                par_union_ok, _, _, _ = f.evaluate(f.ctx, projects, pv_ids | ld_ids)

                # JOINT
                f._proj = None
                _jids, joint_kw = _seg.certify(f, projects, a.n_jobs)

                # SEQUENTIAL, both orders (2nd track vs base + accepted-1st-track)
                f_pv = ckt24_with_anchor(f, pv, pv_ids)          # base + accepted PV
                _l2, ld2_kw = _seg.certify(f_pv, ld, a.n_jobs)
                seq_pv1_kw = pv_kw + ld2_kw

                f_ld = ckt24_with_anchor(f, ld, ld_ids)          # base + accepted load
                _p2, pv2_kw = _seg.certify(f_ld, pv, a.n_jobs)
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
                    pd.DataFrame(rows).to_csv(OUT / "m64_sequential_ckt24.csv", index=False)

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
        d.to_csv(OUT / "m64_sequential_ckt24.csv", index=False)

    print("\n" + "=" * 88)
    for sp in a.setpoints:
        sub = d[d.setpoint == sp]
        par = sub.cost_vs_parallel_pct.replace([np.inf, -np.inf], np.nan).dropna()
        seq = sub.residual_vs_sequential_pct.replace([np.inf, -np.inf], np.nan).dropna()
        print(f"  source {sp:.2f}:")
        if len(par):
            print(f"    joint vs PARALLEL   (M60's number): {par.min():+.0f}% to {par.max():+.0f}% "
                  f"(median {par.median():+.0f}%)")
        if len(seq):
            print(f"    joint vs BEST SEQ   (the RESIDUAL) : {seq.min():+.0f}% to {seq.max():+.0f}% "
                  f"(median {seq.median():+.0f}%)   <- irreducible, no order recovers it")
        print(f"    parallel union AC-safe: {int(sub.par_union_ac_feasible.sum())}/{len(sub)} "
              f"(sequential unions are AC-safe by construction)")
    print("\n  Ckt7 (M63) residual at its honest setpoint 1.04: +0% to +45%, median +20%.")
    print("  If Ckt24's residual is also tens of %, the irreducible cost is a two-feeder claim.\n")
    if a.save:
        print("  saved -> outputs/m64_sequential_ckt24.csv")


if __name__ == "__main__":
    main()
