"""M103 -- THE HEADLINE FEEDER'S SHIPPED YEAR, WHICH THE PAPER SAYS IT DOES NOT HAVE

WHY
---
The manuscript states twice that the year-round question cannot be put to the feeder carrying
its headline -- "Ckt24 ships no load shapes, so the same question cannot be put to the feeder
carrying the headline", and "EPRI and ENWL ship no time series, so this cannot be tested on
them". Both are false, and the second is false in a way a referee can check in thirty seconds:

  data/opendss/ckt24/master_ckt24.dss
    New Loadshape.LS_PhaseA npts=8760 interval=1 mult=(file=LS_PhaseA.txt) action=normalize
    ... LS_PhaseB, LS_PhaseC, LS_ThreePhase, Other_Bus_Load

  data/opendss/ckt24/Allocated_Loads_ckt24.dss
    3,890 of 3,890 loads carry `yearly=LS_PhaseX`

EPRI ships a full normalized year, per phase, bound to every load. So does the ENWL LVNS
release, whose published dataset pairs the 25 OpenDSS network models with time-series demand
and low-carbon-technology profiles; our cached copy of the models simply does not include the
profile archive.

AND THE SNAPSHOT IS THE ANNUAL PEAK
-----------------------------------
`action=normalize` puts the maximum of each shape at 1.0, and the allocated loads are set at
that maximum. The paper's snapshot is therefore hour-of-annual-peak, and the feeder spends the
year well below it: the three phase shapes have annual means of 0.476, 0.482 and 0.477, and
minima near 0.21.

That direction matters. For an EXPORT-limited allocation, light load is the adverse case, not
the benign one -- less local demand to absorb the generation leg, so the overvoltage the
co-located pair is built around gets worse. Certifying at the annual peak is optimistic for
exactly the constraint the headline residual lives on. This is the same defect exp127 found on
SMART-DS from the opposite direction (a NON-coincident nameplate peak), now on the feeder that
carries the 12.5%.

WHAT THIS FILE MEASURES
-----------------------
For each draw: take the accept-sets the snapshot study produced, and re-check every one of the
8,760 shipped hours by exact AC power flow.

  joint          the joint study's accept-set -- the numerator of the residual
  fixed_point    the alternating fixed point -- the segregated baseline
  baseline       no requests at all, which is the per-hour reference the substation
                 non-worsening rule needs (it is an equality against the SHIPPED loading, and
                 that loading is itself hourly; a rule read against the peak hour would be
                 vacuous at every other one)

Requests hold their nameplate at every hour. That is the "an agreement holds" arm of
Section 5.7 and the only one this release supports: Ckt24 ships demand shapes, not PV or EV
profiles, so giving the two legs their own measured profiles would mean importing them from
somewhere else and is a different experiment.

THE SOLVE
---------
OpenDSS yearly mode, one step per hour, `ControlMode=Static` so the substation regulator taps
as it would in service. The oracle is exp125's unchanged -- same exclusions, same tolerances,
same non-worsening rule -- with the per-hour baseline substituted for the snapshot one. The
request loads are pinned to a flat 8,760-point shape, because a load with no `yearly=` in
yearly mode silently inherits OpenDSS's `Default` daily curve and would be scaled by it.

Run:
  uv run python experiments/131_ckt24_yearly.py --seeds 0 1 2 --save
  uv run python experiments/131_ckt24_yearly.py --hours 720 --seeds 0   # a month, to smoke it
"""
from __future__ import annotations

import argparse
import multiprocessing as mp
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
_u24 = __import__("125_ckt24_unbalanced")

OUT = Path(__file__).resolve().parents[1] / "outputs"
NHOURS = 8760


def _flat_shape(f, n=NHOURS):
    """A constant 8,760-point shape for the request loads, written where the context can read
    it. Inline `mult=[1.0]` would define a ONE-point shape; whether OpenDSS wraps that over a
    year is a version-dependent detail we should not be relying on."""
    d = Path(_u24._TRACE_ROOT) / "flat"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "ones.txt"
    if not p.exists():
        p.write_text("\n".join(["1.0"] * n) + "\n")
    f._ctx.Command(f'New Loadshape.rq_flat npts={n} interval=1 mult=(file="{p}")')
    return p


def _prepare(f, projects, hours_mode=True):
    """Stage the requests, pin them to the flat shape, and put the circuit in yearly mode."""
    f._stage(projects)
    c = f._ctx
    _flat_shape(f)
    for p in projects:
        c.Command(f"Load.{p.id}.yearly=rq_flat")
    if hours_mode:
        c.Command("Set ControlMode=Static")
        c.Command("Set Mode=Yearly stepsize=1h number=1")


def _at_hour(f, projects, accepted_ids, h):
    """One hour of the shipped year, through exp125's own limit logic."""
    c = f._ctx
    f._set(accepted_ids)
    c.Solution.Hour(h)
    c.Solution.Seconds(0.0)
    return f.evaluate(f.ctx, projects, accepted_ids)


def extreme_hours(shapes=("LS_PhaseA", "LS_PhaseB", "LS_PhaseC", "Other_Bus_Load")):
    """The hours a year-round oracle actually has to check.

    The shipped shapes are normalized, so the snapshot (every multiplier at 1.0) is a
    SIMULTANEOUS annual maximum -- heavier than any real hour, and therefore already the
    binding case for undervoltage and for import-side thermal. What the snapshot does not
    cover is the other end: at the lightest hours there is less local demand to absorb the
    generation leg, so the overvoltage that the co-located residual lives on is worse.

    Each shape's annual minimum is one candidate. Reporting them separately rather than
    intersecting them keeps the oracle honest when the three phases bottom out at different
    hours, which they do.
    """
    root = Path(_u24.MASTER).parent
    out = {}
    for s in shapes:
        v = np.loadtxt(root / f"{s}.txt")
        out[f"min_{s}"] = int(np.argmin(v))
    return out


class YearOracle:
    """exp125's feeder, with `evaluate` required to hold at EVERY hour in `hours`.

    Only the verdict changes: the certifier keeps its snapshot linear model, and a set it
    proposes is accepted only if exact AC power flow clears it at each hour. That is the same
    epistemic split the Volt/VAr arm uses -- search on one model, judge on another, and never
    report a number the judge did not sign.

    The substation non-worsening rule needs care. It is an equality against the SHIPPED
    loading, and the shipped loading is itself hourly; reading a light hour against the peak
    reference would make the rule vacuous exactly where the feeder is most exportable. So the
    per-hour baseline is measured once, with no requests, and swapped in per hour.
    """

    def __init__(self, base, hours, ref=None):
        self._b = base
        self.hours = dict(hours)
        self._ref: dict = dict(ref or {})

    def __getattr__(self, k):
        return getattr(self._b, k)

    def _ensure(self, projects):
        """Yearly mode and a flat request shape, re-applied whenever the circuit is re-staged.

        `_stage` reloads the DSS context when the project list changes, and a reload puts the
        circuit back in snapshot mode with the request loads carrying no `yearly=`. A load
        with no yearly shape silently inherits OpenDSS's `Default` curve, which would scale
        every request by an unrelated daily profile -- silently, and in the direction that
        would flatter us at the light hours this file exists to check.
        """
        b = self._b
        b._stage(projects)
        if getattr(b, "_year_for", None) is b._staged:
            return
        c = b._ctx
        _flat_shape(b)
        for p in list(getattr(b, "_anchor", ())) + list(projects):
            c.Command(f"Load.{p.id}.yearly=rq_flat")
        c.Command("Set ControlMode=Static")
        c.Command("Set Mode=Yearly stepsize=1h number=1")
        b._year_for = b._staged

    def calibrate(self, projects):
        """Per-hour baseline, measured once on the pristine feeder with no requests.

        THE HOUR THE FEEDER BREAKS ITSELF. At its minimum-load hour the shipped model sits
        at vmax = 1.0514 pu with nothing connected, above its own 1.05 limit. A year-round
        oracle applying the absolute band therefore rejects every accept-set including the
        empty one, which is how the first run of this file returned zero hosting capacity on
        every draw. That is the same class of defect exp60 documents at the snapshot -- 45
        pre-broken transformers and a substation shipped at 118.68% -- and it gets the same
        treatment: where the BASELINE is already outside a limit at an hour, the rule at that
        hour becomes non-worsening against the baseline rather than the absolute limit.

        Only the voltage band needs it. Across the oracle hours the worst baseline line
        loading is 90.0% and the worst transformer 99.6%, both inside; `thermal_ok` records
        that per hour so the exception stays auditable and cannot silently widen.
        """
        b = self._b
        self._ensure(projects)
        for h in self.hours.values():
            b._set(set())
            b._ctx.Solution.Hour(h)
            b._ctx.Solution.Solve()
            _ok, _mw, _vio, info = b.evaluate(b.ctx, projects, set())
            self._ref[h] = dict(
                sub_s=b._elem_s(f"Transformer.{b.sub_name}"),
                vmin=float(info["vmin"]), vmax=float(info["vmax"]),
                thermal_ok=bool(info["line_pct"] <= 100.0 + _u24.TOL_PCT
                                and info["trafo_pct"] <= 100.0 + _u24.TOL_PCT))
        bad = [h for h, r in self._ref.items() if not r["thermal_ok"]]
        if bad:
            print(f"      WARNING: baseline already thermally violating at hours {bad}; "
                  f"those hours are judged on a non-worsening voltage rule only")
        return self

    def evaluate(self, ctx, projects, accepted_ids):
        b = self._b
        self._ensure(projects)
        snap = (b.sub_base_s, b.VMIN_PU, b.VMAX_PU)
        worst, agg, mw = None, 0.0, 0.0
        try:
            for h in self.hours.values():
                r = self._ref.get(h)
                if r is not None:
                    b.sub_base_s = r["sub_s"]
                    # non-worsening exactly where the baseline is out of band, absolute
                    # everywhere else -- never a relaxation the baseline does not force
                    b.VMIN_PU = min(snap[1], r["vmin"])
                    b.VMAX_PU = max(snap[2], r["vmax"])
                b._ctx.Solution.Hour(h)
                ok, mw, vio, info = b.evaluate(ctx, projects, accepted_ids)
                agg += float(vio)
                if worst is None or vio > worst[2]:
                    worst = (ok, mw, vio, info)
        finally:
            b.sub_base_s, b.VMIN_PU, b.VMAX_PU = snap
        _ok, _m, _v, info = worst
        return (agg == 0.0), mw, agg, info


def year_family(fam, base_f, hours):
    """`fam`'s hooks with the year-round oracle in place of the snapshot one."""
    oracle = YearOracle(base_f, hours)

    def _with_anchor(fw, anchor_projects, anchor_ids):
        inner = fam.with_anchor(getattr(fw, "_b", fw), anchor_projects, anchor_ids)
        return YearOracle(inner, hours, ref=oracle._ref)   # same reference, not re-measured

    def _reset(fw, projects=()):
        fam.reset(getattr(fw, "_b", fw), projects)

    return _alt.Family(f"{fam.name}_year", lambda: oracle, fam.make_projects,
                       _with_anchor, fam.certify,
                       f"{fam.desc} + feasibility required at {len(hours)} shipped hours",
                       reset=_reset), oracle


_W: dict = {}


def _init_worker(f, projects):
    _W["f"], _W["projects"] = f, projects
    _prepare(f, projects)


def _chunk(args):
    """(set-name, accepted ids, hour block) -> per-hour violation and the info that explains it."""
    name, acc, lo, hi = args
    f, projects = _W["f"], _W["projects"]
    out = []
    for h in range(lo, hi):
        ok, _mw, vio, info = _at_hour(f, projects, acc, h)
        out.append((h, bool(ok), float(vio), float(info.get("vmin", np.nan)),
                    float(info.get("vmax", np.nan)), float(info.get("line_pct", np.nan)),
                    float(info.get("trafo_pct", np.nan)), float(info.get("sub_pct", np.nan))))
    return name, out


def year(f, projects, sets, n_jobs, hours=NHOURS, block=120):
    """Every hour of every named accept-set, in parallel over hour blocks."""
    tasks = [(nm, acc, lo, min(lo + block, hours))
             for nm, acc in sets.items() for lo in range(0, hours, block)]
    cols = ("hour", "ok", "vio", "vmin", "vmax", "line_pct", "trafo_pct", "sub_pct")
    res: dict = {nm: [] for nm in sets}
    ctx = mp.get_context("fork")
    with ctx.Pool(n_jobs, initializer=_init_worker, initargs=(f, projects)) as pool:
        done = 0
        for nm, out in pool.imap_unordered(_chunk, tasks):
            res[nm] += out
            done += len(out)
            if done % (block * 20) == 0:
                print(f"      {done}/{len(tasks) * block} hour-solves", flush=True)
    return {nm: pd.DataFrame(sorted(v), columns=cols) for nm, v in res.items()}


def restudy(a):
    """The residual re-measured with feasibility required across the shipped year."""
    hours = extreme_hours()
    tot = sum(np.loadtxt(Path(_u24.MASTER).parent / f"{s}.txt")
              for s in ("LS_PhaseA", "LS_PhaseB", "LS_PhaseC"))
    hours["max_total"] = int(np.argmax(tot))
    print(f"  oracle hours: {hours}\n")

    f = _u24.Ckt24DSSFeeder(_u24.MASTER)
    snap_fam = _u24.make_family(f, "EPRI Ckt24 UNBALANCED, co-located, 50 kW")
    fam, oracle = year_family(snap_fam, f, hours)

    rows = []
    hdr = (f"  {'seed':>4} | {'gen':>6} {'load':>6} {'seq':>6} {'JOINT':>6} {'alt':>6} | "
           f"{'R1%':>6} {'R*%':>6} | {'mixed':>5} {'cert%':>6} | {'yr bad':>7} | {'s':>5}")
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for seed in a.seeds:
        t0 = time.time()
        projects = fam.make_projects(f, seed, a.k, a.size_kw)
        fam.reset(oracle, projects)
        oracle.calibrate(projects)
        w = _alt.three_way_generic(fam, oracle, projects, a.n_jobs)
        joint = w["joint_kw"]
        joint_ids, _j = fam.certify(oracle, projects, a.n_jobs)
        best, best_ids = -1.0, set()
        for start in ("gen", "load"):
            acc = _cert.fixed_point_accept_set(fam, oracle, projects, a.n_jobs, start)
            t = sum(abs(p.p_mw) for p in acc) * 1000.0
            if t > best:
                best, best_ids = t, {p.id for p in acc}
        r1, rstar = joint - w["best_sequential_kw"], joint - best

        c = _cert.certificate(fam, oracle, projects, best_ids)
        mixed = c.pop("_mixed_pairs")
        _taken, cert_kw = _share.certified_disjoint_pairs(fam, oracle, projects,
                                                          best_ids, mixed)
        share = (100.0 * cert_kw / rstar) if rstar > 1e-9 else np.nan

        # the five-hour oracle is a claim about all 8,760: verify the set it chose
        res = year(f, projects, {"baseline": set(), "joint": set(joint_ids)},
                   a.n_jobs, a.hours)
        base = res["baseline"].set_index("hour")
        d = res["joint"].set_index("hour")
        # every limit read against the hour's own baseline where the baseline is already
        # outside it, absolute where it is not -- the rule `calibrate` documents, applied
        # to all 8,760 rather than to the five the oracle checked
        ok = ((d.vmin >= np.minimum(f.VMIN_PU, base.vmin) - _u24.TOL_PU)
              & (d.vmax <= np.maximum(f.VMAX_PU, base.vmax) + _u24.TOL_PU)
              & (d.line_pct <= np.maximum(100.0, base.line_pct) + _u24.TOL_PCT)
              & (d.trafo_pct <= np.maximum(100.0, base.trafo_pct) + _u24.TOL_PCT)
              & (d.sub_pct <= base.sub_pct + _u24.TOL_PCT))
        base_bad = int(((base.vmax > f.VMAX_PU + _u24.TOL_PU)
                        | (base.vmin < f.VMIN_PU - _u24.TOL_PU)
                        | (base.line_pct > 100.0 + _u24.TOL_PCT)
                        | (base.trafo_pct > 100.0 + _u24.TOL_PCT)).sum())
        rows.append(dict(seed=seed, k=a.k, size_kw=a.size_kw, oracle_hours=len(hours),
                         **w, alternation_kw=best, r1_kw=r1, rstar_kw=rstar,
                         r1_pct=100 * r1 / joint if joint else np.nan,
                         rstar_pct=100 * rstar / joint if joint else np.nan,
                         n_mixed_pairs=c["n_mixed_pairs"], certified_kw=cert_kw,
                         certified_share_pct=share,
                         verify_hours=len(d), verify_bad=int((~ok).sum()),
                         verify_pct_bad=100.0 * float((~ok).mean()),
                         baseline_bad_hours=base_bad, dt=time.time() - t0))
        print(f"  {seed:>4d} | {w['gen_alone_kw']:>6.0f} {w['load_alone_kw']:>6.0f} "
              f"{w['best_sequential_kw']:>6.0f} {joint:>6.0f} {best:>6.0f} | "
              f"{100 * r1 / joint if joint else 0:>6.1f} "
              f"{100 * rstar / joint if joint else 0:>6.1f} | {c['n_mixed_pairs']:>5d} "
              f"{share:>6.1f} | {int((~ok).sum()):>7d} | {time.time() - t0:>5.0f}", flush=True)
        if a.save:
            OUT.mkdir(exist_ok=True)
            pd.DataFrame(rows).to_csv(OUT / a.out, index=False)

    d = pd.DataFrame(rows)
    print("\n" + "=" * 96)
    print(f"\n  YEAR-ROUND: R1 median {d.r1_pct.median():.1f}%, "
          f"R* median {d.rstar_pct.median():.1f}%, positive in "
          f"{int((d.rstar_kw > 1e-9).sum())} of {len(d)}")
    print(f"  joint sets verified across all {int(d.verify_hours.iloc[0])} hours: "
          f"{int((d.verify_bad == 0).sum())} of {len(d)} clean, "
          f"worst {int(d.verify_bad.max())} bad hours")
    print("\n  SNAPSHOT (annual peak only) [m101_ckt24_unbalanced_50kw]: "
          "R1 16.7%, R* 12.5%, positive 7/10\n")
    if a.save:
        OUT.mkdir(exist_ok=True)
        d.to_csv(OUT / a.out, index=False)
        print(f"  saved -> outputs/{a.out}\n")


def solo_vs_pair(a):
    """Is a request feasible ALONE at each hour of the shipped year, and is its pair?

    WHY THIS REPLACED THE RE-STUDY
    ------------------------------
    Re-measuring R1 and R* with the year as the oracle does not work with this instrument,
    and the reason is worth recording. Under the year-round oracle nothing on this feeder is
    feasible alone at the light hours, so the certifier's repair loop -- propose a set from
    the linear model, AC-verify, drop requests until it passes -- strips one leg of every
    co-located pair, which makes the other leg worse, and walks the accept-set to empty. It
    reported zero hosting capacity on every draw. That is a defeat of the search heuristic by
    strict simultaneity, not a measurement of capacity, and reporting it as a residual would
    have been wrong in the direction that flatters us.

    What the year CAN answer without any optimizer is the question the residual is built on,
    asked directly: at each hour, how many single requests fit alone, and how many co-located
    pairs fit? Three AC solves per pair per hour, no search anywhere.
    """
    hours = dict(extreme_hours())
    tot = sum(np.loadtxt(Path(_u24.MASTER).parent / f"{s}.txt")
              for s in ("LS_PhaseA", "LS_PhaseB", "LS_PhaseC"))
    hours["max_total"] = int(np.argmax(tot))
    hours = {f"{k}@{v}": v for k, v in hours.items()}
    print(f"  hours: {hours}\n")

    f = _u24.Ckt24DSSFeeder(_u24.MASTER)
    fam = _u24.make_family(f, "EPRI Ckt24 UNBALANCED, co-located, 50 kW")
    snap = (f.VMIN_PU, f.VMAX_PU)
    rows = []
    for seed in a.seeds:
        projects = fam.make_projects(f, seed, a.k, a.size_kw)
        _prepare(f, projects)
        pairs = [(projects[i], projects[i + 1]) for i in range(0, len(projects), 2)]
        for name, h in hours.items():
            _o, _m, _v, base = _at_hour(f, projects, set(), h)
            # the hourly non-worsening rule of `calibrate`, same reasoning
            f.VMIN_PU, f.VMAX_PU = min(snap[0], base["vmin"]), max(snap[1], base["vmax"])
            try:
                g_ok = l_ok = p_ok = 0
                for u, v in pairs:
                    gen, load = (u, v) if u.p_mw < 0 else (v, u)
                    g_ok += bool(_at_hour(f, projects, {gen.id}, h)[0])
                    l_ok += bool(_at_hour(f, projects, {load.id}, h)[0])
                    p_ok += bool(_at_hour(f, projects, {u.id, v.id}, h)[0])
            finally:
                f.VMIN_PU, f.VMAX_PU = snap
            rows.append(dict(seed=seed, hour_name=name, hour=h, n_pairs=len(pairs),
                             gen_alone_ok=g_ok, load_alone_ok=l_ok, pair_ok=p_ok,
                             base_vmin=base["vmin"], base_vmax=base["vmax"],
                             base_line=base["line_pct"], base_trafo=base["trafo_pct"]))
            print(f"    seed {seed} {name:>28}: gen alone {g_ok:>2}/{len(pairs)}   "
                  f"load alone {l_ok:>2}/{len(pairs)}   PAIR {p_ok:>2}/{len(pairs)}   "
                  f"(baseline vmax {base['vmax']:.4f})", flush=True)
        if a.save:
            OUT.mkdir(exist_ok=True)
            pd.DataFrame(rows).to_csv(OUT / a.out, index=False)

    d = pd.DataFrame(rows)
    print("\n" + "=" * 92)
    print("\n  Feasible ALONE vs feasible AS A PAIR, by hour of the shipped year\n")
    print(d.groupby("hour_name").agg(
        draws=("seed", "nunique"), pairs=("n_pairs", "sum"),
        gen_alone=("gen_alone_ok", "sum"), load_alone=("load_alone_ok", "sum"),
        pair=("pair_ok", "sum")).to_string())
    print("\n  The snapshot the paper certifies at is the annual peak. If generation fits")
    print("  alone there and not at light load, while the pair fits at both, then the")
    print("  snapshot UNDERSTATES what only a joint study can seat.\n")
    if a.save:
        OUT.mkdir(exist_ok=True)
        d.to_csv(OUT / a.out, index=False)
        print(f"  saved -> outputs/{a.out}\n")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--mode", choices=["check", "restudy", "solo"], default="check")
    ap.add_argument("--k", type=int, default=24)
    ap.add_argument("--size-kw", type=float, default=50.0)
    ap.add_argument("--seeds", type=int, nargs="+", default=list(range(10)))
    ap.add_argument("--hours", type=int, default=NHOURS)
    ap.add_argument("--n-jobs", type=int, default=6)
    ap.add_argument("--out", default="m124_ckt24_yearly.csv")
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()
    if a.mode == "restudy":
        print("\n  M103b -- THE RESIDUAL RE-MEASURED WITH THE YEAR AS THE ORACLE\n")
        return restudy(a)
    if a.mode == "solo":
        print("\n  M103c -- SOLO VERSUS PAIR FEASIBILITY, HOUR BY HOUR\n")
        return solo_vs_pair(a)

    print("\n  M103 -- EPRI CKT24 CERTIFIED AGAINST ITS SHIPPED 8,760-HOUR YEAR\n")
    print("  The snapshot the paper certifies at is the annual PEAK (shapes are normalized,")
    print("  loads set at the maximum). Light load is the adverse case for an export-limited")
    print("  allocation, so this is the direction that can only cost us.\n")

    f = _u24.Ckt24DSSFeeder(_u24.MASTER)
    fam = _u24.make_family(f, "EPRI Ckt24 UNBALANCED, co-located, 50 kW")

    rows, hourly = [], []
    for seed in a.seeds:
        t0 = time.time()
        projects = fam.make_projects(f, seed, a.k, a.size_kw)
        fam.reset(f, projects)
        w = _alt.three_way_generic(fam, f, projects, a.n_jobs)
        joint_ids, _joint = fam.certify(f, projects, a.n_jobs)
        best, best_ids = -1.0, set()
        for start in ("gen", "load"):
            acc = _cert.fixed_point_accept_set(fam, f, projects, a.n_jobs, start)
            tot = sum(abs(p.p_mw) for p in acc) * 1000.0
            if tot > best:
                best, best_ids = tot, {p.id for p in acc}
        rstar = w["joint_kw"] - best
        print(f"    seed {seed}: joint {w['joint_kw']:.0f} kW ({len(joint_ids)} requests), "
              f"fixed point {best:.0f} kW, R* {rstar:.0f} kW  [{time.time() - t0:.0f}s]")

        sets = {"baseline": set(), "joint": set(joint_ids), "fixed_point": set(best_ids)}
        t1 = time.time()
        res = year(f, projects, sets, a.n_jobs, a.hours)
        # The substation rule is an equality against the SHIPPED loading, which is hourly.
        # exp125 reads it against the snapshot; here every hour gets its own reference, so a
        # light hour is judged against a light baseline rather than against the peak.
        base = res["baseline"].set_index("hour")
        for nm in ("joint", "fixed_point"):
            d = res[nm].set_index("hour")
            sub_ok = d.sub_pct <= base.sub_pct + _u24.TOL_PCT
            # `vio` already contains the snapshot-referenced substation term; recompute the
            # verdict with the hourly reference instead of trusting it.
            v_ok = ((d.vmin >= f.VMIN_PU - _u24.TOL_PU) & (d.vmax <= f.VMAX_PU + _u24.TOL_PU)
                    & (d.line_pct <= 100.0 + _u24.TOL_PCT)
                    & (d.trafo_pct <= 100.0 + _u24.TOL_PCT))
            ok = v_ok & sub_ok
            bad = d[~ok]
            rows.append(dict(seed=seed, accept_set=nm, k=a.k, size_kw=a.size_kw,
                             hours=len(d), n_infeasible=int((~ok).sum()),
                             pct_infeasible=100.0 * float((~ok).mean()),
                             joint_kw=w["joint_kw"], fixed_point_kw=best, rstar_kw=rstar,
                             worst_hour=int(bad.vio.idxmax()) if len(bad) else -1,
                             worst_vmax=float(d.vmax.max()), worst_vmin=float(d.vmin.min()),
                             worst_line=float(d.line_pct.max()),
                             worst_trafo=float(d.trafo_pct.max()),
                             n_volt=int((~v_ok).sum()), n_sub=int((~sub_ok).sum()),
                             dt=time.time() - t1))
            print(f"      {nm:>12}: infeasible at {int((~ok).sum()):>5} of {len(d)} hours "
                  f"({100.0 * float((~ok).mean()):>5.1f}%)  vmax {d.vmax.max():.4f}  "
                  f"vmin {d.vmin.min():.4f}  line {d.line_pct.max():.0f}%", flush=True)
            hh = d.reset_index()
            hh["seed"], hh["accept_set"] = seed, nm
            hourly.append(hh)
        if a.save:
            OUT.mkdir(exist_ok=True)
            pd.DataFrame(rows).to_csv(OUT / a.out, index=False)

    d = pd.DataFrame(rows)
    print("\n" + "=" * 88)
    print("\n  SUMMARY -- hours of the shipped year at which the snapshot allocation fails\n")
    print(d.groupby("accept_set").agg(
        draws=("seed", "size"), median_pct=("pct_infeasible", "median"),
        max_pct=("pct_infeasible", "max"), clean=("n_infeasible", lambda s: int((s == 0).sum())),
        worst_vmax=("worst_vmax", "max")).to_string())
    print("\n  A joint set that holds all year means the snapshot residual is a year-round")
    print("  quantity under a nameplate agreement. Failures are hours at which the shipped")
    print("  demand is too light to absorb the generation leg -- the coincidence objection,")
    print("  on the feeder carrying the headline.\n")
    if a.save:
        OUT.mkdir(exist_ok=True)
        d.to_csv(OUT / a.out, index=False)
        pd.concat(hourly).to_csv(OUT / a.out.replace(".csv", "_hourly.csv"), index=False)
        print(f"  saved -> outputs/{a.out}\n")


if __name__ == "__main__":
    main()
