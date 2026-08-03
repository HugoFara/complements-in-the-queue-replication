"""M93 -- THE CARVE-OUT AS A CONSTRAINT, NOT A LABEL

THE OBJECTION
-------------
Section 6.2 rescues the co-located cells by declaring them the object the reform actually names:
dispatchable generation under NET-EXPORT CONTROL, where a control agreement holds the
cancellation at every hour instead of hoping the sun and the demand coincide. exp112 (M86)
tested that claim by replaying an accept-set under a band. The Applied Energy referee's answer
is that this is still a relabel: the accept-set was chosen by an optimizer that knew nothing
about the band, and then had the band applied afterwards. Put the band INSIDE the certification,
re-certify, and replay -- otherwise the carve-out is a promissory note.

This file does that.

THE MODEL
---------
Requests are co-located in pairs: each site gets one generation and one load request at the same
bus, which is the configuration the carve-out is about. A site is CONTROLLABLE only if both its
legs are approved -- a photovoltaic array with no load behind it cannot honour a net-exchange
band, because it has nothing to import against. So:

  both legs approved   the site is under control. Its net exchange is confined to
                       [-beta * rating, +beta * rating] at every interval, and certification
                       checks both extremes. beta = 0 makes the site electrically invisible.
  one leg approved     the site is uncontrolled and follows the feeder's own shapes, exactly as
                       in the uncontrolled case.

That asymmetry is the whole point, and it is not something we impose: it is what a segregated
process physically is. A generation-only track can never produce a controllable site, however
many rounds it runs, because control needs both signs at one point of common coupling. The
joint study can. If the residual under the band is large and survives all 192 intervals, the
carve-out is a modelled result rather than an assertion.

WHAT IT REPORTS
---------------
For each beta: the exact joint, per-track, sequential and alternation optima under band-aware
certification across every swept interval, and the residual between them. beta = 1 is the
uncontrolled regime and should reproduce the ordinary multi-period numbers of exp117.

Run:
  uv run python experiments/119_banded_certification.py \
      --mat cache/swiss/55nodes_real_swiss_distribution_grid.mat \
      --betas 0.0 0.2 0.5 1.0 --k 16 --seeds 0 1 2 3 --save
"""
from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

import numpy as np
import pandapower as pp
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

_c = __import__("68_ckt7_gldf_headline")
_mp = __import__("117_swiss_multiperiod")
_x = __import__("113_exact_enumeration")

OUT = Path(__file__).resolve().parents[1] / "outputs"


def build_paired(f, k, seed, size_kw):
    """K requests as K/2 co-located pairs: one generation and one load leg per bus.

    Pair membership is carried in the id (`s<j>g` / `s<j>l`) so the oracle can recognise a
    controllable site without a side table surviving the fork into worker processes.
    """
    n_sites = k // 2
    rng = random.Random(seed)
    buses = [rng.choice(f.load_buses) for _ in range(n_sites)]
    out = []
    for j, b in enumerate(buses):
        out.append(_c.Project(id=f"s{j}g", bus=int(b), p_mw=-size_kw / 1000.0))
        out.append(_c.Project(id=f"s{j}l", bus=int(b), p_mw=+size_kw / 1000.0))
    return out


def _site_of(rid):
    return rid[:-1]


class BandedSwiss(_mp.MultiPeriodSwiss):
    """Multi-period Swiss certification where fully-approved co-located sites are band-limited."""

    beta = 1.0

    def evaluate(self, ctx, projects, accepted_ids):
        net, rows = self._staged(projects)
        by_id = {p.id: p for p in projects}
        acc = [r for r in rows if r in accepted_ids]
        net.load.loc[list(rows.values()), "in_service"] = [r in accepted_ids for r in rows]

        # Which sites have both legs approved, and are therefore controllable?
        seen: dict[str, set] = {}
        for rid in acc:
            seen.setdefault(_site_of(rid), set()).add(rid[-1])
        controlled = {s for s, legs in seen.items() if legs == {"g", "l"}}

        nat = slice(0, self.n_native)
        pcol = net.load.columns.get_loc("p_mw")
        qcol = net.load.columns.get_loc("q_mvar")
        worst = {"vmax": 0.0, "vmin": 2.0, "line_pct": 0.0}

        for (t, d) in self.intervals:
            net.load.iloc[nat, pcol] = self.nat_p[:, t, d]
            net.load.iloc[nat, qcol] = self.nat_q[:, t, d]
            ps, ls = self.pv_scale[(t, d)], self.load_scale[(t, d)]
            # A controlled site is bounded by +/- beta * rating; the extremes bound the whole
            # admissible dispatch, so two solves settle every interval. Both legs of the pair
            # are represented by putting the site net on the generation leg and zeroing the
            # other, which is what the network actually sees.
            for direction in (-1.0, +1.0):
                for rid in acc:
                    p = by_id[rid]
                    site = _site_of(rid)
                    if site in controlled:
                        rating = abs(p.p_mw)
                        val = (direction * self.beta * rating) if rid.endswith("g") else 0.0
                    else:
                        val = float(p.p_mw) * (ps if p.p_mw < 0 else ls)
                    net.load.at[rows[rid], "p_mw"] = val
                self.n_solves += 1
                try:
                    pp.runpp(net, **_c.RUNPP_KWARGS)
                except pp.LoadflowNotConverged:
                    return False, 0.0, float("inf"), {"diverged": True, "t": t, "d": d}
                vm = net.res_bus.vm_pu
                vmn, vmx = float(vm.min()), float(vm.max())
                lp = float(net.res_line.loading_percent.max())
                worst["vmax"] = max(worst["vmax"], vmx)
                worst["vmin"] = min(worst["vmin"], vmn)
                worst["line_pct"] = max(worst["line_pct"], lp)
                viol = (max(0.0, vmx - self.VMAX_PU) + max(0.0, self.VMIN_PU - vmn)
                        + max(0.0, lp - self.LOADING_MAX_PCT))
                if viol > 0.0:
                    worst.update(t=t, d=d)
                    return False, 0.0, viol, worst
                if not controlled:
                    break        # nothing band-limited: the two directions are the same solve
        mw = sum(abs(by_id[rid].p_mw) for rid in accepted_ids)
        return True, mw, 0.0, worst


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--mat", required=True)
    ap.add_argument("--betas", type=float, nargs="+", default=[0.0, 0.2, 0.5, 1.0])
    ap.add_argument("--k", type=int, default=16)
    ap.add_argument("--size-kw", type=float, default=500.0)
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2, 3])
    ap.add_argument("--day-types", type=int, nargs="+", default=None)
    ap.add_argument("--vn-kv", type=float, default=21.0)
    ap.add_argument("--vmin", type=float, default=0.95)
    ap.add_argument("--vmax", type=float, default=1.05)
    ap.add_argument("--max-nodes", type=int, default=4096)
    ap.add_argument("--n-jobs", type=int, default=6)
    ap.add_argument("--out", default="m93_banded_certification.csv")
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()

    probe = BandedSwiss(a.mat, None, a.vn_kv, a.vmin, a.vmax)
    d_ref = probe.snap["d"]
    days = a.day_types if a.day_types else sorted({d_ref, 0})
    intervals = [(t, d) for d in days for t in range(96)]

    print("\n  M93 -- THE CARVE-OUT AS A CERTIFICATION CONSTRAINT\n")
    print(f"  Swiss 55-node, {a.k // 2} co-located sites (one generation + one load leg each),")
    print(f"  {a.size_kw:.0f} kW per leg, certified across all {len(intervals)} intervals of")
    print(f"  day-types {days}. A site is band-limited only when BOTH legs are approved.\n")

    rows = []
    for beta in a.betas:
        print(f"  beta = {beta:g}:", flush=True)
        for seed in a.seeds:
            # A banded single-interval pass first. The snapshot is one of the swept intervals and
            # the band applies identically at all of them, so anything it rejects is rejected
            # all-hours too, and the expensive pass only has to confirm the survivors.
            f0 = BandedSwiss(a.mat, None, a.vn_kv, a.vmin, a.vmax)
            f0.beta = float(beta)
            projects = build_paired(f0, a.k, seed, a.size_kw)
            pre = _x.enumerate_all(f0, projects, a.n_jobs)

            f = BandedSwiss(a.mat, intervals, a.vn_kv, a.vmin, a.vmax)
            f.beta = float(beta)
            projects = build_paired(f, a.k, seed, a.size_kw)
            try:
                r, _feas = _mp.run_arm(f"beta{beta:g}", f, projects, a.n_jobs, a.max_nodes,
                                       prefilter=pre)
            except Exception as exc:
                print(f"    seed {seed}: FAILED {type(exc).__name__}: {exc}", flush=True)
                continue
            r.update(beta=beta, seed=seed, k=a.k, size_kw=a.size_kw,
                     n_intervals=len(intervals))
            rows.append(r)
            print(f"    seed {seed}: feasible {r['n_feasible']:>6d}/{r['n_subsets']}   "
                  f"tracks {r['gen_track_kw']:.0f}/{r['load_track_kw']:.0f}   "
                  f"seq {r['best_sequential_kw']:.0f}   JOINT {r['joint_kw']:.0f} kW   "
                  f"R1 {r['residual_pct']:.1f}%   R* {r['irreducible_pct']:.1f}%   "
                  f"[{r['enum_seconds']:.0f}s]", flush=True)

    if not rows:
        raise SystemExit("no cells completed")
    d = pd.DataFrame(rows)

    print("\n  SUMMARY -- median over seeds\n")
    print(f"  {'beta':>6} {'gen kW':>8} {'load kW':>8} {'seq kW':>8} {'joint kW':>9} "
          f"{'R1 %':>7} {'R* %':>7}")
    for beta, g in d.groupby("beta"):
        print(f"  {beta:>6.2f} {g.gen_track_kw.median():>8.0f} {g.load_track_kw.median():>8.0f} "
              f"{g.best_sequential_kw.median():>8.0f} {g.joint_kw.median():>9.0f} "
              f"{g.residual_pct.median():>7.1f} {g.irreducible_pct.median():>7.1f}")

    print("\n  Every number here is certified against all intervals by construction, so the")
    print("  192/192 question exp104 answered with 49/192 is answered here by the constraint")
    print("  set rather than by a replay. What is left to judge is how much capacity the band")
    print("  buys, and whether a segregated process can reach it.\n")

    if a.save:
        OUT.mkdir(exist_ok=True)
        d.to_csv(OUT / a.out, index=False)
        print(f"  saved -> outputs/{a.out}\n")


if __name__ == "__main__":
    main()
