"""M92 -- IS THE RESIDUAL AN ARTIFACT OF A STORAGE-FREE REQUEST MODEL?

THE OBJECTION
-------------
Both referee reports raise it, and it is the sharpest version of the "your objective rewards
cancelling pairs" complaint. A large share of real photovoltaic interconnection requests
co-request a battery. A battery is a single request that carries BOTH SIGNS -- it exports when
discharging and imports when charging -- so it internalises, inside one request and therefore
inside one study track, exactly the complementarity that this paper says a segregated process
throws away. If the residual collapses once requests may bring storage, then what we measured
is a property of a request model nobody submits any more, not of the study boundary.

THE MODEL
---------
A co-requesting site at bus b declares photovoltaic nameplate P and battery power rating
S = rho * P, and interconnects at a point-of-interconnection rating of P -- which is what an
AC-coupled photovoltaic-plus-storage site actually requests, and why storage is attractive: it
shifts energy within an export limit rather than raising it. What the battery adds is the
ability to IMPORT. The network therefore sees a site net anywhere in [-P, +S]: full export as
before, through to charging from the grid at the battery's rating.

That envelope is the referee's point made precise. A photovoltaic request with a battery is a
single request carrying BOTH SIGNS, so it internalises inside one request -- and therefore
inside one study track -- the complementarity this paper says segregation throws away. If the
residual collapses as rho grows, we measured a property of a request model nobody submits.

Certifying such a site means certifying the whole envelope, and a request set is only as
feasible as its worst admissible dispatch, so we check the two uniform extremes: every
co-requesting site at its export floor, and every one at its import ceiling. That is the
bounding argument exp112 uses for net-export bands.

  rho = 0     the paper's request model: photovoltaic only, one sign per request
  rho = 0.5   a battery rated half the array, the common commercial pairing
  rho = 1.0   a battery rated to the array, the largest pairing in ordinary tariff practice

NOT MODELLED, DELIBERATELY: a site whose inverter is oversized so the battery can discharge on
top of full photovoltaic output, exporting up to P + S. That is possible for a DC-coupled system
but it is not what an export-limited interconnection request is, and certifying it is
degenerate on Ckt7 -- the feeder has about 0.011 p.u. of voltage headroom at its shipped
setpoint, so raising peak export by 25% makes every single generation request infeasible alone
and the queue collapses to load-only. Measured, and reported here rather than in the paper,
because the collapse says something about Ckt7's headroom and nothing about storage.

WHAT EACH OUTCOME MEANS
-----------------------
  residual shrinks with rho   storage substitutes for co-study; the paper must scope its claim
                              to queues of single-signed requests and say so prominently
  residual holds              the complementarity a battery internalises is not the
                              complementarity a joint study exploits -- a battery cancels
                              against ITSELF at one bus, while the residual comes from
                              cancellation BETWEEN requests at different buses
  residual grows              storage widens each request's envelope, tightening the feasible
                              set, and segregation costs more rather than less

Exactly enumerated, so no optimizer stands between the model and the number.

Run:
  uv run python experiments/118_storage_corequest.py --rhos 0 0.5 1.0 --k 14 --seeds 0 1 2 3 --save
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandapower as pp
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

_c = __import__("68_ckt7_gldf_headline")
_s = __import__("83_setpoint_sweep")
_alt = __import__("109_alternating_rounds")
_x = __import__("113_exact_enumeration")

OUT = Path(__file__).resolve().parents[1] / "outputs"


class StorageFeeder(_s.SlackFreeFeeder):
    """Ckt7, where generation requests carry a battery and are certified over their envelope.

    `rho` is the battery power rating as a multiple of the photovoltaic nameplate. The site
    keeps its point-of-interconnection rating: it exports at most its nameplate p (p < 0 for
    generation), exactly as without the battery, and gains the ability to import up to
    rho*|p|. Load requests are unchanged -- this file asks what storage on the GENERATION side
    does, because that is where the referees' question comes from.
    """

    rho = 0.0

    def evaluate(self, ctx, projects, accepted_ids):
        net, rows = self._staged(projects)
        net.load.loc[list(rows.values()), "in_service"] = [r in accepted_ids for r in rows]
        by_id = {p.id: p for p in projects}
        if self.rho <= 0.0:
            return super().evaluate(ctx, projects, accepted_ids)

        worst = {"vmax": 0.0, "vmin": 2.0, "line_pct": 0.0, "trafo_pct": 0.0}
        for mult in (1.0, -self.rho):     # export at nameplate, then import at battery rating
            for rid, row in rows.items():
                p = by_id[rid]
                net.load.at[row, "p_mw"] = float(p.p_mw) * (mult if p.p_mw < 0 else 1.0)
            try:
                pp.runpp(net, **_c.RUNPP_KWARGS)
            except pp.LoadflowNotConverged:
                return False, 0.0, float("inf"), {"diverged": True}
            vm = net.res_bus.vm_pu.drop(index=self.slack)
            vmn, vmx = float(vm.min()), float(vm.max())
            lp = net.res_line.loading_percent.drop(self.excl_lines, errors="ignore")
            line_pct = float(lp.max()) if len(lp) else 0.0
            trafo_pct = float(net.res_trafo.loading_percent.max()) if len(net.trafo) else 0.0
            worst["vmax"] = max(worst["vmax"], vmx)
            worst["vmin"] = min(worst["vmin"], vmn)
            worst["line_pct"] = max(worst["line_pct"], line_pct)
            worst["trafo_pct"] = max(worst["trafo_pct"], trafo_pct)
            viol = (max(0.0, vmx - self.VMAX_PU) + max(0.0, self.VMIN_PU - vmn)
                    + max(0.0, line_pct - self.LOADING_MAX_PCT)
                    + max(0.0, trafo_pct - self.LOADING_MAX_PCT))
            if viol > 0.0:
                return False, 0.0, viol, worst
        # Awarded capacity is the declared nameplate, not the envelope: a site connects its
        # array and its battery, and counting the battery's rating as extra hosted capacity
        # would inflate every total in proportion to rho.
        mw = sum(abs(by_id[rid].p_mw) for rid in accepted_ids)
        return True, mw, 0.0, worst


def run_arm(f, projects, n_jobs, max_nodes):
    """Exact joint / per-track / sequential / alternation through the storage-aware oracle.

    Deliberately not exp113's `run_cell`: that also runs the GLDF certifier for a heuristic
    comparison, and the GLDF model is built from a single operating point with no notion of a
    storage envelope. Its number would not mean anything here, and every quantity below is
    exact anyway.
    """
    gen, load = _x.sign_totals(projects)
    feas = _x.enumerate_all(f, projects, n_jobs)
    gbits, lbits = _x._bits_of(projects, "gen"), _x._bits_of(projects, "load")
    joint, _ = _x._optimal_masks(gen, load, feas, gbits | lbits)
    gtrack, _ = _x._optimal_masks(gen, load, feas, gbits)
    ltrack, _ = _x._optimal_masks(gen, load, feas, lbits)
    sg, *_ = _x.exact_sequential(None, f, projects, gen, load, feas, n_jobs, "gen", verbose=False)
    sl, *_ = _x.exact_sequential(None, f, projects, gen, load, feas, n_jobs, "load", verbose=False)
    best_seq = max(sg, sl)
    ag, _n1, c1 = _x.exact_alternation(None, f, projects, gen, load, feas, n_jobs, "gen", max_nodes)
    al, _n2, c2 = _x.exact_alternation(None, f, projects, gen, load, feas, n_jobs, "load", max_nodes)
    alt = max(ag, al)
    return dict(n_feasible=int(feas.sum()), n_subsets=len(feas),
                exact_joint_kw=joint, exact_gen_track_kw=gtrack, exact_load_track_kw=ltrack,
                exact_best_sequential_kw=best_seq, exact_alternation_kw=alt,
                alternation_capped=bool(c1 or c2),
                exact_residual_kw=joint - best_seq,
                exact_residual_pct=100 * (joint - best_seq) / joint if joint else 0.0,
                exact_irreducible_kw=joint - alt,
                exact_irreducible_pct=100 * (joint - alt) / joint if joint else 0.0)


def feeder_at_rho(rho, setpoint=1.04):
    ctx = _c.build_network()
    ctx.net.ext_grid.loc[:, "vm_pu"] = setpoint
    pp.runpp(ctx.net, **_c.RUNPP_KWARGS)
    f = StorageFeeder.from_ctx(f"ckt7-storage{rho:g}", ctx, None,
                               _c.VMIN_PU, _c.VMAX_PU, _c.LOADING_MAX_PCT)
    f.slack = int(ctx.net.ext_grid.bus.iloc[0])
    f.setpoint = setpoint
    f.rho = float(rho)
    return f


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--rhos", type=float, nargs="+", default=[0.0, 0.5, 1.0])
    ap.add_argument("--k", type=int, default=14)
    ap.add_argument("--size-kw", type=float, default=100.0)
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2, 3])
    ap.add_argument("--max-nodes", type=int, default=4096)
    ap.add_argument("--n-jobs", type=int, default=8)
    ap.add_argument("--out", default="m92_storage_corequest.csv")
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()

    print("\n  M92 -- THE RESIDUAL WHEN GENERATION REQUESTS BRING A BATTERY\n")
    print("  rho is the battery power rating as a multiple of the array. Each co-requesting")
    print("  site is certified across its whole net-injection envelope, both extremes.")
    print(f"  Ckt7, K={a.k} at {a.size_kw:.0f} kW, exactly enumerated at every rho.\n")

    rows = []
    for rho in a.rhos:
        print(f"  rho = {rho:g}:", flush=True)
        for seed in a.seeds:
            f = feeder_at_rho(rho)
            projects = _s._build_mixed(f, a.k, seed, 0.5, a.size_kw)
            try:
                r = run_arm(f, projects, a.n_jobs, a.max_nodes)
            except Exception as exc:
                print(f"    seed {seed}: FAILED {type(exc).__name__}: {exc}", flush=True)
                continue
            r.update(rho=rho, seed=seed, k=a.k, size_kw=a.size_kw)
            rows.append(r)
            print(f"    seed {seed}: feasible {r['n_feasible']:>6d}/{r['n_subsets']}   "
                  f"tracks {r['exact_gen_track_kw']:.0f}/{r['exact_load_track_kw']:.0f}   "
                  f"seq {r['exact_best_sequential_kw']:.0f}   "
                  f"JOINT {r['exact_joint_kw']:.0f} kW   "
                  f"R1 {r['exact_residual_pct']:.1f}%   "
                  f"R* {r['exact_irreducible_pct']:.1f}%", flush=True)

    if not rows:
        raise SystemExit("no cells completed")
    d = pd.DataFrame(rows)

    print("\n  SUMMARY -- median over seeds\n")
    print(f"  {'rho':>6} {'gen kW':>8} {'load kW':>8} {'seq kW':>8} {'joint kW':>9} "
          f"{'R1 %':>7} {'R* %':>7}")
    for rho, g in d.groupby("rho"):
        print(f"  {rho:>6.2f} {g.exact_gen_track_kw.median():>8.0f} "
              f"{g.exact_load_track_kw.median():>8.0f} "
              f"{g.exact_best_sequential_kw.median():>8.0f} "
              f"{g.exact_joint_kw.median():>9.0f} "
              f"{g.exact_residual_pct.median():>7.1f} "
              f"{g.exact_irreducible_pct.median():>7.1f}")

    base = d[d.rho == min(a.rhos)]
    print("\n  Against the storage-free model: a residual that holds as rho grows is not an")
    print("  artifact of single-signed requests. A battery cancels against its own array at one")
    print("  bus; the residual comes from cancellation between requests at different buses.\n")
    if len(base):
        b1 = base.exact_residual_pct.median()
        for rho, g in d.groupby("rho"):
            print(f"    rho={rho:g}:  R1 {g.exact_residual_pct.median():.1f}% "
                  f"({g.exact_residual_pct.median() - b1:+.1f} pt vs rho={min(a.rhos):g}), "
                  f"positive in {int((g.exact_residual_kw > 0).sum())}/{len(g)} draws")
    print()

    if a.save:
        OUT.mkdir(exist_ok=True)
        d.to_csv(OUT / a.out, index=False)
        print(f"  saved -> outputs/{a.out}\n")


if __name__ == "__main__":
    main()
