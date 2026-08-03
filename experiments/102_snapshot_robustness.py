"""M74 -- does the co-located SMART-DS residual survive temporal non-coincidence?

exp101's co-located residual (+60..+129%) is certified at ONE snapshot: PV and load both at
nameplate, where the PV export cancels the load draw. The sharpest objection to any co-location
headline is that this cancellation is INSTANTANEOUS -- solar PV peaks at midday, EV/industrial
load in the evening -- so a pair certified jointly feasible at the coincident peak can VIOLATE a
limit at a non-coincident corner, where one is present without the other. "Certified jointly
feasible" is certified at one instant.

This file puts the objection on record. It takes the joint-certified co-located accept-set (the
exp101 machinery) and re-solves the OpenDSS power flow at the two extreme corners of the (PV, load)
operating box:

  * MAX-PV / MIN-LOAD  (midday):  PV at nameplate, load OFF -> worst OVERvoltage
  * MIN-PV / MAX-LOAD  (evening): PV OFF, load at nameplate -> worst UNDERvoltage

The coincident corner (both at nameplate) is the certified point and must come back feasible; it
is solved too, as a sanity check that the re-solve reproduces the certification.

INTERPRETATION. If both off-diagonal corners stay inside the 0.94-1.10 band, the residual is
robust to non-coincidence and the objection is pre-empted cheaply. If a corner violates, the
co-located residual is real only for SIMULTANEITY-limited co-location -- which is exactly the
dispatchable-generation, net-export-controlled "Energy Park" the reform actually targets (Brattle
2025, for Bloom Energy: fuel cells matched 24/7 to a flat data-center load, a control mechanism
that caps net grid exchange), NOT uncontrolled solar-plus-evening-EV. Either way the §2 claim is
scoped honestly, and the scope lands on the reform's own object.

Run:  .venv/bin/python experiments/102_snapshot_robustness.py --feeder uhs2_1247--udt10746 --save
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("OMP_NUM_THREADS", "1")

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

_smartds = __import__("101_residual_smartds")     # MixedDSSFeeder, load, build_colocated
_c = __import__("68_ckt7_gldf_headline")          # Project
_seq = __import__("91_sequential_segregation")    # certify

REPO = Path(__file__).resolve().parents[1]
CACHE = REPO / "cache" / "smartds"
OUT = REPO / "outputs"


def _scaled(projects, accepted_ids, pv_scale, load_scale):
    """The accepted projects with PV kW * pv_scale and load kW * load_scale -- a snapshot of the
    same certified allocation at a different point on the (PV, load) box. PV is p_mw<0, load p_mw>0."""
    out = []
    for p in projects:
        if p.id not in accepted_ids:
            continue
        s = pv_scale if p.p_mw < 0 else load_scale
        q = _c.Project(id=p.id, bus=p.bus, p_mw=p.p_mw * s)
        q.bus_name, q.phase, q.gldf_key = p.bus_name, p.phase, p.gldf_key
        out.append(q)
    return out


def solve_snapshot(f, snap_projects):
    """AC-solve the accept-set at a snapshot and read the band. Fresh list -> `evaluate` re-stages."""
    f._staged = None
    ok, mw, viol, d = f.evaluate(f.ctx, snap_projects, {p.id for p in snap_projects})
    return dict(feasible=ok, violation=viol, vmin=d.get("vmin", float("nan")),
                vmax=d.get("vmax", float("nan")))


def check_cell(f, K, seed, pv_kw, ev_kw, n_jobs):
    """Certify the co-located queue jointly (coincident nameplate), then re-solve the accept-set at
    the two off-diagonal corners."""
    projects = _smartds.build_colocated(f, K, seed, pv_kw, ev_kw)
    f._staged = None
    f._keys = {p.bus: p.gldf_key for p in projects}
    acc_ids, joint_kw = _seq.certify(f, projects, n_jobs)
    acc = set(acc_ids)
    n_pv = sum(1 for p in projects if p.id in acc and p.p_mw < 0)
    n_ld = sum(1 for p in projects if p.id in acc and p.p_mw > 0)

    corners = {
        "coincident": (1.0, 1.0),   # the certified point -- sanity
        "maxPV_minLD": (1.0, 0.0),  # midday: PV export, no load to absorb it -> overvoltage risk
        "minPV_maxLD": (0.0, 1.0),  # evening: load draw, no PV to support it -> undervoltage risk
    }
    row = dict(feeder=f.name, k=len(projects), seed=seed, pv_kw=pv_kw, ev_kw=ev_kw,
               accepted_kw=joint_kw, n_pv=n_pv, n_load=n_ld)
    for name, (pvs, lds) in corners.items():
        d = solve_snapshot(f, _scaled(projects, acc, pvs, lds))
        row[f"{name}_feasible"] = d["feasible"]
        row[f"{name}_vmin"] = d["vmin"]
        row[f"{name}_vmax"] = d["vmax"]
        row[f"{name}_viol"] = d["violation"]
    return row


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--feeder", default="uhs2_1247--udt10746")
    ap.add_argument("--ks", type=int, nargs="+", default=[16, 32])
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    ap.add_argument("--pv-kw", type=float, default=100.0)
    ap.add_argument("--ev-kw", type=float, default=100.0)
    ap.add_argument("--n-jobs", type=int, default=6)
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()

    master = str(CACHE / a.feeder / "Master.dss")
    f = _smartds.load(master)
    _ok0, _, _, d0 = f.evaluate(f.ctx, [], set())
    print(f"\n  M74 -- SNAPSHOT ROBUSTNESS of the SMART-DS co-located residual  ({f.name})")
    print(f"  {len(f.order)} nodes; baseline vmin {d0['vmin']:.4f} vmax {d0['vmax']:.4f} "
          f"(band {f.VMIN_PU}-{f.VMAX_PU})")
    print(f"  Certify the co-located PV+EV queue jointly (both at nameplate), then re-solve the")
    print(f"  accept-set at the two off-diagonal corners of the (PV, load) box.\n")

    hdr = (f"  {'K':>3} {'seed':>4} {'accepted':>8} {'PV/LD':>6} | "
           f"{'coincident (cert)':>19} | {'maxPV/minLD (noon)':>20} | {'minPV/maxLD (eve)':>20}")
    print(hdr)
    print(f"  {'':>3} {'':>4} {'kW':>8} {'#':>6} | "
          f"{'vmin  vmax  ok':>19} | {'vmin  vmax  ok':>20} | {'vmin  vmax  ok':>20}")
    print("  " + "-" * (len(hdr) - 2))

    rows = []
    for K in a.ks:
        for seed in a.seeds:
            t0 = time.time()
            r = check_cell(f, K, seed, a.pv_kw, a.ev_kw, a.n_jobs)
            r["dt"] = time.time() - t0
            rows.append(r)
            if a.save:
                OUT.mkdir(exist_ok=True)
                pd.DataFrame(rows).to_csv(OUT / "m74_snapshot_robustness.csv", index=False)

            def cell(tag):
                mark = "ok" if r[f"{tag}_feasible"] else "XX"
                return f"{r[f'{tag}_vmin']:.3f} {r[f'{tag}_vmax']:.3f} {mark:>3}"
            print(f"  {r['k']:>3} {seed:>4} {r['accepted_kw']:>8.0f} "
                  f"{r['n_pv']:>2}/{r['n_load']:<3} | {cell('coincident'):>19} | "
                  f"{cell('maxPV_minLD'):>20} | {cell('minPV_maxLD'):>20}")

    df = pd.DataFrame(rows)
    n_noon_bad = int((~df["maxPV_minLD_feasible"]).sum())
    n_eve_bad = int((~df["minPV_maxLD_feasible"]).sum())
    n = len(df)
    print(f"\n  coincident (certified): feasible {int(df['coincident_feasible'].sum())}/{n}  "
          f"(sanity -- re-solve must reproduce the certification)")
    print(f"  maxPV/minLD (midday):   VIOLATES {n_noon_bad}/{n}   worst vmax "
          f"{df['maxPV_minLD_vmax'].max():.3f}")
    print(f"  minPV/maxLD (evening):  VIOLATES {n_eve_bad}/{n}   worst vmin "
          f"{df['minPV_maxLD_vmin'].min():.3f}")
    if n_noon_bad or n_eve_bad:
        print("\n  -> The co-located residual is certified at the COINCIDENT snapshot and does NOT")
        print("     survive the off-diagonal corners: the pairs cancel only when both are present.")
        print("     So the +60..+129% residual is real for SIMULTANEITY-limited co-location --")
        print("     dispatchable generation with net-export control (the Brattle/Bloom Energy Park,")
        print("     the reform's actual object) -- and must be scoped away from uncontrolled")
        print("     solar-plus-evening-EV, where a time series would catch the non-coincident corner.")
    else:
        print("\n  -> Both corners stay inside the band: the residual is robust to non-coincidence.")
    if a.save:
        print("\n  saved -> outputs/m74_snapshot_robustness.csv")


if __name__ == "__main__":
    main()
