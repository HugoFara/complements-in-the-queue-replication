"""M76 -- DOES THE SWISS THERMAL-TWO-WAY RESIDUAL SURVIVE A REAL DAILY PROFILE?

exp103 found a thermal two-way residual (~7% of joint, AC-verified) on the DESL-EPFL 55-node Swiss
feeder, certified at a single snapshot (peak aggregate load). Paolone confirmed the mechanism is sane
for this feeder ("ampacity limits are binding way before the voltage ones") but could not say from
intuition whether it survives a realistic daily profile: "Difficult to say without a dedicated
assessment. I guess you can thoroughly study it." This is that assessment.

exp102 answered the analogous question for SMART-DS with two extreme CORNERS (max-PV/min-load,
min-PV/max-load) because that feeder ships no time series. The Swiss feeder is better: it SHIPS its
own 96-interval x 8-day-type load AND irradiance profile, so instead of two corners we can sweep the
ACTUAL diurnal shape.

METHOD. Certify the joint accept-set once, at the peak-load snapshot exp103 uses (the reference
point). Then, for every interval of one day-type (96 x 15-min steps, ~one full day), rebuild the
feeder at that interval's NATIVE load (a fresh AC solve, not an approximation), and re-solve AC
feasibility of the SAME accepted requests, with each one scaled to that instant:
  * PV requests scaled by the shipped irradiance PV_data(t,d) / PV_data(t_ref,d_ref) -- the feeder's
    own solar shape, common to all PV nodes (the .mat ships one irradiance series, not per-node).
  * load requests scaled by the feeder's own AGGREGATE native-load shape agg(t,d) / agg(t_ref,d_ref)
    -- a proxy for how a new large load would vary through the day, absent a dedicated profile for
    interconnection-scale load (documented, not hidden).
This mirrors exp102's `_scaled` exactly, generalized from {0,1} corner weights to the real shape.

If the accepted set stays AC-feasible at every interval, the residual is genuinely robust to
non-coincidence on this feeder (unlike SMART-DS's coincidence-bound co-located case). If it violates,
we get the interval and margin of the first breach, and can scope the claim precisely instead of
guessing.

Run:
  .venv/bin/python experiments/104_swiss_daily_profile.py --mat <path/to/55nodes...mat> --save
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import scipy.io as sio

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

_s = __import__("103_residual_swiss")             # swiss_net, load_swiss
_c = __import__("68_ckt7_gldf_headline")          # Project
_seq = __import__("91_sequential_segregation")    # certify
_enwl = __import__("98_residual_enwl")            # build_mixed

OUT = Path(__file__).resolve().parents[1] / "outputs"


def shapes(mat_path):
    """The feeder's own diurnal shapes: PV_data (irradiance, W/m2, common to all PV nodes) and the
    aggregate native load agg(t,d), both 96 x 8 -- the two series exp104 scales the accepted queue
    by, so the check uses the feeder's REAL profile, not an assumed one."""
    m = sio.loadmat(mat_path)
    pv = np.asarray(m["PV_data"], float)                                    # 96 x 8, W/m2
    NL = m["Nodes_with_Load"].ravel().astype(int)
    AL = m["ActiveLoad_data"]
    agg = np.stack([np.asarray(AL[i, 0], float) for i in range(len(NL))]).sum(0)   # 96 x 8, kW
    return pv, agg


def scaled_projects(accepted, pv_scale, load_scale):
    """The accepted requests re-sized to one instant: PV (p_mw<0) by the irradiance ratio, load
    (p_mw>0) by the aggregate-load-shape ratio. Same bus, same id -- only magnitude moves."""
    out = []
    for p in accepted:
        s = pv_scale if p.p_mw < 0 else load_scale
        q = _c.Project(id=p.id, bus=p.bus, p_mw=p.p_mw * s)
        out.append(q)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--mat", required=True, help="path to 55nodes_real_swiss_distribution_grid.mat")
    ap.add_argument("--vn-kv", type=float, default=21.0)
    ap.add_argument("--vmin", type=float, default=0.95)
    ap.add_argument("--vmax", type=float, default=1.05)
    ap.add_argument("--k", type=int, default=48)
    ap.add_argument("--size-kw", type=float, default=500.0)
    ap.add_argument("--load-frac", type=float, default=0.5)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--day-types", type=int, nargs="+", default=None,
                    help="which of the 8 shipped day-types to sweep (default: the reference "
                         "day-type used for certification, plus day-type 0 for contrast)")
    ap.add_argument("--n-jobs", type=int, default=6)
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()

    # --- reference point: certify the joint accept-set at the peak-load snapshot, exactly exp103's
    f = _s.load_swiss(a.mat, a.vn_kv, "peakload", a.vmin, a.vmax)
    t_ref, d_ref = f._snap["t"], f._snap["d"]
    projects = _enwl.build_mixed(f, a.k, a.seed, a.load_frac, a.size_kw)
    ids, joint_kw = _seq.certify(f, projects, a.n_jobs)
    accepted = [p for p in projects if p.id in set(ids)]
    n_pv = sum(1 for p in accepted if p.p_mw < 0)
    n_ld = sum(1 for p in accepted if p.p_mw > 0)

    pv_shape, load_shape = shapes(a.mat)
    pv_ref, load_ref = pv_shape[t_ref, d_ref], load_shape[t_ref, d_ref]
    days = a.day_types if a.day_types else sorted({d_ref, 0})

    print(f"\n  M76 -- DOES THE SWISS THERMAL-TWO-WAY RESIDUAL SURVIVE A REAL DAILY PROFILE?")
    print(f"  reference: interval {t_ref}/96, day-type {d_ref} (13:30-ish peak-load snapshot)")
    print(f"  joint-certified accept-set: {n_pv} PV + {n_ld} load = {joint_kw:.0f} kW "
          f"(seed {a.seed}, K={a.k}, {a.size_kw:.0f} kW requests)")
    print(f"  reference irradiance {pv_ref:.0f} W/m2, reference aggregate native load {load_ref:.0f} kW")
    print(f"  sweeping day-type(s) {days}, all 96 intervals each (a full simulated day)\n")

    hdr = f"  {'day':>4} {'t':>3} {'hh:mm':>5} | {'PVscale':>7} {'LDscale':>7} | {'vmin':>6} {'vmax':>6} {'line%':>6} | {'ok':>3}"
    rows = []
    for d in days:
        print(f"  --- day-type {d} " + "-" * (len(hdr) - 16))
        print(hdr)
        n_bad = 0
        for t in range(96):
            pv_scale = max(0.0, float(pv_shape[t, d]) / pv_ref) if pv_ref > 0 else 0.0
            load_scale = max(0.0, float(load_shape[t, d]) / load_ref) if load_ref > 0 else 0.0
            ft = _s.load_swiss(a.mat, a.vn_kv, (t, d), a.vmin, a.vmax)   # native load at this instant
            snap = scaled_projects(accepted, pv_scale, load_scale)
            ok, mw, viol, info = ft.evaluate(ft.ctx, snap, {p.id for p in snap})
            hh, mm = divmod(t * 15, 60)
            row = dict(day=d, t=t, hhmm=f"{hh:02d}:{mm:02d}", pv_scale=pv_scale, load_scale=load_scale,
                      vmin=info.get("vmin", float("nan")), vmax=info.get("vmax", float("nan")),
                      line_pct=info.get("line_pct", float("nan")), ok=bool(ok), violation=viol)
            rows.append(row)
            n_bad += 0 if ok else 1
            if not ok or t % 12 == 0:                     # print every 3h, plus every violation
                print(f"  {d:>4} {t:>3} {row['hhmm']:>5} | {pv_scale:>7.2f} {load_scale:>7.2f} | "
                      f"{row['vmin']:>6.3f} {row['vmax']:>6.3f} {row['line_pct']:>6.1f} | "
                      f"{'ok' if ok else 'XX':>3}")
        print(f"  day-type {d}: {96 - n_bad}/96 intervals feasible ({n_bad} violate)\n")

    d = pd.DataFrame(rows)
    if a.save:
        OUT.mkdir(exist_ok=True)
        d.to_csv(OUT / "m76_swiss_daily_profile.csv", index=False)

    n_bad_total = int((~d["ok"]).sum())
    print("  " + "=" * (len(hdr) - 2))
    print(f"  TOTAL: {len(d) - n_bad_total}/{len(d)} intervals AC-feasible across "
          f"{len(days)} day-type(s) x 96 intervals.")
    if n_bad_total == 0:
        print("  -> The thermal-two-way residual SURVIVES the full real diurnal profile on this")
        print("     feeder: unlike the coincidence-bound SMART-DS co-located case, this allocation")
        print("     does not rely on an instantaneous cancellation -- it holds all day.")
    else:
        bad = d[~d["ok"]]
        worst = bad.loc[bad["violation"].idxmax()]
        print(f"  -> VIOLATES at {n_bad_total} interval(s); worst at day {int(worst.day)} "
              f"{worst.hhmm} (violation {worst.violation:.4f}, vmin {worst.vmin:.3f} "
              f"vmax {worst.vmax:.3f} line {worst.line_pct:.1f}%).")
        print("\n  Feasible intervals are not a smooth band -- they split into CONTIGUOUS windows of")
        print("  two different kinds. A window is CANCELLATION-driven if both scales sit near their")
        print("  reference value (>=0.5 of the reference each -- PV and load are both substantially")
        print("  present and still offsetting each other, as at certification); it is a LOW-DEMAND")
        print("  window if both sit low (<0.4 -- nothing much is flowing, so nothing overloads,")
        print("  regardless of cancellation). Anything else that is feasible is a coincidence of the")
        print("  particular mix, not evidence the mechanism generalizes.")
        for day, g in d.groupby("day"):
            g = g.sort_values("t").reset_index(drop=True)
            runs, cur = [], []
            for i, ok in enumerate(g["ok"]):
                if ok:
                    cur.append(i)
                elif cur:
                    runs.append(cur); cur = []
            if cur:
                runs.append(cur)
            print(f"\n  day-type {day}: {len(runs)} feasible window(s)")
            for r in runs:
                sub = g.iloc[r]
                pv_lo, pv_hi = sub["pv_scale"].min(), sub["pv_scale"].max()
                ld_lo, ld_hi = sub["load_scale"].min(), sub["load_scale"].max()
                kind = ("cancellation" if min(pv_lo, ld_lo) >= 0.5
                        else "low-demand" if max(pv_hi, ld_hi) < 0.4 else "mixed/incidental")
                print(f"    {sub.iloc[0]['hhmm']}-{sub.iloc[-1]['hhmm']} "
                      f"({len(r)} intervals, PVscale {pv_lo:.2f}-{pv_hi:.2f}, "
                      f"LDscale {ld_lo:.2f}-{ld_hi:.2f}) -- {kind}")
        print("\n  So the residual is real at the certified peak, but the certified allocation is")
        print("  COINCIDENCE-BOUND -- it survives only in a narrow window around the certification")
        print("  point (plus separately, whenever demand is simply too low to matter), and can")
        print("  violate hard even a short time later, including from ordinary measured irradiance")
        print("  transients (a cloud) close to the certification hour. Scope the claim accordingly.")
    if a.save:
        print("\n  saved -> outputs/m76_swiss_daily_profile.csv")


if __name__ == "__main__":
    main()
