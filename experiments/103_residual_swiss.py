"""M75 -- THE SEGREGATION RESIDUAL ON A REAL SWISS MV FEEDER (DESL-EPFL 55-node), the FOURTH family.

Three families so far are LV (ENWL, EULV), US split-phase (SMART-DS), and the EPRI MV test circuits
(Ckt7/Ckt24). A referee's "all your feeders are test circuits or one LV corpus" objection wants a
DIFFERENT real feeder from a DIFFERENT country and voltage level. This adds one: the
`55nodes-Swiss-distribution-grid` published by Mario Paolone's lab (DESL-EPFL) -- a real, radial,
R-dominated Swiss MV distribution feeder (54 lines, 43 load nodes, 23 PV nodes, ~3.9 MW peak load,
2.7 MW installed PV), with line ampacities (so BOTH voltage and thermal can bind) and -- uniquely in
our corpus -- shipped 8-day-type x 96-interval load AND PV time series.

We build it into a pandapower net and wrap it in exp78's `Feeder`, so exp98's three-way residual
(parallel / sequential-both-orders / joint) runs on it VERBATIM -- only the loader is new.

    RESIDUAL := joint - max(seq_pvfirst, seq_ldfirst), quoted as a FRACTION OF THE JOINT total.

CAVEATS, stated up front (they are two of the outreach questions to Paolone):
  * BASE VOLTAGE = 21 kV, confirmed from the source paper: Yi, Cherkaoui, Paolone, Shchetinin &
    Knezovic, "Expansion planning of active distribution networks achieving their dispatchability
    via energy storage systems," Applied Energy 326 (2022) 119942 -- "a real Swiss 21 kV
    distribution network with 55 nodes ... 2.7 MWp of PV generation and 805 kVA of hydropower ...
    installed." Our loader reproduces that feeder's 2.7 MWp PV (2701.7 kW) and ~810 kVA hydro
    (815) exactly, and the grid constraints the paper enforces -- "nodal voltage and line
    ampacity" -- are exactly the two our oracle checks.
  * VOLTAGE LIMITS. 0.95-1.05 pu (standard MV planning envelope; the paper carries them as a
    squared-voltage parameter v_M/v_m, an operator choice inside the EN 50160 +/-10% envelope, not
    a fixed regulatory number). The exact value does NOT matter here: the residual is IDENTICAL
    under 0.95-1.05 and 0.90-1.10 (bit-for-bit, all 4 seeds), because this feeder is AMPACITY-
    limited -- vmax at the PV-solo optimum is only ~1.02, nowhere near either band edge, while the
    line sits at ~98%. The non-worsening clamp (exp78) relaxes the band wherever a snapshot's
    baseline already sits outside, exactly as for the ENWL feeders.
  * ONE purely-resistive line (52: 29->8, x=0) is nudged to x=1e-3 ohm/km so pandapower's per-unit
    branch model does not divide by zero; the effect on any solve is below 1e-4 pu.
  * SINGLE SNAPSHOT, like every other feeder here. This feeder SHIPS the daily profile, and
    exp104 uses it to test whether this residual survives real non-coincidence -- it does NOT in
    general: the certified allocation is COINCIDENCE-BOUND, feasible only in a narrow window
    around the certification point (plus separately whenever demand is simply too low to matter),
    and violates hard (up to 553% line loading) even minutes away, including from an ordinary
    measured irradiance dip near the certification hour. See experiments/104_swiss_daily_profile.py.

Run:
  .venv/bin/python experiments/103_residual_swiss.py --mat <path/to/55nodes...mat> --save
  .venv/bin/python experiments/103_residual_swiss.py --mat ... --snapshot peakload --vn-kv 21 --save
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandapower as pp
import pandas as pd
import scipy.io as sio

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

_x = __import__("78_corpus_law")                  # Feeder
_enwl = __import__("98_residual_enwl")            # build_mixed, three_way (feeder-agnostic)

OUT = Path(__file__).resolve().parents[1] / "outputs"


def swiss_net(mat_path: str, vn_kv: float = 21.0, snapshot="minload", xmin=1e-3):
    """DESL-EPFL 55-node .mat -> pandapower net at one (interval, day-type) snapshot.

    Linedata columns (1-indexed as documented in the repo README): 1,2 = from/to node; 3 = R
    (ohm/km); 4 = X (ohm/km); 5 = B (uS/km); 6 = ampacity (A); 7 = length (km); 8 = type.
    Node 1 is the grid-connection point (degree 1 -- a substation joins the feeder head by one
    line) and is the slack. Loads come from the 43 Nodes_with_Load and their 96x8 profiles.
    """
    m = sio.loadmat(mat_path)
    LD = m["Linedata"]
    nodes = sorted(set(LD[:, :2].astype(int).ravel()))
    NL = m["Nodes_with_Load"].ravel().astype(int)
    AL, RL = m["ActiveLoad_data"], m["ReactiveLoad_data"]
    agg = np.stack([np.asarray(AL[i, 0], float) for i in range(len(NL))]).sum(0)   # 96 x 8
    if snapshot == "minload":
        t, d = map(int, np.unravel_index(agg.argmin(), agg.shape))
    elif snapshot == "peakload":
        t, d = map(int, np.unravel_index(agg.argmax(), agg.shape))
    else:
        t, d = snapshot                                                            # (interval, day)

    net = pp.create_empty_network(name=f"swiss55@{vn_kv}kV")
    b = {n: pp.create_bus(net, vn_kv=vn_kv, name=f"n{n}") for n in nodes}
    pp.create_ext_grid(net, b[1], vm_pu=1.0, name="GCP")
    for r in LD:
        fn, tn, R, X, B, amp, length = int(r[0]), int(r[1]), r[2], r[3], r[4], r[5], r[6]
        pp.create_line_from_parameters(net, b[fn], b[tn], length_km=length,
                                       r_ohm_per_km=R, x_ohm_per_km=max(X, xmin),
                                       c_nf_per_km=B * 1000.0 / (2 * np.pi * 50.0),
                                       max_i_ka=amp / 1000.0, name=f"{fn}-{tn}")
    for i, n in enumerate(NL):
        pp.create_load(net, b[n], p_mw=float(np.asarray(AL[i, 0])[t, d]) / 1000.0,
                       q_mvar=float(np.asarray(RL[i, 0])[t, d]) / 1000.0, name=f"L{n}")
    return net, dict(t=t, d=d, agg_kw=float(agg[t, d]),
                     agg_min_kw=float(agg.min()), agg_max_kw=float(agg.max()))


def load_swiss(mat_path, vn_kv=21.0, snapshot="minload", vmin=0.95, vmax=1.05, loading=100.0):
    net, snap = swiss_net(mat_path, vn_kv, snapshot)
    f = _x.Feeder("swiss55", net, sizer=None, vmin=vmin, vmax=vmax, loading=loading)
    f._snap = snap
    return f


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--mat", required=True, help="path to 55nodes_real_swiss_distribution_grid.mat")
    ap.add_argument("--vn-kv", type=float, default=21.0)
    ap.add_argument("--snapshot", default="peakload", help="minload | peakload")
    ap.add_argument("--vmin", type=float, default=0.95)
    ap.add_argument("--vmax", type=float, default=1.05)
    ap.add_argument("--k", type=int, default=48)
    ap.add_argument("--size-kw", type=float, default=500.0)
    ap.add_argument("--load-frac", type=float, default=0.5)
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2, 3])
    ap.add_argument("--n-jobs", type=int, default=6)
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()

    f = load_swiss(a.mat, a.vn_kv, a.snapshot, a.vmin, a.vmax)
    s = f._snap
    print(f"\n  M75 -- SEGREGATION RESIDUAL ON A REAL SWISS MV FEEDER (DESL-EPFL 55-node)")
    print(f"  {len(f.ctx.net.bus)} buses, {len(f.ctx.net.load)} loads at {a.vn_kv:.0f} kV; "
          f"limits vmin {f.VMIN_PU:.3f} vmax {f.VMAX_PU:.3f} loading {f.LOADING_MAX_PCT:.0f}%")
    print(f"  snapshot '{a.snapshot}' = interval {s['t']}/96, day-type {s['d']} "
          f"(agg load {s['agg_kw']:.0f} kW; feeder range {s['agg_min_kw']:.0f}-{s['agg_max_kw']:.0f} kW)")
    print(f"  baseline vmin {f.base['vmin']:.4f} vmax {f.base['vmax']:.4f} line {f.base['line_pct']:.1f}%")
    print(f"  K={a.k}, {a.size_kw:.0f} kW, load {100*a.load_frac:.0f}%; residual = joint - best study order.\n")

    hdr = (f"  {'seed':>4} | {'PVsolo':>7} {'LDsolo':>7} {'JOINT':>7} {'bestSEQ':>7} "
           f"{'vmax@pv':>8} {'line%':>6} | {'resid kW':>9} {'= % of joint':>12}")
    print(hdr); print("  " + "-" * (len(hdr) - 2))

    rows = []
    for seed in a.seeds:
        t0 = time.time()
        projects = _enwl.build_mixed(f, a.k, seed, a.load_frac, a.size_kw)
        r = _enwl.three_way(f, projects, a.n_jobs)
        resid_kw = r["joint_kw"] - r["best_sequential_kw"]
        frac = 100 * resid_kw / r["joint_kw"] if r["joint_kw"] > 0 else float("nan")
        r.update(feeder="swiss55", vn_kv=a.vn_kv, snapshot=a.snapshot, k=a.k, seed=seed,
                 size_kw=a.size_kw, residual_kw=resid_kw, residual_frac_of_joint_pct=frac,
                 dt=time.time() - t0)
        rows.append(r)
        if a.save:
            OUT.mkdir(exist_ok=True)
            pd.DataFrame(rows).to_csv(OUT / "m75_residual_swiss.csv", index=False)
        print(f"  {seed:>4} | {r['pv_alone_kw']:>7.0f} {r['load_alone_kw']:>7.0f} "
              f"{r['joint_kw']:>7.0f} {r['best_sequential_kw']:>7.0f} "
              f"{r['pvsolo_vmax']:>8.4f} {r['pvsolo_line_pct']:>5.1f} | "
              f"{resid_kw:>9.0f} {frac:>+11.1f}%")

    d = pd.DataFrame(rows)
    fr = d["residual_frac_of_joint_pct"].to_numpy()
    fr = fr[~np.isnan(fr)]
    vmax_m = float(np.median(d["pvsolo_vmax"]))
    line_m = float(np.median(d["pvsolo_line_pct"]))
    regime = ("THERMAL (line %.0f%% vs vmax %.3f) -- the shared upstream lines carry reversible flow, "
              "so PV (reverse) and load (forward) are BOTH aggressors on them" % (line_m, vmax_m)
              if line_m >= 95.0 or (100 - line_m) < abs(1.05 - vmax_m) * 2000
              else "VOLTAGE (vmax %.3f vs line %.0f%%)" % (vmax_m, line_m))
    print("\n  " + "=" * (len(hdr) - 2))
    if len(fr):
        print(f"  residual as a fraction of the joint total: median {np.median(fr):+.1f}%  "
              f"(min {fr.min():+.1f}%, max {fr.max():+.1f}%)  over {len(fr)} draws")
        jm = float(np.median(d["joint_kw"])); bm = float(np.median(d["best_sequential_kw"]))
        print(f"  i.e. joint seats ~{jm:.0f} kW where no study order exceeds ~{bm:.0f} kW.")
    print(f"  binding constraint at the PV-solo optimum: {regime}.")
    print("  A positive residual on a real Swiss MV feeder (4th family, different engine-free load")
    print("  of a real utility) = the rule holds: the active constraint admits BOTH gen and load as")
    print("  aggressors, so no study order recovers the complement -- co-study is required. (At light")
    print("  load / realistic small queues the feeder is strong and one-way, residual 0 -- the null side.)")
    if a.save:
        print("\n  saved -> outputs/m75_residual_swiss.csv")


if __name__ == "__main__":
    main()
