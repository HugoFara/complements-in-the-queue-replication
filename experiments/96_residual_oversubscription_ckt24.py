"""M68 -- THE SEGREGATION RESIDUAL vs OVERSUBSCRIPTION ON CKT24 (the thermal feeder).

exp95 (M67) mapped Ckt7: the residual (joint - best study order) is exactly 0 while the
generation track fits solo, and turns on and grows once the PV track is oversubscribed
(+2.7 / +14.0 / +23.9% at 1.05 / 1.30 / 1.41x). That is the VOLTAGE feeder, where the
coupling is BIDIRECTIONAL -- a PV needs a co-located load to be seatable AND a load needs a
co-located PV, so some capacity is only jointly feasible and no order recovers it.

Ckt24 was expected to be the ONE-WAY control. Load alone hosts ZERO (a new load worsens the
substation transformer, which already sits at its non-worsening cap); PV export RELIEVES that
transformer; and the transformer's loading depends only on AGGREGATE net PV, not on where the PV
sits -- so "the PV set that hosts the most PV alone" is also "the set that relieves the most" is
also "the set that enables the most load", and a generation-FIRST order should recover
everything. M64 confirmed exactly that: residual 0 in all nine of its cells. But those cells
were K=24, and at K=24 the binding constraint is thermal.

THIS SWEEP FALSIFIES "always 0", and the way it fails is the real finding. Past oversubscription
~2.5x with 100 kW requests the residual TURNS POSITIVE -- median +4-6%, and up to +43% in
individual cells (AC-verified, violation 0). The mechanism, checked directly: once enough coarse
PV is requested, the binding constraint at the PV-alone optimum stops being the transformer and
becomes NODAL OVERVOLTAGE (vmax pinned at ~1.05 while the transformer sits below its cap). And
overvoltage coupling is TWO-WAY, exactly as on Ckt7: a co-located load suppresses voltage and
lets in PV that was overvoltage-infeasible alone. In the +43% cell, joint seats 6 PV where PV
alone seats 4 -- the extra 200 kW of PV is enabled by the 4 loads joint co-locates with it, and
no PV-first order can find it because PV-first fixes the PV set against the bare baseline.

    So the residual's ON/OFF switch is not FEEDER IDENTITY -- it is WHICH CONSTRAINT BINDS.
    Thermal binding is one-way (residual 0); voltage binding is two-way (residual > 0). Ckt7 is
    always voltage-limited, so always two-way. Ckt24 is thermal-limited at low oversubscription
    (residual 0) and crosses into voltage-limited under enough coarse-PV oversubscription
    (residual up to +43%). One mechanism predicts both feeders AND the within-Ckt24 transition.

The cost-vs-PARALLEL is reported alongside, because it does NOT vanish on Ckt24 -- parallel
study (each track vs the bare baseline) leaves load stranded at 0, and only a sequential
PV-first order or joint co-study recovers it. So the honest Ckt24 statement is two-part:
segregation costs real capacity, AND on a one-way feeder a generation-first order recovers all
of it; the irreducible residual is Ckt7's alone.

A CKT24-SPECIFIC CALIBRATION, and it is the interesting part. On Ckt7 the PV track is
overvoltage-scarce even at fine granularity, so 30 kW requests oversubscribe it. On Ckt24 PV
RELIEVES the binding (substation-transformer) constraint, so fine-grained PV barely
oversubscribes -- at 30 kW the feeder simply hosts more, and PV_requested / PV_solo stays ~1.0
up past 4 MW requested. Ckt24's PV scarcity is the small per-bus OVERVOLTAGE headroom
(+0.0043 pu), which bites only when each request is COARSE: at 100 kW just 44% of buses host a
PV solo, so a K=40 queue is ~3.5x oversubscribed (exp88's M60). So this sweep fixes the size at
100 kW -- the granularity at which the PV track actually oversubscribes -- and varies total.

Load_frac 0.5, oversubscription reported as (PV requested) / (PV certified solo); load solo is
0 by construction. Ckt24 forced to source 1.05 (its only feasible setpoint, exp88).

Run:
  .venv/bin/python experiments/96_residual_oversubscription_ckt24.py --save
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

_seg = __import__("88_ckt24_segregation")          # Ckt24Feeder, build_mixed, certify
_seq = __import__("92_sequential_segregation_ckt24")   # ckt24_with_anchor

OUT = Path(__file__).resolve().parents[1] / "outputs"

SIZE_KW = 100.0                    # coarse enough that PV overvoltage-scarcity bites (see docstring)
TOTALS_KW = [700, 1300, 2000, 2800, 3600, 4400]
SETPOINT = 1.05                    # FORCED -- Ckt24's only feasible setpoint (exp88)


def three_way(f, projects, n_jobs):
    """M64's residual on one Ckt24 queue: parallel, sequential (both orders), joint.

    Mirrors exp93.three_way but uses the Ckt24 anchor (exp92), which keeps the substation
    transformer's non-worsening cap at the SHIPPED value while baking the first track in -- so
    the approved PV's relief does not become a false floor for the load track."""
    pv = [p for p in projects if p.p_mw < 0]
    ld = [p for p in projects if p.p_mw > 0]

    f._proj = None
    pv_ids, pv_kw = _seg.certify(f, pv, n_jobs)
    f._proj = None
    ld_ids, ld_kw = _seg.certify(f, ld, n_jobs)          # 0 on Ckt24 (load alone worsens trafo)
    seg_par = pv_kw + ld_kw

    f._proj = None
    _j, joint = _seg.certify(f, projects, n_jobs)

    f_pv = _seq.ckt24_with_anchor(f, pv, pv_ids)          # base + accepted PV -> relief
    _l2, ld2 = _seg.certify(f_pv, ld, n_jobs)
    seq_pv1 = pv_kw + ld2

    f_ld = _seq.ckt24_with_anchor(f, ld, ld_ids)          # base + accepted load (empty set)
    _p2, pv2 = _seg.certify(f_ld, pv, n_jobs)
    seq_ld1 = ld_kw + pv2

    best_seq = max(seq_pv1, seq_ld1)
    return dict(pv_alone_kw=pv_kw, load_alone_kw=ld_kw, seg_parallel_kw=seg_par,
                seq_pvfirst_kw=seq_pv1, seq_loadfirst_kw=seq_ld1, best_sequential_kw=best_seq,
                joint_kw=joint,
                cost_vs_parallel_pct=(100 * (joint / seg_par - 1) if seg_par > 0 else np.nan),
                residual_vs_sequential_pct=(100 * (joint / best_seq - 1) if best_seq > 0 else np.nan))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--setpoint", type=float, default=SETPOINT)
    ap.add_argument("--load-frac", type=float, default=0.5)
    ap.add_argument("--size-kw", type=float, default=SIZE_KW)
    ap.add_argument("--totals-kw", type=float, nargs="+", default=TOTALS_KW)
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2, 3, 4, 5])
    ap.add_argument("--n-jobs", type=int, default=8)
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()

    print("\n  M68 -- RESIDUAL vs OVERSUBSCRIPTION  (Ckt24 @%.2f, %.0f kW requests, load %.0f%%)\n"
          % (a.setpoint, a.size_kw, 100 * a.load_frac))
    print("  residual is 0 while the transformer (thermal) binds; it turns positive once coarse-PV")
    print("  oversubscription makes OVERVOLTAGE bind (two-way, co-located load enables extra PV).\n")

    f = _seg.Ckt24Feeder.at_setpoint(a.setpoint)

    hdr = (f"  {'total':>6} {'K':>4} | {'PV req':>7} {'PV solo':>8} {'oversub':>8} | "
           f"{'vs parallel':>12} | {'residual %: med':>15} {'min':>6} {'max':>6}")
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))

    rows = []
    for total in a.totals_kw:
        K = max(2, int(round(total / a.size_kw)))
        per = []
        for seed in a.seeds:
            t0 = time.time()
            projects = _seg.build_mixed(f, K, seed, a.load_frac, a.size_kw)
            pv_req = sum(abs(p.p_mw) for p in projects if p.p_mw < 0) * 1000
            r = three_way(f, projects, a.n_jobs)
            oversub = (pv_req / r["pv_alone_kw"]) if r["pv_alone_kw"] > 0 else float("inf")
            r.update(setpoint=a.setpoint, load_frac=a.load_frac, size_kw=a.size_kw, k=K,
                     seed=seed, total_kw=total, pv_requested_kw=pv_req, oversub_ratio=oversub,
                     dt=time.time() - t0)
            rows.append(r)
            per.append(r)
            if a.save:
                OUT.mkdir(exist_ok=True)
                pd.DataFrame(rows).to_csv(OUT / "m68_residual_oversub_ckt24.csv", index=False)

        res = np.array([p["residual_vs_sequential_pct"] for p in per], float)
        res = res[~np.isnan(res)]
        parg = np.array([p["cost_vs_parallel_pct"] for p in per], float)
        parg = parg[~np.isnan(parg)]
        pv_req_m = np.median([p["pv_requested_kw"] for p in per])
        pv_solo_m = np.median([p["pv_alone_kw"] for p in per])
        ov = np.median([p["oversub_ratio"] for p in per if np.isfinite(p["oversub_ratio"])] or [np.nan])
        if len(res):
            pstr = (f"{np.median(parg):>+10.0f}%" if len(parg) else f"{'--':>11}")
            print(f"  {total:>6.0f} {K:>4} | {pv_req_m:>7.0f} {pv_solo_m:>8.0f} {ov:>7.2f}x | "
                  f"{pstr:>12} | {np.median(res):>+14.1f}% "
                  f"{np.min(res):>+5.1f} {np.max(res):>+5.1f}")

    d = pd.DataFrame(rows)
    if a.save:
        OUT.mkdir(exist_ok=True)
        d.to_csv(OUT / "m68_residual_oversub_ckt24.csv", index=False)

    print("\n" + "=" * 84)
    print("  Residual vs oversubscription (Ckt24, %.0f kW requests):" % a.size_kw)
    for total in a.totals_kw:
        sub = d[d.total_kw == total]
        r = sub.residual_vs_sequential_pct.replace([np.inf, -np.inf], np.nan).dropna()
        ov = sub.oversub_ratio.replace([np.inf, -np.inf], np.nan).dropna()
        if len(r):
            # residual clamped at 0 for the bar; on Ckt24 it should barely register
            bar = "#" * int(round(max(0.0, r.median()) / 2)) or "."
            print(f"    oversub {ov.median():>4.2f}x (total {total:>4.0f} kW): "
                  f"residual {r.median():>+6.1f}%  {bar}")
    print("\n  Residual is 0 while the substation transformer (THERMAL) binds -- PV-first recovers")
    print("  all -- and turns positive (to +43% in a cell) once coarse-PV oversubscription makes")
    print("  OVERVOLTAGE the binding constraint, where co-located load enables extra PV (two-way).")
    print("  The switch is WHICH CONSTRAINT BINDS, not feeder identity: thermal one-way, voltage")
    print("  two-way. Ckt24 shows both regimes; Ckt7 (always voltage-limited) shows only the 2nd.\n")
    if a.save:
        print("  saved -> outputs/m68_residual_oversub_ckt24.csv")


if __name__ == "__main__":
    main()
