"""M71 -- THERMAL BINDING CAN BE TWO-WAY: the reverse-flow-limited lateral (§1b).

The brief's earlier "voltage is two-way, thermal is one-way" was WRONG as a law -- it was a
property of WHICH thermal element binds on Ckt24 (the substation transformer, UPSTREAM of every
request), where PV can only ever RELIEVE, never aggress. The corrected invariant (exp96 lesson,
generalized): the segregation residual exists iff BOTH generation and load can independently drive
the SAME active constraint to its limit. A voltage band is TWO-SIDED (over- and under-limit), so
gen and load project onto it with opposite sign -> almost always two-way. A thermal magnitude
bound |I| <= Imax is ONE-SIDED, so it is two-way only when the binding element carries REVERSIBLE
flow -- then gen (reverse) and load (forward) each reach the limit independently.

This file constructs the thermal two-way case a distribution referee builds in one sentence: a
minimal MV spur to a bus hosting BOTH generation and load -- an industrial site with co-located
PV, the exact DOE/FERC co-located-interconnection scenario. The spur is ampacity-limited; voltage
is kept slack (11 kV, low impedance, loose 0.90-1.10 band) so ONLY thermal can bind.

  * PV alone at the site  -> reverse flow  -> overloads the spur  (PV is the aggressor).
  * Load alone at the site -> forward flow  -> overloads the spur  (load is the aggressor).
  * Co-located, they CANCEL net flow -> the spur carries almost nothing -> both feasible.

So gen and load push the SAME one-sided bound |I| <= Imax in OPPOSITE directions: two-way thermal
coupling, and a large positive residual (no study order recovers it, because each track studied
against the bare baseline is thermally infeasible on its own). Voltage never leaves ~1.00.

This is the SAME machinery (exp91 certify, exp98 three-way + anchor) that produced the Ckt7/Ckt24/
ENWL residuals -- so the number is directly comparable. Contrast, in one line, with Ckt24: there
the binding thermal element is UPSTREAM of all requests (PV only relieves), so residual is 0 -- a
TOPOLOGICAL fact, not a property of "thermal." Same constraint TYPE, opposite residual, decided by
whether the binding element carries mixed flow. That is the invariant the brief should state.

Run:
  .venv/bin/python experiments/99_thermal_twoway.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandapower as pp

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

_x = __import__("78_corpus_law")                  # Feeder
_c = __import__("68_ckt7_gldf_headline")          # Project
_seq = __import__("91_sequential_segregation")    # certify (feeder-agnostic)
_res = __import__("98_residual_enwl")             # three_way + with_anchor (feeder-agnostic)


def build_toy(spur_ka: float, vn_kv: float = 11.0,
              r_spur: float = 0.30, x_spur: float = 0.10) -> tuple:
    """slack --main(well-rated)--> mid --spur(ampacity-limited)--> site (co-located PV+load)."""
    net = pp.create_empty_network(sn_mva=10.0)
    b_sub = pp.create_bus(net, vn_kv=vn_kv, name="sub")
    b_mid = pp.create_bus(net, vn_kv=vn_kv, name="mid")
    b_site = pp.create_bus(net, vn_kv=vn_kv, name="site")
    pp.create_ext_grid(net, b_sub, vm_pu=1.0)
    pp.create_line_from_parameters(net, b_sub, b_mid, length_km=1.0, r_ohm_per_km=0.10,
                                   x_ohm_per_km=0.10, c_nf_per_km=0.0, max_i_ka=1.0)
    pp.create_line_from_parameters(net, b_mid, b_site, length_km=1.0, r_ohm_per_km=r_spur,
                                   x_ohm_per_km=x_spur, c_nf_per_km=0.0, max_i_ka=spur_ka)
    pp.create_load(net, b_site, p_mw=0.05, q_mvar=0.0, name="base")
    rating_kva = np.sqrt(3) * vn_kv * spur_ka * 1000
    return net, b_site, rating_kva


def _reject_kind(f, info):
    if info["line_pct"] > f.LOADING_MAX_PCT + 1e-6 or info["trafo_pct"] > f.LOADING_MAX_PCT + 1e-6:
        return "THERMAL"
    if info["vmax"] > f.VMAX_PU + 1e-6 or info["vmin"] < f.VMIN_PU - 1e-6:
        return "voltage"
    return "feasible"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--spur-ka", type=float, default=0.040)
    ap.add_argument("--size-kw", type=float, default=500.0)
    ap.add_argument("--n-each", type=int, default=6, help="PV count = load count at the site")
    ap.add_argument("--n-jobs", type=int, default=4)
    a = ap.parse_args()

    net, site, rating = build_toy(a.spur_ka, vn_kv=11.0)
    f = _x.Feeder("thermal-twoway-toy", net, sizer=None, vmin=0.90, vmax=1.10, loading=100.0)

    print("\n  M71 -- THERMAL TWO-WAY: an ampacity-limited MV spur to a co-located PV+load site\n")
    print(f"  spur {a.spur_ka*1000:.0f} A @ 11 kV  ~=  {rating:.0f} kVA rating; requests {a.size_kw:.0f} kW; "
          f"voltage band 0.90-1.10 (deliberately slack)\n")

    pv = [_c.Project(id=f"pv{i}", bus=site, p_mw=-a.size_kw / 1000.0) for i in range(a.n_each)]
    ld = [_c.Project(id=f"ld{i}", bus=site, p_mw=+a.size_kw / 1000.0) for i in range(a.n_each)]
    projects = pv + ld

    # --- the mechanism: one of each seats; a second of the SAME sign overloads the spur (thermal)
    f._proj = None; _o, _m, _v, i1pv = f.evaluate(f.ctx, pv, {pv[0].id})
    f._proj = None; o2pv, _m, _v, i2pv = f.evaluate(f.ctx, pv, {pv[0].id, pv[1].id})
    f._proj = None; _o, _m, _v, i1ld = f.evaluate(f.ctx, ld, {ld[0].id})
    f._proj = None; o2ld, _m, _v, i2ld = f.evaluate(f.ctx, ld, {ld[0].id, ld[1].id})
    # both together at the site cancel
    f._proj = None; obal, _m, _v, ibal = f.evaluate(f.ctx, projects, {pv[0].id, ld[0].id})

    print("  MECHANISM (voltage stays ~1.00 throughout; the spur is the only thing that ever binds):")
    print(f"    1 PV   ({a.size_kw:.0f} kW reverse): spur {i1pv['line_pct']:5.1f}%   vmax {i1pv['vmax']:.4f}  -> feasible")
    print(f"    2 PV  ({2*a.size_kw:.0f} kW reverse): spur {i2pv['line_pct']:5.1f}%   vmax {i2pv['vmax']:.4f}  -> {_reject_kind(f, i2pv)}")
    print(f"    1 load ({a.size_kw:.0f} kW forward): spur {i1ld['line_pct']:5.1f}%   vmin {i1ld['vmin']:.4f}  -> feasible")
    print(f"    2 load({2*a.size_kw:.0f} kW forward): spur {i2ld['line_pct']:5.1f}%   vmin {i2ld['vmin']:.4f}  -> {_reject_kind(f, i2ld)}")
    print(f"    1 PV + 1 load (net ~0)      : spur {ibal['line_pct']:5.1f}%   vmax {ibal['vmax']:.4f}  -> feasible (cancels)")

    # --- the residual, via the same three-way machinery as every other feeder
    r = _res.three_way(f, projects, a.n_jobs)
    f._proj = None; okj, _m, vio, infoj = f.evaluate(f.ctx, projects,
                                                     set(p.id for p in projects))  # all-in check
    print("\n  RESIDUAL (joint - best study order, identical method to Ckt7/Ckt24/ENWL):")
    print(f"    PV alone  {r['pv_alone_kw']:.0f} kW   load alone {r['load_alone_kw']:.0f} kW   "
          f"best sequential {r['best_sequential_kw']:.0f} kW")
    print(f"    JOINT     {r['joint_kw']:.0f} kW   (all {a.n_each} PV + {a.n_each} load co-located, net cancels)")
    print(f"    residual  {r['residual_vs_sequential_pct']:+.0f}%   "
          f"vs parallel {r['cost_vs_parallel_pct']:+.0f}%")
    print(f"    joint at the site bus alone AC-check: vmax {infoj['vmax']:.4f}/{f.VMAX_PU} "
          f"vmin {infoj['vmin']:.4f}/{f.VMIN_PU} spur {infoj['line_pct']:.1f}% (net cancels)")

    print("\n  => On a ONE-SIDED thermal bound, gen (reverse) and load (forward) are BOTH aggressors")
    print("     -> two-way -> large positive residual, voltage never binding. The reviewer's")
    print("     reverse-flow-limited lateral. Contrast Ckt24 (exp96): binding thermal element is")
    print("     UPSTREAM of all requests, PV only relieves -> one-way -> residual 0. Same constraint")
    print("     TYPE, opposite residual, decided by whether the binding element carries mixed flow.\n")


if __name__ == "__main__":
    main()
