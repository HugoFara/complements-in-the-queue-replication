"""M97 -- IS n13f4's THIRD MECHANISM THE UNBALANCE, OR THE DEFAULTED LINE RATING?

WHY
---
exp122 solves n13f4 unbalanced and the residual disappears. Two things changed at once,
so that run on its own cannot say which did it:

  1. the power-flow model went from balanced positive-sequence to three-phase, and
  2. line thermal was dropped, because ENWL specifies no NormAmps anywhere and the balanced
     arm's uniform `max_i_ka = 0.4` on all 783 lines is OpenDSS's fallback, not feeder data.

This file holds (1) fixed and changes only (2): the BALANCED rescale arm of exp114, with
line ratings inflated out of the way and the 800 kVA substation transformer -- which IS real
data -- still enforced at 100%. Three outcomes, three different papers:

  * residual survives  -> the defaulted rating was not carrying it, and exp122's collapse is
                          a genuine unbalance effect. The third mechanism is an artifact of
                          the balanced solve and must be withdrawn.
  * residual collapses -> the third mechanism was manufactured by an arbitrary 400 A default
                          applied uniformly from service cable to main. It must be withdrawn
                          for a different and worse reason, and every other ENWL number in
                          the paper inherits the same doubt.
  * partial            -> report both contributions separately.

Either way the answer belongs in the paper, because n13f4-at-compliance is now the corpus's
counterexample to "co-location is necessary" (Batch B: certified share 100%, median sigma
2.19, halves at different buses).

Run:
  uv run python experiments/123_n13f4_thermal_control.py --save
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

_x = __import__("78_corpus_law")
_alt = __import__("109_alternating_rounds")
_cert = __import__("110_simultaneity_certificate")
_share = __import__("120_certified_share")
_hard = __import__("114_n13f4_hard_limits")

OUT = Path(__file__).resolve().parents[1] / "outputs"


def fixed_scale_feeder(n_jobs, alpha, vmin=0.94, vmax=1.10, loading=100.0):
    """The BALANCED feeder at an imposed uniform load scale, with book limits.

    Controls the one variable exp122 could not hold fixed. The unbalanced solve reaches the
    0.94 floor at LoadMult 0.101 where the balanced solve reaches it at 0.376, so exp122
    changed the engine AND the operating point together. Running the balanced engine at the
    unbalanced arm's own alpha separates them: if the residual dies here too, the collapse is
    about how lightly loaded the compliant point is, not about phase unbalance.
    """
    _ctx, f_rel, _rel, _mk, desc = _x.load_feeder("enwl_n13f4", n_jobs)
    f_rel = copy.deepcopy(f_rel)
    net = f_rel.ctx.net
    net.load.loc[:, "p_mw"] *= alpha
    net.load.loc[:, "q_mvar"] *= alpha
    pp.runpp(net, **_x.RUNPP_KWARGS)
    achieved = float(net.res_bus.vm_pu.min())
    ctx = __import__("68_ckt7_gldf_headline").NetworkContext(
        net=net, excluded_line_idx=[], excluded_buses=set())
    f = _hard.HardFeeder.from_ctx(f_rel.name + f"-x{alpha:.4f}", ctx, f_rel.sizer,
                                  vmin, vmax, loading)
    f.excl_buses = set()
    f._proj = f._enet = f._rows = None
    fam = _hard.family_for("enwl_n13f4_fixed", f, _hard.hard_with_anchor,
                           f"{desc} -- BALANCED at LoadMult x{alpha:.4f} (the unbalanced "
                           f"arm's own alpha), baseline vmin {achieved:.4f}")
    return fam, f, alpha, achieved


def rescaled_feeder(n_jobs, drop_line_thermal, vmin=0.94, vmax=1.10, loading=100.0):
    """exp114's RESCALE arm, optionally with line ratings inflated out of the constraint set.

    The transformer keeps its 100% cap in both arms: 800 kVA is published in
    Transformers.txt, so unlike the line ratings it is not a default standing in for absent
    data. Inflating `max_i_ka` rather than raising LOADING_MAX_PCT is what keeps the two
    apart -- the feeder's own limit object applies one percentage to lines and transformers
    alike.
    """
    _ctx, f_rel, _rel, _mk, desc = _x.load_feeder("enwl_n13f4", n_jobs)
    if drop_line_thermal:
        f_rel = copy.deepcopy(f_rel)
        f_rel.ctx.net.line.loc[:, "max_i_ka"] = 1e3
        pp.runpp(f_rel.ctx.net, **_x.RUNPP_KWARGS)
        f_rel._proj = f_rel._enet = f_rel._rows = None   # `_staged` is a METHOD; `_proj` is the cache key
    f, alpha, achieved = _hard.build_rescaled(f_rel, vmin, vmax, loading)
    if drop_line_thermal:
        f.ctx.net.line.loc[:, "max_i_ka"] = 1e3
        pp.runpp(f.ctx.net, **_x.RUNPP_KWARGS)
        f._proj = f._enet = f._rows = None
    fam = _hard.family_for("enwl_n13f4_rescale", f, _hard.hard_with_anchor,
                           f"{desc} -- RESCALE x{alpha:.3f}, "
                           f"line thermal {'DROPPED' if drop_line_thermal else 'default 0.4 kA'}")
    return fam, f, alpha, achieved


def run_arm(label, fam, f, seeds, k, size_kw, n_jobs):
    rows = []
    hdr = (f"  {'seed':>4} | {'gen':>6} {'load':>6} {'seq':>6} {'JOINT':>6} {'alt':>6} | "
           f"{'R1%':>6} {'R*%':>6} | {'mixed':>5} {'cert%':>6}")
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for seed in seeds:
        t0 = time.time()
        projects = fam.make_projects(f, seed, k, size_kw)
        fam.reset(f, projects)
        w = _alt.three_way_generic(fam, f, projects, n_jobs)
        joint = w["joint_kw"]
        best, best_ids = -1.0, set()
        for start in ("gen", "load"):
            acc = _cert.fixed_point_accept_set(fam, f, projects, n_jobs, start)
            tot = sum(abs(p.p_mw) for p in acc) * 1000.0
            if tot > best:
                best, best_ids = tot, {p.id for p in acc}
        rstar, r1 = joint - best, joint - w["best_sequential_kw"]
        c = _cert.certificate(fam, f, projects, best_ids)
        mixed = c.pop("_mixed_pairs")
        _taken, cert_kw = _share.certified_disjoint_pairs(fam, f, projects, best_ids, mixed)
        share = (100.0 * cert_kw / rstar) if rstar > 1e-9 else np.nan
        rows.append(dict(arm=label, seed=seed, k=k, size_kw=size_kw, **w,
                         alternation_kw=best, r1_kw=r1, rstar_kw=rstar,
                         r1_pct=100 * r1 / joint if joint else np.nan,
                         rstar_pct=100 * rstar / joint if joint else np.nan,
                         n_mixed_pairs=c["n_mixed_pairs"],
                         n_addable_singles=c["n_addable_singles"],
                         certified_kw=cert_kw, certified_share_pct=share,
                         dt=time.time() - t0))
        print(f"  {seed:>4d} | {w['gen_alone_kw']:>6.0f} {w['load_alone_kw']:>6.0f} "
              f"{w['best_sequential_kw']:>6.0f} {joint:>6.0f} {best:>6.0f} | "
              f"{rows[-1]['r1_pct']:>6.1f} {rows[-1]['rstar_pct']:>6.1f} | "
              f"{c['n_mixed_pairs']:>5d} {share:>6.1f}", flush=True)
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--k", type=int, default=24)
    ap.add_argument("--size-kw", type=float, default=100.0)
    ap.add_argument("--seeds", type=int, nargs="+", default=list(range(20)))
    ap.add_argument("--n-jobs", type=int, default=6)
    ap.add_argument("--unbalanced-alpha", type=float, default=0.1011,
                    help="also run the BALANCED engine at this scale (exp122's own alpha)")
    ap.add_argument("--out", default="m97_n13f4_thermal_control.csv")
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()

    print("\n  M97 -- BALANCED n13f4 WITH AND WITHOUT THE DEFAULTED LINE RATING\n")
    print("  Both arms are balanced and both are rescaled to the 0.94 floor. The only")
    print("  difference is whether the 783 lines carry OpenDSS's fallback 0.4 kA or no")
    print("  rating at all. The 800 kVA transformer is enforced in both -- it is real data.\n")

    arms = [("default 0.4 kA", lambda nj: rescaled_feeder(nj, False)),
            ("line thermal dropped", lambda nj: rescaled_feeder(nj, True))]
    if a.unbalanced_alpha:
        arms.append((f"balanced @ x{a.unbalanced_alpha:g}",
                     lambda nj: fixed_scale_feeder(nj, a.unbalanced_alpha)))
    rows = []
    for label, build in arms:
        fam, f, alpha, achieved = build(a.n_jobs)
        print(f"  {fam.desc}")
        print(f"    LoadMult x{alpha:.4f} -> baseline vmin {achieved:.4f}\n")
        rows += run_arm(label, fam, f, a.seeds, a.k, a.size_kw, a.n_jobs)
        if a.save:
            OUT.mkdir(exist_ok=True)
            pd.DataFrame(rows).to_csv(OUT / a.out, index=False)
        print()

    d = pd.DataFrame(rows)
    print("=" * 78)
    print("\n  SUMMARY -- median over draws\n")
    g = d.groupby("arm").agg(draws=("seed", "size"), joint=("joint_kw", "median"),
                             r1=("r1_pct", "median"), rstar=("rstar_pct", "median"),
                             rstar_pos=("rstar_kw", lambda s: int((s > 1e-9).sum())),
                             cert=("certified_share_pct", "median"))
    print(g.to_string())
    print("\n  Published balanced arm (m88, rescale): R1 86.4%  R* 48.6%  R*>0 in 15/20")
    print("  exp122 unbalanced arm: see outputs/m96_enwl_unbalanced.csv\n")
    if a.save:
        OUT.mkdir(exist_ok=True)
        d.to_csv(OUT / a.out, index=False)
        print(f"  saved -> outputs/{a.out}\n")


if __name__ == "__main__":
    main()
