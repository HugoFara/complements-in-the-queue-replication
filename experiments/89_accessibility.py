r"""M61 -- IS THE MIXED-QUEUE SYSTEM EVEN *ACCESSIBLE*? (the greedoid axiom)

E2/M53 showed a mixed PV+load queue breaks DOWNWARD CLOSURE: a request that is infeasible
alone can be feasible in company. Downward closure is an axiom of independence systems, so
the whole greedy/rank-quotient apparatus (Jenkyns; Korte-Hausmann) simply does not apply.

The natural next question -- raised in the review brief -- is whether a WEAKER axiom
survives. Greedoids (Korte, Lovasz & Schrader 1991) relax hereditary to **ACCESSIBILITY**:

    every nonempty feasible set S contains SOME element x with S \ {x} still feasible.

That is much weaker than "every subset of S is feasible". It says only that you can always
walk a feasible set back to the empty set one element at a time, never leaving feasibility.
If it holds, the mixed-queue system is at least a candidate greedoid and there is a theory
to reach for. If it FAILS -- if some certified accept-set is a feasibility ISLAND that
cannot be reduced by any single removal without violating a limit -- then the system is not
a greedoid either, and the failure is far more radical than "the greedy bound is loose".

WHY IT COULD PLAUSIBLY FAIL HERE, AND WHY THAT WOULD MATTER. On a thermally-pinned feeder
(Ckt24) a set of EV loads is only seatable because PV export offsets them. Remove a PV
request and the loads overload a transformer; remove a load and the remaining PV may push
overvoltage. A jointly-certified mixed set can therefore be feasible while EVERY
single-element removal from it is not. Physically that is an allocation you cannot
DECOMMISSION one project at a time -- which, if real, is a licensing fact and not only a
combinatorial one.

The test is exact, model-free and cheap: |S| AC solves per certified set, on the real oracle.

Usage:
  python experiments/89_accessibility.py --feeder ckt7 --save
  python experiments/89_accessibility.py --feeder ckt24 --save
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

OUT = Path(__file__).resolve().parents[1] / "outputs"
LOAD_FRACTIONS = [0.0, 0.15, 0.3, 0.5, 0.7, 0.85, 1.0]


def analyse(f, projects, accepted, tag):
    """Given a certified feasible accept-set, report:
      * accessible?      does SOME single removal stay feasible?
      * n_removable      how many of the |S| single removals stay feasible
      * hereditary?      do ALL single removals stay feasible (the independence-system axiom)
    """
    S = sorted(accepted)
    n_ok = 0
    for rid in S:
        f._proj = None
        ok, _, _, _ = f.evaluate(f.ctx, projects, set(S) - {rid})
        n_ok += int(ok)
    return dict(tag=tag, size=len(S), n_removable=n_ok,
                accessible=bool(n_ok > 0) if S else True,
                hereditary_1step=bool(n_ok == len(S)) if S else True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--feeder", choices=["ckt7", "ckt24"], default="ckt24")
    ap.add_argument("--load-fractions", type=float, nargs="+", default=LOAD_FRACTIONS)
    ap.add_argument("--size-kw", type=float, default=100.0)
    ap.add_argument("--k", type=int, default=40)
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    ap.add_argument("--n-jobs", type=int, default=8)
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()

    if a.feeder == "ckt24":
        _s = __import__("88_ckt24_segregation")
        mk = _s.Ckt24Feeder.at_setpoint(1.05)        # forced setpoint (M59)
        build = _s.build_mixed
    else:
        _s = __import__("83_setpoint_sweep")
        mk = _s.SlackFreeFeeder.at_setpoint(1.04, 0.94, 1.05, 100.0)   # honest setpoint (M55)
        build = _s._build_mixed
    f = mk

    print(f"\n  M61 -- ACCESSIBILITY OF THE MIXED-QUEUE FEASIBILITY SYSTEM  ({a.feeder.upper()})\n")
    print("    hereditary (independence system): EVERY single removal stays feasible")
    print("    accessible (greedoid)           : SOME  single removal stays feasible")
    print("    neither                         : the set is a feasibility ISLAND\n")
    hdr = (f"  {'load%':>6} {'seed':>5} {'|S|':>5} {'removable':>10} | {'hereditary':>11}"
           f" {'accessible':>11}")
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))

    rows = []
    for lf in a.load_fractions:
        for seed in a.seeds:
            t0 = time.time()
            projects = build(f, a.k, seed, lf, a.size_kw)
            f._proj = None
            ids, mw, _ = _m.certified_allocation(f.ctx, projects, f, f,
                                                 n_jobs=a.n_jobs, verbose=False)
            r = analyse(f, projects, set(ids), f"{lf}_{seed}")
            r.update(feeder=a.feeder, load_frac=lf, seed=seed, k=a.k,
                     certified_kw=mw * 1000, dt=time.time() - t0)
            rows.append(r)
            print(f"  {100*lf:>5.0f}% {seed:>5} {r['size']:>5} {r['n_removable']:>6}/{r['size']:<3}"
                  f" | {str(r['hereditary_1step']):>11} {str(r['accessible']):>11}")

    d = pd.DataFrame(rows)
    if a.save:
        OUT.mkdir(exist_ok=True)
        d.to_csv(OUT / f"m61_accessibility_{a.feeder}.csv", index=False)

    nz = d[d["size"] > 0]
    print("\n" + "=" * 72)
    print(f"  cells with a non-empty accept-set : {len(nz)}/{len(d)}")
    print(f"  HEREDITARY (all removals feasible): {int(nz.hereditary_1step.sum())}/{len(nz)}"
          f"   <- the independence-system axiom")
    print(f"  ACCESSIBLE (some removal feasible): {int(nz.accessible.sum())}/{len(nz)}"
          f"   <- the greedoid axiom")
    islands = nz[~nz.accessible]
    if len(islands):
        print(f"\n  *** {len(islands)} FEASIBILITY ISLAND(S): a certified allocation that NO single")
        print("      removal can reduce without violating a limit. The system is not a greedoid.")
    else:
        print("\n  No islands: accessibility SURVIVES where downward closure fails, so the")
        print("  mixed-queue system is a candidate greedoid (accessible but not hereditary).")
    if a.save:
        print(f"\n  saved -> outputs/m61_accessibility_{a.feeder}.csv")


if __name__ == "__main__":
    main()
