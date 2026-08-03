"""M91 -- THE SWISS RESIDUAL UNDER MULTI-PERIOD CERTIFICATION

THE OBJECTION
-------------
exp104 (M76) certified a Swiss accept-set at one snapshot and then REPLAYED it against the
feeder's own measured day, finding it feasible in 49 of 192 intervals. Both referee reports say
the same thing about that result: replaying a snapshot answer and disclaiming it is not the
correct response. The correct response is to RE-CERTIFY -- make feasibility at every interval
the constraint set, for the joint study and both sequential orders alike -- and report what the
residual is worth when the accepted set has to work all day. If the time-coupled residual is
near zero, the paper's magnitude claims on this feeder do not survive their own scoping section.

WHAT THIS FILE DOES
-------------------
Replaces the single-snapshot feasibility oracle of Eq. (2) with an ALL-INTERVAL one, and reruns
the whole comparison through it. A request set is feasible iff it satisfies voltage and ampacity
limits at EVERY swept interval of the feeder's shipped profile -- nothing else changes: same
feeder, same buses, same limits, same request draw.

  request sizing   A request's declared size is its NAMEPLATE. At interval t it injects
                   nameplate * shape(t) / max_t shape(t), so the shape is normalised to its own
                   peak and a request never exceeds its rating. This makes the multi-period
                   problem well-posed WITHOUT reference to any certification snapshot, which the
                   snapshot formulation cannot do. PV follows the shipped irradiance; load
                   follows the feeder's own aggregate demand shape (exp104's documented proxy --
                   the .mat ships no interconnection-scale load profile).

  the comparison   Both oracles are run on the SAME draws: snapshot (the paper's) and
                   multi-period. Both are exactly enumerated at K where 2**K is affordable, so
                   neither number carries an optimizer caveat and the difference between them is
                   attributable to the constraint set alone.

WHY IT IS AFFORDABLE
--------------------
A 55-node AC solve is milliseconds, but 2**17 x 192 of them is not. Two things make it cheap.
Intervals are checked in descending severity, so an infeasible set usually dies on the first or
second solve rather than the 192nd. And a set that is infeasible at the snapshot is not
re-checked at all -- the snapshot interval is one of the swept ones, so it is simply the first
one tried.

WHAT THE OUTCOMES MEAN
----------------------
  residual holds        the Swiss magnitude survives time coupling and Section 5.6's disclaimer
                        was too strong
  residual shrinks      report both numbers; the multi-period one is the defensible magnitude
  residual vanishes     the snapshot residual on this feeder was an artifact of same-instant
                        cancellation, which is what Section 5.6 suspected but could not show

Run:
  uv run python experiments/117_swiss_multiperiod.py \
      --mat cache/swiss/55nodes_real_swiss_distribution_grid.mat --k 15 --seeds 0 1 2 3 --save
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandapower as pp
import pandas as pd
import scipy.io as sio

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

_s = __import__("103_residual_swiss")        # swiss_net, load_swiss
_c = __import__("68_ckt7_gldf_headline")     # Project, RUNPP_KWARGS
_enwl = __import__("98_residual_enwl")       # build_mixed
_x = __import__("113_exact_enumeration")     # enumerate_all and the exact readers

OUT = Path(__file__).resolve().parents[1] / "outputs"


def shapes(mat_path):
    """The feeder's own 96 x 8 diurnal shapes: irradiance and aggregate native load."""
    m = sio.loadmat(mat_path)
    pv = np.asarray(m["PV_data"], float)
    NL = m["Nodes_with_Load"].ravel().astype(int)
    AL = m["ActiveLoad_data"]
    agg = np.stack([np.asarray(AL[i, 0], float) for i in range(len(NL))]).sum(0)
    return pv, agg


def native_load_series(mat_path):
    """Per-load-node active and reactive series, 96 x 8, in MW/MVAr and in the node order
    `swiss_net` creates them, so a column can be written straight onto net.load."""
    m = sio.loadmat(mat_path)
    NL = m["Nodes_with_Load"].ravel().astype(int)
    AL, RL = m["ActiveLoad_data"], m["ReactiveLoad_data"]
    p = np.stack([np.asarray(AL[i, 0], float) for i in range(len(NL))]) / 1000.0   # n x 96 x 8
    q = np.stack([np.asarray(RL[i, 0], float) for i in range(len(NL))]) / 1000.0
    return p, q


class MultiPeriodSwiss:
    """The Swiss feeder with an ALL-INTERVAL feasibility oracle.

    Exposes the same `evaluate(ctx, projects, accepted_ids)` contract as the snapshot Feeders, so
    exp113's enumeration machinery drives it unchanged. `intervals=None` degenerates to the
    single snapshot the paper certifies at, which is how the control arm is produced -- the two
    arms then differ in nothing but the constraint set.
    """

    def __init__(self, mat, intervals, vn_kv=21.0, vmin=0.95, vmax=1.05, loading=100.0):
        net, snap = _s.swiss_net(mat, vn_kv, "peakload")
        self.net = net
        self.snap = snap
        self.VMIN_PU, self.VMAX_PU, self.LOADING_MAX_PCT = vmin, vmax, loading
        self.name = "swiss55-multiperiod"
        self.ctx = self
        self.sizer = None
        self._proj = None
        self.load_buses = sorted(set(int(b) for b in net.load.bus.unique()))

        pv_shape, agg_shape = shapes(mat)
        self.pv_ref = float(pv_shape.max())
        self.load_ref = float(agg_shape.max())
        self.nat_p, self.nat_q = native_load_series(mat)
        self.n_native = len(net.load)

        if intervals is None:
            intervals = [(snap["t"], snap["d"])]
        # Severity order: the interval most likely to reject a set is tried first, so an
        # infeasible accept-set costs one solve rather than 192. Export stress rises with
        # irradiance, import stress with native demand; ranking on their sum catches both ends.
        sev = [(pv_shape[t, d] / self.pv_ref + agg_shape[t, d] / self.load_ref, t, d)
               for (t, d) in intervals]
        sev.sort(reverse=True)
        self.intervals = [(t, d) for _s_, t, d in sev]
        self.pv_scale = {(t, d): float(pv_shape[t, d]) / self.pv_ref for (t, d) in self.intervals}
        self.load_scale = {(t, d): float(agg_shape[t, d]) / self.load_ref
                           for (t, d) in self.intervals}
        self.n_solves = 0

    # -- the staged net: native loads plus one pandapower load per request, toggled in service
    def _staged(self, projects):
        if self._proj == [p.id for p in projects]:
            return self.net, self._rows
        net = self.net
        net.load = net.load.iloc[:self.n_native]
        rows = {}
        for p in projects:
            rows[p.id] = pp.create_load(net, bus=int(p.bus), p_mw=float(p.p_mw), q_mvar=0.0,
                                        name=p.id, in_service=False)
        self._proj = [p.id for p in projects]
        self._rows = rows
        return net, rows

    def reset(self, projects):
        self._proj = None
        self._staged(projects)

    def evaluate(self, ctx, projects, accepted_ids):
        net, rows = self._staged(projects)
        by_id = {p.id: p for p in projects}
        acc = [r for r in rows if r in accepted_ids]
        net.load.loc[list(rows.values()), "in_service"] = [r in accepted_ids for r in rows]

        nat = slice(0, self.n_native)
        worst = {"vmax": 0.0, "vmin": 2.0, "line_pct": 0.0}
        for (t, d) in self.intervals:
            net.load.iloc[nat, net.load.columns.get_loc("p_mw")] = self.nat_p[:, t, d]
            net.load.iloc[nat, net.load.columns.get_loc("q_mvar")] = self.nat_q[:, t, d]
            ps, ls = self.pv_scale[(t, d)], self.load_scale[(t, d)]
            for rid in acc:
                p = by_id[rid]
                net.load.at[rows[rid], "p_mw"] = float(p.p_mw) * (ps if p.p_mw < 0 else ls)
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
        mw = sum(abs(by_id[rid].p_mw) for rid in accepted_ids)
        return True, mw, 0.0, worst


def run_arm(label, f, projects, n_jobs, max_nodes, prefilter=None):
    """Exact joint / per-track / sequential / alternation through one feasibility oracle.

    `prefilter` is the snapshot arm's feasibility array. Multi-period feasibility implies
    snapshot feasibility, because the snapshot interval is one of the swept ones, so every
    subset the snapshot already rejected can be marked infeasible without a single solve. On an
    oversubscribed cell that is most of the cube.
    """
    gen, load = _x.sign_totals(projects)
    t0 = time.time()
    if prefilter is None:
        feas = _x.enumerate_all(f, projects, n_jobs)
    else:
        feas = _x.enumerate_subset(f, projects, n_jobs, np.flatnonzero(prefilter))
    dt = time.time() - t0

    gbits, lbits = _x._bits_of(projects, "gen"), _x._bits_of(projects, "load")
    joint, _ = _x._optimal_masks(gen, load, feas, gbits | lbits)
    gtrack, _ = _x._optimal_masks(gen, load, feas, gbits)
    ltrack, _ = _x._optimal_masks(gen, load, feas, lbits)
    sg, _sgg, _sgl, _na, _sk = _x.exact_sequential(None, f, projects, gen, load, feas,
                                                   n_jobs, "gen", verbose=False)
    sl, _slg, _sll, _nb, _sk2 = _x.exact_sequential(None, f, projects, gen, load, feas,
                                                    n_jobs, "load", verbose=False)
    best_seq = max(sg, sl)
    ag, _n1, c1 = _x.exact_alternation(None, f, projects, gen, load, feas, n_jobs,
                                       "gen", max_nodes)
    al, _n2, c2 = _x.exact_alternation(None, f, projects, gen, load, feas, n_jobs,
                                       "load", max_nodes)
    alt = max(ag, al)

    row = dict(arm=label, n_feasible=int(feas.sum()), n_subsets=len(feas),
               joint_kw=joint, gen_track_kw=gtrack, load_track_kw=ltrack,
               seq_genfirst_kw=sg, seq_loadfirst_kw=sl, best_sequential_kw=best_seq,
               alternation_kw=alt, alternation_capped=bool(c1 or c2),
               residual_kw=joint - best_seq,
               residual_pct=100 * (joint - best_seq) / joint if joint else 0.0,
               irreducible_kw=joint - alt,
               irreducible_pct=100 * (joint - alt) / joint if joint else 0.0,
               requested_kw=float(gen.max() + load.max()), enum_seconds=dt)
    return row, feas


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--mat", required=True)
    ap.add_argument("--k", type=int, default=15)
    ap.add_argument("--size-kw", type=float, default=500.0)
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2, 3])
    ap.add_argument("--day-types", type=int, nargs="+", default=None,
                    help="which shipped day-types to require feasibility on "
                         "(default: the certification day-type plus day-type 0, exp104's 192)")
    ap.add_argument("--vn-kv", type=float, default=21.0)
    ap.add_argument("--vmin", type=float, default=0.95)
    ap.add_argument("--vmax", type=float, default=1.05)
    ap.add_argument("--max-nodes", type=int, default=4096)
    ap.add_argument("--n-jobs", type=int, default=6)
    ap.add_argument("--out", default="m91_swiss_multiperiod.csv")
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()

    probe = MultiPeriodSwiss(a.mat, None, a.vn_kv, a.vmin, a.vmax)
    d_ref = probe.snap["d"]
    days = a.day_types if a.day_types else sorted({d_ref, 0})
    intervals = [(t, d) for d in days for t in range(96)]

    print("\n  M91 -- THE SWISS RESIDUAL UNDER MULTI-PERIOD CERTIFICATION\n")
    print(f"  snapshot arm : interval {probe.snap['t']}/96 of day-type {d_ref}, "
          f"the point the paper certifies at")
    print(f"  multi arm    : all {len(intervals)} intervals of day-types {days} required "
          f"feasible simultaneously")
    print(f"  K={a.k} at {a.size_kw:.0f} kW nameplate, exactly enumerated in both arms.\n")

    rows = []
    for seed in a.seeds:
        print(f"  seed {seed}:", flush=True)
        snap_feas = None
        for label, iv in (("snapshot", None), ("multiperiod", intervals)):
            f = MultiPeriodSwiss(a.mat, iv, a.vn_kv, a.vmin, a.vmax)
            projects = _enwl.build_mixed(f, a.k, seed, 0.5, a.size_kw)
            try:
                r, feas = run_arm(label, f, projects, a.n_jobs, a.max_nodes,
                                  prefilter=snap_feas if label == "multiperiod" else None)
            except Exception as exc:
                print(f"    {label}: FAILED {type(exc).__name__}: {exc}", flush=True)
                continue
            if label == "snapshot":
                snap_feas = feas
            r.update(seed=seed, k=a.k, size_kw=a.size_kw, n_intervals=len(iv) if iv else 1)
            rows.append(r)
            print(f"    {label:>12}: feasible {r['n_feasible']:>6d}/{r['n_subsets']}   "
                  f"tracks {r['gen_track_kw']:.0f}/{r['load_track_kw']:.0f}   "
                  f"seq {r['best_sequential_kw']:.0f}   JOINT {r['joint_kw']:.0f} kW   "
                  f"R1 {r['residual_pct']:.1f}%   R* {r['irreducible_pct']:.1f}%   "
                  f"[{r['enum_seconds']:.0f}s]", flush=True)

    if not rows:
        raise SystemExit("no cells completed")
    d = pd.DataFrame(rows)

    print("\n  SUMMARY -- median over seeds\n")
    print(f"  {'arm':>12} {'joint kW':>9} {'seq kW':>8} {'alt kW':>8} {'R1 %':>7} {'R* %':>7}")
    for arm in ("snapshot", "multiperiod"):
        g = d[d.arm == arm]
        if not len(g):
            continue
        print(f"  {arm:>12} {g.joint_kw.median():>9.0f} {g.best_sequential_kw.median():>8.0f} "
              f"{g.alternation_kw.median():>8.0f} {g.residual_pct.median():>7.1f} "
              f"{g.irreducible_pct.median():>7.1f}")

    snap, multi = d[d.arm == "snapshot"], d[d.arm == "multiperiod"]
    if len(snap) and len(multi):
        print(f"\n  Joint capacity under time coupling: "
              f"{100 * multi.joint_kw.median() / snap.joint_kw.median():.1f}% of the snapshot "
              f"figure.")
        print(f"  The residual is what survives on top of that: "
              f"{snap.residual_pct.median():.1f}% -> {multi.residual_pct.median():.1f}% of joint, "
              f"irreducible {snap.irreducible_pct.median():.1f}% -> "
              f"{multi.irreducible_pct.median():.1f}%.")
        print("\n  A residual that persists here is one an all-hours process would still leave on")
        print("  the table; one that vanishes was same-instant cancellation and nothing more.\n")

    if a.save:
        OUT.mkdir(exist_ok=True)
        d.to_csv(OUT / a.out, index=False)
        print(f"  saved -> outputs/{a.out}\n")


if __name__ == "__main__":
    main()
