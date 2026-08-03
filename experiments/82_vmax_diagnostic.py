"""M54 -- IS Ckt7's BASELINE vmax A SETPOINT, NOT A PHYSICAL FACT?

A reviewer's catch, and it is our own §8 lesson turned on us: "suspect the
plumbing before the physics -- three times out of three."

exp68's oracle takes  vmax = net.res_bus.vm_pu.max()  over EVERY bus, with no
exclusion of the ext_grid (slack) bus. A 1,255-bus feeder whose baseline vmax
lands EXACTLY on the round 1.0500 limit is not physics; it is a setpoint. If the
OpenDSS Vsource is defined at pu=1.05 and we are counting it as a constraint,
then every "PV is infeasible everywhere" result in M53/§0 is an artefact: no
interconnection request can move the slack, so it can never be relieved and
never be violated -- it just sits on the limit and poisons the feasibility test.

Three diagnostics, all cheap:
  D1  Which bus attains vmax at baseline? Print it, its type, its distance from
      the source. If it is the slack, we are done.
  D2  dvmax/dP for each candidate bus. If that spans less than ~3x, the same bus
      is binding for every request -- which is what a global, request-insensitive
      binding bus looks like.
  D3  Re-run the §0 seatability test with the source bus excluded.

Usage:  python experiments/82_vmax_diagnostic.py
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import numpy as np
import pandapower as pp
import pandapower.topology as top
import networkx as nx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

exp68 = __import__("68_ckt7_gldf_headline")


def main() -> None:
    ctx = exp68.build_network()
    net = ctx.net
    pp.runpp(net, **exp68.RUNPP_KWARGS)

    src = int(net.ext_grid.bus.iloc[0])
    setpoint = float(net.ext_grid.vm_pu.iloc[0])
    vm = net.res_bus.vm_pu

    print("\n  D1 -- WHICH BUS ATTAINS BASELINE vmax?\n")
    print(f"    ext_grid bus            : {src}  ({net.bus.at[src, 'name']})")
    print(f"    ext_grid vm_pu SETPOINT : {setpoint:.6f}")
    print(f"    VMAX_PU limit           : {exp68.VMAX_PU:.6f}")

    argmax = int(vm.idxmax())
    print(f"\n    baseline vmax           : {float(vm.max()):.6f}  at bus {argmax}"
          f"  ({net.bus.at[argmax, 'name']})")
    print(f"    is that the slack?      : {'YES  <-- the limit is a SETPOINT' if argmax == src else 'no'}")

    # top-8 buses by voltage, flagging the slack
    top8 = vm.sort_values(ascending=False).head(8)
    print("\n    top-8 buses by baseline voltage:")
    for b, v in top8.items():
        tag = "  <-- SLACK (ext_grid)" if int(b) == src else ""
        print(f"      bus {int(b):>5}  {net.bus.at[int(b), 'name']:<22}  {v:.6f}{tag}")

    # the honest question: what is vmax over the NON-slack buses?
    vm_nonslack = vm.drop(index=src)
    argmax_ns = int(vm_nonslack.idxmax())
    print(f"\n    vmax EXCLUDING the slack: {float(vm_nonslack.max()):.6f}"
          f"  at bus {argmax_ns} ({net.bus.at[argmax_ns, 'name']})")
    print(f"    headroom to 1.05        : {exp68.VMAX_PU - float(vm_nonslack.max()):+.6f} pu")

    # ---------------------------------------------------------------- D2
    print("\n\n  D2 -- dvmax/dP ACROSS THE CANDIDATE BUSES\n")
    print("    (inject -50 kW of PV at one candidate bus at a time; report the")
    print("     resulting vmax, both counting and excluding the slack)\n")

    candidates = [b for b in net.load.bus.unique() if b not in ctx.excluded_buses]
    print(f"    candidate buses: {len(candidates)}")

    probe_mw = -0.050
    rows = []
    for b in candidates:
        n2 = copy.deepcopy(net)
        pp.create_load(n2, bus=int(b), p_mw=probe_mw, q_mvar=0.0, name="probe")
        try:
            pp.runpp(n2, **exp68.RUNPP_KWARGS)
        except pp.LoadflowNotConverged:
            continue
        v2 = n2.res_bus.vm_pu
        rows.append((int(b),
                     float(v2.max()),                      # counting slack
                     float(v2.drop(index=src).max())))     # excluding slack

    arr = np.array([[r[1], r[2]] for r in rows])
    base_all = float(vm.max())
    base_ns = float(vm_nonslack.max())

    d_all = arr[:, 0] - base_all
    d_ns = arr[:, 1] - base_ns

    def span(d):
        pos = d[d > 1e-9]
        if len(pos) < 2:
            return float("nan")
        return float(pos.max() / pos.min())

    print(f"\n    WITH the slack counted (what exp68 does):")
    print(f"      dvmax/dP: min {d_all.min():+.6f}   max {d_all.max():+.6f} pu")
    print(f"      buses with ZERO sensitivity (dvmax <= 1e-9): "
          f"{int((d_all <= 1e-9).sum())}/{len(d_all)}")
    print(f"      spread (max/min over positive): {span(d_all):.2f}x")

    print(f"\n    EXCLUDING the slack:")
    print(f"      dvmax/dP: min {d_ns.min():+.6f}   max {d_ns.max():+.6f} pu")
    print(f"      buses with ZERO sensitivity (dvmax <= 1e-9): "
          f"{int((d_ns <= 1e-9).sum())}/{len(d_ns)}")
    print(f"      spread (max/min over positive): {span(d_ns):.2f}x")
    print("\n    (reviewer's test: a spread < ~3x means the SAME bus binds for every")
    print("     request -- i.e. the binding bus is global and request-insensitive.)")

    # ---------------------------------------------------------------- D3
    print("\n\n  D3 -- SEATABILITY, WITH AND WITHOUT THE SLACK IN THE CONSTRAINT SET\n")
    print("    §0's claim: a 50 kW PV request is seatable at EVERY bus and a")
    print("    100 kW PV request at NONE. Re-test both ways.\n")

    print(f"    {'size':>6}  {'seatable (slack counted)':>26}  {'seatable (slack excluded)':>26}")
    for kw in (20, 50, 75, 100, 150):
        n_all = n_ns = 0
        for b in candidates:
            n2 = copy.deepcopy(net)
            pp.create_load(n2, bus=int(b), p_mw=-kw / 1000.0, q_mvar=0.0, name="probe")
            try:
                pp.runpp(n2, **exp68.RUNPP_KWARGS)
            except pp.LoadflowNotConverged:
                continue
            v2 = n2.res_bus.vm_pu
            lp = n2.res_line.loading_percent.drop(ctx.excluded_line_idx, errors="ignore")
            tp = float(n2.res_trafo.loading_percent.max()) if len(n2.trafo) else 0.0
            therm = (float(lp.max()) if len(lp) else 0.0) <= exp68.LOADING_MAX_PCT and tp <= exp68.LOADING_MAX_PCT

            ok_all = (float(v2.max()) <= exp68.VMAX_PU
                      and float(v2.min()) >= exp68.VMIN_PU and therm)
            v2ns = v2.drop(index=src)
            ok_ns = (float(v2ns.max()) <= exp68.VMAX_PU
                     and float(v2ns.min()) >= exp68.VMIN_PU and therm)
            n_all += int(ok_all)
            n_ns += int(ok_ns)
        print(f"    {kw:>4}kW  {n_all:>10}/{len(candidates):<15}  {n_ns:>10}/{len(candidates):<15}")

    print("\n    If column 2 is 0 and column 3 is ~199, §0 was a plumbing bug and")
    print("    'PV is infeasible everywhere' was the slack sitting on its setpoint.\n")


if __name__ == "__main__":
    main()
