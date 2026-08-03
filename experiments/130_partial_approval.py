"""M102 -- PARTIAL APPROVAL: DOES SPLITTING THE REQUEST DISSOLVE THE RESIDUAL?

WHY
---
Reduced-capacity approval is ordinary interconnection practice: an operator that cannot seat
50 kW offers 25. The paper's own granularity sweep is therefore an objection to its own
conclusion, and the third referee makes it:

  "Your granularity sweep shows the reference residual at zero (median) when requests are
  25 kW at the same queue total. That strongly suggests a segregated process that offers the
  50 kW applicant a staged or reduced approval dissolves the residual without any tariff
  merger -- which directly undercuts 'the case for reform'. Either run the direct test (split
  each 50 kW request into two 25 kW halves and re-run alternation on the same draws) or
  concede in Limitations that the irreducibility claim is relative to atomic requests."

The premise is half right and that is why the direct test is needed. The published 25 kW cell
(m101_ckt24_unbalanced_25kw) is K=24 at 25 kW -- 600 kW of queue against the 50 kW cell's
1,200 -- so it is NOT the same queue total, and its median-zero residual confounds granularity
with oversubscription. exp95 already showed oversubscription is the switch that turns R1 on at
all. So the observed collapse at 25 kW may be nothing to do with partial approval.

THE TEST
--------
Same draws, same sites, same queue total, same feeder, same oracle. Each 50 kW request is
replaced by `--split` requests of 50/split kW at the SAME bus and phase, and the whole
three-way study is re-run on the finer queue:

  K=24 at 50 kW      12 co-located sites, one 50 kW leg each way        1,200 kW
  K=48 at 25 kW      the same 12 sites, two 25 kW legs each way         1,200 kW

Both arms see the split, which is the honest comparison: partial approval is available to the
segregated process and to the joint study alike, so if the residual survives it is not an
artifact of forcing the joint study to take atomic requests.

READING THE RESULT
------------------
  R* -> 0        partial approval is a segregated instrument that dissolves the residual,
                 and the reform claim is scoped to atomic requests. Concede it.
  R* survives    granularity moves the magnitude (Section 5.6 already reports that) but does
                 not remove the object, and the 25 kW cell's zero was oversubscription.

Run:
  uv run python experiments/130_partial_approval.py --split 2 --save
  uv run python experiments/130_partial_approval.py --split 2 --seeds 0 1 2 --no-certificate
"""
from __future__ import annotations

import argparse
import copy
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

OUT = Path(__file__).resolve().parents[1] / "outputs"


def split_projects(projects, n):
    """Each request becomes `n` requests of 1/n the size at the same attach point.

    The id keeps the parent's name with a letter suffix, so a split queue can be traced back
    to the atomic one it came from, and `gldf_key` -- the oracle's bus.phase handle -- is
    copied unchanged, which is what makes the two arms the same physical queue.
    """
    if n <= 1:
        return list(projects)
    out = []
    for p in projects:
        for j in range(n):
            q = copy.copy(p)
            q.id = f"{p.id}{chr(ord('a') + j)}"
            q.p_mw = p.p_mw / n
            out.append(q)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--k", type=int, default=24)
    ap.add_argument("--size-kw", type=float, default=50.0)
    ap.add_argument("--split", type=int, default=2)
    ap.add_argument("--seeds", type=int, nargs="+", default=list(range(10)))
    ap.add_argument("--n-jobs", type=int, default=6)
    ap.add_argument("--no-certificate", action="store_true")
    ap.add_argument("--out", default="m123_partial_approval.csv")
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()

    print("\n  M102 -- PARTIAL APPROVAL ON THE REFERENCE CELL\n")
    print(f"  Each {a.size_kw:.0f} kW request split into {a.split} x "
          f"{a.size_kw / a.split:.1f} kW at the same bus.phase. Queue total unchanged.\n")

    f = _u24.Ckt24DSSFeeder(_u24.MASTER)
    fam = _u24.make_family(f, f"EPRI Ckt24 UNBALANCED, co-located, "
                              f"{a.size_kw:.0f} kW split x{a.split}")
    rows = []
    hdr = (f"  {'seed':>4} | {'K':>3} {'gen':>6} {'load':>6} {'seq':>6} {'JOINT':>6} "
           f"{'alt':>6} | {'R1%':>6} {'R*%':>6} | {'mixed':>5} {'cert%':>6} | {'s':>5}")
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for seed in a.seeds:
        t0 = time.time()
        atomic = fam.make_projects(f, seed, a.k, a.size_kw)
        projects = split_projects(atomic, a.split)
        fam.reset(f, projects)
        w = _alt.three_way_generic(fam, f, projects, a.n_jobs)
        joint = w["joint_kw"]
        best, best_ids = -1.0, set()
        for start in ("gen", "load"):
            acc = _cert.fixed_point_accept_set(fam, f, projects, a.n_jobs, start)
            tot = sum(abs(p.p_mw) for p in acc) * 1000.0
            if tot > best:
                best, best_ids = tot, {p.id for p in acc}
        r1, rstar = joint - w["best_sequential_kw"], joint - best

        n_mixed, cert_kw = 0, 0.0
        if not a.no_certificate:
            c = _cert.certificate(fam, f, projects, best_ids)
            mixed = c.pop("_mixed_pairs")
            n_mixed = c["n_mixed_pairs"]
            _taken, cert_kw = _share.certified_disjoint_pairs(fam, f, projects,
                                                              best_ids, mixed)
        share = (100.0 * cert_kw / rstar) if rstar > 1e-9 else np.nan
        rows.append(dict(feeder="ckt24", engine="opendss_unbalanced", seed=seed,
                         placement="colocated", k=len(projects), split=a.split,
                         size_kw=a.size_kw / a.split, parent_size_kw=a.size_kw,
                         queue_kw=a.k * a.size_kw, **w,
                         alternation_kw=best, r1_kw=r1, rstar_kw=rstar,
                         r1_pct=100 * r1 / joint if joint else np.nan,
                         rstar_pct=100 * rstar / joint if joint else np.nan,
                         n_mixed_pairs=n_mixed, certified_kw=cert_kw,
                         certified_share_pct=share, dt=time.time() - t0))
        print(f"  {seed:>4d} | {len(projects):>3d} {w['gen_alone_kw']:>6.0f} "
              f"{w['load_alone_kw']:>6.0f} {w['best_sequential_kw']:>6.0f} {joint:>6.0f} "
              f"{best:>6.0f} | {100 * r1 / joint if joint else 0:>6.1f} "
              f"{100 * rstar / joint if joint else 0:>6.1f} | {n_mixed:>5d} {share:>6.1f} | "
              f"{time.time() - t0:>5.0f}", flush=True)
        if a.save:
            OUT.mkdir(exist_ok=True)
            pd.DataFrame(rows).to_csv(OUT / a.out, index=False)

    d = pd.DataFrame(rows)
    print("\n" + "=" * 88)
    print(f"\n  split x{a.split}: R1 median {d.r1_pct.median():.1f}%, "
          f"R* median {d.rstar_pct.median():.1f}%, "
          f"positive in {int((d.rstar_kw > 1e-9).sum())} of {len(d)}")
    print("\n  ATOMIC comparators on the same feeder and oracle:")
    print("    K=24 @ 50 kW (1,200 kW queue): R1 16.7%, R* 12.5%, positive 7/10"
          "   [m101_ckt24_unbalanced_50kw]")
    print("    K=24 @ 25 kW (  600 kW queue): R1  0.0%, R*  0.0%, positive 4/10"
          "   [m101_ckt24_unbalanced_25kw -- HALF the queue, not a granularity control]\n")
    if a.save:
        OUT.mkdir(exist_ok=True)
        d.to_csv(OUT / a.out, index=False)
        print(f"  saved -> outputs/{a.out}\n")


if __name__ == "__main__":
    main()
