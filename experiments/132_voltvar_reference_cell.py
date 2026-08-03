"""M104 -- VOLT/VAR ON THE ACTUAL REFERENCE CELL, AND THE PAIR CERTIFICATE UNDER THE DROOP

WHY, PART 1: THE PUBLISHED Q(V) NUMBER IS ON THE WRONG ENGINE
--------------------------------------------------------------
exp124's end-to-end arm was run through `120_certified_share.CELLS["ckt24_50"]`, whose
docstring calls itself "the paper's REFERENCE cell". It is not. That constructor calls
`109_alternating_rounds.get_family("ckt24")`, which builds `88_ckt24_segregation.Ckt24Feeder`
-- the BALANCED pandapower positive-sequence feeder -- at 50 kW. The paper's reference cell is
exp125's `Ckt24DSSFeeder`: the shipped three-phase model, solved natively in OpenDSS. The
distinction is the whole point of Section 5.2: at 100 kW the two engines differ by a factor of
five, because a single-phase 100 kW request draws 417 A through a 195-299 A service and the
balanced solve spreads it over three phases at 139 A.

So the manuscript's sentence "we re-ran the whole three-way study on the reference cell with
Q(V) live in every AC verification" compares a BALANCED Q(V) residual against a
PHASE-RESOLVED unity-power-factor one. This file measures both on the same engine.

WHY, PART 2: THE CERTIFICATE HAS NEVER BEEN RUN UNDER REGULATION
-----------------------------------------------------------------
The third referee:

  "The headline 12.5% is pair-certified at unity power factor, a configuration IEEE 1547-2018
  jurisdictions increasingly do not permit for new DER, while the realistic configuration has
  search evidence only. The cheap partial fix: run the pair certificate at the Q(V) fixed
  points. If certified pairs exist under the droop, the abstract stands; if not, the
  abstract's 'all of it certified by explicit pairs' needs a unity-power-factor qualifier."

exp124 answered a different question -- whether the UNITY-PF pairs survive being re-checked
under the droop (42 of 142 lost the hypothesis, only 16 stayed jointly feasible). That scopes
the old pairs; it does not ask what a regulated process certifies. This file runs the
certificate machinery itself with Q(V) live in every AC verdict, so the pairs are the ones a
regulated study would find.

DESIGN, UNCHANGED FROM exp124
-----------------------------
Q(V) lives in the AC verification, not in the MILP: the certifier keeps its unity-power-factor
linear model and every set it proposes is judged by an AC solve in which the generation
requests regulate to the IEEE 1547-2018 Category B default curve. Design decision 1 of exp124,
for the reason given there -- putting Q(V) in the MILP needs a linearized reactive model whose
error would have to be validated on the optimizer's own trajectory (M42).

NO RE-ANCHORING HERE, AND THAT IS DELIBERATE
--------------------------------------------
exp124's hypothesis check re-reads the substation non-worsening reference at the fixed point,
because there the question is relative: does a half become feasible on top of an already-
approved, already-regulating base? An end-to-end study asks the absolute question, and its
reference is the shipped feeder -- which has no requests on it and therefore no inverters and
no vars, exactly as the unity-PF arm assumes. Both arms are judged against the same shipped
118.68% substation loading, so the comparison is clean.

Run:
  uv run python experiments/132_voltvar_reference_cell.py --seeds 0 1 2 --save
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

_alt = __import__("109_alternating_rounds")
_cert = __import__("110_simultaneity_certificate")
_share = __import__("120_certified_share")
_u24 = __import__("125_ckt24_unbalanced")
_vv = __import__("124_voltvar_colocated")

OUT = Path(__file__).resolve().parents[1] / "outputs"


def vv_family(fam, base_f):
    """exp124's wrapper, forced onto the OpenDSS path, over exp125's three-phase feeder."""
    def _wrap(f):
        return _vv.VoltVarDSS(f)

    def _with_anchor(fw, anchor_projects, anchor_ids):
        return _wrap(fam.with_anchor(getattr(fw, "_b", fw), anchor_projects, anchor_ids))

    def _reset(fw, projects=()):
        fam.reset(getattr(fw, "_b", fw), projects)

    return _alt.Family(f"{fam.name}_voltvar", lambda: _wrap(base_f), fam.make_projects,
                       _with_anchor, fam.certify,
                       f"{fam.desc} + IEEE 1547 Category B Volt/VAr in AC verification",
                       reset=_reset)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--k", type=int, default=24)
    ap.add_argument("--size-kw", type=float, default=50.0)
    ap.add_argument("--seeds", type=int, nargs="+", default=list(range(10)))
    ap.add_argument("--n-jobs", type=int, default=6)
    ap.add_argument("--no-certificate", action="store_true")
    ap.add_argument("--out", default="m125_voltvar_reference.csv")
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()

    print("\n  M104 -- CATEGORY B VOLT/VAR ON THE PHASE-RESOLVED REFERENCE CELL\n")
    print("  The published Q(V) end-to-end number was measured on the BALANCED feeder at")
    print("  50 kW and compared against a PHASE-RESOLVED unity-PF one. Same engine here.\n")

    f = _u24.Ckt24DSSFeeder(_u24.MASTER)
    fam = _u24.make_family(f, "EPRI Ckt24 UNBALANCED, co-located, 50 kW")
    vfam = vv_family(fam, f)
    vf = vfam.make_feeder()

    rows = []
    hdr = (f"  {'seed':>4} | {'gen':>6} {'load':>6} {'seq':>6} {'JOINT':>6} {'alt':>6} | "
           f"{'R1%':>6} {'R*%':>6} | {'mixed':>5} {'cert%':>6} | {'QV':>6} {'fail':>5} | "
           f"{'s':>5}")
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for seed in a.seeds:
        t0 = time.time()
        _vv.STATS.reset()
        projects = vfam.make_projects(vf, seed, a.k, a.size_kw)
        vfam.reset(vf, projects)
        w = _alt.three_way_generic(vfam, vf, projects, a.n_jobs)
        joint = w["joint_kw"]
        best, best_ids = -1.0, set()
        for start in ("gen", "load"):
            acc = _cert.fixed_point_accept_set(vfam, vf, projects, a.n_jobs, start)
            tot = sum(abs(p.p_mw) for p in acc) * 1000.0
            if tot > best:
                best, best_ids = tot, {p.id for p in acc}
        r1, rstar = joint - w["best_sequential_kw"], joint - best

        n_mixed, cert_kw, n_single, n_same = 0, 0.0, 0, 0
        if not a.no_certificate:
            c = _cert.certificate(vfam, vf, projects, best_ids)
            mixed = c.pop("_mixed_pairs")
            n_mixed, n_single = c["n_mixed_pairs"], c["n_addable_singles"]
            n_same = c["n_same_sign_pairs"]
            _taken, cert_kw = _share.certified_disjoint_pairs(vfam, vf, projects,
                                                              best_ids, mixed)
        share = (100.0 * cert_kw / rstar) if rstar > 1e-9 else np.nan
        rows.append(dict(cell="ckt24_50_3ph", engine="opendss_unbalanced", regime="cat_b",
                         seed=seed, k=a.k, size_kw=a.size_kw, **w,
                         alternation_kw=best, r1_kw=r1, rstar_kw=rstar,
                         r1_pct=100 * r1 / joint if joint else np.nan,
                         rstar_pct=100 * rstar / joint if joint else np.nan,
                         n_addable_singles=n_single, n_same_sign_pairs=n_same,
                         n_mixed_pairs=n_mixed, certified_kw=cert_kw,
                         certified_share_pct=share,
                         vv_evals=_vv.STATS.evals, vv_failures=_vv.STATS.failures,
                         vv_fail_pct=_vv.STATS.fail_pct, dt=time.time() - t0))
        print(f"  {seed:>4d} | {w['gen_alone_kw']:>6.0f} {w['load_alone_kw']:>6.0f} "
              f"{w['best_sequential_kw']:>6.0f} {joint:>6.0f} {best:>6.0f} | "
              f"{100 * r1 / joint if joint else 0:>6.1f} "
              f"{100 * rstar / joint if joint else 0:>6.1f} | {n_mixed:>5d} {share:>6.1f} | "
              f"{_vv.STATS.evals:>6d} {_vv.STATS.fail_pct:>4.1f}% | "
              f"{time.time() - t0:>5.0f}", flush=True)
        if a.save:
            OUT.mkdir(exist_ok=True)
            pd.DataFrame(rows).to_csv(OUT / a.out, index=False)

    d = pd.DataFrame(rows)
    print("\n" + "=" * 96)
    print(f"\n  Category B, phase-resolved: R1 median {d.r1_pct.median():.1f}%, "
          f"R* median {d.rstar_pct.median():.1f}%, positive in "
          f"{int((d.rstar_kw > 1e-9).sum())} of {len(d)}, "
          f"negative in {int((d.rstar_kw < -1e-9).sum())}")
    s = d.certified_share_pct.dropna()
    if len(s):
        print(f"  pairs certified under the droop: median share {s.median():.1f}% of R*, "
              f"{int((d.n_mixed_pairs > 0).sum())} of {len(d)} draws carry a pair")
    else:
        print("  NO certified pairs under the droop on any draw -- the abstract's "
              "'certified by explicit pairs' needs a unity-power-factor qualifier")
    print("\n  Unity power factor, same engine and cell [m101_ckt24_unbalanced_50kw]:")
    print("    R1 16.7%, R* 12.5%, positive 7/10, certified share 100%\n")
    if a.save:
        OUT.mkdir(exist_ok=True)
        d.to_csv(OUT / a.out, index=False)
        print(f"  saved -> outputs/{a.out}\n")


if __name__ == "__main__":
    main()
