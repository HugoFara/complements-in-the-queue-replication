"""M78 -- THE RESIDUAL UNDER VOLT/VAR, IN THE PAPER'S OWN CURRENCY

exp100 answered the smart-inverter objection with the wrong denominator. It compared JOINT
(fixed power factor) against GENERATION-SOLO (Volt/VAr enabled) and reported the gap. That is a
solo comparison, and the whole point of the residual is that a solo comparison overstates
segregation's cost, because a real sequential study recovers part of it. The objection --
"enable Volt/VAr and the residual evaporates" -- is about the residual, so it has to be answered
with the residual:

    residual(Volt/VAr) = JOINT(Volt/VAr) - max(seq gen-first, seq load-first), all with Volt/VAr

Volt/VAr is enabled in EVERY study here -- both solo tracks, both sequential orders, and the
joint study -- so the comparison is internally consistent and the sequential orders get the full
benefit of reactive support too. That is the version that can actually refute the paper.

Volt/VAr model: IEEE 1547-2018 Category B droop, overvoltage half. Each PV inverter absorbs
reactive power above 1.03 pu, linearly to 44% of |P| at 1.08 pu, solved to a fixed point
(pandapower load convention: q_mvar > 0 consumes VAR, lowering voltage).

One modeling point exp100 did not have to face. In a sequential study the first track's approvals
are already in the base case, so their inverters must ALSO be running Volt/VAr while the second
track is studied. This file therefore stages the first track as a fixed prefix of the accept-set
rather than baking it into the network as a fixed q=0 load, so every PV in the network -- approved
or under study -- follows the same droop. Baking them in at q=0 would understate the sequential
orders and inflate the residual, which is the direction that would flatter the paper.

The greedy packer is identical with and without Volt/VAr, so both columns are apples-to-apples.

Run:
  .venv/bin/python experiments/107_voltvar_residual.py --save
  .venv/bin/python experiments/107_voltvar_residual.py --feeders enwl_n13f4 --seeds 0
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandapower as pp
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

_x = __import__("78_corpus_law")                  # Feeder, load_feeder, RUNPP_KWARGS
_res = __import__("98_residual_enwl")             # build_mixed
_vv = __import__("100_voltvar_gate")              # vvar_q, the Category B droop

OUT = Path(__file__).resolve().parents[1] / "outputs"


def evaluate(f, projects, accepted, vvar, fixed=()):
    """AC oracle over `fixed | accepted`. With vvar, iterate EVERY PV in the network to the
    Volt/VAr fixed point -- including the `fixed` prefix, which is a previous track's approvals."""
    net = pp.copy.deepcopy(f.ctx.net)
    by = {p.id: p for p in projects}
    live = list(fixed) + [rid for rid in accepted if rid not in set(fixed)]
    rows = {}
    for rid in live:
        p = by[rid]
        rows[rid] = pp.create_load(net, bus=int(p.bus), p_mw=float(p.p_mw), q_mvar=0.0, name=rid)
    try:
        pp.runpp(net, **_x.RUNPP_KWARGS)
    except pp.LoadflowNotConverged:
        return False, {}
    if vvar and rows:
        idx = list(rows.values())
        for _ in range(30):
            q_prev = net.load.loc[idx, "q_mvar"].to_numpy().copy()
            for rid, ridx in rows.items():
                p = by[rid]
                if p.p_mw < 0:                                # PV inverters only
                    vm = float(net.res_bus.vm_pu.loc[int(p.bus)])
                    net.load.at[ridx, "q_mvar"] = _vv.vvar_q(vm, abs(p.p_mw))
            try:
                pp.runpp(net, **_x.RUNPP_KWARGS)
            except pp.LoadflowNotConverged:
                return False, {}
            if np.max(np.abs(net.load.loc[idx, "q_mvar"].to_numpy() - q_prev)) < 1e-5:
                break
    vmax = float(net.res_bus.vm_pu.max())
    vmin = float(net.res_bus.vm_pu.min())
    line = float(net.res_line.loading_percent.max()) if len(net.line) else 0.0
    trafo = float(net.res_trafo.loading_percent.max()) if len(net.trafo) else 0.0
    ok = (vmax <= f.VMAX_PU and vmin >= f.VMIN_PU
          and line <= f.LOADING_MAX_PCT and trafo <= f.LOADING_MAX_PCT)
    return ok, dict(vmax=vmax, vmin=vmin, line=line, trafo=trafo)


def greedy(f, projects, cand, vvar, fixed=()):
    """Pack `cand` on top of the fixed prefix; identical procedure with and without Volt/VAr."""
    acc, remaining, improved = set(), list(cand), True
    while improved:
        improved = False
        for p in list(remaining):
            ok, _ = evaluate(f, projects, acc | {p.id}, vvar, fixed)
            if ok:
                acc.add(p.id)
                remaining.remove(p)
                improved = True
    by = {p.id: p for p in projects}
    return acc, sum(abs(by[rid].p_mw) for rid in acc) * 1000.0


def three_way(f, projects, vvar):
    """Solo / sequential (both orders) / joint, all under the same Volt/VAr setting."""
    gen = [p for p in projects if p.p_mw < 0]
    load = [p for p in projects if p.p_mw > 0]

    gen_ids, gen_kw = greedy(f, projects, gen, vvar)
    load_ids, load_kw = greedy(f, projects, load, vvar)
    _j, joint_kw = greedy(f, projects, projects, vvar)

    # Sequential: the first track's approvals stay live (and keep following the droop).
    _l2, load2 = greedy(f, projects, load, vvar, fixed=sorted(gen_ids))
    seq_genfirst = gen_kw + load2
    _g2, gen2 = greedy(f, projects, gen, vvar, fixed=sorted(load_ids))
    seq_loadfirst = load_kw + gen2

    best_seq = max(seq_genfirst, seq_loadfirst)
    residual = joint_kw - best_seq
    return dict(gen_alone_kw=gen_kw, load_alone_kw=load_kw,
                seg_parallel_kw=gen_kw + load_kw,
                seq_genfirst_kw=seq_genfirst, seq_loadfirst_kw=seq_loadfirst,
                best_sequential_kw=best_seq, joint_kw=joint_kw,
                residual_kw=residual,
                residual_frac_of_joint_pct=(100.0 * residual / joint_kw if joint_kw > 0 else np.nan))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--feeders", nargs="+", default=["enwl_n13f4", "enwl_n2f1"])
    ap.add_argument("--size-kw", type=float, default=100.0)
    ap.add_argument("--k", type=int, default=24)
    ap.add_argument("--load-frac", type=float, default=0.5)
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2, 3])
    ap.add_argument("--n-jobs", type=int, default=8)
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()

    print("\n  M78 -- THE RESIDUAL WITH AND WITHOUT SMART-INVERTER VOLT/VAR\n")
    print(f"  K={a.k} ({int(a.k*(1-a.load_frac))} generation + {int(a.k*a.load_frac)} load), "
          f"{a.size_kw:.0f} kW each. Volt/VAr in EVERY study, sequential orders included.\n")

    hdr = (f"  {'feeder':>11} {'seed':>4} {'Volt/VAr':>9} | {'gen solo':>8} {'best seq':>8} "
           f"{'joint':>7} | {'residual kW':>11} {'% of joint':>10}")
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))

    rows = []
    for name in a.feeders:
        _ctx, f, _rel, _mk, _desc = _x.load_feeder(name, a.n_jobs)
        for seed in a.seeds:
            projects = _res.build_mixed(f, a.k, seed, a.load_frac, a.size_kw)
            for vvar in (False, True):
                t0 = time.time()
                r = three_way(f, projects, vvar)
                r.update(feeder=name, seed=seed, k=a.k, size_kw=a.size_kw,
                         load_frac=a.load_frac, voltvar=vvar, dt=time.time() - t0)
                rows.append(r)
                print(f"  {name:>11} {seed:>4} {str(vvar):>9} | {r['gen_alone_kw']:>8.0f} "
                      f"{r['best_sequential_kw']:>8.0f} {r['joint_kw']:>7.0f} | "
                      f"{r['residual_kw']:>11.0f} {r['residual_frac_of_joint_pct']:>9.1f}%")
                if a.save:
                    OUT.mkdir(exist_ok=True)
                    pd.DataFrame(rows).to_csv(OUT / "m78_voltvar_residual.csv", index=False)

    d = pd.DataFrame(rows)
    print("\n" + "=" * 96)
    for name in a.feeders:
        s = d[d.feeder == name]
        off = s[~s.voltvar].residual_frac_of_joint_pct
        on = s[s.voltvar].residual_frac_of_joint_pct
        print(f"  {name}: residual {off.median():.1f}% of joint at fixed PF, "
              f"{on.median():.1f}% with Volt/VAr enabled throughout "
              f"(n={len(on)} seeds)")
    print("\n  If the Volt/VAr column stays materially positive, reactive support does not")
    print("  dissolve the residual, answered in the residual's own currency rather than a")
    print("  solo comparison.\n")
    if a.save:
        print("  saved -> outputs/m78_voltvar_residual.csv")


if __name__ == "__main__":
    main()
