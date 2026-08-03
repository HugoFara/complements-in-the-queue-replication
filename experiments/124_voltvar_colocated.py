"""M98 -- IEEE 1547 CATEGORY B VOLT/VAR ON THE TWO CELLS THAT NOW CARRY THE CLAIM

WHY
---
After Batch D2 retracted n13f4, the irreducible residual rests on exactly two cells: EPRI
Ckt24 and SMART-DS under co-located placement. The adversarial referee's second major comment
is aimed precisely there:

  "The unity-power-factor choice is defended as conservative via the r >> x argument and a
  two-feeder Volt/VAr test -- both on ENWL LV circuits, and with a different (greedy) packer
  that is explicitly not comparable to the headline instrument. But the irreducible residual
  lives on Ckt24 and SMART-DS, which are MV-class feeders where x/r is materially higher and
  Category B reactive authority is real... the private-constraint mechanism is exactly the
  configuration reactive support attacks best: the blocking constraint is the SITE'S OWN node
  voltage, and the site's own inverter is the device with maximum var authority over it. If a
  gen request with Cat B Volt/VAr becomes individually feasible at its own PCC, Proposition
  1's hypothesis fails and the pair ceases to be strictly simultaneous."

That is the sharpest threat left standing to the paper, and it is now the ONLY threat to the
headline, because the two cells it names are the whole of the remaining evidence.

DESIGN
------
Two decisions, both deliberate.

1. VOLT/VAR LIVES IN THE AC VERIFICATION, NOT IN THE MILP. The certifier keeps its
   unity-power-factor linear model and every accept-set it proposes is re-verified by an AC
   solve in which the requests DO regulate. A set that survives is therefore AC-true under
   Volt/VAr regardless of what the linear model believed, which is the same epistemic
   standard as the rest of the paper. Putting Q(V) inside the MILP would need a linearized
   reactive model whose error would then have to be validated on the optimizer's own
   trajectory -- the M42 lesson -- and would buy nothing the AC check does not already give.

2. THE Q(V) FIXED POINT IS IMPLEMENTED HERE, IN BOTH ENGINES, RATHER THAN DELEGATED TO
   pandapower CONTROLLERS AND OpenDSS InvControl. Those are different implementations with
   different convergence rules and different defaults; a difference between Ckt24 and
   SMART-DS would then be uninterpretable. One loop, one curve, both feeders.

The curve is the IEEE 1547-2018 Category B default characteristic, in per-unit of the
request's apparent-power rating, with Q positive = injecting vars (voltage-raising):

    V <= 0.92        Q = +0.44
    0.92 .. 0.98     linear +0.44 -> 0
    0.98 .. 1.02     Q = 0                (deadband)
    1.02 .. 1.08     linear 0 -> -0.44
    V >= 1.08        Q = -0.44

Requests are modelled at their nameplate as apparent-power rating, so a 100 kW request has
44 kvar of authority in each direction. The loop re-solves until every regulating bus moves
less than `tol` p.u., or `max_iter` is reached; non-convergence is reported, never silently
accepted as feasible.

WHAT IS MEASURED
----------------
Two things, and the second is the one that decides Proposition 1.

  A. R1 and R* recomputed end-to-end with Volt/VAr active in every AC check.
  B. THE PROPOSITION 1 HYPOTHESIS DIRECTLY: for each certified mixed pair from exp120, is
     either half individually feasible at the fixed point ONCE ITS OWN INVERTER REGULATES?
     If yes for any pair, that pair stops being strictly simultaneous and the referee is
     right. This is two AC solves per pair and needs no optimizer at all.

Run:
  uv run python experiments/124_voltvar_colocated.py --cells ckt24 --save
  uv run python experiments/124_voltvar_colocated.py --cells ckt24 smartds --hypothesis-only
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

_alt = __import__("109_alternating_rounds")
_cert = __import__("110_simultaneity_certificate")
_share = __import__("120_certified_share")

OUT = Path(__file__).resolve().parents[1] / "outputs"

# IEEE 1547-2018 Category B default Volt/VAr characteristic, (V p.u., Q per-unit of rating).
CAT_B = [(0.92, 0.44), (0.98, 0.0), (1.02, 0.0), (1.08, -0.44)]


class _Stats:
    """Q(V) convergence, counted across every wrapper a draw creates."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.evals = 0
        self.failures = 0
        self.iters = []

    def note(self, iters, ok):
        self.evals += 1
        self.iters.append(iters)
        if not ok:
            self.failures += 1

    @property
    def fail_pct(self):
        return 100.0 * self.failures / self.evals if self.evals else float("nan")


STATS = _Stats()


def q_of_v(v, curve=CAT_B):
    """Piecewise-linear Q(V), flat outside the end points. Q > 0 injects vars."""
    xs = [p[0] for p in curve]
    ys = [p[1] for p in curve]
    return float(np.interp(v, xs, ys, left=ys[0], right=ys[-1]))


class VoltVarWrapper:
    """A feeder whose `evaluate` runs a Q(V) fixed point over the ACCEPTED requests.

    Everything else -- the certifier, the linear model, the anchor machinery -- is the base
    feeder's, untouched. Only the AC verdict changes, which is exactly the intended scope:
    the search still reasons at unity power factor and only reality is allowed to regulate.
    """

    def __init__(self, base, tol=1e-4, max_iter=60, curve=CAT_B, damp=1.0):
        self._b = base
        self.tol, self.max_iter, self.curve = tol, max_iter, curve
        # The iteration is SLOW, not unstable, and the distinction cost a wrong fix.
        #
        # Honest accounting showed 1.66% of evaluations never reaching a fixed point, one
        # SMART-DS draw at 20.5%. That looked like overshoot, so a relaxation step went in --
        # and made it marginally WORSE (1.86%). Replaying a failing accept-set settles it:
        # the trajectory is monotone, not a limit cycle, converging geometrically with a
        # ratio near 0.8, and it needs 22 iterations against a cap of 20. Damping only slows
        # it further: the same set takes 22 undamped, 39 at damp 0.5, 81 at damp 0.2.
        #
        # So the cap is the fix and damping is not. `damp` stays as a knob for a feeder that
        # genuinely oscillates -- and it costs nothing when it is 1.0 -- but the default is
        # undamped with enough iterations to finish. Neither setting can move the answer:
        # the fixed point is where q equals q_target(v), and both change only the path to it.
        self.damp = float(damp)
        self._q_prev: dict = {}
        # Per-instance counters under-report by construction: `with_anchor` builds a NEW
        # wrapper for every anchored feeder, and `alternate` builds one per half-round, so
        # everything those wrappers do is discarded when they go out of scope. The first
        # end-to-end run reported 32 non-convergences for seventeen consecutive draws --
        # the base wrapper's total, frozen, not the computation's. Counting is therefore
        # module-level, and STATS.reset() is called per draw.
        self.vv_iters, self.vv_failures = [], 0

    def __getattr__(self, k):                 # delegate the rest of the Feeder API
        return getattr(self._b, k)

    def _q_targets(self, projects, accepted_ids, vbus):
        """Q(V) for the GENERATION requests only.

        IEEE 1547 governs distributed energy resources, so the var authority belongs to the
        generation half of a site, not to the load half -- an EV depot or industrial draw is
        modelled here as a constant-power load and has no 1547 obligation. Granting it the
        same +-0.44 would double the reactive demand at every co-located bus, which is both
        unphysical and, on a feeder whose substation transformer already sits near its cap,
        enough to swamp the very effect this file is measuring.
        """
        q = {}
        for p in projects:
            if p.id not in accepted_ids or float(p.p_mw) >= 0:
                continue                      # loads do not regulate
            v = vbus.get(int(p.bus))
            if v is None:
                continue
            q[p.id] = q_of_v(v, self.curve) * abs(float(p.p_mw))
        return q

    def _damped(self, rid, q_target):
        q = self._q_prev.get(rid, 0.0)
        q = q + self.damp * (q_target - q)
        self._q_prev[rid] = q
        return q

    def evaluate(self, ctx, projects, accepted_ids):
        raise NotImplementedError                # engine-specific, see subclasses below


class VoltVarPP(VoltVarWrapper):
    """pandapower path: set q_mvar on the staged request loads, then DELEGATE the verdict.

    The wrapper must not reimplement `evaluate`. Each feeder in this corpus carries its own
    limit logic -- Ckt24 excludes pre-existing overloads (transformer 468, a 25 kVA service
    unit, sits at 310% with nothing connected), the ENWL feeders carry a non-worsening clamp,
    the hard-limit variants carry book limits over a reduced candidate pool. Re-deriving the
    verdict here silently replaced all of that with the generic rule and reported every set
    as infeasible against a baseline violation it did not cause.
    """

    def evaluate(self, ctx, projects, accepted_ids):
        b = self._b
        net, rows = b._staged(projects)
        by_id = {p.id: p for p in projects}
        for rid in rows:                       # start from unity, as the search assumed
            net.load.at[rows[rid], "q_mvar"] = 0.0
        prev, out, converged = None, None, False
        self._q_prev = {}          # each evaluate starts from unity, as the search assumed
        for it in range(self.max_iter):
            out = b.evaluate(ctx, projects, accepted_ids)
            if out[3].get("diverged"):
                self.vv_failures += 1
                STATS.note(it + 1, False)
                return out
            vbus = {int(bi): float(v) for bi, v in net.res_bus.vm_pu.items()}
            cur = np.array([vbus.get(int(by_id[r].bus), 1.0) for r in sorted(accepted_ids)])
            # Nothing to regulate is CONVERGED, not a failure. Counting it as non-convergence
            # is how the first pass reported dozens of them: an accept-set with no accepted
            # generation has no Q(V) fixed point to reach, so the loop ran to max_iter and
            # fell through to the else-branch.
            if not len(cur) or not self._q_targets(projects, accepted_ids, vbus):
                converged = True
                break
            if prev is not None and np.max(np.abs(cur - prev)) < self.tol:
                converged = True
                break
            prev = cur
            for rid, qv in self._q_targets(projects, accepted_ids, vbus).items():
                # generation exports (p_mw < 0); Q > 0 must INJECT vars, i.e. negative load q
                qd = self._damped(rid, qv)
                net.load.at[rows[rid], "q_mvar"] = -qd
        else:
            self.vv_failures += 1
            out = b.evaluate(ctx, projects, accepted_ids)
        self.vv_iters.append(it + 1)
        STATS.note(it + 1, converged)
        return out


class VoltVarDSS(VoltVarWrapper):
    """OpenDSS path (SMART-DS): edit each staged request Load's kvar, then delegate."""

    def evaluate(self, ctx, projects, accepted_ids):
        b = self._b
        b._stage(projects)
        c = b._ctx
        by_id = {p.id: p for p in projects}
        for p in projects:
            c.Command(f"Load.{p.id}.kvar=0")
        prev, out, converged = None, None, False
        self._q_prev = {}          # each evaluate starts from unity, as the search assumed
        for it in range(self.max_iter):
            out = b.evaluate(ctx, projects, accepted_ids)
            if out[3].get("diverged"):
                self.vv_failures += 1
                STATS.note(it + 1, False)
                return out
            vbus = {}
            for p in projects:
                if p.id not in accepted_ids:
                    continue
                c.Circuit.SetActiveBus(p.bus_name)
                pu = np.asarray(c.Bus.puVmagAngle())[0::2]
                pu = pu[pu > 0]
                vbus[int(p.bus)] = float(pu.min()) if len(pu) else 1.0
            cur = np.array([vbus.get(int(by_id[r].bus), 1.0) for r in sorted(accepted_ids)])
            # Nothing to regulate is CONVERGED, not a failure. Counting it as non-convergence
            # is how the first pass reported dozens of them: an accept-set with no accepted
            # generation has no Q(V) fixed point to reach, so the loop ran to max_iter and
            # fell through to the else-branch.
            if not len(cur) or not self._q_targets(projects, accepted_ids, vbus):
                converged = True
                break
            if prev is not None and np.max(np.abs(cur - prev)) < self.tol:
                converged = True
                break
            prev = cur
            for rid, qv in self._q_targets(projects, accepted_ids, vbus).items():
                qd = self._damped(rid, qv)
                c.Command(f"Load.{rid}.kvar={-qd * 1000.0}")
        else:
            self.vv_failures += 1
            out = b.evaluate(ctx, projects, accepted_ids)
        self.vv_iters.append(it + 1)
        STATS.note(it + 1, converged)
        return out


def wrap(cell, f):
    return VoltVarDSS(f) if cell == "smartds" else VoltVarPP(f)


def vv_family(cell, fam, base_f):
    """`fam`'s hooks with every AC verdict regulating -- measurement A of this file.

    The hypothesis check below re-tests UNITY-POWER-FACTOR pairs under regulation, which
    answers whether a given pair survives but not what a Volt/VAr-aware process would find.
    Those are different questions, and on Ckt24 they have different answers: 42 of 142 pairs
    stop being strictly simultaneous, but only 16 stay jointly feasible at all, so the pairs
    the unity-PF search chose are not the pairs to re-price.

    Answering the second question needs R1 and R* recomputed end to end with Q(V) live in
    every AC check. The certifier still reasons at unity power factor -- design decision 1 in
    the module docstring -- so the linear model is the base feeder's and only the verdict
    moves. Anchoring unwraps, anchors the base feeder, and re-wraps, so the anchor's own
    approvals regulate too.
    """
    def _with_anchor(fw, anchor_projects, anchor_ids):
        return wrap(cell, fam.with_anchor(getattr(fw, "_b", fw), anchor_projects, anchor_ids))

    def _reset(fw, projects=()):
        fam.reset(getattr(fw, "_b", fw), projects)

    return _alt.Family(f"{fam.name}_voltvar", lambda: wrap(cell, base_f),
                       fam.make_projects, _with_anchor, fam.certify,
                       f"{fam.desc} + IEEE 1547 Category B Volt/VAr in AC verification",
                       reset=_reset)


def end_to_end(cell, fam, f, k, size_kw, seeds, n_jobs):
    """R1 and R* with Q(V) active in every AC check, against the unity-PF cell it replaces."""
    vfam = vv_family(cell, fam, f)
    vf = vfam.make_feeder()
    rows = []
    hdr = (f"  {'seed':>4} | {'gen':>7} {'load':>7} {'seq':>7} {'JOINT':>7} {'alt':>7} | "
           f"{'R1%':>6} {'R*%':>6} | {'QVevals':>6} {'fail':>6} | {'s':>5}")
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for seed in seeds:
        t0 = time.time()
        STATS.reset()                     # per draw, across every wrapper the draw creates
        projects = vfam.make_projects(vf, seed, k, size_kw)
        vfam.reset(vf, projects)
        w = _alt.three_way_generic(vfam, vf, projects, n_jobs)
        joint = w["joint_kw"]
        best = -1.0
        for start in ("gen", "load"):
            acc = _cert.fixed_point_accept_set(vfam, vf, projects, n_jobs, start)
            best = max(best, sum(abs(p.p_mw) for p in acc) * 1000.0)
        r1, rstar = joint - w["best_sequential_kw"], joint - best
        rows.append(dict(cell=cell, seed=seed, k=k, size_kw=size_kw, regime="cat_b_voltvar",
                         **w, alternation_kw=best, r1_kw=r1, rstar_kw=rstar,
                         r1_pct=100 * r1 / joint if joint else np.nan,
                         rstar_pct=100 * rstar / joint if joint else np.nan,
                         vv_evals=STATS.evals, vv_failures=STATS.failures,
                         vv_fail_pct=STATS.fail_pct,
                         vv_iters_median=float(np.median(STATS.iters)) if STATS.iters else np.nan,
                         dt=time.time() - t0))
        print(f"  {seed:>4d} | {w['gen_alone_kw']:>7.0f} {w['load_alone_kw']:>7.0f} "
              f"{w['best_sequential_kw']:>7.0f} {joint:>7.0f} {best:>7.0f} | "
              f"{100 * r1 / joint if joint else 0:>6.1f} "
              f"{100 * rstar / joint if joint else 0:>6.1f} | "
              f"{STATS.evals:>6d} {STATS.fail_pct:>5.1f}% | "
              f"{time.time() - t0:>5.0f}", flush=True)
    return rows


def hypothesis_check(cell, fam, f, src, seeds, reanchor=True):
    """Proposition 1's hypothesis, under Volt/VAr, on the pairs exp120 certified.

    For each certified pair: is either half individually feasible on top of the fixed point
    once its own inverter regulates? Any 'yes' breaks strict simultaneity for that pair.

    RE-ANCHORING THE SUBSTATION REFERENCE (`reanchor`, Ckt24 only)
    -------------------------------------------------------------
    The first pass scored Ckt24 22/22 INCONCLUSIVE, for a reason that has nothing to do with
    any pair. That feeder ships its substation transformer overloaded, so exp60 replaces the
    100% cap with a non-worsening rule against the SHIPPED loading (111.0589%) -- an equality
    the pristine feeder sits exactly on. Switch the already-approved inverters from unity to
    a Category B curve and they draw reactive power the shipped baseline never carried; the
    substation moves by ~0.003% and a zero-tolerance equality rejects the base itself.

    The question being asked is relative: does the HALF become feasible on top of the fixed
    point? Its control is therefore the fixed point under the SAME inverter regime, not the
    shipped feeder under a different one. So the reference is re-read at `acc` with Volt/VAr
    active, and never loosened below exp60's shipped value. Both are recorded per pair.
    """
    d = pd.read_csv(src)
    sub = d[(d.cell == cell)]
    vv = wrap(cell, f)
    shipped_ref = getattr(f, "sub_base_pct", None)
    rows = []
    for _, r in sub.iterrows():
        if int(r.seed) not in seeds:
            continue
        pairs = [p for p in str(r.certified_pairs).split(";") if "+" in p]
        if not pairs:
            continue
        projects = fam.make_projects(f, int(r.seed), int(r.k), float(r.size_kw))
        acc = {s for s in str(r.fixed_point_ids).split(";") if s}
        ref = shipped_ref
        if reanchor and shipped_ref is not None:
            f.sub_base_pct = shipped_ref
            fam.reset(f, projects)
            _o, _m0, _v0, i0 = vv.evaluate(f.ctx, projects, acc)
            ref = max(float(shipped_ref), float(i0.get("sub_pct", shipped_ref)))
            f.sub_base_pct = ref
        # A pair tested against a base that still fails proves nothing either way, so that
        # verdict is recorded as inconclusive rather than scored.
        fam.reset(f, projects)
        base_ok, _m, _vi, _i = vv.evaluate(f.ctx, projects, acc)
        for pr in pairs:
            u, v = pr.split("+")
            res = {}
            for half in (u, v):
                fam.reset(f, projects)
                ok, _mw, _vio, _inf = vv.evaluate(f.ctx, projects, acc | {half})
                res[half] = bool(ok)
            fam.reset(f, projects)
            both, _m2, _v2, _i2 = vv.evaluate(f.ctx, projects, acc | {u, v})
            broke = bool(res[u] or res[v])
            rows.append(dict(cell=cell, seed=int(r.seed), pair=pr,
                             sub_ref_shipped=shipped_ref, sub_ref_used=ref,
                             base_feasible_vv=bool(base_ok),
                             gen_alone_feasible=res[u], load_alone_feasible=res[v],
                             both_feasible=bool(both),
                             hypothesis_broken=(bool(base_ok) and broke),
                             still_simultaneous=(bool(base_ok) and not broke and bool(both)),
                             inconclusive=(not bool(base_ok))))
    return rows, vv


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cells", nargs="+", default=["ckt24", "smartds"])
    ap.add_argument("--seeds", type=int, nargs="+", default=list(range(20)))
    ap.add_argument("--n-jobs", type=int, default=6)
    ap.add_argument("--src", default="m94_certified_share.csv")
    ap.add_argument("--hypothesis-only", action="store_true")
    ap.add_argument("--end-to-end", action="store_true",
                    help="measurement A: recompute R1 and R* with Q(V) live in "
                         "every AC check, instead of re-testing unity-PF pairs")
    ap.add_argument("--e2e-seeds", type=int, nargs="+", default=list(range(10)))
    ap.add_argument("--no-reanchor", action="store_true",
                    help="score against the SHIPPED substation reference "
                         "(reproduces the 22/22-inconclusive first pass)")
    ap.add_argument("--out", default="m98_voltvar.csv")
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()

    print("\n  M98 -- IEEE 1547 CATEGORY B VOLT/VAR ON THE CO-LOCATED CELLS\n")
    print("  Q(V) runs inside the AC verification only; the certifier still reasons at")
    print("  unity power factor, so every surviving set is AC-true WITH regulation.")
    print("  Curve: +-0.44 pu of rating, deadband 0.98-1.02, saturating at 0.92/1.08.\n")

    if a.end_to_end:
        allrows = []
        for cell in a.cells:
            fam, f, k, size_kw = _share.CELLS[cell](a.n_jobs)
            print(f"  {fam.desc}  --- R1/R* END TO END UNDER CATEGORY B\n")
            allrows += end_to_end(cell, fam, f, k, size_kw, a.e2e_seeds, a.n_jobs)
            print()
        d = pd.DataFrame(allrows)
        print("=" * 78)
        print(d.groupby("cell")[["r1_pct", "rstar_pct"]].median().to_string())
        print("\n  Compare against the unity-power-factor cells these replace: the")
        print("  hypothesis check asks whether a GIVEN pair survives regulation; this asks")
        print("  what a process that knew about regulation would have found instead.\n")
        if a.save:
            OUT.mkdir(exist_ok=True)
            d.to_csv(OUT / a.out, index=False)
            print(f"  saved -> outputs/{a.out}\n")
        return

    allrows = []
    for cell in a.cells:
        fam, f, k, size_kw = _share.CELLS[cell](a.n_jobs)
        print(f"  {fam.desc}")
        rows, vv = hypothesis_check(cell, fam, f, OUT / a.src, set(a.seeds),
                                    reanchor=not a.no_reanchor)
        d = pd.DataFrame(rows)
        if not len(d):
            print("    no certified pairs found for this cell\n")
            continue
        usable = d[~d.inconclusive]
        print(f"    pairs tested                    : {len(d)}")
        print(f"    inconclusive (base fails w/ VV) : {int(d.inconclusive.sum())}")
        print(f"    usable                          : {len(usable)}")
        if len(usable):
            print(f"      gen half feasible alone       : {int(usable.gen_alone_feasible.sum())}")
            print(f"      load half feasible alone      : {int(usable.load_alone_feasible.sum())}")
            print(f"      HYPOTHESIS BROKEN             : {int(usable.hypothesis_broken.sum())}"
                  f" / {len(usable)}")
            print(f"      still strictly simultaneous   : "
                  f"{int(usable.still_simultaneous.sum())} / {len(usable)}")
        print(f"    Q(V) iterations: median {int(np.median(vv.vv_iters))}, "
              f"non-converged {vv.vv_failures}")
        print()
        allrows += rows

    if allrows:
        d = pd.DataFrame(allrows)
        print("=" * 78)
        print("\n  SUMMARY\n")
        print(d.groupby("cell").agg(
            pairs=("pair", "size"),
            inconclusive=("inconclusive", "sum"),
            gen_alone=("gen_alone_feasible", "sum"),
            load_alone=("load_alone_feasible", "sum"),
            hypothesis_broken=("hypothesis_broken", "sum"),
            still_simultaneous=("still_simultaneous", "sum")).to_string())
        print("\n  Every pair that stays strictly simultaneous is one whose blocking")
        print("  constraint its own inverter's full Category B authority cannot clear.")
        print("  Any pair that does not is a counterexample to Proposition 1's hypothesis")
        print("  under realistic inverter capability, and the headline must be scoped to")
        print("  unity-power-factor requests.\n")
        if a.save:
            OUT.mkdir(exist_ok=True)
            d.to_csv(OUT / a.out, index=False)
            print(f"  saved -> outputs/{a.out}\n")


if __name__ == "__main__":
    main()
