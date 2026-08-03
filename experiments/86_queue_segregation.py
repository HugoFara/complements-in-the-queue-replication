"""M58 -- WHAT DOES IT COST TO STUDY GENERATION AND LOAD IN SEPARATE QUEUES?

This is the experiment the complementarity result has been pointing at, and neither exp81 nor
exp83 actually ran it. Both varied a queue's COMPOSITION (how much of it is load) and compared
across queues. That answers "does a mixed queue host more than a pure one", which is a
statement about queues. It does not answer the institutional question, which is about a
PROCESS applied to ONE queue:

    Real interconnection processes study generation and load SEPARATELY -- different tariffs,
    different study tracks, often different departments. Take one fixed queue of PV and EV
    requests. Study it the way a utility does, and study it jointly. What is the difference?

    SEGREGATED   certify the PV requests against the baseline feeder (no new loads),
                 certify the LOAD requests against the baseline feeder (no new PV),
                 connect the union.
    JOINT        certify all requests together.

The same requests, the same feeder, the same physics. Only the STUDY BOUNDARY moves.

TWO OUTCOMES, AND BOTH ARE FINDINGS:
  * If JOINT > SEGREGATED, segregation leaves hosting capacity on the table -- a PV
    application and an EV-depot application that would fit together are studied in different
    queues and one of them is rejected as physically impossible.
  * If the SEGREGATED UNION is not even AC-feasible, segregation is not merely conservative,
    it is UNSAFE: each track certifies against a baseline the other track invalidates. We
    check this explicitly (`union_feasible`), because it is the more serious claim and we do
    not get to assume which way it falls.

RUN AT TWO SETPOINTS, because M55 showed the published one is a pin:
    1.05  EPRI's shipped source voltage. Baseline vmax 1.0488, headroom 0.0012 pu; PV is
          solo-feasible at 7/199 buses. Any PV result here is about the setpoint.
    1.04  One notch down, still legal (baseline vmin 0.9503 > 0.94). Headroom 0.0112 pu; PV
          is solo-feasible at 194/199 buses. THIS is the number to quote: the feeder has real
          headroom and PV works on its own, so anything segregation costs here is physics.

Usage:
  python experiments/86_queue_segregation.py --save
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

_m = __import__("75_milp_maximality")
_s = __import__("83_setpoint_sweep")

OUT = Path(__file__).resolve().parents[1] / "outputs"

SETPOINTS = [1.05, 1.04]
LOAD_FRACTIONS = [0.3, 0.5, 0.7]


def certify(f, projects, n_jobs):
    """The certified (AC-feasible, maximal) allocation over `projects`. Empty queue -> 0."""
    if not projects:
        return set(), 0.0
    ids, mw, _ = _m.certified_allocation(f.ctx, projects, f, f, n_jobs=n_jobs, verbose=False)
    return set(ids), mw * 1000


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--setpoints", type=float, nargs="+", default=SETPOINTS)
    ap.add_argument("--load-fractions", type=float, nargs="+", default=LOAD_FRACTIONS)
    ap.add_argument("--size-kw", type=float, default=100.0)
    ap.add_argument("--k", type=int, default=17)
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    ap.add_argument("--n-jobs", type=int, default=8)
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()

    print("\n  M58 -- THE COST OF STUDYING GENERATION AND LOAD SEPARATELY  (Ckt7)\n")
    print(f"  one fixed queue of K={a.k} requests at {a.size_kw:.0f} kW; only the STUDY")
    print(f"  BOUNDARY moves. median over seeds {a.seeds}.\n")

    hdr = (f"  {'source':>7} {'load%':>6} | {'PV alone':>9}{'load alone':>11}{'SEGREG':>9}"
           f" | {'JOINT':>8} | {'joint-vs-segreg':>16} {'union AC-ok?':>13}")
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))

    rows = []
    for sp in a.setpoints:
        f = _s.SlackFreeFeeder.at_setpoint(sp, 0.94, 1.05, 100.0)
        for lf in a.load_fractions:
            per_seed = []
            for seed in a.seeds:
                t0 = time.time()
                projects = _s._build_mixed(f, a.k, seed, lf, a.size_kw)
                pv = [p for p in projects if p.p_mw < 0]
                ld = [p for p in projects if p.p_mw > 0]

                # --- SEGREGATED: each track certified against the BASELINE feeder ---
                pv_ids, pv_kw = certify(f, pv, a.n_jobs)
                ld_ids, ld_kw = certify(f, ld, a.n_jobs)
                seg_kw = pv_kw + ld_kw

                # --- is the union of the two separately-certified sets even AC-feasible? ---
                union = pv_ids | ld_ids
                f._proj = None                       # restage: the project list changed
                union_ok, _, _, _ = f.evaluate(f.ctx, projects, union)

                # --- JOINT: one study over the same requests ---
                f._proj = None
                _j_ids, joint_kw = certify(f, projects, a.n_jobs)

                per_seed.append((pv_kw, ld_kw, seg_kw, joint_kw, union_ok))
                rows.append(dict(setpoint=sp, load_frac=lf, seed=seed, k=a.k,
                                 size_kw=a.size_kw, n_pv=len(pv), n_load=len(ld),
                                 pv_alone_kw=pv_kw, load_alone_kw=ld_kw,
                                 segregated_kw=seg_kw, joint_kw=joint_kw,
                                 union_ac_feasible=bool(union_ok),
                                 cost_of_segregation_pct=(100 * (joint_kw / seg_kw - 1)
                                                          if seg_kw > 0 else float("nan")),
                                 dt=time.time() - t0))

            arr = np.array([[p[0], p[1], p[2], p[3]] for p in per_seed])
            med = np.median(arr, axis=0)
            all_ok = all(p[4] for p in per_seed)
            any_ok = any(p[4] for p in per_seed)
            gain = (f"{100 * (med[3] / med[2] - 1):+.0f}%" if med[2] > 0
                    else ("inf" if med[3] > 0 else "0%"))
            ok_s = "yes" if all_ok else ("some" if any_ok else "NO")
            print(f"  {sp:>7.2f} {100*lf:>5.0f}% | {med[0]:>9.0f}{med[1]:>11.0f}{med[2]:>9.0f}"
                  f" | {med[3]:>8.0f} | {gain:>16} {ok_s:>13}")

    d = pd.DataFrame(rows)
    if a.save:
        OUT.mkdir(exist_ok=True)
        d.to_csv(OUT / "m58_segregation_ckt7.csv", index=False)

    print("\n" + "=" * 92)
    for sp in a.setpoints:
        sub = d[d.setpoint == sp]
        c = sub.cost_of_segregation_pct.replace([np.inf, -np.inf], np.nan).dropna()
        print(f"  source {sp:.2f}:  joint beats segregated study by "
              f"{c.min():+.0f}% to {c.max():+.0f}%  (median {c.median():+.0f}%)"
              f"   | union AC-feasible in {int(sub.union_ac_feasible.sum())}/{len(sub)} cells")
    print("\n  'union AC-feasible' = can the utility actually energise the two separately-")
    print("  certified sets together? If NO, segregation is not conservative, it is UNSAFE.\n")
    if a.save:
        print("  saved -> outputs/m58_segregation_ckt7.csv")


if __name__ == "__main__":
    main()
