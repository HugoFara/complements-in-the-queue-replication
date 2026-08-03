"""M36: does SMART-DS's "joint search finds 0/5" negative (M27/M28, Sec. 7.4 +
Appendix B) survive a method that actually reaches the ceiling?

MOTIVATION
----------
M35 (exp64) showed NSGA-II reaches only 22.8-61.3% of achievable capacity on
EPRI Ckt24, while a Generalized-LinDistFlow MILP reaches 74.0-90.7%. That
casts doubt on every NSGA-II-derived number in this paper -- and the most
load-bearing one is the SMART-DS negative: `nsga2_search_dss` beat
`skip_continue_baseline_dss` on 0/5 feeders (M27), and M28 confirmed that even
a *seeded* NSGA-II found exactly 0.0% beyond skip-continue on 5/5. That was
read as "genuinely no room." If NSGA-II is systematically leaving half the
capacity on the table, that reading needs re-testing, not trusting.

RESULT UP FRONT: the negative SURVIVES, and is now much stronger -- but the
decisive argument turned out to need no power-flow model at all.

1. A RIGOROUS, MODEL-FREE CEILING
---------------------------------
No method, however optimal, can accept more than ALL the requests. So
skip-continue's seated kW against the total requested kW bounds every possible
gain, exactly, with zero modelling:

    feeder              total_req   skip_kw   accepted   MAX possible gain
    uhs2..udt10746        469.6      437.4     38/40          +7.4%
    uhs2..udt12473        469.6      439.1     38/40          +6.9%
    uhs4..udt11389        480.2      480.2     40/40           0.0%   <- unbound
    uhs4..udt12982        484.6      472.5     39/40          +2.6%
    uhs7..udt9675         464.4      436.9     38/40          +6.3%

Skip-continue already seats 38-40 of 40 requests. **Even a perfect oracle wins
at most 0-7.4% here, and on one feeder literally nothing** -- against EPRI
Ckt24, where FCFS left +150% to +490% on the table (M35). The M27/M28 negative
therefore cannot be a search artifact of any consequence: there is structurally
almost nothing to find. This is a hard bound, not an estimate.

2. HOW MUCH OF THAT <=7.4% IS ACTUALLY ATTAINABLE? -- the GLDF-MILP
--------------------------------------------------------------------
Same model as M35 (Huang/Cui/Zhou/Bernstein, IEEE CDC 2021, arXiv:2104.02118):

    v = diag(conj(E)) E + M p + N q + Lambda,   Z := Y_LL^-1,
    M := 2 Re( diag(E) conj(Z) diag(E)^-1 )

but built from OpenDSS's own Y rather than pandapower's, since the SMART-DS
corpus runs on the `AllocationProblemDSS` (OpenDSS-direct) oracle. Two
OpenDSS-specific things that had to be got right, both found by the correctness
gate rather than assumed:

  - **Loads must be OUT of Y.** GLDF treats loads as power INJECTIONS, but
    OpenDSS bakes each load's Norton admittance into its system Y. Building Y
    from a context with every load DISABLED (lines/transformers/capacitors
    retained) fixes this -- verified by checking that E reproduces the
    loads-disabled AC solve (max |dV| = 5e-3 V).
  - **`max_loading_percent=1e6`** in this corpus (exp57/58's own setting), i.e.
    thermal is effectively OFF. Voltage is the only binding constraint, so the
    MILP needs only the GLDF voltage rows -- no thermal model (unlike M35's
    Ckt24, where thermal genuinely bound).

Requests here are EV *demand* (6-17 kW, positive), not PV export, so feasibility
is MONOTONE (adding load only depresses voltage) and the binding constraint is
undervoltage.

**Disclosed model-accuracy limit.** The correctness gate (single-node injection
scaling sweep) shows GLDF here does NOT converge to zero error as the
perturbation vanishes: it under-predicts the voltage drop by a persistent
~3.6-6%. Diagnosed, not hand-waved -- GLDF drops the feedback through the
EXISTING constant-power loads, which have negative incremental admittance, so
the real network is weaker than GLDF's load-free Y believes. (Ruled out as
causes: load Norton admittances in Y -- fixed above; regulator retapping -- these
feeders have 0 regulators.) On Ckt24 this error was 1-3 mpu and harmless; here
it is comparable in size to the entire ~7% of available room, so the MILP's
*proposals* are not trustworthy on their own. We therefore never trust them:
every accept-set is AC-reverified through the real `AllocationProblemDSS` oracle
and greedily repaired until it passes. The consequence is an ASYMMETRIC claim,
stated honestly:
  - a positive result WOULD be trustworthy (it is AC-verified);
  - a zero result is weaker evidence than on Ckt24 (it could partly reflect
    model error) -- which is exactly why findings 1 and 3, which need no model,
    carry the argument.

3. AN EXACT-ORACLE CHECK WITH NO MODEL ERROR AT ALL
---------------------------------------------------
Because the GLDF error is comparable to the available room, "the MILP found
nothing" is not on its own conclusive. So `ac_swap_search` enumerates the FULL
single-swap + single-add neighbourhood of skip-continue's accept-set directly
against the REAL AC oracle -- exhaustively, no model. With 40 requests and ~38
accepted that is only ~70 AC solves. This is the move set exp62 (Sec. 7.10)
identified as the one this problem actually needs.

RESULT (2026-07-11) -- the negative holds on all three checks, 0/5 each
-----------------------------------------------------------------------
| feeder          | skip-continue | model-free | seeded  | GLDF- | exact-AC |
|                 |               | ceiling    | NSGA-II | MILP  | 1-swap   |
|-----------------|---------------|-----------:|--------:|------:|---------:|
| uhs2..udt10746  | 437.4 (38/40) |      +7.4% |   +0.0% | +0.0% |    +0.0% |
| uhs2..udt12473  | 439.1 (38/40) |      +6.9% |   +0.0% | +0.0% |    +0.0% |
| uhs4..udt11389  | 480.2 (40/40) |      +0.0% |   +0.0% | +0.0% |    +0.0% |
| uhs4..udt12982  | 472.5 (39/40) |      +2.6% |   +0.0% | +0.0% |    +0.0% |
| uhs7..udt9675   | 436.9 (38/40) |      +6.3% |   +0.0% | +0.0% |    +0.0% |

The GLDF-MILP beats skip-continue on **0/5**; the exhaustive exact-AC 1-swap
search beats it on **0/5**. On every feeder the MILP's own optimum, at any
margin where its answer AC-verifies, converges to EXACTLY skip-continue's
accept-set. Skip-continue's allocation is therefore a certified local optimum
of the exact problem under the swap move set, and it sits within 0-7.4% of an
absolute, model-free ceiling.

Worth recording as the mechanism, since it is the interesting part: at zero
margin the MILP *wants* 39 of 40 requests on udt10746 (452.6 kW, +3.5%), but the
real AC oracle rejects that set (vmin = 0.9369 < 0.94) -- GLDF's ~4-6% optimism,
exactly as the gate predicted. The apparent room is a model artifact; it is not
there in the real physics.

CONCLUSION: M27/M28's SMART-DS negative SURVIVES contact with a method that
demonstrably reaches 74-90% of the ceiling elsewhere (M35). It is *strengthened*:
the earlier claim was "our search found no room"; the claim now is "there is
almost no room to find -- at most 0-7.4% even for a perfect oracle, and 0.0% is
attainable under an exhaustive exact-AC swap search." This is the OPPOSITE of
what M35 did to Ckt24 (where NSGA-II was hiding a +150-490% opportunity), and
the contrast is itself the point: the regime map's *negative* half is real
physics, not a search artifact, while its *positive* half was understated by the
search. Both halves get sharper.

Run:  uv run python experiments/65_smartds_gldf_recheck.py [--save]
"""

from __future__ import annotations

import argparse
import csv
import glob
import os
import random
import time
from pathlib import Path

import numpy as np
import opendssdirect as dss_base
import scipy.sparse as sp
import scipy.sparse.linalg as spla
from scipy.optimize import Bounds, LinearConstraint, milp

from allocation import (
    AllocationProblemDSS,
    DSSRequest,
    skip_continue_baseline_dss,
)

REPO = Path(__file__).resolve().parents[1]
CACHE = REPO / "cache" / "smartds"
KW_LO, KW_HI = 6.0, 17.0          # exp57/58's EV band
VMIN_PU, VMAX_PU = 0.94, 1.10     # AllocationProblemDSS defaults
BIG_LOADING = 1e6                 # exp57/58: thermal effectively off

# M28's own reported numbers, for direct comparison (outputs/m28_*.csv)
SEEDED_NSGA2_VS_SKIP = {"uhs2_1247--udt10746": 0.0, "uhs2_1247--udt12473": 0.0,
                         "uhs4_1247--udt11389": 0.0, "uhs4_1247--udt12982": 0.0,
                         "uhs7_1247--udt9675": 0.0}


# ---------------------------------------------------------------- exp57/58 setup
def screen_feasible(masters):
    out = []
    for m in masters:
        try:
            if AllocationProblemDSS(master_path=m, requests=[],
                                     max_loading_percent=BIG_LOADING).evaluate([]).feasible:
                out.append(m)
        except Exception:
            pass
    return out


def candidate_attach_points(master_path, exclude):
    ctx = dss_base.NewContext()
    ctx.Command("Clear")
    ctx.Command(f'Redirect "{master_path}"')
    ctx.Solution.Solve()
    pts = []
    for name in ctx.Circuit.AllBusNames():
        if name in exclude:
            continue
        ctx.Circuit.SetActiveBus(name)
        for node in ctx.Bus.Nodes():
            pts.append((name, node))
    return pts


def build_requests(points, k, seed):
    rng = random.Random(seed)
    sample = rng.sample(points, min(k, len(points)))
    return [DSSRequest(id=f"ev{i}", bus=b, phase=ph, p_kw=rng.uniform(KW_LO, KW_HI),
                        queue_order=i) for i, (b, ph) in enumerate(sample)]


def pick_nsga_subset(feasible, n):
    sized = sorted(feasible, key=lambda m: os.path.getsize(str(Path(m).parent / "Lines.dss"))
                   if (Path(m).parent / "Lines.dss").exists() else 0)
    idxs = sorted({int(round(i)) for i in
                   [j * (len(sized) - 1) / max(n - 1, 1) for j in range(n)]})
    return {sized[i] for i in idxs}


# ---------------------------------------------------------------- GLDF from OpenDSS
def _ynode_v(ctx):
    raw = np.asarray(ctx.Circuit.YNodeVArray(), dtype=float)
    return raw[0::2] + 1j * raw[1::2]


def build_gldf_dss(master: str, req_nodes: list[str]):
    """GLDF pieces from OpenDSS's own Y. `req_nodes` are 'BUS.PHASE' keys.

    Y is built with every LOAD DISABLED: GLDF wants loads as power injections,
    but OpenDSS folds each load's Norton admittance into its system Y. Lines,
    transformers and capacitors stay in, as they must.
    """
    base = dss_base.NewContext()
    base.Command("Clear")
    base.Command(f'Redirect "{master}"')
    base.Solution.Solve()

    cy = dss_base.NewContext()
    cy.Command("Clear")
    cy.Command(f'Redirect "{master}"')
    i = cy.Loads.First()
    while i:
        cy.Command(f"Disable Load.{cy.Loads.Name()}")
        i = cy.Loads.Next()
    cy.Solution.Solve()

    data, idx, ptr = cy.YMatrix.getYsparse()
    n = len(ptr) - 1
    Y = sp.csc_matrix((data, idx, ptr), shape=(n, n))
    order = [s.upper() for s in cy.Circuit.YNodeOrder()]

    cy.Vsources.First()
    cy.Circuit.SetActiveElement(f"Vsource.{cy.Vsources.Name()}")
    src_bus = cy.CktElement.BusNames()[0].split(".")[0].upper()

    slack = [k for k, nm in enumerate(order) if nm.split(".")[0] == src_bus]
    pq = np.array([k for k in range(n) if k not in set(slack)])

    V = _ynode_v(base)                       # baseline, loads ENABLED
    lu = spla.splu(sp.csc_matrix(Y[pq, :][:, pq]))
    E = lu.solve(-(np.asarray(Y[pq, :][:, slack].todense()) @ V[slack]))

    pos = {int(b): k for k, b in enumerate(pq)}
    v_base = np.abs(V[pq]) ** 2              # volts^2, exact at baseline

    # per-node voltage base (LN volts), for the pu limits
    vbase = np.zeros(len(pq))
    for k, node_i in enumerate(pq):
        bus = order[node_i].split(".")[0]
        base.Circuit.SetActiveBus(bus)
        vbase[k] = base.Bus.kVBase() * 1000.0

    # only the columns of Z at request nodes are needed
    m_cols: dict[str, np.ndarray] = {}
    for key in set(req_nodes):
        if key not in order:
            continue
        jp = pos.get(order.index(key))
        if jp is None:
            continue
        e = np.zeros(len(pq)); e[jp] = 1.0
        z = lu.solve(e.astype(complex))
        m_cols[key] = 2.0 * np.real(E * np.conj(z) / E[jp])

    return dict(v_base=v_base, vbase=vbase, m_cols=m_cols, order=order, pq=pq, pos=pos)


def gldf_milp(problem: AllocationProblemDSS, requests, g, v_margin_pu: float = 0.0):
    """maximize sum p_kw x_r  s.t.  vmin^2 <= v_base + A x <= vmax^2 (per node).

    Thermal is absent because this corpus disables it (max_loading_percent=1e6).
    Bounds are CLAMPED non-worsening (M35's lesson): a node already outside the
    tightened band may not be pushed further out, but need not be brought back.
    """
    K = len(requests)
    A = np.zeros((len(g["pq"]), K))
    for r, req in enumerate(requests):
        key = f"{req.bus.upper()}.{req.phase}"
        col = g["m_cols"].get(key)
        if col is None:
            continue
        A[:, r] = col * (-req.p_kw * 1000.0)          # load -> negative injection (W)

    hi = ((VMAX_PU - v_margin_pu) * g["vbase"]) ** 2 - g["v_base"]
    lo = ((VMIN_PU + v_margin_pu) * g["vbase"]) ** 2 - g["v_base"]
    hi = np.maximum(hi, 0.0)
    lo = np.minimum(lo, 0.0)

    keep = np.abs(A).max(axis=1) > 1e-9
    if not keep.any():
        return {r.id for r in requests}, sum(r.p_kw for r in requests)

    con = LinearConstraint(A[keep], lo[keep], hi[keep])
    c = np.array([-r.p_kw for r in requests])
    res = milp(c=c, constraints=[con], integrality=np.ones(K), bounds=Bounds(0, 1),
               options={"time_limit": 120.0, "mip_rel_gap": 1e-6, "presolve": True})
    if res.x is None:
        return set(), 0.0
    x = np.round(res.x).astype(int)
    ids = {r.id for r, xi in zip(requests, x) if xi}
    return ids, float(sum(r.p_kw for r, xi in zip(requests, x) if xi))


def repair(problem: AllocationProblemDSS, requests, g, ids: set[str], max_drops=25):
    """AC-verify, and while infeasible drop the request giving the most
    undervoltage relief per kW surrendered (ranked by GLDF's own sensitivity at
    the worst node). Feasibility is monotone here (loads only depress voltage),
    so dropping always helps."""
    ids = set(ids)
    by_id = {r.id: r for r in requests}
    for _ in range(max_drops):
        res = problem.evaluate(ids)
        if res.feasible:
            return ids, res.accommodated_mw * 1000.0
        if not ids:
            return set(), 0.0
        # worst node = the one with the lowest voltage; rank by relief-per-kW
        worst = int(np.argmin(g["v_base"] / (g["vbase"] ** 2)))
        best, best_score = None, -np.inf
        for rid in ids:
            r = by_id[rid]
            col = g["m_cols"].get(f"{r.bus.upper()}.{r.phase}")
            relief = abs(float(col[worst])) if col is not None else 0.0
            score = relief / r.p_kw if r.p_kw else 0.0
            if score > best_score:
                best, best_score = rid, score
        if best is None:
            return set(), 0.0
        ids.discard(best)
    res = problem.evaluate(ids)
    return (ids, res.accommodated_mw * 1000.0) if res.feasible else (set(), 0.0)


def ac_swap_search(problem: AllocationProblemDSS, requests, start: set[str],
                    max_rounds: int = 6) -> tuple[set[str], float, int]:
    """EXHAUSTIVE single-swap / single-add steepest-ascent search on the REAL AC
    oracle -- no power-flow model, hence no model error.

    Why this exists: GLDF's ~4-6% sensitivity error on these feeders (see module
    docstring) is comparable to the entire ~7% of available room, so a "the MILP
    found nothing" result is not on its own conclusive. This is. With 40 requests
    and ~38 accepted, the full 1-swap neighbourhood is only ~38x2 = 76 accept-sets
    plus the adds -- small enough to enumerate EXHAUSTIVELY against the exact
    oracle. If no single swap or add improves the seated kW, skip-continue's
    allocation is a certified local optimum of the exact problem under the move
    set that exp62 showed is the one this problem actually needs.

    Feasibility is monotone here (EV load only depresses voltage), so a pure ADD
    is only possible where headroom genuinely remains.
    """
    by_id = {r.id: r for r in requests}
    cur = set(start)
    cur_kw = sum(by_id[i].p_kw for i in cur)
    evals = 0

    for _ in range(max_rounds):
        best, best_kw = None, cur_kw
        out = [r.id for r in requests if r.id not in cur]

        for add in out:                                  # pure adds
            trial = cur | {add}
            kw = cur_kw + by_id[add].p_kw
            if kw <= best_kw:
                continue
            evals += 1
            if problem.evaluate(trial).feasible:
                best, best_kw = trial, kw

        for add in out:                                  # swaps: drop one, add one
            for drop in list(cur):
                kw = cur_kw + by_id[add].p_kw - by_id[drop].p_kw
                if kw <= best_kw:
                    continue                             # can't improve; skip the solve
                trial = (cur - {drop}) | {add}
                evals += 1
                if problem.evaluate(trial).feasible:
                    best, best_kw = trial, kw

        if best is None:
            break                                        # certified 1-swap local optimum
        cur, cur_kw = best, best_kw

    return cur, cur_kw, evals


def run_feeder(master: str, k: int, seed: int) -> dict:
    name = os.path.basename(os.path.dirname(master))
    t0 = time.time()
    src = f"{name.split('--')[1]}-{name.split('--')[0]}x"
    pts = candidate_attach_points(master, {src})
    requests = build_requests(pts, k, seed)
    total_kw = sum(r.p_kw for r in requests)

    problem = AllocationProblemDSS(master_path=master, requests=requests,
                                    max_loading_percent=BIG_LOADING)
    skip = skip_continue_baseline_dss(problem)
    skip_kw = skip.accommodated_mw * 1000.0

    g = build_gldf_dss(master, [f"{r.bus.upper()}.{r.phase}" for r in requests])

    best_kw, best_n, best_margin = 0.0, 0, float("nan")
    for margin in (0.000, 0.002, 0.005, 0.010):
        ids, _ = gldf_milp(problem, requests, g, v_margin_pu=margin)
        if not ids:
            continue
        ids_r, kw_r = repair(problem, requests, g, ids)
        if kw_r > best_kw:
            best_kw, best_n, best_margin = kw_r, len(ids_r), margin

    # exact-oracle check, no model error (see `ac_swap_search`)
    ls_ids, ls_kw, ls_evals = ac_swap_search(problem, requests, skip.accepted_ids)

    return dict(
        name=name, k=k, total_kw=total_kw,
        skip_kw=skip_kw, skip_acc=len(skip.accepted_ids),
        ceiling_free_pct=(total_kw / skip_kw - 1) * 100 if skip_kw else float("nan"),
        milp_kw=best_kw, milp_acc=best_n, milp_margin=best_margin,
        milp_vs_skip_pct=(best_kw / skip_kw - 1) * 100 if skip_kw else float("nan"),
        acswap_kw=ls_kw, acswap_acc=len(ls_ids), acswap_evals=ls_evals,
        acswap_vs_skip_pct=(ls_kw / skip_kw - 1) * 100 if skip_kw else float("nan"),
        seeded_nsga2_vs_skip_pct=SEEDED_NSGA2_VS_SKIP.get(name, float("nan")),
        dt=time.time() - t0)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--k", type=int, default=40)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--nsga-subset", type=int, default=5)
    ap.add_argument("--only", type=str, default=None)
    ap.add_argument("--save", action="store_true")
    args = ap.parse_args()

    masters = sorted(glob.glob(str(CACHE / "uhs*" / "Master.dss")))
    print("M36: re-checking SMART-DS's 0/5 negative with a GLDF-MILP\n")
    feasible = screen_feasible(masters)
    subset = pick_nsga_subset(feasible, args.nsga_subset)
    if args.only:
        subset = {m for m in subset if args.only in m}
    print(f"{len(feasible)} baseline-feasible feeders; re-running the SAME "
          f"{len(subset)} exp57/58 used (K={args.k}, seed={args.seed})\n")

    rows = []
    for m in sorted(subset):
        r = run_feeder(m, args.k, args.seed)
        rows.append(r)
        print(f"  {r['name']:24s} skip={r['skip_kw']:6.1f} ({r['skip_acc']:2d}/{r['k']})"
              f"   model-free ceiling {r['ceiling_free_pct']:+5.1f}%")
        print(f"      seeded NSGA-II {r['seeded_nsga2_vs_skip_pct']:+6.1f}%"
              f"   GLDF-MILP {r['milp_vs_skip_pct']:+6.1f}% ({r['milp_acc']:2d}/{r['k']})"
              f"   exact-AC 1-swap {r['acswap_vs_skip_pct']:+6.1f}% "
              f"({r['acswap_acc']:2d}/{r['k']}, {r['acswap_evals']} AC evals)"
              f"   [{r['dt']:.0f}s]")

    print("\n" + "=" * 78)
    w_milp = sum(1 for r in rows if r["milp_vs_skip_pct"] > 0.05)
    w_ls = sum(1 for r in rows if r["acswap_vs_skip_pct"] > 0.05)
    print(f"GLDF-MILP        beats skip-continue on {w_milp}/{len(rows)} feeders")
    print(f"exact-AC 1-swap  beats skip-continue on {w_ls}/{len(rows)} feeders")
    print(f"max conceivable gain anywhere (model-free, no power flow): "
          f"{max(r['ceiling_free_pct'] for r in rows):+.1f}%")

    if args.save and rows:
        out = REPO / "outputs"
        out.mkdir(exist_ok=True)
        path = out / "m36_smartds_gldf_recheck.csv"
        fields = sorted({k for r in rows for k in r})
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader(); w.writerows(rows)
        print(f"saved {path}")


if __name__ == "__main__":
    main()
