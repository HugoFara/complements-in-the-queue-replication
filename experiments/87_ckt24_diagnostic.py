"""M59 -- DOES CKT24 HAVE THE SAME PIN, AND DID WE IMPORT ITS REGULATOR?

M54/M55 found Ckt7's baseline vmax was a SETPOINT (115 kV source at 1.05 pu, fixed-tap
substation transformers, NO regulator -> 0.0012 pu of headroom). Before replicating M58's
segregation headline on Ckt24 we have to know whether Ckt24 is the same feeder in disguise.

It is NOT, and the difference is a modelling question we have never asked:

    Ckt7    New Circuit.ckt7   pu=1.05  basekv=115     NO RegControl anywhere.
    Ckt24   New Circuit.ckt24  pu=1.05  basekV=230     New Regcontrol.SubXFMR_Regulator
                                                       vreg=123.0 band=3 ptratio=166

vreg=123 V with ptratio=166 targets ~123*166 = 20,418 V line-neutral at the regulated
winding -- about **1.025 pu**, not 1.05. So the REAL Ckt24 regulates its feeder head roughly
two and a half percent BELOW where Ckt7's sits.

The question this file answers: **does pandapower's OpenDSS import carry the RegControl
over, or does it freeze the tap?**

    ANSWER (and D2 below reads WRONG at first glance -- see the correction): it FREEZES it,
    but at the CORRECT solved position, not the raw source. D2 finds net.controller empty
    and tap_pos NaN and concludes "not imported" -- that conclusion is WRONG and is
    corrected here. The importer bakes dss.Transformers.Tap() = 1.0125 into vn_lv_kv
    (34.93125 kV vs a 34.5 kV nominal bus), so Ckt24's BASELINE is right; the tap ratio is
    in the model, just not in the tap_pos column D2 looks at. What is actually missing is
    the regulator's RESPONSE: the tap cannot move, so as PV export raises voltage the model
    never taps DOWN to absorb it. That UNDERSTATES PV hosting capacity, making every Ckt24
    headline a conservative floor -- the opposite error from Ckt7's, whose 1.05 was a free
    choice that INFLATED its story. Lifting it needs a pandapower build that imports the
    RegControl's tap state; experiments/90_regulator_gate.py is the gate check.

Reports, in order:
  D1  the ext_grid setpoint; the argmax-voltage bus; vmax with and WITHOUT the slack.
  D2  the substation transformer's tap position as imported, and what a 1.025 pu regulated
      head would imply. Is there a regulator in net.trafo's tap fields at all?
  D3  headroom, and solo seatability of a PV request by size (the exp82 probe).
  D4  the feasible source-setpoint window: how far can the source be backed off before the
      BASELINE itself breaks the 0.94 floor? (Ckt7's window was [1.04, 1.05].)

Usage:  python experiments/87_ckt24_diagnostic.py
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import numpy as np
import pandapower as pp

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

_c = __import__("64_ckt24_gldf_bound")

PROBE_SIZES_KW = [20, 50, 100, 200, 400]
SETPOINTS = [1.05, 1.04, 1.03, 1.02, 1.00, 0.98]
N_PROBE = 400          # random subsample of the candidate pool; a FRACTION needs no census


def main() -> None:
    ctx = _c.build_network()
    net = ctx.net
    pp.runpp(net, **_c.RUNPP_KWARGS)

    src = int(net.ext_grid.bus.iloc[0])
    vm = net.res_bus.vm_pu

    print("\n  D1 -- THE SLACK, AND THE TRUE PQ MAXIMUM\n")
    print(f"    ext_grid bus / setpoint : {src}  /  {float(net.ext_grid.vm_pu.iloc[0]):.6f}")
    print(f"    VMAX_PU limit           : {_c.VMAX_PU:.6f}")
    print(f"    baseline vmax (ALL bus) : {float(vm.max()):.6f}  at bus {int(vm.idxmax())}")
    print(f"    is that the slack?      : "
          f"{'YES -- same censoring as Ckt7' if int(vm.idxmax()) == src else 'no'}")

    vm_ns = vm.drop(index=src)
    bmax, bmin = float(vm_ns.max()), float(vm_ns.min())
    print(f"\n    vmax EXCLUDING slack    : {bmax:.6f}  at bus {int(vm_ns.idxmax())}")
    print(f"    vmin                    : {bmin:.6f}")
    print(f"    OVERVOLTAGE headroom    : {_c.VMAX_PU - bmax:+.6f} pu   "
          f"(Ckt7's was +0.00119)")
    print(f"    UNDERVOLTAGE margin     : {bmin - _c.VMIN_PU:+.6f} pu")

    # ------------------------------------------------------------------ D2
    print("\n\n  D2 -- WAS THE REGULATOR IMPORTED?\n")
    print("    OpenDSS defines:  New Regcontrol.SubXFMR_Regulator vreg=123 band=3 ptratio=166")
    print("    -> a regulated head of roughly 123*166 = 20,418 V L-N  (~1.025 pu)\n")
    tcols = [c for c in net.trafo.columns if c.startswith("tap")]
    print(f"    net.trafo rows              : {len(net.trafo)}")
    print(f"    tap-related columns present : {tcols}")
    if "tap_pos" in net.trafo.columns:
        tp = net.trafo.tap_pos
        n_tapped = int(tp.notna().sum())
        print(f"    transformers with a tap_pos : {n_tapped}/{len(net.trafo)}")
        if n_tapped:
            nz = tp[tp.notna() & (tp != 0)]
            print(f"    NON-ZERO tap positions      : {len(nz)}  {list(nz.head(5).items())}")
    # pandapower has no RegControl concept; a controller would show up here if anything did
    ctrl = getattr(net, "controller", None)
    print(f"    net.controller rows         : {0 if ctrl is None else len(ctrl)}"
          f"   <- an imported LTC would live here")
    print("\n    If tap positions are all 0/absent and there are no controllers, the LTC was")
    print("    NOT imported: our Ckt24 head voltage is the raw source, not the regulated one.")

    # ------------------------------------------------------------------ D3
    print("\n\n  D3 -- SOLO SEATABILITY OF A PV REQUEST\n")
    candidates = [b for b in net.load.bus.unique() if b not in ctx.excluded_buses]

    # THE NON-WORSENING CAP, and the first pass of this file got it WRONG.
    # Ckt24's 45 MVA substation transformer sits at 111.06% loading AT BASELINE (an EPRI
    # allocation-factor artifact, documented in PROVENANCE). A flat `<= 100%` thermal test
    # is therefore failed by the EMPTY NETWORK, and every probe inherits that failure --
    # the first run of this diagnostic reported "0/3650 seatable, thermal binding at 3588"
    # and that was the bug, not the feeder. exp64/exp78 forbid each limit being made WORSE
    # than baseline instead, which reduces to the plain limit wherever baseline is inside it.
    b_line = float(net.res_line.loading_percent.drop(ctx.excluded_line_idx,
                                                     errors="ignore").max())
    b_trafo = float(net.res_trafo.loading_percent.max()) if len(net.trafo) else 0.0
    load_cap = max(_c.LOADING_MAX_PCT, b_line, b_trafo)
    print(f"    baseline line loading  : {b_line:.2f}%")
    print(f"    baseline trafo loading : {b_trafo:.2f}%   <- the 45 MVA substation transformer")
    print(f"    NON-WORSENING cap used : {load_cap:.2f}%   (a flat 100% is failed by the")
    print(f"                             EMPTY network, which is what broke this file's first run)\n")
    # Subsample the bus pool and STAGE one probe load rather than deepcopying a 6,058-bus
    # net per solve: we want a seatability FRACTION, and 400 buses give it to ~2.5%. Probing
    # all 3,650 with a deepcopy each is ~18k full-network copies and runs for hours.
    import random as _r
    pool = sorted(candidates)
    if len(pool) > N_PROBE:
        pool = _r.Random(0).sample(pool, N_PROBE)
    probe = pp.create_load(net, bus=int(pool[0]), p_mw=0.0, q_mvar=0.0,
                           name="probe", in_service=True)

    print(f"    candidate buses: {len(candidates)}; probing a random {len(pool)} of them\n")
    print(f"    {'size':>7}  {'seatable':>14}   binding constraint at the failures")
    for kw in PROBE_SIZES_KW:
        n_ok = 0
        why = {"v": 0, "thermal": 0}
        for b in pool:
            net.load.at[probe, "bus"] = int(b)
            net.load.at[probe, "p_mw"] = -kw / 1000.0
            try:
                pp.runpp(net, **_c.RUNPP_KWARGS)
            except pp.LoadflowNotConverged:
                continue
            v2 = net.res_bus.vm_pu.drop(index=src)
            lp = net.res_line.loading_percent.drop(ctx.excluded_line_idx, errors="ignore")
            tp2 = float(net.res_trafo.loading_percent.max()) if len(net.trafo) else 0.0
            v_ok = float(v2.max()) <= _c.VMAX_PU and float(v2.min()) >= _c.VMIN_PU
            t_ok = (float(lp.max()) if len(lp) else 0.0) <= load_cap and tp2 <= load_cap
            if v_ok and t_ok:
                n_ok += 1
            elif not v_ok:
                why["v"] += 1
            else:
                why["thermal"] += 1
        print(f"    {kw:>4}kW   {n_ok:>5}/{len(pool):<7}   "
              f"voltage {why['v']:<5} thermal {why['thermal']}")
    net.load.drop(index=probe, inplace=True)
    pp.runpp(net, **_c.RUNPP_KWARGS)

    # ------------------------------------------------------------------ D4
    print("\n\n  D4 -- THE FEASIBLE SOURCE-SETPOINT WINDOW\n")
    print(f"    {'source':>7} {'base vmax':>11} {'headroom':>10} {'base vmin':>11}  baseline OK?")
    for sp in SETPOINTS:
        n2 = copy.deepcopy(net)
        n2.ext_grid.loc[:, "vm_pu"] = sp
        try:
            pp.runpp(n2, **_c.RUNPP_KWARGS)
        except pp.LoadflowNotConverged:
            print(f"    {sp:>7.2f} {'DIVERGED':>11}")
            continue
        v2 = n2.res_bus.vm_pu.drop(index=src)
        mx, mn = float(v2.max()), float(v2.min())
        ok = "yes" if mn >= _c.VMIN_PU and mx <= _c.VMAX_PU else \
             ("NO -- vmin breaks 0.94" if mn < _c.VMIN_PU else "NO -- vmax breaks 1.05")
        print(f"    {sp:>7.2f} {mx:>11.6f} {_c.VMAX_PU - mx:>+10.5f} {mn:>11.6f}  {ok}")

    print("\n    Ckt7's window was [1.04, 1.05] -- its own 0.087 pu drop ate the ANSI band.")
    print("    If Ckt24's window is WIDER, its honest setpoint is lower, and M58 must be")
    print("    re-measured there rather than at Ckt7's 1.04.\n")


if __name__ == "__main__":
    main()
