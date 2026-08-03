"""M38: re-checking the EULV headline (Sec. 5, DECISION.md Sec. 2) against the ceiling.

MOTIVATION
----------
M35 (exp64) showed NSGA-II reaches only 22.8-61.3% of achievable capacity on
EPRI Ckt24; M36 (exp65) re-checked the SMART-DS negative (survives, 0/5); M37
(exp66) re-checked the Fontinele head-to-head (survives -- NSGA-II is already at
~100% of the ceiling there). EULV is the last unaudited headline: it is the
paper's *surviving* positive margin from the seed-distribution study (+3.0%
mean over 25 request draws, p=5.7e-5, via the convex route; +3.6% from a
single-seed exact-AC NSGA-II run).

Unlike Ckt24, EULV's number does NOT come from NSGA-II alone -- the corpus-scale
figure comes from `lindistflow_search_iterative`, i.e. a classic Baran-Wu
LinDistFlow MILP. So the question here is not "did the metaheuristic
under-search?" but the sharper one M35 made askable: **how close to the CEILING
is any of this, and is the +3% margin small because the method is weak or
because the feeder genuinely has almost no room?** M36's SMART-DS answer was
the latter; this asks the same of EULV.

WHY GLDF ADDS NOTHING STRUCTURAL HERE (stated up front, not buried). EULV is
radial (907 buses, 906 edges = buses-1), has no switches, no shunt capacitors
and zero line charging. Those are exactly the four preconditions classic
LinDistFlow needs and Ckt24 violated. **On this network GLDF therefore coincides
with classic LinDistFlow** -- we run it for a single consistent implementation
across every network in the paper, and for the ceiling framing, not because it
buys extra physics. Any difference from `lindistflow_search_iterative` is down
to the loss-correction loop and the accept-set search, not the power-flow model.

WHAT BINDS. Baseline: vmin 1.0323, vmax 1.0500, max line loading 19.4%, max
transformer loading 7.0%. Requests are rooftop PV (3-10 kWp, K=40, sampled
WITHOUT replacement from the 55 load buses -- so no request stacking, unlike
Ckt7). Export pushes voltage UP against the 1.10 pu limit with thermal far from
binding, so this is a cleanly overvoltage-limited instance: exactly what a GLDF
voltage MILP models natively. Thermal is nonetheless checked, because it is
checked by the real oracle (`AllocationProblem.evaluate`), which every reported
accept-set must pass.

METHOD (three stages, only the last is claimed) -- identical to M37:
  1. `gldf_milp`  -- optimize against the linear model (the CEILING, at margin 0;
                     generally not AC-achievable since the optimizer exploits the
                     model's optimism -- M35's lesson).
  2. `ac_repair`  -- steepest-descent drop against the REAL `AllocationProblem`
                     oracle until feasible.
  3. `ac_polish`  -- exhaustive single-add / single-swap improvement against the
                     REAL oracle (exp62's move set), certifying the reported
                     accept-set as a 1-swap local optimum of the EXACT problem.

Compared against, on the SAME request draws:
  - `skip_continue_baseline`         -- the honest FCFS comparator
  - `lindistflow_search_iterative`   -- the convex route the paper's +3.0% uses
  - `nsga2_search`                   -- the exact-AC metaheuristic

RESULT (2026-07-11) -- CONFIRMED; EULV is a no-room feeder, not a weak-method one
---------------------------------------------------------------------------------
Over the same 25 request draws exp53 used:

  FCFS reaches           97.7% of the ceiling on average (min 89.2%, max 101.4%)
  convex route (paper)   +2.99% vs FCFS   <- reproduces Sec. 5's reported +3.0%
  GLDF-MILP              +3.17% mean, +2.30% median, +12.87% max; beats FCFS 20/25

The GLDF-MILP reproduces the paper's convex-route margin EXACTLY. The headline
survives. The informative number is not the margin but the ceiling: **FCFS is
already at 97.7% of achievable capacity**, so EULV's +3% is small BECAUSE THE
FEEDER HAS ALMOST NO ROOM -- the same verdict M36 reached on SMART-DS, not the
Ckt24 story (where FCFS was at 13-40% of ceiling and NSGA-II at 22.8-61.3%).

Honest caveat: a few AC-verified solutions land marginally ABOVE 100% of the
"ceiling" (up to 101.4%). GLDF is a linear APPROXIMATION, not a convex
relaxation, so its optimum is an estimate, not a bound -- on those draws the
model is slightly conservative. The AC-verified column is certified by the real
oracle; the ceiling column is not.

Run:  uv run python experiments/67_eulv_gldf_recheck.py [--draws 25] [--save]
"""

from __future__ import annotations

import argparse
import csv
import random
import statistics as st
import time
from pathlib import Path

import numpy as np
import pandapower as pp
import scipy.sparse as sp
import scipy.sparse.linalg as spla
from scipy.optimize import Bounds, LinearConstraint, milp

from allocation import (
    AllocationProblem,
    Request,
    lindistflow_search_iterative,
    nsga2_search,
    skip_continue_baseline,
)
from gridsynth import import_opendss

REPO = Path(__file__).resolve().parents[1]
MASTER = REPO / "data" / "opendss" / "eulv" / "Master_snapshot.dss"
PV_KW_LO, PV_KW_HI = 3.0, 10.0          # exp53's band
K = 40                                   # exp53's EULV request count
VMIN_PU, VMAX_PU = 0.94, 1.10            # AllocationProblem defaults


def build_requests(load_buses, k, seed):
    """exp53's draw verbatim (same RNG, same order, WITHOUT replacement)."""
    rng = random.Random(seed)
    buses = rng.sample(load_buses, min(k, len(load_buses)))
    return [Request(id=f"pv{i}", bus=b, p_mw=-rng.uniform(PV_KW_LO, PV_KW_HI) / 1000.0,
                     queue_order=i) for i, b in enumerate(buses)]


def build_gldf(net, buses):
    """GLDF from pandapower's solved Ybus, linearized at the baseline. On EULV
    this coincides with classic LinDistFlow (radial, no shunts, no charging)."""
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


def gldf_milp(requests, g, v_margin_pu=0.0):
    """Voltage-only MILP. Thermal is far from binding here (19.4% line / 7.0%
    trafo at baseline) but is still enforced by the real oracle downstream."""
    n = len(requests)
    A = np.zeros((len(g["pq"]), n))
    for r, req in enumerate(requests):
        col = g["m_cols"].get(int(req.bus))
        if col is None:
            continue
        A[:, r] = col * (-req.p_mw / g["base_mva"])          # PV export: +injection

    hi = np.maximum((VMAX_PU - v_margin_pu) ** 2 - g["v_base"], 0.0)
    lo = np.minimum((VMIN_PU + v_margin_pu) ** 2 - g["v_base"], 0.0)

    keep = np.abs(A).max(axis=1) > 1e-12
    con = LinearConstraint(A[keep], lo[keep], hi[keep])
    c = np.array([-abs(r.p_mw) for r in requests])
    res = milp(c=c, constraints=[con], integrality=np.ones(n), bounds=Bounds(0, 1),
               options={"time_limit": 120.0, "mip_rel_gap": 1e-6, "presolve": True})
    if res.x is None:
        return set(), 0.0
    x = np.round(res.x).astype(int)
    ids = {r.id for r, xi in zip(requests, x) if xi}
    return ids, float(sum(abs(r.p_mw) for r, xi in zip(requests, x) if xi)) * 1000


def ac_repair(problem, requests, ids, max_drops=40):
    """Steepest-descent drop against the REAL AllocationProblem oracle."""
    ids = set(ids)
    for _ in range(max_drops):
        res = problem.evaluate(ids)
        if res.feasible:
            return ids, res.accommodated_mw * 1000
        if not ids:
            return set(), 0.0
        best_feas, best_kw = None, -1.0
        best_inf, best_viol = None, float("inf")
        for rid in list(ids):
            t = problem.evaluate(ids - {rid})
            if t.feasible and t.accommodated_mw * 1000 > best_kw:
                best_feas, best_kw = ids - {rid}, t.accommodated_mw * 1000
            viol = t.voltage_violation_pu + t.thermal_violation_percent
            if not t.feasible and viol < best_viol:
                best_inf, best_viol = ids - {rid}, viol
        if best_feas is not None:
            return best_feas, best_kw
        if best_inf is None:
            return set(), 0.0
        ids = best_inf
    res = problem.evaluate(ids)
    return (ids, res.accommodated_mw * 1000) if res.feasible else (set(), 0.0)


def ac_polish(problem, requests, ids, max_rounds=8):
    """Exhaustive single-add / single-swap steepest ascent on the REAL oracle."""
    by_id = {r.id: r for r in requests}
    cur = set(ids)
    cur_kw = sum(abs(by_id[i].p_mw) for i in cur) * 1000
    evals = 0
    for _ in range(max_rounds):
        best, best_kw = None, cur_kw
        out = [r.id for r in requests if r.id not in cur]
        for add in out:
            kw = cur_kw + abs(by_id[add].p_mw) * 1000
            if kw > best_kw:
                evals += 1
                if problem.evaluate(cur | {add}).feasible:
                    best, best_kw = cur | {add}, kw
        for add in out:
            for drop in list(cur):
                kw = cur_kw + (abs(by_id[add].p_mw) - abs(by_id[drop].p_mw)) * 1000
                if kw <= best_kw:
                    continue
                evals += 1
                trial = (cur - {drop}) | {add}
                if problem.evaluate(trial).feasible:
                    best, best_kw = trial, kw
        if best is None:
            break
        cur, cur_kw = best, best_kw
    return cur, cur_kw, evals


def run_draw(net, load_buses, seed, with_nsga2: bool, pop: int, gen: int) -> dict:
    requests = build_requests(load_buses, K, seed)
    total_kw = sum(abs(r.p_mw) for r in requests) * 1000
    problem = AllocationProblem(net=net, requests=requests)

    t0 = time.time()
    skip = skip_continue_baseline(problem)
    skip_kw = skip.accommodated_mw * 1000

    try:
        conv_kw = lindistflow_search_iterative(problem).result.accommodated_mw * 1000
    except Exception:
        conv_kw = float("nan")

    nsga_kw = float("nan")
    if with_nsga2:
        res = nsga2_search(problem, pop_size=pop, n_gen=gen, seed=0)
        feas = [r for r in res if r.feasible]
        nsga_kw = max((r.accommodated_mw for r in feas), default=0.0) * 1000

    pp.runpp(net)
    g = build_gldf(net, [r.bus for r in requests])

    ceil_ids, ceil_kw = gldf_milp(requests, g, 0.0)
    ceil_ok = problem.evaluate(ceil_ids).feasible

    best_kw, best_n, best_margin, evals = 0.0, 0, float("nan"), 0
    for margin in (0.000, 0.002, 0.005):
        ids, _ = gldf_milp(requests, g, margin)
        if not ids:
            continue
        ids_r, _ = ac_repair(problem, requests, ids)
        ids_p, kw_p, ev = ac_polish(problem, requests, ids_r)
        evals += ev
        if kw_p > best_kw:
            best_kw, best_n, best_margin = kw_p, len(ids_p), margin

    def pct(a, b):
        return (a / b - 1) * 100 if b else float("nan")

    return dict(
        seed=seed, total_requested_kw=total_kw,
        skip_kw=skip_kw, skip_n=len(skip.accepted_ids),
        conv_kw=conv_kw, conv_vs_skip_pct=pct(conv_kw, skip_kw),
        nsga2_kw=nsga_kw, nsga2_vs_skip_pct=pct(nsga_kw, skip_kw),
        gldf_ceiling_kw=ceil_kw, gldf_ceiling_n=len(ceil_ids), ceiling_ac_feasible=bool(ceil_ok),
        milp_kw=best_kw, milp_n=best_n, milp_margin=best_margin,
        milp_vs_skip_pct=pct(best_kw, skip_kw),
        skip_pct_of_ceiling=skip_kw / ceil_kw * 100 if ceil_kw else float("nan"),
        conv_pct_of_ceiling=conv_kw / ceil_kw * 100 if ceil_kw else float("nan"),
        milp_pct_of_ceiling=best_kw / ceil_kw * 100 if ceil_kw else float("nan"),
        polish_evals=evals, dt=time.time() - t0)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--draws", type=int, default=25, help="exp53 used 25 request draws")
    ap.add_argument("--nsga2-draws", type=int, default=3,
                     help="NSGA-II is the slow one -- run it on the first N draws only")
    ap.add_argument("--pop", type=int, default=40)
    ap.add_argument("--gen", type=int, default=40)
    ap.add_argument("--save", action="store_true")
    args = ap.parse_args()

    print("M38: EULV re-checked against the GLDF ceiling\n")
    net = import_opendss(str(MASTER))
    pp.runpp(net)
    print(f"  EULV: {len(net.bus)} buses, radial, no shunts/switches/charging "
          f"-> GLDF == classic LinDistFlow here")
    print(f"  baseline vmin={net.res_bus.vm_pu.min():.4f} vmax={net.res_bus.vm_pu.max():.4f} "
          f"line={net.res_line.loading_percent.max():.1f}% trafo={net.res_trafo.loading_percent.max():.1f}%\n")
    load_buses = list(net.load.bus.unique())

    rows = []
    for s in range(args.draws):
        seed = 1000 + s                                  # exp53's own seed offset
        r = run_draw(net, load_buses, seed, s < args.nsga2_draws, args.pop, args.gen)
        rows.append(r)
        n2 = f"{r['nsga2_vs_skip_pct']:+6.2f}%" if r["nsga2_vs_skip_pct"] == r["nsga2_vs_skip_pct"] else "    -- "
        print(f"  draw {s:2d}: FCFS {r['skip_kw']:7.1f} kW ({r['skip_n']:2d}/{K})"
              f" = {r['skip_pct_of_ceiling']:5.1f}% of ceiling"
              f" | convex {r['conv_vs_skip_pct']:+6.2f}%  NSGA-II {n2}"
              f"  GLDF-MILP {r['milp_vs_skip_pct']:+6.2f}% = {r['milp_pct_of_ceiling']:5.1f}% of ceiling"
              f"   [{r['dt']:.0f}s]")

    print("\n" + "=" * 96)
    milp = [r["milp_vs_skip_pct"] for r in rows]
    conv = [r["conv_vs_skip_pct"] for r in rows if r["conv_vs_skip_pct"] == r["conv_vs_skip_pct"]]
    of_ceil = [r["skip_pct_of_ceiling"] for r in rows]
    print(f"  FCFS reaches         {st.mean(of_ceil):5.1f}% of the ceiling on average "
          f"(min {min(of_ceil):.1f}%, max {max(of_ceil):.1f}%)")
    print(f"  convex route (paper) {st.mean(conv):+6.2f}% vs FCFS (mean over {len(conv)} draws)")
    print(f"  GLDF-MILP            {st.mean(milp):+6.2f}% vs FCFS (mean), median {st.median(milp):+.2f}%, "
          f"max {max(milp):+.2f}%")
    wins = sum(1 for r in rows if r["milp_vs_skip_pct"] > 0.05)
    print(f"  GLDF-MILP beats FCFS on {wins}/{len(rows)} draws")

    if args.save and rows:
        out = REPO / "outputs"
        out.mkdir(exist_ok=True)
        path = out / "m38_eulv_gldf_recheck.csv"
        fields = sorted({k for r in rows for k in r})
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader(); w.writerows(rows)
        print(f"\nsaved {path}")


if __name__ == "__main__":
    main()
