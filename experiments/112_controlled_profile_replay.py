"""M86 -- THE CARVE-OUT, MODELLED: A CONTROLLED ACCEPT-SET AGAINST A REAL DAY

WHAT THIS SETTLES
-----------------
exp104 (M76) replayed a snapshot-certified Swiss accept-set against the feeder's own measured
profile and found it feasible in only 49 of 192 intervals. The paper's answer is Section 6.2's
carve-out: the co-located cells are claimed for dispatchable generation under NET-EXPORT
CONTROL, where a control mechanism holds the cancellation at all hours by construction. That
sentence is an argument, not a measurement, and it is load-bearing -- it is the only thing
standing between the irreducible residual and the diurnal fragility result.

This file measures it. Same feeder, same profile, same 192 intervals, same certified accept-set
-- but the accepted requests are CO-LOCATED pairs operated under a net-exchange band instead of
being scaled independently by irradiance and demand.

THE DIFFERENCE FROM exp104
--------------------------
exp104 scales each accepted request by its own shape: PV by irradiance, load by the feeder's
native demand curve. Those two shapes peak at different hours, so the cancellation the snapshot
certified simply stops holding -- which is the finding, and it stands.

Under control the site does not follow those shapes. It follows its agreement: net exchange
within +/- beta x rated at every instant, delivered by dispatchable generation (Ireland's rule
requires dispatchable onsite generation precisely because curtailment alone cannot serve the
evening, when there is no sun to curtail). The network sees only the site net, so the whole
family of admissible dispatches is bounded by the two uniform extremes, which is what we check.

Only the feeder's OWN native load still varies with the profile, rebuilt at every interval from
the shipped data, exactly as exp104 does.

WHAT IT REPORTS
---------------
Feasible intervals out of 192, per band width, with the two directions kept separate because
an accept-set certified at the joint optimum is packed against its load-side limits and so has
far less import headroom than export headroom (exp111). beta = 0 is perfect cancellation, where
the sites are electrically invisible and the feeder sees only its own load; beta = 1 is no
control, the exp104 regime.

Run:
  uv run python experiments/112_controlled_profile_replay.py \
      --mat cache/swiss/55nodes_real_swiss_distribution_grid.mat --save
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

_s = __import__("103_residual_swiss")             # load_swiss
_seq = __import__("91_sequential_segregation")    # certify
_alt = __import__("109_alternating_rounds")       # build_colocated_pp
_ctl = __import__("111_net_export_control")       # site_net_projects
_day = __import__("104_swiss_daily_profile")      # shapes, scaled_projects

OUT = Path(__file__).resolve().parents[1] / "outputs"

BETAS = [0.0, 0.1, 0.2, 0.3, 0.5, 0.7, 1.0]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--mat", required=True)
    ap.add_argument("--vn-kv", type=float, default=21.0)
    ap.add_argument("--vmin", type=float, default=0.95)
    ap.add_argument("--vmax", type=float, default=1.05)
    ap.add_argument("--k", type=int, default=48)
    ap.add_argument("--size-kw", type=float, default=500.0)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--betas", type=float, nargs="+", default=BETAS)
    ap.add_argument("--day-types", type=int, nargs="+", default=None)
    ap.add_argument("--n-jobs", type=int, default=6)
    ap.add_argument("--out", default="m86_controlled_profile.csv")
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()

    f = _s.load_swiss(a.mat, a.vn_kv, "peakload", a.vmin, a.vmax)
    t_ref, d_ref = f._snap["t"], f._snap["d"]

    # CO-LOCATED draw, certified jointly at the reference snapshot -- the accept-set the
    # carve-out is about, not the randomly-placed one exp104 replayed.
    projects = _alt.build_colocated_pp(f, a.k, a.seed, a.size_kw)
    f._proj = None
    ids, joint_kw = _seq.certify(f, projects, a.n_jobs)
    accepted = [p for p in projects if p.id in set(ids)]
    sites = {int(p.bus) for p in accepted}

    pv_shape, load_shape = _day.shapes(a.mat)
    pv_ref, load_ref = pv_shape[t_ref, d_ref], load_shape[t_ref, d_ref]
    days = a.day_types if a.day_types else sorted({d_ref, 0})

    print("\n  M86 -- CONTROLLED CO-LOCATED ACCEPT-SET vs THE SWISS MEASURED DAY\n")
    print(f"  accept-set: {len(accepted)} requests = {joint_kw:.0f} kW over {len(sites)} co-located sites")
    print(f"  reference interval {t_ref}/96, day-type {d_ref}; sweeping day-types {days}, 96 intervals each")
    print("  UNCONTROLLED replays each request on its own shape (exp104's regime);")
    print("  CONTROLLED holds every site to |net| <= beta x rated at every instant.\n")

    rows = []
    t0 = time.time()
    for d in days:
        for t in range(96):
            ft = _s.load_swiss(a.mat, a.vn_kv, (t, d), a.vmin, a.vmax)   # native load at instant

            # the uncontrolled baseline, exp104's construction on this accept-set
            pv_sc = max(0.0, float(pv_shape[t, d]) / pv_ref) if pv_ref > 0 else 0.0
            ld_sc = max(0.0, float(load_shape[t, d]) / load_ref) if load_ref > 0 else 0.0
            snap = _day.scaled_projects(accepted, pv_sc, ld_sc)
            ft._proj = None
            unc_ok, _m, _v, unc_i = ft.evaluate(ft.ctx, snap, {p.id for p in snap})

            for b in a.betas:
                res = {}
                for sign, nm in ((-1.0, "export"), (+1.0, "import")):
                    pr = _ctl.site_net_projects(accepted, b, a.size_kw, sign)
                    if not pr:
                        res[nm] = (True, {})
                        continue
                    ft._proj = None
                    ok, _m2, _v2, info = ft.evaluate(ft.ctx, pr, {p.id for p in pr})
                    res[nm] = (bool(ok), info)
                rows.append(dict(day=d, t=t, beta=b,
                                 pv_scale=pv_sc, load_scale=ld_sc,
                                 uncontrolled_ok=bool(unc_ok),
                                 uncontrolled_line_pct=unc_i.get("line_pct"),
                                 export_ok=res["export"][0], import_ok=res["import"][0],
                                 both_ok=res["export"][0] and res["import"][0],
                                 export_line_pct=res["export"][1].get("line_pct"),
                                 import_line_pct=res["import"][1].get("line_pct")))
        print(f"  day-type {d} done ({time.time() - t0:.0f}s)", flush=True)
        if a.save:
            OUT.mkdir(exist_ok=True)
            pd.DataFrame(rows).to_csv(OUT / a.out, index=False)

    d_all = pd.DataFrame(rows)
    n_int = d_all[["day", "t"]].drop_duplicates().shape[0]
    unc = d_all.drop_duplicates(["day", "t"]).uncontrolled_ok.sum()

    print("\n" + "=" * 74)
    print(f"\n  UNCONTROLLED (exp104's regime): {unc}/{n_int} intervals feasible\n")
    print(f"  {'band':>6} {'export-safe':>12} {'import-safe':>12} {'both':>10}")
    print("  " + "-" * 44)
    for b, g in d_all.groupby("beta"):
        print(f"  {b:>6.1f} {int(g.export_ok.sum()):>7d}/{n_int:<4d} "
              f"{int(g.import_ok.sum()):>7d}/{n_int:<4d} {int(g.both_ok.sum()):>5d}/{n_int:<4d}")
    print("\n  'both' is the conservative reading: every admissible dispatch inside the band")
    print("  is feasible at that interval. Export and import differ because the accept-set")
    print("  was certified at the joint optimum, which packs the feeder against whichever")
    print("  side binds first.\n")
    if a.save:
        print(f"  saved -> outputs/{a.out}\n")


if __name__ == "__main__":
    main()
