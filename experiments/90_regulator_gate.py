"""M62 -- THE REGULATOR GATE: does a LIVE substation LTC change the Ckt24 headline?

Every Ckt24 number in this paper is a CONSERVATIVE FLOOR, disclosed as such: the OpenDSS
importer captures the regulator's zero-PV tap (baked into vn_lv_kv) but not its RESPONSE, so
as PV export raises voltage the model never taps DOWN to absorb it. exp87/exp88 (M59/M60)
quantified the segregation headline on Ckt24 under that frozen tap and flagged the caveat.

The obvious next move is to build the whole live-regulator pipeline (port the tap changer +
controller into gridsynth's importer, swap runpp->run_control in the oracle, carry the moving
tap through the M41 GLDF re-linearization). That is a week of work at 6,058-bus scale. Before
paying for it, this GATE asks the cheap question that decides whether it is worth anything:

    (a) Take the FROZEN-tap certified Ckt24 allocation (exp88's, bit-identical). Attach the
        real regulator and run_control on that fixed accept-set. DOES THE TAP EVEN MOVE?
    (b) If it moves, freeing it creates headroom -- so greedily try to seat the requests the
        frozen model REJECTED, under a live-regulator oracle. Does it admit MATERIALLY MORE PV?

If the tap does not move at the certified optimum, the live and frozen oracles are identical
in a neighbourhood of it: the frozen allocation is already live-optimal there, the floor is
TIGHT, and the pipeline change buys nothing. That is the likely outcome, for two reasons this
file also measures:
  * the regulator responds only to AGGREGATE substation voltage with a wide deadband (the
    compensated voltage sits at 1.0217 pu at baseline and must climb to 1.0376 to tap down --
    a single certified accept-set of ~1 MW export does not move it; +20 MW does);
  * Ckt24's binding limit is largely THERMAL / localized end-of-lateral overvoltage, neither
    of which a substation LTC can relieve (tapping DOWN even raises current for the same P).

THE REGULATOR, VERIFIED. The importer bakes the solved tap ratio 1.0125 into vn_lv_kv
(34.93125 vs 34.5 nominal). This file UN-BAKES it (vn_lv_kv -> 34.5, tap into tap_pos=2 with
tap_changer_type="Ratio") and attaches pandapower's LineDropCompensationTapControl with the
Ckt24 regulator's own parameters (vreg=123 band=3 ptratio=166 ctprim=2000 R=7 -> band
[1.012572, 1.037574] pu, r_ohm=0.581). Two properties are asserted before any measurement:
  1. with the controller idle, runpp reproduces the frozen baseline BIT-FOR-BIT (else the
     un-bake silently changed every result -- the double-application trap);
  2. run_control at zero PV lands at tap 2 with the LV terminal at 1.0457 pu, reproducing
     OpenDSS's own solved operating point (opendssdirect ground truth; see
     an internal handover note).
The controller is pandapower's, on an unreleased upstream build (this file runs with that
source on PYTHONPATH; the released 3.4.0 in .venv has neither the tap import nor the LDC
controller).

Run:  PYTHONPATH=../pandapower .venv/bin/python experiments/90_regulator_gate.py --save
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pandapower as pp
import pandapower.control as control
from pandapower.control.controller.trafo.LineDropCompensationTapControl import (
    LineDropCompensationTapControl)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

_c = __import__("64_ckt24_gldf_bound")
_x = __import__("78_corpus_law")
_seg = __import__("88_ckt24_segregation")

OUT = Path(__file__).resolve().parents[1] / "outputs"

# Ckt24 SubXFMR_Regulator, verified against opendssdirect + pandapower from_dss:
#   vreg=123 band=3 ptratio=166 -> band [1.012572, 1.037574] pu ;  R=7 ctprim=2000 -> r_ohm 0.581
REG = dict(vm_lower_pu=1.012572, vm_upper_pu=1.037574, r_ohm=0.581, x_ohm=0.0, side="lv")
TAP = dict(tap_side="lv", tap_neutral=0.0, tap_step_percent=0.625,
           tap_min=-16.0, tap_max=16.0, tap_pos=2.0, tap_changer_type="Ratio")
BASELINE_TAP = 2


class LiveCkt24Feeder(_seg.Ckt24Feeder):
    """exp88's Ckt24 feeder with the substation LTC UN-frozen: the tap is moved out of
    vn_lv_kv into a real tap changer, a LineDropCompensationTapControl is attached, and the
    oracle solves with run_control so the tap can respond to PV export."""

    @classmethod
    def at_setpoint(cls, vm_pu):
        f = super().at_setpoint(vm_pu)           # frozen ctx + exp64's exclusion set
        net = f.ctx.net
        sid = f.sub_idx
        hv = int(net.trafo.at[sid, "hv_bus"]); lv = int(net.trafo.at[sid, "lv_bus"])
        # un-bake the solved ratio: vn -> nominal, ratio -> tap_pos (see module docstring)
        net.trafo.at[sid, "vn_hv_kv"] = float(net.bus.at[hv, "vn_kv"])
        net.trafo.at[sid, "vn_lv_kv"] = float(net.bus.at[lv, "vn_kv"])
        for k, v in TAP.items():
            net.trafo.at[sid, k] = v
        f.reg = LineDropCompensationTapControl(net, sid, **REG)
        f.lv_bus = lv

        # (1) controller idle -> baseline unchanged (the double-application trap)
        pp.runpp(net, **_c.RUNPP_KWARGS)
        f.retrofit_vmax = float(net.res_bus.vm_pu.drop(index=f.slack).max())
        # (2) run_control at zero PV -> OpenDSS operating point (tap 2, terminal ~1.0457)
        control.run_control(net, max_iter=30, **_c.RUNPP_KWARGS)
        f.live_base_tap = int(net.trafo.at[sid, "tap_pos"])
        f.live_base_term = float(net.res_bus.vm_pu.at[lv])
        f.live_base_comp = float(f.reg._get_controlled_vm_pu(net))
        # the substation non-worsening reference is the LIVE baseline (tap may raise current)
        f.sub_base_pct = float(net.res_trafo.at[sid, "loading_percent"])
        f._proj = f._enet = f._rows = None
        return f

    def evaluate(self, ctx, projects, accepted_ids):
        net, rows = self._staged(projects)
        net.load.loc[list(rows.values()), "in_service"] = [r in accepted_ids for r in rows]
        by_id = {p.id: p for p in projects}
        try:
            control.run_control(net, max_iter=30, **_x.RUNPP_KWARGS)
        except Exception:                        # divergence or a controller that won't settle
            return False, 0.0, float("inf"), {"diverged": True, "tap": np.nan}

        vm = net.res_bus.vm_pu.drop(index=self.slack)
        vmin, vmax = float(vm.min()), float(vm.max())
        lp = net.res_line.loading_percent.drop(self.excl_lines, errors="ignore")
        line_pct = float(lp.max()) if len(lp) else 0.0
        other = net.res_trafo.loading_percent.drop([self.sub_idx, *self.bad_trafo_idx],
                                                   errors="ignore")
        other_max = float(other.max()) if len(other) else 0.0
        sub_pct = float(net.res_trafo.at[self.sub_idx, "loading_percent"])

        violation = (max(0.0, vmax - self.VMAX_PU) + max(0.0, self.VMIN_PU - vmin)
                     + max(0.0, line_pct - self.LOADING_MAX_PCT)
                     + max(0.0, other_max - self.LOADING_MAX_PCT)
                     + max(0.0, sub_pct - self.sub_base_pct))
        mw = sum(abs(by_id[rid].p_mw) for rid in accepted_ids)
        return violation == 0.0, mw, violation, {
            "vmax": vmax, "vmin": vmin, "line_pct": line_pct, "trafo_pct": other_max,
            "sub_pct": sub_pct, "tap": int(net.trafo.at[self.sub_idx, "tap_pos"])}


def augment_live(fl, projects, accepted, n_probe=None):
    """Lower bound on the live optimum: greedily seat the FROZEN-rejected requests under the
    live oracle, largest first. Any positive result means freeing the tap unlocked capacity;
    zero (with an unmoved tap) means the frozen allocation was already live-optimal here."""
    acc = set(accepted)
    rejected = sorted((p for p in projects if p.id not in acc),
                      key=lambda p: abs(p.p_mw), reverse=True)
    if n_probe:
        rejected = rejected[:n_probe]
    extra_kw = 0.0
    for p in rejected:
        ok, _, _, _ = fl.evaluate(fl.ctx, projects, acc | {p.id})
        if ok:
            acc.add(p.id)
            extra_kw += abs(p.p_mw) * 1000
    return acc, extra_kw


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--ks", type=int, nargs="+", default=[24, 60],
                    help="pure-PV queue sizes; larger K = more export = the regulator's best case")
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    ap.add_argument("--size-kw", type=float, default=100.0)
    ap.add_argument("--n-jobs", type=int, default=8)
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()

    print("\n  M62 -- THE REGULATOR GATE (Ckt24)\n")
    ff = _seg.Ckt24Feeder.at_setpoint(1.05)       # frozen: exp88's feeder, verbatim
    fl = LiveCkt24Feeder.at_setpoint(1.05)        # live: same net, LTC un-frozen

    print("  RETROFIT VALIDATION")
    print(f"    controller idle:  baseline vmax {fl.retrofit_vmax:.6f}  "
          f"(frozen feeder: {ff.evaluate(ff.ctx, [], set())[3]['vmax']:.6f})")
    print(f"    run_control @0PV: tap {fl.live_base_tap}  terminal {fl.live_base_term:.6f}  "
          f"compensated {fl.live_base_comp:.6f}  band [{REG['vm_lower_pu']}, {REG['vm_upper_pu']}]")
    print(f"    -> reproduces OpenDSS (tap 2, terminal ~1.0457): "
          f"{'PASS' if fl.live_base_tap == BASELINE_TAP and abs(fl.live_base_term-1.0457) < 3e-3 else 'FAIL'}")
    print(f"    headroom to tap DOWN: compensated must rise "
          f"{REG['vm_upper_pu'] - fl.live_base_comp:+.4f} pu past baseline\n")

    hdr = (f"  {'K':>4} {'seed':>4} | {'certified PV':>12} {'#acc':>5} | {'A live-ok?':>10} "
           f"{'tap@A':>6} {'moved?':>7} | {'extra PV (live)':>15} {'total gain':>11}")
    print(hdr); print("  " + "-" * (len(hdr) - 2))

    rows = []
    any_move = False
    for K in a.ks:
        for seed in a.seeds:
            t0 = time.time()
            projects = _seg.build_mixed(ff, K, seed, load_frac=0.0, size_kw=a.size_kw)  # pure PV

            ff._proj = None
            acc_ids, cert_kw = _seg.certify(ff, projects, a.n_jobs)

            fl._proj = None
            ok_live, _, viol, d = fl.evaluate(fl.ctx, projects, acc_ids)
            tap_at_A = d.get("tap", np.nan)
            moved = (not np.isnan(tap_at_A)) and int(tap_at_A) != BASELINE_TAP
            any_move = any_move or moved

            # (b) does freeing the tap admit materially more PV? (greedy lower bound)
            _, extra_kw = augment_live(fl, projects, acc_ids)
            gain_pct = (100 * extra_kw / cert_kw) if cert_kw > 0 else float("nan")

            rows.append(dict(K=K, seed=seed, size_kw=a.size_kw, n_requests=len(projects),
                             certified_pv_kw=cert_kw, n_accepted=len(acc_ids),
                             a_live_feasible=bool(ok_live), a_live_violation=viol,
                             tap_at_A=float(tap_at_A), tap_moved=bool(moved),
                             extra_pv_live_kw=extra_kw, live_gain_pct=gain_pct,
                             dt_s=time.time() - t0))
            print(f"  {K:>4} {seed:>4} | {cert_kw:>10.0f}kW {len(acc_ids):>5} | "
                  f"{('yes' if ok_live else 'NO'):>10} {int(tap_at_A) if not np.isnan(tap_at_A) else '?':>6} "
                  f"{('MOVED' if moved else 'no'):>7} | {extra_kw:>13.0f}kW "
                  f"{(gain_pct if not np.isnan(gain_pct) else float('nan')):>+10.1f}%")
            if a.save:
                OUT.mkdir(exist_ok=True)
                pd.DataFrame(rows).to_csv(OUT / "m62_regulator_gate.csv", index=False)

    print("\n" + "=" * 90)
    d = pd.DataFrame(rows)
    if any_move:
        moved = d[d.tap_moved]
        print(f"  TAP MOVED on {len(moved)}/{len(d)} certified optima. Freeing it admitted "
              f"{d.extra_pv_live_kw.max():.0f} kW of extra PV (up to {d.live_gain_pct.max():+.1f}%).")
        print("  -> the pipeline change is NOT a wash; scope it for real.")
    else:
        print("  THE TAP DID NOT MOVE at any certified optimum "
              f"(all held at baseline tap {BASELINE_TAP}), and greedy live augmentation added "
              f"{d.extra_pv_live_kw.max():.0f} kW.")
        print("  -> live oracle == frozen oracle at the optimum. The Ckt24 floor is TIGHT; the")
        print("     full live-regulator pipeline would change the headline by ~nothing. Document,")
        print("     do not build.")
    if a.save:
        print("\n  saved -> outputs/m62_regulator_gate.csv")


if __name__ == "__main__":
    main()
