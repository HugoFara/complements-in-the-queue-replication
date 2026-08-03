"""M96 -- n13f4 ON ITS NATIVE UNBALANCED MODEL

WHY
---
Batch B promoted n13f4-at-compliance from a robustness check to a third irreducibility
mechanism: its pairs are strictly simultaneous by AC certificate (100% of R*) but they are
NOT private (median sigma 2.19) and they sit at different buses under random placement. That
is a counterexample to "co-location is necessary", so it now carries weight it was never
stress-tested for. Both referees object that it rests on two stacked abstractions:

  AE reviewer, comment 5: "n13f4 now carries an irreducibility claim, and it is computed
  balanced while ENWL ships native OpenDSS models and you have an OpenDSS oracle running for
  SMART-DS. The balanced caveat was acceptable when ENWL carried only a magnitude; it is not
  acceptable under an irreducibility claim that phase-unbalanced voltage behavior could
  plausibly create or destroy."

  Adversarial referee, comment 5(iii): "this is a balanced positive-sequence solve of a
  natively unbalanced LV feeder at a rescaled operating point -- two stacked abstractions on
  the corpus's most fragile number."

They are right, and the gap is larger than either supposed. Solved unbalanced, n13f4's
shipped baseline sits at vmin 0.609 pu, not the 0.817 the balanced solve reports.

WHAT THIS FILE MODELS, AND WHAT IT DELIBERATELY DOES NOT
--------------------------------------------------------
ENWL specifies no conductor ratings. There is not one `NormAmps` in LineCode.txt or
Lines.txt for any feeder in the release, so OpenDSS falls back on its 400 A default and the
pandapower import inherits it: all 783 of n13f4's lines carry `max_i_ka = 0.400`, one uniform
value from the service cable to the main. The balanced arm's "line at 76% loading" is
therefore 76% of a default, not a property of the feeder, and a thermal constraint built on
it would be arbitrary in either engine.

So this arm enforces exactly what the ENWL data actually specifies:

  * the voltage band, per node, on all three phases separately -- the thing an unbalanced
    solve exists to get right, and the constraint binding 175 of 204 pair-halves in Batch B;
  * the 800 kVA substation transformer, which IS real data (Transformers.txt).

Line thermal is dropped rather than defaulted, and that is a narrowing of the constraint set
relative to the balanced arm. It cuts toward MORE capacity and hence, if anything, a smaller
residual -- the conservative direction for a paper claiming the residual is large.

THE OPERATING POINT
-------------------
exp114's RESCALE arm bisects a uniform load scale until the balanced baseline meets the
0.94 floor (alpha = 0.376 on this feeder). The same bisection is redone here against the
UNBALANCED baseline, because the two engines disagree about where compliance starts; using
the balanced alpha on an unbalanced solve would study a feeder that is still non-compliant.
OpenDSS's own `Set LoadMult` applies the scale, so no load is edited by hand.

Run:
  uv run python experiments/122_enwl_unbalanced.py --save
  uv run python experiments/122_enwl_unbalanced.py --seeds 0 1 --no-certificate
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

_sd = __import__("101_residual_smartds")
_seq7 = __import__("91_sequential_segregation")
_alt = __import__("109_alternating_rounds")
_cert = __import__("110_simultaneity_certificate")

OUT = Path(__file__).resolve().parents[1] / "outputs"
ENWL = Path(__file__).resolve().parents[1] / "cache" / "enwl_full"
MASTER = str(ENWL / "network_13" / "Feeder_4" / "Master_snapshot.dss")


def master_for(network: str, feeder: str) -> str:
    return str(ENWL / network / feeder / "Master_snapshot.dss")

_COPY = ("master", "name", "order", "slack", "pq", "vbase", "points",
         "load_mult", "VMIN_PU", "VMAX_PU", "trafo_kva", "trafo_max_pct", "_order_idx")


class ENWLDSSFeeder(_sd.MixedDSSFeeder):
    """n13f4's native three-phase model behind the same Feeder API the balanced arm uses.

    Differs from the SMART-DS feeder in three places: a uniform load scale applied through
    OpenDSS's own `LoadMult`, per-instance voltage limits (the two operating points need
    different ones), and a substation-transformer loading check, since unlike the SMART-DS
    corpus this feeder has a real transformer rating and a queue big enough to exceed it.
    """

    def __init__(self, master=MASTER, load_mult=1.0, vmin=0.94, vmax=1.10,
                 trafo_max_pct=100.0):
        self.load_mult = float(load_mult)
        self.trafo_max_pct = float(trafo_max_pct)
        self.trafo_kva = None
        super().__init__(master)
        self.VMIN_PU, self.VMAX_PU = float(vmin), float(vmax)
        c = self._ctx
        c.Transformers.First()
        self.trafo_kva = float(c.Transformers.kVA())
        self.ctx = SimpleNamespace(
            net=self.ctx.net, feeder=self, excluded_line_idx=[], excluded_buses=set())

    def _fresh(self):
        ctx = super()._fresh()
        if getattr(self, "load_mult", 1.0) != 1.0:
            ctx.Command(f"Set LoadMult={self.load_mult}")
            ctx.Solution.Solve()
        return ctx

    def _trafo_pct(self):
        """Substation transformer apparent-power loading, both terminals, worst case."""
        c = self._ctx
        c.Transformers.First()
        c.Circuit.SetActiveElement(f"Transformer.{c.Transformers.Name()}")
        p = np.asarray(c.CktElement.Powers(), dtype=float)
        n = len(p) // 2                       # first terminal is the first half
        kw, kvar = p[0:n:2], p[1:n:2]
        s = float(np.hypot(kw.sum(), kvar.sum()))
        return 100.0 * s / self.trafo_kva if self.trafo_kva else 0.0

    def evaluate(self, ctx, projects, accepted_ids):
        self._stage(projects)
        self._set(accepted_ids)
        self._ctx.Solution.Solve()
        by_id = {p.id: p for p in projects}
        mw = sum(abs(by_id[r].p_mw) for r in accepted_ids)
        if not self._ctx.Solution.Converged():
            return False, 0.0, float("inf"), {"diverged": True}
        v = np.asarray(self._ctx.Circuit.AllBusMagPu())
        v = v[v > 0]
        if not len(v):
            return False, 0.0, float("inf"), {"diverged": True}
        vmin, vmax = float(v.min()), float(v.max())
        tpct = self._trafo_pct()
        viol = (max(0.0, self.VMIN_PU - vmin) + max(0.0, vmax - self.VMAX_PU)
                + max(0.0, (tpct - self.trafo_max_pct) / 100.0))
        return viol == 0.0, mw, viol, {"vmin": vmin, "vmax": vmax,
                                       "line_pct": 0.0, "trafo_pct": tpct}


def with_anchor(base, anchor_projects, anchor_ids):
    """exp101's DSS-native anchor, carrying this subclass's extra state."""
    f2 = ENWLDSSFeeder.__new__(ENWLDSSFeeder)
    for a in _COPY:
        setattr(f2, a, getattr(base, a))
    f2._anchor = [p for p in anchor_projects if p.id in anchor_ids]
    f2._ctx = f2._fresh()
    f2._staged, f2._on = None, set()
    f2.ctx = SimpleNamespace(net=base.ctx.net, feeder=f2,
                             excluded_line_idx=[], excluded_buses=set())
    f2._keys = {}
    return f2


def baseline(f):
    f._stage([])
    f._ctx.Solution.Solve()
    v = np.asarray(f._ctx.Circuit.AllBusMagPu()); v = v[v > 0]
    return float(v.min()), float(v.max()), f._trafo_pct()


def bisect_loadmult(target=0.94, iters=30, master=MASTER):
    """Largest uniform load scale whose UNBALANCED baseline still meets the floor.

    The balanced arm's alpha does not transfer: the two engines disagree about where this
    feeder becomes compliant, so reusing 0.376 here would study a still-violating feeder.
    """
    lo, hi = 0.0, 1.0
    if baseline(ENWLDSSFeeder(master, load_mult=1.0))[0] >= target:
        return 1.0
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        if baseline(ENWLDSSFeeder(master, load_mult=mid))[0] >= target:
            lo = mid
        else:
            hi = mid
    return lo


def make_family(f, desc, placement="random"):
    """placement="random" is the arm the balanced comparison was built against (m80_alt_s20);
    "colocated" puts each PV and its load at the SAME bus.phase, which is the configuration
    the co-located claim is about and the one this feeder had never been run in."""
    if placement == "colocated":
        builder = lambda ff, seed, k, size: _sd.build_colocated(ff, k, seed, size, size)
    else:
        builder = lambda ff, seed, k, size: _sd.build_mixed(ff, k, seed, 0.5, size, size)
    return _alt.Family(f"enwl_unbalanced_{placement}", lambda: f, builder,
                       with_anchor, _seq7.certify, desc, reset=_alt._reset_dss)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--k", type=int, default=24)
    ap.add_argument("--size-kw", type=float, default=100.0)
    ap.add_argument("--seeds", type=int, nargs="+", default=list(range(20)))
    ap.add_argument("--n-jobs", type=int, default=6)
    ap.add_argument("--vmin", type=float, default=0.94)
    ap.add_argument("--vmax", type=float, default=1.10)
    ap.add_argument("--network", default="network_13")
    ap.add_argument("--feeder", default="Feeder_4")
    ap.add_argument("--treatment", choices=["rescale", "relax"], default="rescale")
    ap.add_argument("--placement", choices=["random", "colocated"], default="random")
    ap.add_argument("--no-certificate", action="store_true")
    ap.add_argument("--out", default="m96_enwl_unbalanced.csv")
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()

    print(f"\n  M96 -- {a.network}/{a.feeder} SOLVED UNBALANCED, ON ITS NATIVE OPENDSS MODEL\n")
    print("  Constraint set is what ENWL actually specifies: the per-node voltage band on")
    print("  all three phases, and the 800 kVA substation transformer. Line thermal is")
    print("  DROPPED, not defaulted -- the release carries no NormAmps anywhere, and the")
    print("  balanced arm's uniform 0.4 kA is OpenDSS's fallback, not feeder data.\n")

    master = master_for(a.network, a.feeder)
    f1 = ENWLDSSFeeder(master, load_mult=1.0, vmin=a.vmin, vmax=a.vmax)
    v1 = baseline(f1)
    print(f"  shipped (LoadMult=1): vmin {v1[0]:.4f}  vmax {v1[1]:.4f}  trafo {v1[2]:.1f}%")
    ref = {"network_13/Feeder_4": 0.8169, "network_2/Feeder_1": 0.9660}.get(
        f"{a.network}/{a.feeder}")
    if ref is not None:
        print(f"    balanced arm reports vmin {ref:.4f} -- the engines disagree by "
              f"{abs(v1[0] - ref):.3f} pu\n")
    else:
        print()

    if a.treatment == "relax":
        # the paper's own non-worsening clamp, on the unbalanced baseline. This is the arm
        # carrying n13f4's headline R1 (76% of joint, abstract), so it has to be checked too.
        alpha = 1.0
        f = ENWLDSSFeeder(master, load_mult=1.0,
                          vmin=min(a.vmin, v1[0]), vmax=max(a.vmax, v1[1]))
        vb = baseline(f)
        print(f"  RELAX, unbalanced: limits clamped to the baseline's own values, "
              f"{f.VMIN_PU:.4f}--{f.VMAX_PU:.4f} pu")
        print(f"    balanced arm clamps to 0.817--1.100\n")
    else:
        alpha = bisect_loadmult(a.vmin, master=master)
        f = ENWLDSSFeeder(master, load_mult=alpha, vmin=a.vmin, vmax=a.vmax)
        vb = baseline(f)
        print(f"  RESCALE, unbalanced: LoadMult={alpha:.4f} -> vmin {vb[0]:.4f} "
              f"vmax {vb[1]:.4f} trafo {vb[2]:.1f}%")
        print(f"    the balanced arm's alpha was 0.376; using it here would leave the "
              f"feeder non-compliant\n")

    fam = make_family(f, f"ENWL {a.network}/{a.feeder} UNBALANCED, {a.treatment}, "
                         f"{a.placement}, LoadMult={alpha:.4f}, "
                         f"{f.VMIN_PU:.3f}-{f.VMAX_PU:.3f} pu", a.placement)
    rows = []
    hdr = (f"  {'seed':>4} | {'gen':>6} {'load':>6} {'seq':>6} {'JOINT':>6} {'alt':>6} | "
           f"{'R1%':>6} {'R*%':>6} | {'mixed':>5} {'cert%':>6}")
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for seed in a.seeds:
        t0 = time.time()
        projects = fam.make_projects(f, seed, a.k, a.size_kw)
        fam.reset(f, projects)
        w = _alt.three_way_generic(fam, f, projects, a.n_jobs)
        joint = w["joint_kw"]
        best, best_ids = -1.0, set()
        for start in ("gen", "load"):
            acc = _cert.fixed_point_accept_set(fam, f, projects, a.n_jobs, start)
            tot = sum(abs(p.p_mw) for p in acc) * 1000.0
            if tot > best:
                best, best_ids = tot, {p.id for p in acc}
        rstar = joint - best
        r1 = joint - w["best_sequential_kw"]

        n_mixed, cert_kw = 0, 0.0
        if not a.no_certificate:
            c = _cert.certificate(fam, f, projects, best_ids)
            mixed = c.pop("_mixed_pairs")
            n_mixed = c["n_mixed_pairs"]
            _taken, cert_kw = __import__("120_certified_share").certified_disjoint_pairs(
                fam, f, projects, best_ids, mixed)
        share = (100.0 * cert_kw / rstar) if rstar > 1e-9 else np.nan

        rows.append(dict(feeder=f"{a.network}/{a.feeder}", engine="opendss_unbalanced", seed=seed,
                         network=a.network, sub=a.feeder, placement=a.placement,
                         treatment=a.treatment, k=a.k, size_kw=a.size_kw, load_mult=alpha,
                         base_vmin=vb[0], base_vmax=vb[1], **w,
                         alternation_kw=best, r1_kw=r1, rstar_kw=rstar,
                         r1_pct=100 * r1 / joint if joint else np.nan,
                         rstar_pct=100 * rstar / joint if joint else np.nan,
                         n_mixed_pairs=n_mixed, certified_kw=cert_kw,
                         certified_share_pct=share, dt=time.time() - t0))
        print(f"  {seed:>4d} | {w['gen_alone_kw']:>6.0f} {w['load_alone_kw']:>6.0f} "
              f"{w['best_sequential_kw']:>6.0f} {joint:>6.0f} {best:>6.0f} | "
              f"{100 * r1 / joint if joint else 0:>6.1f} "
              f"{100 * rstar / joint if joint else 0:>6.1f} | "
              f"{n_mixed:>5d} {share:>6.1f}", flush=True)
        if a.save:
            OUT.mkdir(exist_ok=True)
            pd.DataFrame(rows).to_csv(OUT / a.out, index=False)

    d = pd.DataFrame(rows)
    print("\n" + "=" * 78)
    print("\n  SUMMARY -- median over draws\n")
    print(f"    joint {d.joint_kw.median():.0f} kW   R1 {d.r1_pct.median():.1f}%   "
          f"R* {d.rstar_pct.median():.1f}%   R*>0 in {int((d.rstar_kw > 1e-9).sum())}/{len(d)}")
    s = d.certified_share_pct.dropna()
    if len(s):
        print(f"    certified share of R*: median {s.median():.1f}%, min {s.min():.1f}%")
    if a.network == "network_13":
        print(f"\n  BALANCED arm for the same cell (m88_hard_limits_s20, {a.treatment}):")
        print("    rescale: R1 86.4%  R* 48.6%  R*>0 in 15/20")
        print("    relax:   R1 75.0%  R* 0.0%   R*>0 in 1/20")
    else:
        print("\n  BALANCED arm for n2f1 (m80_alt_s20, random placement):")
        print("    R1 60.0%  R* 0.0%")
    print("\n  A residual that survives here is one phase unbalance did not manufacture.")
    print("  One that vanishes means the balanced solve was carrying the third mechanism.\n")
    if a.save:
        OUT.mkdir(exist_ok=True)
        d.to_csv(OUT / a.out, index=False)
        print(f"  saved -> outputs/{a.out}\n")


if __name__ == "__main__":
    main()
