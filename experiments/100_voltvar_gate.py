"""M72 -- DOES SMART-INVERTER VOLT/VAR DISSOLVE THE RESIDUAL? (§3, the pre-empted attack)

The irreducible residual lives in the voltage regime, and we model PV at fixed unity PF. The
standard referee attack: "enable smart-inverter Volt/VAr and the residual evaporates." The
defense is in the physics, not a hope: on high-R/X LV distribution feeders voltage is driven by
REAL power (dV ~= rP + xQ with r >> x), so reactive Volt/VAr has limited authority *precisely
where the P-coupling of generation and load binds*. This file puts one voltage-limited residual
cell on record with Volt/VAr enabled and shows the residual survives -- and, more usefully, shows
the TWO mechanisms that defeat Volt/VAr, both consequences of r >> x.

Volt/VAr model: IEEE 1547-2018 Category B droop, overvoltage half -- each PV inverter absorbs
reactive power above 1.03 pu, linearly to 44% of |P| at 1.08 pu, solved to a fixed point on Q
(pandapower load convention: q_mvar > 0 consumes VAR, lowering voltage). The greedy packer runs
IDENTICALLY with and without Volt/VAr, so the comparison is fair.

RESULT (n13f4, the clean overvoltage feeder): Volt/VAr lifts PV-solo capacity by only +33% (one
extra request) and the joint still beats Volt/VAr-PV-solo by +475% -- the residual is intact.
WHY, from the forced-PV diagnostic:
  (1) WEAK VOLTAGE AUTHORITY. r/x is ~5 (n13f4) to ~9 (n2f1); dropping vmax from 1.136 to 1.098
      at 4 PV takes the FULL 44% reactive absorption. Reactive barely moves an R-driven voltage.
  (2) THE THERMAL BACKFIRE. That same Q absorption RAISES line current, converting the overvoltage
      limit into a thermal one (4 PV: line 72% -> 102%). Buying voltage headroom with reactive
      costs current -- itself an r >> x consequence.
So reactive support cannot substitute for the real-power complementarity of a co-located load.
(On n2f1 at 100 kW the PV-solo margin is already thermal, and Volt/VAr worsens it: +0%.)

Run:
  .venv/bin/python experiments/100_voltvar_gate.py
  .venv/bin/python experiments/100_voltvar_gate.py --feeder enwl_n2f1
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandapower as pp

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

_x = __import__("78_corpus_law")                  # Feeder, load_feeder, RUNPP_KWARGS
_res = __import__("98_residual_enwl")             # build_mixed

# IEEE 1547-2018 Category B Volt/VAr (overvoltage half): deadband to 1.03, full absorb by 1.08.
VV_V3, VV_V4, VV_QFRAC = 1.03, 1.08, 0.44


def vvar_q(vm: float, p_abs_mw: float) -> float:
    frac = float(np.clip((vm - VV_V3) / (VV_V4 - VV_V3), 0.0, 1.0))
    return frac * VV_QFRAC * p_abs_mw


def evaluate_vv(f, projects, accepted, vvar: bool):
    """AC oracle; with vvar, iterate PV reactive to the Volt/VAr fixed point before judging."""
    net = pp.copy.deepcopy(f.ctx.net)
    by = {p.id: p for p in projects}
    rows = {}
    for rid in accepted:
        p = by[rid]
        rows[rid] = pp.create_load(net, bus=int(p.bus), p_mw=float(p.p_mw), q_mvar=0.0, name=rid)
    try:
        pp.runpp(net, **_x.RUNPP_KWARGS)
    except pp.LoadflowNotConverged:
        return False, float("inf"), {}
    if vvar:
        for _ in range(30):
            q_prev = net.load.loc[list(rows.values()), "q_mvar"].to_numpy().copy()
            for rid, ridx in rows.items():
                p = by[rid]
                if p.p_mw < 0:                                   # PV inverters only
                    vm = float(net.res_bus.vm_pu.loc[int(p.bus)])
                    net.load.at[ridx, "q_mvar"] = vvar_q(vm, abs(p.p_mw))
            try:
                pp.runpp(net, **_x.RUNPP_KWARGS)
            except pp.LoadflowNotConverged:
                return False, float("inf"), {}
            if np.max(np.abs(net.load.loc[list(rows.values()), "q_mvar"].to_numpy() - q_prev)) < 1e-5:
                break
    vmax = float(net.res_bus.vm_pu.max()); vmin = float(net.res_bus.vm_pu.min())
    line = float(net.res_line.loading_percent.max()) if len(net.line) else 0.0
    trafo = float(net.res_trafo.loading_percent.max()) if len(net.trafo) else 0.0
    q_kvar = float(net.load.loc[list(rows.values()), "q_mvar"].sum()) * 1000 if rows else 0.0
    vio = (max(0.0, vmax - f.VMAX_PU) + max(0.0, f.VMIN_PU - vmin)
           + max(0.0, line - f.LOADING_MAX_PCT) + max(0.0, trafo - f.LOADING_MAX_PCT))
    return vio == 0.0, vio, dict(vmax=vmax, line=line, q_kvar=q_kvar)


def greedy_max(f, cand, vvar):
    """Greedy pack: add any request that keeps the set feasible, until none can. Same procedure
    with and without Volt/VAr, so the WITH/WITHOUT comparison is apples-to-apples."""
    acc, remaining, improved = set(), list(cand), True
    while improved:
        improved = False
        for p in list(remaining):
            ok, _, _ = evaluate_vv(f, cand, acc | {p.id}, vvar)
            if ok:
                acc = acc | {p.id}; remaining.remove(p); improved = True
    kw = sum(abs(next(x for x in cand if x.id == rid).p_mw) for rid in acc) * 1000
    return acc, kw


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--feeder", default="enwl_n13f4")
    ap.add_argument("--size-kw", type=float, default=100.0)
    ap.add_argument("--k", type=int, default=24)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()

    _ctx, f, _rel, _mk, desc = _x.load_feeder(a.feeder, 4)
    print(f"\n  M72 -- VOLT/VAR vs THE RESIDUAL  ({desc}, {a.size_kw:.0f} kW)\n")

    r = f.ctx.net.line.r_ohm_per_km * f.ctx.net.line.length_km
    xr = f.ctx.net.line.x_ohm_per_km * f.ctx.net.line.length_km
    rx = (r / xr.replace(0, np.nan)).replace([np.inf, -np.inf], np.nan).dropna()
    print(f"  feeder r/x per line: median {rx.median():.1f}, IQR [{rx.quantile(.25):.1f}, "
          f"{rx.quantile(.75):.1f}]  ->  voltage is real-power-driven (dV ~ rP + xQ, r >> x)\n")

    projects = _res.build_mixed(f, a.k, a.seed, 0.5, a.size_kw)
    pv = [p for p in projects if p.p_mw < 0]
    by = {p.id: p for p in pv}

    _a1, fp_kw = greedy_max(f, pv, vvar=False)
    _a2, vv_kw = greedy_max(f, pv, vvar=True)
    _aj, joint_kw = greedy_max(f, projects, vvar=False)
    print(f"  PV-only greedy max:   fixed-PF {fp_kw:.0f} kW   Volt/VAr {vv_kw:.0f} kW  "
          f"(+{100*(vv_kw/fp_kw-1) if fp_kw>0 else float('nan'):.0f}%)")
    print(f"  JOINT (PV+load):      {joint_kw:.0f} kW   ->  joint beats Volt/VAr-PV-solo by "
          f"+{100*(joint_kw/vv_kw-1) if vv_kw>0 else float('nan'):.0f}%   (the residual SURVIVES)\n")

    print("  the two mechanisms (force the first N PV; watch Volt/VAr trade voltage for current):")
    print(f"    {'N PV':>5} | {'fixed-PF vmax':>13} {'line%':>6} | {'Volt/VAr vmax':>13} {'line%':>6} "
          f"{'Q kvar':>7} | binds")
    for n in range(3, 7):
        acc = {pv[i].id for i in range(min(n, len(pv)))}
        okf, _v, inf = evaluate_vv(f, pv, acc, vvar=False)
        okv, _v, inv = evaluate_vv(f, pv, acc, vvar=True)
        kind = ("feasible" if okv else
                ("THERMAL" if inv["line"] > f.LOADING_MAX_PCT else "voltage"))
        print(f"    {n:>5} | {inf['vmax']:>13.4f} {inf['line']:>5.1f} | {inv['vmax']:>13.4f} "
              f"{inv['line']:>5.1f} {inv['q_kvar']:>7.0f} | {kind}")
    print(f"\n  limits: vmax {f.VMAX_PU}  line {f.LOADING_MAX_PCT:.0f}%. Volt/VAr lowers vmax but "
          f"raises line current;\n  where it rescues voltage it forfeits thermal. Reactive cannot "
          f"replace a co-located load's real power.\n")


if __name__ == "__main__":
    main()
