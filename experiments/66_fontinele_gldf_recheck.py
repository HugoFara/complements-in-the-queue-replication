"""M37: re-checking the Fontinele head-to-head (M29, Sec. 7.7) with the GLDF-MILP.

MOTIVATION
----------
M35 (exp64) showed NSGA-II reaches only 22.8-61.3% of achievable capacity on
EPRI Ckt24, while a Generalized-LinDistFlow MILP reaches 74.0-90.7%. M36
(exp65) then re-checked the SMART-DS *negative* and found it survives (0/5,
with a model-free bound proving <=7.4% room exists at all). That leaves the
paper's *positive* NSGA-II numbers still unaudited -- and the most consequential
one is Sec. 7.7's head-to-head against the closest prior art, Fontinele et al.
[13], on their own two IEEE benchmark networks. That result is the paper's only
"we beat a real competitor" claim. If NSGA-II understated Ckt24 by 5x, our
reported +14.2%/+41.2% (33-bus) and +0.5%/+24.6% (69-bus) medians may be
understating our OWN method's advantage over theirs.

WHAT BINDS HERE (measured, not assumed). At the FIFO baseline on both networks
and both scenarios: line loading 0.0% (their networks carry no ampacity data, so
exp59 leaves them thermally unbounded at max_i_ka=99999, matching their own
pandapower default), total losses 5.1-7.6% against their 15% cap, and vmax
1.0440-1.0499 against their 1.05 limit. **Overvoltage is the only binding
constraint** -- exactly what a GLDF voltage MILP models natively.

THE MILP
--------
Same GLDF model as M35 (Huang/Cui/Zhou/Bernstein, IEEE CDC 2021,
arXiv:2104.02118), built from pandapower's own solved Ybus, linearized at the
base (no-DG) operating point so `v_base` is exact:

    maximize  sum_r p_kw_r * x_r                            x_r in {0,1}
    s.t.      v_i <= V_MAX^2                                (all buses)
              v_i >= V_MIN_PROTECTED^2                      (only buses with
                                                             v_base >= 0.93)
              v = v_base + A x,  A[:,r] = M[:, bus(r)] * dP_r

The asymmetric lower bound is Fontinele's own Algorithm 1 rule, reproduced
exactly: pre-existing undervoltage is tolerated and never re-triggered, so only
buses that were already >= 0.93 pu in the base state are protected. (Both
networks have genuinely undervoltage-depressed tails at base -- 33-bus vmin
0.9131, 69-bus vmin 0.9092 -- so this is not a formality.)

**Their two remaining constraints (line loading <= 100%, total losses <= 15% of
load) are NOT in the MILP.** Loading cannot bind (unbounded ampacity). Losses are
quadratic in branch flows and LinDistFlow drops them by construction, so omitting
the cap makes the MILP a RELAXATION of their rule -- which is what we want from a
ceiling. It is never trusted: every accept-set is re-verified against exp59's
`joint_evaluate`, i.e. their FULL Algorithm 1 rule including losses, and repaired
until it passes.

METHOD (three stages, only the last is claimed)
-----------------------------------------------
1. `gldf_milp`     -- optimize against the linear model. Fast, but its optimum
                      exploits the model's optimism (M35's lesson), so it
                      generally does not satisfy the real rule.
2. `ac_repair`     -- steepest-descent drop against the REAL oracle until the
                      full Fontinele rule (losses included) is satisfied.
3. `ac_polish`     -- exhaustive single-add / single-swap improvement against the
                      REAL oracle, the move set exp62 (Sec. 7.10) identified as
                      the one this problem needs. Guarantees the reported
                      accept-set is a certified 1-swap local optimum of the EXACT
                      problem, not of the linear surrogate.

Only stage 3's output is reported as the method's result, and it is AC-verified
under their own rule. The unrepaired stage-1 optimum is reported separately as
the (unachievable) GLDF ceiling.

RESULT (2026-07-11) -- the head-to-head SURVIVES, and marginally improves
------------------------------------------------------------------------
Median gain over the same FIFO baseline, 3 seeds/scenario/network:

| network | scen | NSGA-II (M29) | GLDF-MILP | Fontinele et al. [13] |
|---------|------|--------------:|----------:|-----------------------|
| 33-bus  | I3   |        +14.2% |    +14.3% | +5.6 to +31.8%        |
| 33-bus  | I4   |        +41.2% |    +41.5% | +24.3 to +67.6%       |
| 69-bus  | I3   |         +0.5% |     +0.5% | +0.0 to +0.3%         |
| 69-bus  | I4   |        +24.6% |    +25.7% | +3.2 to +17.3% (m +9.7)|

**NSGA-II reaches 98.2-100.3% of the ceiling on these networks.** It was NOT
under-searching here. The MILP strictly beats it on 7/12 instances, but only by
0.1-3.6pp. M29's 69-bus I4 headline (our median exceeds [13]'s entire reported
range) is confirmed, marginally strengthened, and can now be called essentially
OPTIMAL rather than "what our metaheuristic happened to find".

This is the OPPOSITE of M35's Ckt24 finding (NSGA-II at 22.8-61.3% of ceiling),
and the contrast is the point: **NSGA-II's failure is a SCALE effect.** At 50
binaries on a 33/69-bus network it finds the optimum; at Ckt24's 150-300 binaries
on 6,058 buses it finds under half. Every benchmark-scale result in this paper
stands; only the real utility-scale one was understated.

Honest caveat: several AC-verified solutions land marginally ABOVE 100% of the
"ceiling" (up to 100.9%). GLDF is a linear APPROXIMATION, not a convex
relaxation, so its optimum is an estimate, not a bound -- on those instances the
model is slightly conservative. The AC-verified column is certified by the real
oracle; the ceiling column is not.

Run:  uv run python experiments/66_fontinele_gldf_recheck.py [--save]
"""

from __future__ import annotations

import argparse
import copy
import csv
import random
import time
from pathlib import Path

import numpy as np
import pandapower as pp
import pandapower.networks as pn
import scipy.sparse as sp
import scipy.sparse.linalg as spla
from scipy.optimize import Bounds, LinearConstraint, milp

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
_f = __import__("59_fontinele_head_to_head")

SCENARIOS = _f.SCENARIOS                       # I3, I4 -- their restrictive cases
V_MAX = _f.V_MAX                               # 1.05
V_MIN_PROTECTED = _f.V_MIN_PROTECTED           # 0.93
build_case69 = _f.build_case69
build_projects = _f.build_projects
fontinele_fifo = _f.fontinele_fifo
joint_evaluate = _f.joint_evaluate             # their FULL Algorithm 1 rule

REPO = Path(__file__).resolve().parents[1]

# exp59 (M29) reported NSGA-II gains over the same FIFO baseline, and Fontinele
# et al.'s own reported MA ranges -- printed for reference, not recomputed.
NSGA2_GAIN_PCT = {
    ("33-bus", "I3", 0): 14.2, ("33-bus", "I3", 1): 2.8, ("33-bus", "I3", 2): 20.6,
    ("33-bus", "I4", 0): 41.2, ("33-bus", "I4", 1): 43.2, ("33-bus", "I4", 2): 31.6,
    ("69-bus", "I3", 0): 1.1, ("69-bus", "I3", 1): 0.0, ("69-bus", "I3", 2): 0.5,
    ("69-bus", "I4", 0): 28.7, ("69-bus", "I4", 1): 24.6, ("69-bus", "I4", 2): 10.4,
}
FONTINELE_MA = {("33-bus", "I3"): "+5.6 to +31.8%", ("33-bus", "I4"): "+24.3 to +67.6%",
                 ("69-bus", "I3"): "+0.0 to +0.3%", ("69-bus", "I4"): "+3.2 to +17.3% (mean +9.7)"}


# ------------------------------------------------------------------ GLDF
def build_gldf(net, buses):
    """GLDF from pandapower's own solved Ybus, linearized at the base (no-DG)
    operating point. Both networks are radial with no shunts and no line
    charging, so GLDF here coincides with classic LinDistFlow -- we use it for
    one implementation across every network in the paper, not for extra power."""
    ppc = net._ppc
    Y = sp.csr_matrix(ppc["internal"]["Ybus"])
    base_mva = float(ppc["baseMVA"])
    lookup = net._pd2ppc_lookups["bus"]
    slack = int(lookup[int(net.ext_grid.bus.iloc[0])])
    pq = np.array([i for i in range(Y.shape[0]) if i != slack])

    Y_LL = sp.csc_matrix(Y[pq, :][:, pq])
    Y_LS = np.asarray(Y[pq, :][:, [slack]].todense()).ravel()
    lu = spla.splu(Y_LL)
    E = lu.solve(-Y_LS * float(net.ext_grid.vm_pu.iloc[0]))

    inv = {int(lookup[b]): b for b in net.bus.index}
    v_base = np.array([net.res_bus.vm_pu[inv[int(i)]] for i in pq]) ** 2

    pos = {int(b): i for i, b in enumerate(pq)}
    m_cols = {}
    for b in set(buses):
        j = pos.get(int(lookup[int(b)]))
        if j is None:
            continue
        e = np.zeros(len(pq)); e[j] = 1.0
        z = lu.solve(e.astype(complex))
        m_cols[int(b)] = 2.0 * np.real(E * np.conj(z) / E[j])
    return dict(v_base=v_base, m_cols=m_cols, base_mva=base_mva, pq=pq, lookup=lookup)


def gldf_milp(projects, g, v_margin_pu=0.0):
    """Voltage-only MILP (loading can't bind; the loss cap is a relaxation --
    see docstring). Fontinele's asymmetric undervoltage rule is reproduced:
    only buses already >= 0.93 pu at base carry a lower bound."""
    K = len(projects)
    A = np.zeros((len(g["pq"]), K))
    for r, p in enumerate(projects):
        col = g["m_cols"].get(int(p.bus))
        if col is None:
            continue
        A[:, r] = col * (p.p_kw / 1000.0 / g["base_mva"])   # DG: positive injection

    hi = np.maximum((V_MAX - v_margin_pu) ** 2 - g["v_base"], 0.0)
    protected = g["v_base"] >= V_MIN_PROTECTED ** 2
    lo = np.full(len(g["pq"]), -np.inf)
    lo[protected] = np.minimum((V_MIN_PROTECTED + v_margin_pu) ** 2 - g["v_base"][protected], 0.0)

    keep = np.abs(A).max(axis=1) > 1e-12
    con = LinearConstraint(A[keep], lo[keep], hi[keep])
    c = np.array([-p.p_kw for p in projects])
    res = milp(c=c, constraints=[con], integrality=np.ones(K), bounds=Bounds(0, 1),
               options={"time_limit": 120.0, "mip_rel_gap": 1e-6, "presolve": True})
    if res.x is None:
        return set(), 0.0
    x = np.round(res.x).astype(int)
    ids = {p.id for p, xi in zip(projects, x) if xi}
    return ids, float(sum(p.p_kw for p, xi in zip(projects, x) if xi))


# --------------------------------------------------- exact-oracle repair + polish
def ac_repair(net_base, projects, v_base, total_load_mw, ids: set[str], max_drops=40):
    """Steepest-descent drop against the REAL Fontinele rule (losses included).
    Not monotone in general -- DG can either raise or lower losses -- so we do not
    assume a direction: at each step we try dropping EVERY accepted project and
    keep whichever most reduces the violation (or, if any drop lands feasible, the
    feasible one with the most kW)."""
    ids = set(ids)
    by_id = {p.id: p for p in projects}
    for _ in range(max_drops):
        ok, kw, viol = joint_evaluate(net_base, projects, v_base, total_load_mw, ids)
        if ok:
            return ids, kw
        if not ids:
            return set(), 0.0
        best_feas, best_feas_kw = None, -1.0
        best_inf, best_viol = None, float("inf")
        for rid in list(ids):
            trial = ids - {rid}
            ok_t, kw_t, viol_t = joint_evaluate(net_base, projects, v_base, total_load_mw, trial)
            if ok_t and kw_t > best_feas_kw:
                best_feas, best_feas_kw = trial, kw_t
            if not ok_t and viol_t < best_viol:
                best_inf, best_viol = trial, viol_t
        if best_feas is not None:
            return best_feas, best_feas_kw
        if best_inf is None:
            return set(), 0.0
        ids = best_inf
    ok, kw, _ = joint_evaluate(net_base, projects, v_base, total_load_mw, ids)
    return (ids, kw) if ok else (set(), 0.0)


def ac_polish(net_base, projects, v_base, total_load_mw, ids: set[str], max_rounds=8):
    """Exhaustive single-add / single-swap steepest ascent against the REAL rule
    (exp62's move set). Certifies the reported accept-set as a 1-swap local
    optimum of the EXACT problem, not of the linear surrogate."""
    by_id = {p.id: p for p in projects}
    cur = set(ids)
    cur_kw = sum(by_id[i].p_kw for i in cur)
    evals = 0
    for _ in range(max_rounds):
        best, best_kw = None, cur_kw
        out = [p.id for p in projects if p.id not in cur]
        for add in out:
            kw = cur_kw + by_id[add].p_kw
            if kw > best_kw:
                evals += 1
                ok, kw_t, _ = joint_evaluate(net_base, projects, v_base, total_load_mw, cur | {add})
                if ok and kw_t > best_kw:
                    best, best_kw = cur | {add}, kw_t
        for add in out:
            for drop in list(cur):
                kw = cur_kw + by_id[add].p_kw - by_id[drop].p_kw
                if kw <= best_kw:
                    continue
                evals += 1
                trial = (cur - {drop}) | {add}
                ok, kw_t, _ = joint_evaluate(net_base, projects, v_base, total_load_mw, trial)
                if ok and kw_t > best_kw:
                    best, best_kw = trial, kw_t
        if best is None:
            break
        cur, cur_kw = best, best_kw
    return cur, cur_kw, evals


def run_instance(net_base, name, scenario, seed) -> dict:
    lo, hi = SCENARIOS[scenario]
    projects = build_projects(net_base, 50, lo, hi, seed)
    total_kw = sum(p.p_kw for p in projects)

    net = copy.deepcopy(net_base)
    pp.runpp(net)
    v_base = net.res_bus.vm_pu.copy()
    total_load_mw = net.load.p_mw.sum()

    t0 = time.time()
    fifo = fontinele_fifo(net_base, projects)
    _, fifo_kw, _ = joint_evaluate(net_base, projects, v_base, total_load_mw, fifo)

    g = build_gldf(net, [p.bus for p in projects])

    # ceiling (no margin) -- generally not achievable under the real rule
    ceil_ids, ceil_kw = gldf_milp(projects, g, 0.0)
    ok_c, _, viol_c = joint_evaluate(net_base, projects, v_base, total_load_mw, ceil_ids)

    # the METHOD: MILP -> AC repair -> AC polish, over a small margin ladder
    best_kw, best_ids, best_margin, polish_evals = 0.0, set(), float("nan"), 0
    for margin in (0.000, 0.002, 0.005):
        ids, _ = gldf_milp(projects, g, margin)
        if not ids:
            continue
        ids_r, _ = ac_repair(net_base, projects, v_base, total_load_mw, ids)
        ids_p, kw_p, ev = ac_polish(net_base, projects, v_base, total_load_mw, ids_r)
        polish_evals += ev
        if kw_p > best_kw:
            best_kw, best_ids, best_margin = kw_p, ids_p, margin
    dt = time.time() - t0

    nsga = NSGA2_GAIN_PCT.get((name, scenario, seed), float("nan"))
    nsga_kw = fifo_kw * (1 + nsga / 100) if fifo_kw else float("nan")

    return dict(
        network=name, scenario=scenario, seed=seed, total_requested_kw=total_kw,
        fifo_kw=fifo_kw, fifo_n=len(fifo),
        nsga2_ref_pct=nsga, nsga2_ref_kw=nsga_kw,
        gldf_ceiling_kw=ceil_kw, gldf_ceiling_n=len(ceil_ids),
        ceiling_ac_feasible=bool(ok_c), ceiling_ac_violation=viol_c,
        milp_kw=best_kw, milp_n=len(best_ids), milp_margin=best_margin,
        milp_vs_fifo_pct=(best_kw / fifo_kw - 1) * 100 if fifo_kw else float("nan"),
        fifo_pct_of_ceiling=fifo_kw / ceil_kw * 100 if ceil_kw else float("nan"),
        nsga2_pct_of_ceiling=nsga_kw / ceil_kw * 100 if ceil_kw else float("nan"),
        milp_pct_of_ceiling=best_kw / ceil_kw * 100 if ceil_kw else float("nan"),
        polish_evals=polish_evals, dt=dt)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--save", action="store_true")
    args = ap.parse_args()

    print("M37: re-checking the Fontinele head-to-head (Sec. 7.7) with the GLDF-MILP\n")
    nets = {"33-bus": pn.case33bw(), "69-bus": build_case69()}

    rows = []
    for name, net_base in nets.items():
        for scenario in SCENARIOS:
            print(f"{name} {scenario}   [Fontinele et al. MA: {FONTINELE_MA[(name, scenario)]}]")
            for seed in range(args.seeds):
                r = run_instance(net_base, name, scenario, seed)
                rows.append(r)
                print(f"   seed {seed}: FIFO {r['fifo_kw']:8.1f} kW ({r['fifo_n']:2d}/50)"
                      f" = {r['fifo_pct_of_ceiling']:5.1f}% of ceiling")
                print(f"            NSGA-II (M29) {r['nsga2_ref_pct']:+6.1f}%"
                      f" = {r['nsga2_pct_of_ceiling']:5.1f}% of ceiling"
                      f"   |   GLDF-MILP {r['milp_vs_fifo_pct']:+6.1f}%"
                      f" ({r['milp_n']:2d}/50, AC-verified) = {r['milp_pct_of_ceiling']:5.1f}% of ceiling"
                      f"   [ceiling {r['gldf_ceiling_kw']:.0f} kW]  {r['dt']:.0f}s")
            print()

    import statistics as st
    print("=" * 92)
    for name in nets:
        for scenario in SCENARIOS:
            sub = [r for r in rows if r["network"] == name and r["scenario"] == scenario]
            n2 = st.median(r["nsga2_ref_pct"] for r in sub)
            mp = st.median(r["milp_vs_fifo_pct"] for r in sub)
            print(f"  {name:7s} {scenario}  median gain vs FIFO:  NSGA-II (M29) {n2:+6.1f}%"
                  f"   GLDF-MILP {mp:+6.1f}%   [Fontinele {FONTINELE_MA[(name, scenario)]}]")

    if args.save and rows:
        out = REPO / "outputs"
        out.mkdir(exist_ok=True)
        path = out / "m37_fontinele_gldf_recheck.csv"
        fields = sorted({k for r in rows for k in r})
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader(); w.writerows(rows)
        print(f"\nsaved {path}")


if __name__ == "__main__":
    main()
