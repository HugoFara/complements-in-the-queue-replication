"""M88 -- THE CORPUS'S LARGEST RESIDUAL UNDER A DEFENSIBLE LIMIT SET  (ENWL n13f4)

THE OBJECTION
-------------
n13f4 carries the paper's largest headline residual (76% of joint, ~1,750 kW) and it is also
the feeder whose voltage floor the non-worsening rule relaxes furthest: its shipped baseline
sits at 0.817 p.u., so the effective lower bound becomes 0.82 p.u. Both referee reports make the
same point, in nearly the same words -- 0.82 p.u. is not an operating standard anywhere, and a
feeder whose headline is 76% should not also be the feeder whose limits were bent to admit its
own baseline. One of them asks specifically: show the residual under a defensible limit set,
with the pre-existing violations handled the way the EPRI overloads are handled.

THE TWO TREATMENTS, SIDE BY SIDE
--------------------------------
RELAX (the paper's, exp98/exp109). Keep every bus in the candidate pool and in the feasibility
check, and relax the binding limit to the baseline's own value. A request is judged on whether
it makes an existing violation WORSE. This is the right question for a connection study and it
is applied by one generic rule to every feeder in the corpus.

EXCLUDE (this file, the EPRI treatment). Keep the book limits hard -- 0.94 / 1.10 p.u., 100%
loading -- and instead remove what already violates them: every bus outside the band at baseline
leaves both the candidate pool and the voltage check, and every branch over 100% leaves the
thermal check. This is exactly what Ckt7 and Ckt24 get for their pre-existing overloads, so
applying it here makes the corpus's treatment uniform in the OTHER direction.

Neither treatment is a superset of the other and the two do not study the same queue: excluding
buses shrinks the candidate pool, so the same seed draws different requests. The comparison is
therefore between two STUDIES of the same feeder, not two scorings of one accept-set, and the
number that has to survive is the residual, not the accept-set.

WHAT WOULD FALSIFY THE PAPER
----------------------------
If n13f4's residual is an artifact of the relaxed floor, the EXCLUDE arm returns a much smaller
residual, or a joint capacity so small that the 1,750 kW magnitude cannot be reproduced under
any defensible limit. Either outcome belongs in Results rather than Limitations, which is where
that discussion currently sits. If the residual survives, the headline is limit-robust and the
0.82 p.u. figure is a scoring convention rather than a load-bearing assumption.

Run:
  uv run python experiments/114_n13f4_hard_limits.py --seeds 0 1 2 3 --save
  uv run python experiments/114_n13f4_hard_limits.py --feeders enwl_n13f4 enwl_n2f1 --save
"""
from __future__ import annotations

import argparse
import copy
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pandapower as pp

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

_c = __import__("68_ckt7_gldf_headline")
_x = __import__("78_corpus_law")
_seq = __import__("91_sequential_segregation")
_enwl = __import__("98_residual_enwl")
_alt = __import__("109_alternating_rounds")

OUT = Path(__file__).resolve().parents[1] / "outputs"


class HardFeeder(_x.Feeder):
    """A Feeder holding the BOOK limits, with baseline violators excluded instead of relaxed.

    `_x.Feeder.from_ctx` already reads the candidate pool and the excluded branches out of the
    context, and `anchored_context` copies both forward, so the only thing left to override is
    the voltage check: the base class minimizes over every bus, and here the buses that start
    outside the band must not be scored at all.
    """

    def evaluate(self, ctx, projects, accepted_ids):
        net, rows = self._staged(projects)
        net.load.loc[list(rows.values()), "in_service"] = [r in accepted_ids for r in rows]
        by_id = {p.id: p for p in projects}
        try:
            pp.runpp(net, **_x.RUNPP_KWARGS)
        except pp.LoadflowNotConverged:
            return False, 0.0, float("inf"), {"diverged": True}
        vm = net.res_bus.vm_pu.drop(index=list(self.excl_buses), errors="ignore")
        vmin, vmax = float(vm.min()), float(vm.max())
        lp = net.res_line.loading_percent.drop(self.excl_lines, errors="ignore")
        line_pct = float(lp.max()) if len(lp) else 0.0
        trafo_pct = float(net.res_trafo.loading_percent.max()) if len(net.trafo) else 0.0
        violation = (max(0.0, vmax - self.VMAX_PU) + max(0.0, self.VMIN_PU - vmin)
                     + max(0.0, line_pct - self.LOADING_MAX_PCT)
                     + max(0.0, trafo_pct - self.LOADING_MAX_PCT))
        mw = sum(abs(by_id[rid].p_mw) for rid in accepted_ids)
        return violation == 0.0, mw, violation, {
            "vmax": vmax, "vmin": vmin, "line_pct": line_pct, "trafo_pct": trafo_pct}


def hard_with_anchor(base_f, anchor_projects, anchor_ids):
    ctx2 = base_f.anchored_context(base_f.ctx, anchor_projects, anchor_ids)
    f2 = HardFeeder.from_ctx(base_f.name + "+anchor", ctx2, base_f.sizer,
                             base_f.VMIN_PU, base_f.VMAX_PU, base_f.LOADING_MAX_PCT)
    f2.excl_buses = set(base_f.excl_buses)
    f2._proj = None
    return f2


def build_hard(relaxed_f, vmin, vmax, loading):
    """The EXCLUDE arm: book limits, baseline violators removed from pool and from scoring."""
    net = copy.deepcopy(relaxed_f.ctx.net)
    pp.runpp(net, **_x.RUNPP_KWARGS)
    vmpu = net.res_bus.vm_pu
    bad_bus = {int(b) for b in vmpu.index[(vmpu < vmin) | (vmpu > vmax)]}
    bad_line = [int(i) for i in net.res_line.index[net.res_line.loading_percent > loading]]
    bad_trafo = [int(i) for i in net.res_trafo.index[net.res_trafo.loading_percent > loading]]
    ctx = _c.NetworkContext(net=net, excluded_line_idx=bad_line, excluded_buses=bad_bus)
    f = HardFeeder.from_ctx(relaxed_f.name + "-hard", ctx, relaxed_f.sizer, vmin, vmax, loading)
    f.excl_buses = bad_bus
    f._proj = None
    return f, bad_bus, bad_line, bad_trafo


def build_rescaled(relaxed_f, vmin, vmax, loading, iters=40, margin=0.0):
    """The RESCALE arm: fix the BASELINE instead of the limits, then apply the book limits.

    EXCLUDE answers the objection by removing what violates; on a feeder whose undervoltage is
    a whole-length voltage-drop phenomenon that removes nearly everything, which says the
    feeder is non-compliant but says nothing about the residual. The other defensible reading
    of "a defensible limit set" is to study the feeder at a load level where it complies --
    standard hosting-capacity practice, since the study snapshot is a choice. We bisect a
    uniform scale on the shipped baseline load to the largest value meeting the floor, then run
    hard book limits over the FULL candidate pool.

    `margin` guards the obvious artifact. Bisecting to the floor EXACTLY leaves the baseline
    sitting on 0.94 with no load headroom anywhere, which is the condition most likely to
    manufacture an irreducible residual by itself: every load request then needs a generation
    request to offset it. Targeting vmin + margin while still ENFORCING the book floor tests
    whether the finding survives an operating point that is not knife-edge.
    """
    net = copy.deepcopy(relaxed_f.ctx.net)
    p0 = net.load.p_mw.values.copy()
    q0 = net.load.q_mvar.values.copy()

    def vmin_at(alpha):
        net.load.loc[:, "p_mw"] = p0 * alpha
        net.load.loc[:, "q_mvar"] = q0 * alpha
        pp.runpp(net, **_x.RUNPP_KWARGS)
        return float(net.res_bus.vm_pu.min())

    target = vmin + margin
    alpha = 1.0
    if vmin_at(1.0) < target:
        lo, hi = 0.0, 1.0
        for _ in range(iters):
            mid = 0.5 * (lo + hi)
            if vmin_at(mid) >= target:
                lo = mid
            else:
                hi = mid
        alpha = lo
    achieved = vmin_at(alpha)
    ctx = _c.NetworkContext(net=net, excluded_line_idx=[], excluded_buses=set())
    f = HardFeeder.from_ctx(relaxed_f.name + "-rescaled", ctx, relaxed_f.sizer,
                            vmin, vmax, loading)
    f.excl_buses = set()
    f._proj = None
    return f, alpha, achieved


def family_for(name, f, with_anchor, desc):
    return _alt.Family(
        name, lambda: f,
        lambda ff, seed, k, size: _enwl.build_mixed(ff, k, seed, 0.5, size),
        with_anchor, _seq.certify, desc)


def measure(fam, f, seed, k, size_kw, n_jobs):
    projects = fam.make_projects(f, seed, k, size_kw)
    fam.reset(f, projects)
    w = _alt.three_way_generic(fam, f, projects, n_jobs)
    best_alt = -np.inf
    for start in ("gen", "load"):
        total, _n, _hr, _pr, _per, ok, _info = _alt.alternate(fam, f, projects, n_jobs,
                                                              start=start)
        if not ok:
            raise SystemExit(f"alternation fixed point not AC-feasible ({fam.name}, seed {seed})")
        best_alt = max(best_alt, total)
    j = w["joint_kw"]
    return dict(**w, alternation_kw=best_alt,
                r1_kw=j - w["best_sequential_kw"],
                r1_pct=100 * (j - w["best_sequential_kw"]) / j if j else 0.0,
                rstar_kw=j - best_alt,
                rstar_pct=100 * (j - best_alt) / j if j else 0.0)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--feeders", nargs="+", default=["enwl_n13f4"])
    ap.add_argument("--k", type=int, default=24)
    ap.add_argument("--size-kw", type=float, default=100.0)
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2, 3])
    ap.add_argument("--vmin", type=float, default=0.94)
    ap.add_argument("--vmax", type=float, default=1.10)
    ap.add_argument("--loading", type=float, default=100.0)
    ap.add_argument("--rescale-margin", type=float, default=0.0,
                    help="rescale the baseline to vmin+margin, but still enforce vmin")
    ap.add_argument("--n-jobs", type=int, default=6)
    ap.add_argument("--out", default="m88_hard_limits.csv")
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()

    print("\n  M88 -- RESIDUAL UNDER RELAXED vs BOOK VOLTAGE LIMITS\n")
    print(f"  RELAX:   the paper's non-worsening clamp (limits follow the baseline)")
    print(f"  EXCLUDE: hard {a.vmin}--{a.vmax} p.u. / {a.loading:.0f}%, with baseline")
    print(f"           violators dropped from the candidate pool and from the check\n")

    rows = []
    for name in a.feeders:
        _ctx, f_rel, _rel, _mk, desc = _x.load_feeder(name, a.n_jobs)
        f_hard, bad_bus, bad_line, bad_trafo = build_hard(f_rel, a.vmin, a.vmax, a.loading)
        f_scaled, alpha, v_scaled = build_rescaled(f_rel, a.vmin, a.vmax, a.loading,
                                                   margin=a.rescale_margin)

        n_bus = len(f_rel.ctx.net.bus)
        print(f"  {desc}")
        print(f"    baseline           vmin {f_rel.base.get('vmin', float('nan')):.3f}  "
              f"vmax {f_rel.base.get('vmax', float('nan')):.3f}  "
              f"line {f_rel.base.get('line_pct', float('nan')):.0f}%")
        print(f"    RELAX limits       {f_rel.VMIN_PU:.3f}--{f_rel.VMAX_PU:.3f} p.u., "
              f"{f_rel.LOADING_MAX_PCT:.0f}%   candidate buses {len(f_rel.load_buses)}")
        print(f"    EXCLUDE limits     {f_hard.VMIN_PU:.3f}--{f_hard.VMAX_PU:.3f} p.u., "
              f"{f_hard.LOADING_MAX_PCT:.0f}%   candidate buses {len(f_hard.load_buses)}")
        print(f"    dropped            {len(bad_bus)}/{n_bus} buses, {len(bad_line)} lines, "
              f"{len(bad_trafo)} transformers")
        print(f"    RESCALE            baseline load x{alpha:.3f} brings vmin to "
              f"{v_scaled:.3f}; "
              f"candidate buses {len(f_scaled.load_buses)}\n", flush=True)

        arms = [("relax", f_rel, _enwl.enwl_with_anchor),
                ("rescale", f_scaled, hard_with_anchor)]
        if len(f_hard.load_buses) >= 2:
            arms.insert(1, ("exclude", f_hard, hard_with_anchor))
        else:
            print(f"    EXCLUDE leaves {len(f_hard.load_buses)} candidate bus(es): under book")
            print("    limits almost nothing on this feeder may host a request at all, so the")
            print("    arm is degenerate rather than informative and is not run. That is the")
            print("    direct answer to the objection -- and the reason the clamp exists.\n")
            rows.append(dict(feeder=name, treatment="exclude", seed=-1, k=a.k,
                             n_candidate_buses=len(f_hard.load_buses), joint_kw=float("nan"),
                             r1_pct=float("nan"), rstar_pct=float("nan")))

        for treat, ff, anch in arms:
            fam = family_for(name, ff, anch, desc)
            hdr = (f"    {'seed':>4} {'gen-only':>9} {'load-only':>10} {'bestSEQ':>9} "
                   f"{'ALTERN':>8} {'JOINT':>8} | {'R1 %':>6} {'R* %':>6}")
            print(f"    --- {treat.upper()}")
            print(hdr)
            for seed in a.seeds:
                t0 = time.time()
                r = measure(fam, ff, seed, a.k, a.size_kw, a.n_jobs)
                r.update(feeder=name, treatment=treat, seed=seed, k=a.k, size_kw=a.size_kw,
                         n_candidate_buses=len(ff.load_buses), vmin_pu=ff.VMIN_PU,
                         rescale_alpha=alpha, rescale_margin=a.rescale_margin,
                         baseline_vmin=v_scaled,
                         vmax_pu=ff.VMAX_PU, loading_pct=ff.LOADING_MAX_PCT,
                         dt=time.time() - t0)
                rows.append(r)
                print(f"    {seed:>4d} {r['gen_alone_kw']:>9.0f} {r['load_alone_kw']:>10.0f} "
                      f"{r['best_sequential_kw']:>9.0f} {r['alternation_kw']:>8.0f} "
                      f"{r['joint_kw']:>8.0f} | {r['r1_pct']:>6.1f} {r['rstar_pct']:>6.1f}",
                      flush=True)
                if a.save:
                    OUT.mkdir(exist_ok=True)
                    pd.DataFrame(rows).to_csv(OUT / a.out, index=False)
            print()

    d = pd.DataFrame(rows)
    print("=" * 78)
    print("\n  Median over seeds\n")
    print(f"  {'feeder':>14} {'treatment':>10} {'joint kW':>10} {'R1 %':>8} {'R* %':>8}")
    print("  " + "-" * 54)
    for (name, treat), g in d[d.seed >= 0].groupby(["feeder", "treatment"], sort=False):
        print(f"  {name:>14} {treat:>10} {g.joint_kw.median():>10.0f} "
              f"{g.r1_pct.median():>8.1f} {g.rstar_pct.median():>8.1f}")
    print()
    if a.save:
        print(f"  saved -> outputs/{a.out}\n")


if __name__ == "__main__":
    main()
