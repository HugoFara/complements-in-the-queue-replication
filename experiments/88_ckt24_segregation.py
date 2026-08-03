"""M60 -- DOES THE SEGREGATION HEADLINE HOLD ON CKT24?

M58 measured the cost of studying generation and load in separate queues on Ckt7 (+43-129%,
median +75%). "One feeder" is the obvious attack. Ckt24 is the right second feeder: 6,058
buses, and it binds on THERMAL as well as voltage, where Ckt7 is purely overvoltage-limited.

M59 (exp87) established three things first, and two of them are corrections to US:

  1. CKT24'S SLACK IS ALSO AT 1.05 and is also the argmax of baseline vmax -- the same
     censoring as Ckt7. The true PQ maximum is 1.045695, so the OVERVOLTAGE HEADROOM is
     +0.0043 pu (Ckt7's was +0.0012). Excluded here, as in M54.

  2. CKT24'S TAP CHANGER IS FROZEN, NOT MISSING. (Corrected: an earlier version of this
     file claimed the regulator "was never imported" -- that was WRONG.) OpenDSS defines
        New Regcontrol.SubXFMR_Regulator vreg=123 band=3 ptratio=166
     targeting a regulated head of ~1.025 pu, and the importer DOES capture it: it reads the
     solved tap (dss.Transformers.Tap() = 1.0125) and bakes it into vn_lv_kv (34.93125 kV
     vs a 34.5 kV nominal bus). So the BASELINE is correct. What is missing is the
     regulator's RESPONSE: the tap is pinned at the no-PV position, so as PV export raises
     voltage the model never taps DOWN to absorb it, where a real LTC would. That
     UNDERSTATES PV hosting capacity, so every Ckt24 headline is a CONSERVATIVE floor.
     (Ckt7's error ran the other way.) See an internal handover note.

  3. THE FEASIBLE SETPOINT WINDOW IS [1.03, 1.05], wider than Ckt7's [1.04, 1.05]:

        source   base vmax   headroom   base vmin
          1.05    1.045695   +0.00430    0.972560
          1.04    1.034847   +0.01515    0.960592
          1.03    1.023979   +0.02602    0.948573   <- honest setpoint
          1.02    1.013089   +0.03691    0.936499   NO -- baseline breaks the 0.94 floor

     BUT THAT WINDOW WAS MEASURED ON VOLTAGE ALONE, AND IT IS WRONG. Backing the source
     off raises CURRENT for the same power, so thermal loading gets WORSE:

        source   vmax(PQ)    vmin   line%   other trafo%   baseline feasible?
          1.050   1.04570  0.97256   94.01          99.72   OK
          1.045   1.04027  0.96658   94.50         100.28   VIOLATES (trafo)
          1.030   1.02398  0.94857   96.00         102.00   VIOLATES (trafo)

     A distribution transformer sits at 99.72% AT BASELINE. One notch down and it is over.
     **CKT24 CANNOT BE DE-PINNED: 1.05 is its ONLY feasible setpoint.**

     This INVERTS Ckt7's story rather than repeating it. Ckt7's 1.05 was a FREE CHOICE
     (a 115 kV source with no regulator), and backing it off to 1.04 destroyed our
     complementarity headline. Ckt24's 1.05 is FORCED by thermal, so running there is not
     a pin -- it is the feeder's only operating point. And Ckt24 has REAL PV headroom
     there: 177/400 probed buses host a 100 kW PV request SOLO (44%), against Ckt7's
     7/199 (3.5%) at its own 1.05. So a Ckt24 segregation number is not a division by zero,
     and it needs no setpoint correction to be honest.

THE CONSTRAINT SET IS EXP64'S, NOT RE-DERIVED -- and that is deliberate. Two bugs in M59's
first pass both came from re-deriving it by hand:
  * a flat `<= 100%` thermal cap is failed by the EMPTY network (Ckt24's substation
    transformer sits at 111.06% at baseline), so every probe "failed thermally";
  * a global non-worsening cap `max(100, baseline)` inherits the 309.78% of a PRE-BROKEN
    transformer that exp64 excludes outright, which silently DISABLES the thermal check.
exp64 does the correct thing: DROP the 37 pre-broken transformers and the substation
transformer from the plain 100% test, and hold the substation transformer NON-WORSENING
against its own baseline. This file delegates to that rule rather than restating it.

Usage:
  python experiments/88_ckt24_segregation.py --save
"""
from __future__ import annotations

import argparse
import copy
import random
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pandapower as pp

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

_c = __import__("64_ckt24_gldf_bound")
_m = __import__("75_milp_maximality")
_x = __import__("78_corpus_law")

OUT = Path(__file__).resolve().parents[1] / "outputs"
SETPOINTS = [1.05]      # FORCED -- see the docstring; Ckt24 cannot be de-pinned
LOAD_FRACTIONS = [0.3, 0.5, 0.7]


class Ckt24Feeder(_x.Feeder):
    """exp78's staged oracle (toggle `in_service`, never deepcopy) wearing exp64's EXACT
    constraint set, plus M54's slack exclusion.

    exp78's own `Feeder.evaluate` takes `max()` over ALL transformers against one flat
    LOADING_MAX_PCT. That is fine on Ckt7 (its exclusions are lines; it has no pre-broken
    transformer) and CATASTROPHIC on Ckt24, where a 309.78% pre-broken transformer would
    make every accept-set infeasible. Hence this override."""

    @classmethod
    def at_setpoint(cls, vm_pu):
        ctx = _c.build_network()
        ctx.net.ext_grid.loc[:, "vm_pu"] = vm_pu
        pp.runpp(ctx.net, **_c.RUNPP_KWARGS)

        f = cls.__new__(cls)
        f.name, f.sizer, f.ctx = f"ckt24@{vm_pu:.2f}", None, ctx
        f.VMIN_PU, f.VMAX_PU, f.LOADING_MAX_PCT = _c.VMIN_PU, _c.VMAX_PU, _c.LOADING_MAX_PCT
        f.excl_lines = list(ctx.excluded_line_idx)
        f.slack = int(ctx.net.ext_grid.bus.iloc[0])
        f.setpoint = vm_pu
        # exp64's exclusions, taken from the context rather than recomputed
        f.sub_idx = ctx.sub_idx
        f.bad_trafo_idx = list(ctx.bad_trafo_idx)
        # The substation transformer's non-worsening reference must be re-read AT THIS
        # SETPOINT -- it is not the shipped 111.06% once the source moves.
        f.sub_base_pct = float(ctx.net.res_trafo.at[ctx.sub_idx, "loading_percent"])
        f.load_buses = sorted(int(b) for b in ctx.net.load.bus.unique()
                              if b not in ctx.excluded_buses)
        f._proj = f._enet = f._rows = None
        return f

    def evaluate(self, ctx, projects, accepted_ids):
        net, rows = self._staged(projects)
        net.load.loc[list(rows.values()), "in_service"] = [r in accepted_ids for r in rows]
        by_id = {p.id: p for p in projects}
        try:
            pp.runpp(net, **_x.RUNPP_KWARGS)
        except pp.LoadflowNotConverged:
            return False, 0.0, float("inf"), {"diverged": True}

        vm = net.res_bus.vm_pu.drop(index=self.slack)            # M54: no request moves it
        vmin, vmax = float(vm.min()), float(vm.max())
        lp = net.res_line.loading_percent.drop(self.excl_lines, errors="ignore")
        line_pct = float(lp.max()) if len(lp) else 0.0
        # exp64's rule: the 37 pre-broken transformers and the substation transformer are
        # NOT subject to the plain 100% test.
        other = net.res_trafo.loading_percent.drop([self.sub_idx, *self.bad_trafo_idx],
                                                   errors="ignore")
        other_max = float(other.max()) if len(other) else 0.0
        sub_pct = float(net.res_trafo.at[self.sub_idx, "loading_percent"])

        violation = (max(0.0, vmax - self.VMAX_PU) + max(0.0, self.VMIN_PU - vmin)
                     + max(0.0, line_pct - self.LOADING_MAX_PCT)
                     + max(0.0, other_max - self.LOADING_MAX_PCT)
                     + max(0.0, sub_pct - self.sub_base_pct))   # non-worsening
        mw = sum(abs(by_id[rid].p_mw) for rid in accepted_ids)
        return violation == 0.0, mw, violation, {
            "vmax": vmax, "vmin": vmin, "line_pct": line_pct,
            "trafo_pct": other_max, "sub_pct": sub_pct}

    # exp64's own GLDF / thermal builders, so the MILP sees the same network the oracle does
    def build_gldf(self, ctx, buses):
        return _c.build_gldf(ctx, buses)

    def build_thermal(self, ctx, projects):
        return _c.build_thermal(ctx, projects)

    def anchored_context(self, ctx, projects, anchor_ids):
        net = copy.deepcopy(ctx.net)
        by_id = {p.id: p for p in projects}
        for rid in anchor_ids:
            p = by_id[rid]
            pp.create_load(net, bus=p.bus, p_mw=p.p_mw, q_mvar=0.0, name=rid)
        pp.runpp(net, **_c.RUNPP_KWARGS)
        n2 = copy.copy(ctx)
        n2.net = net
        return n2


def build_mixed(f, k, seed, load_frac, size_kw):
    """exp81/exp86's draw: the bus stream and the sign stream are separate RNGs, so changing
    the mix never moves a request -- only its sign."""
    rng_b = random.Random(seed)
    buses = [rng_b.choice(f.load_buses) for _ in range(k)]
    rng_s = random.Random(seed + 10_000)
    idx = list(range(k))
    rng_s.shuffle(idx)
    is_load = set(idx[:int(round(load_frac * k))])
    return [_c.Project(id=f"r{i}", bus=int(b),
                       p_mw=(size_kw / 1000.0) if i in is_load else (-size_kw / 1000.0))
            for i, b in enumerate(buses)]


def certify(f, projects, n_jobs):
    if not projects:
        return set(), 0.0
    ids, mw, _ = _m.certified_allocation(f.ctx, projects, f, f, n_jobs=n_jobs, verbose=False)
    return set(ids), mw * 1000


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--setpoints", type=float, nargs="+", default=SETPOINTS)
    ap.add_argument("--load-fractions", type=float, nargs="+", default=LOAD_FRACTIONS)
    ap.add_argument("--size-kw", type=float, default=100.0)
    ap.add_argument("--k", type=int, default=24)
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    ap.add_argument("--n-jobs", type=int, default=8)
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()

    print("\n  M60 -- THE COST OF SEPARATING GENERATION AND LOAD, ON CKT24\n")
    print(f"  one fixed queue of K={a.k} at {a.size_kw:.0f} kW; only the STUDY BOUNDARY moves.")
    print(f"  slack excluded (M54); exp64's constraint set (sub trafo non-worsening,")
    print(f"  37 pre-broken trafos dropped). median over seeds {a.seeds}.\n")

    hdr = (f"  {'source':>7} {'load%':>6} | {'PV alone':>9}{'load alone':>11}{'SEGREG':>9}"
           f" | {'JOINT':>8} | {'joint-vs-segreg':>16} {'union AC-ok?':>13}")
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))

    rows = []
    for sp in a.setpoints:
        f = Ckt24Feeder.at_setpoint(sp)
        for lf in a.load_fractions:
            per_seed = []
            for seed in a.seeds:
                t0 = time.time()
                projects = build_mixed(f, a.k, seed, lf, a.size_kw)
                pv = [p for p in projects if p.p_mw < 0]
                ld = [p for p in projects if p.p_mw > 0]

                f._proj = None
                pv_ids, pv_kw = certify(f, pv, a.n_jobs)
                f._proj = None
                ld_ids, ld_kw = certify(f, ld, a.n_jobs)
                seg_kw = pv_kw + ld_kw

                f._proj = None
                union_ok, _, _, _ = f.evaluate(f.ctx, projects, pv_ids | ld_ids)

                f._proj = None
                _j, joint_kw = certify(f, projects, a.n_jobs)

                per_seed.append((pv_kw, ld_kw, seg_kw, joint_kw, union_ok))
                rows.append(dict(setpoint=sp, load_frac=lf, seed=seed, k=a.k,
                                 size_kw=a.size_kw, n_pv=len(pv), n_load=len(ld),
                                 pv_alone_kw=pv_kw, load_alone_kw=ld_kw,
                                 segregated_kw=seg_kw, joint_kw=joint_kw,
                                 union_ac_feasible=bool(union_ok),
                                 cost_of_segregation_pct=(100 * (joint_kw / seg_kw - 1)
                                                          if seg_kw > 0 else float("nan")),
                                 dt=time.time() - t0))
                if a.save:
                    OUT.mkdir(exist_ok=True)
                    pd.DataFrame(rows).to_csv(OUT / "m60_segregation_ckt24.csv", index=False)

            arr = np.array([[p[0], p[1], p[2], p[3]] for p in per_seed])
            med = np.median(arr, axis=0)
            all_ok = all(p[4] for p in per_seed)
            gain = (f"{100 * (med[3] / med[2] - 1):+.0f}%" if med[2] > 0
                    else ("inf" if med[3] > 0 else "0%"))
            ok_s = "yes" if all_ok else ("some" if any(p[4] for p in per_seed) else "NO")
            print(f"  {sp:>7.2f} {100*lf:>5.0f}% | {med[0]:>9.0f}{med[1]:>11.0f}{med[2]:>9.0f}"
                  f" | {med[3]:>8.0f} | {gain:>16} {ok_s:>13}")

    d = pd.DataFrame(rows)
    if a.save:
        OUT.mkdir(exist_ok=True)
        d.to_csv(OUT / "m60_segregation_ckt24.csv", index=False)

    print("\n" + "=" * 92)
    for sp in a.setpoints:
        sub = d[d.setpoint == sp]
        c = sub.cost_of_segregation_pct.replace([np.inf, -np.inf], np.nan).dropna()
        if len(c):
            print(f"  source {sp:.2f}:  joint beats segregated by {c.min():+.0f}% to "
                  f"{c.max():+.0f}%  (median {c.median():+.0f}%)"
                  f"   | union AC-feasible {int(sub.union_ac_feasible.sum())}/{len(sub)}")
    print(f"\n  Ckt7 (M58, at its own honest setpoint 1.04): +43% to +129%, median +75%.")
    print("  If Ckt24 lands in the same band, the headline is not one feeder.\n")
    if a.save:
        print("  saved -> outputs/m60_segregation_ckt24.csv")


if __name__ == "__main__":
    main()
