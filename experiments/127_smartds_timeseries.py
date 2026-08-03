"""D1 -- THE SEGREGATION RESIDUAL AGAINST SMART-DS's OWN YEAR, CERTIFIED AT EVERY INTERVAL.

THE OBJECTION
-------------
Every SMART-DS number in the paper is certified against one snapshot. Reviewer 2 asks what a
snapshot is worth. On this feeder the answer is sharper than "one operating point among many":

SMART-DS ships every load profile normalized so its own maximum is exactly 1.0 -- verified here
for all 159 shapes -- so the nameplate kW in `Loads.dss` is that load's ANNUAL PEAK, and the
snapshot puts all 1,382 loads at their individual annual peaks simultaneously. That instant does
not occur. The feeder's true aggregate never exceeds 50.4% of it:

    coincident-peak (the snapshot)   7,022 kW
    true annual maximum              3,537 kW   (50.4%)
    median                           1,400 kW   (19.9%)
    annual minimum                     612 kW   ( 8.7%)

The error is not conservative, because it has opposite signs on the two tracks. Native load
ABSORBS generation, so 7 MW of phantom load flatters the generation track; it OPPOSES the EV
load, so the same phantom load penalizes the load track. A co-located residual is built out of
both, so it cannot be signed a priori -- it has to be measured.

WHAT THIS FILE DOES
-------------------
It replaces the snapshot with the shipped year: 35,040 fifteen-minute intervals driven by the
feeder's own 318 profile files, fetched from OEDI. Requests are constant, which is the carve-out
regime of Section 6.2 -- a co-located site under net-export control holds its net at all hours
by agreement, and only the feeder's native load moves.

Certifying against 35,040 intervals inside an optimizer is not affordable. Certifying against a
handful and hoping is not honest. So the oracle runs a LAZY CONSTRAINT loop:

  1. certify against a small critical set of intervals (initially the annual load extremes),
  2. VERIFY the resulting accept-set against all 35,040 intervals -- 75 s, measured,
  3. if any interval rejects it, add that interval to the critical set and re-certify.

It terminates only when the accept-set survives the entire year, so what it reports is a
year-round certificate, not a sample of one. The 192-interval sampling the Swiss cells use
(exp112, exp117, exp119) is a weaker instrument; it is used there because that feeder's data is
192 intervals long.

SELF-TEST
---------
At multiplier 1.0 the time-varying oracle must reproduce the published snapshot exactly: the
timeseries model differs from the one the paper solves only in that `yearly=` is uncommented
(Lines, LineCodes and Transformers are byte-identical, checked). `--self-test` asserts it.

Run:
  uv run python experiments/127_smartds_timeseries.py --seeds 0 1 2 3 --save
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
import weakref
from pathlib import Path

os.environ.setdefault("OMP_NUM_THREADS", "1")

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

_r = __import__("101_residual_smartds")           # MixedDSSFeeder, build_colocated, load
_alt = __import__("109_alternating_rounds")       # alternate, three_way_generic, Family
_seq = __import__("91_sequential_segregation")    # certify

REPO = Path(__file__).resolve().parents[1]
CACHE = REPO / "cache" / "smartds"
OUT = REPO / "outputs"

FEEDER = "uhs2_1247--udt10746"
N_INTERVALS = 35040                                # 15-minute steps over 2018
STEP_H = 0.25


# --------------------------------------------------------------------- the shipped year
class Year:
    """The feeder's own 35,040-interval load year, as multiplier matrices in the order the
    OpenDSS `Loads` collection walks.

    Built once per process and shared with every feeder copy: at 159 x 35,040 doubles per matrix
    it is 89 MB, which is worth paying once and never again.
    """

    def __init__(self, ts_dir: Path, prof_dir: Path):
        shapes = {}
        for line in (ts_dir / "LoadShapes.dss").read_text().splitlines():
            m = re.match(r"\s*New\s+Loadshape\.(\S+)", line, re.I)
            if m:
                shapes[m.group(1).lower()] = [f.split("/")[-1] for f in
                                              re.findall(r"file=([^)\s]+)", line, re.I)]
        # `yearly=` is commented out in the snapshot variant, so match it explicitly
        per_load = {}
        for line in (ts_dir / "Loads.dss").read_text().splitlines():
            m = re.match(r"\s*New\s+Load\.(\S+)", line, re.I)
            if m:
                y = re.search(r"(?<!!)\byearly=(\S+)", line)
                if y:
                    per_load[m.group(1).lower()] = y.group(1).lower()
        self.names = sorted(shapes)
        self.P = np.stack([np.loadtxt(prof_dir / shapes[n][0]) for n in self.names])
        self.Q = np.stack([np.loadtxt(prof_dir / shapes[n][1]) for n in self.names])
        assert self.P.shape == (len(self.names), N_INTERVALS), self.P.shape
        # the normalization the whole framing rests on, asserted rather than assumed
        assert np.allclose(self.P.max(axis=1), 1.0), "profiles are not peak-normalized"
        self._row = {n: i for i, n in enumerate(self.names)}
        self.per_load = per_load

    def rows_for(self, dss_load_names) -> np.ndarray:
        miss = [n for n in dss_load_names if n not in self.per_load]
        if miss:
            raise SystemExit(f"{len(miss)} loads carry no yearly shape, e.g. {miss[:3]}")
        return np.array([self._row[self.per_load[n]] for n in dss_load_names])


_YEAR: Year | None = None


def year() -> Year:
    global _YEAR
    if _YEAR is None:
        _YEAR = Year(CACHE / f"{FEEDER}_ts", CACHE / "profiles")
    return _YEAR


# --------------------------------------------------------------------- the oracle
class TimeSeriesDSSFeeder(_r.MixedDSSFeeder):
    """exp101's oracle, but the feeder's native load follows the shipped year and a set is
    feasible only if it is feasible at EVERY interval in `self.intervals`.

    The requests themselves are constant. That is the net-export-control regime the paper's
    Section 6.2 claims for co-located sites, not an oversight: a site that holds its net exchange
    by agreement presents the same injection at 03:00 and at 17:00, and only the feeder around it
    moves. The uncontrolled regime, where a photovoltaic leg follows irradiance and stops
    cancelling its load leg, is exp104's question and was answered on the Swiss feeder.
    """

    intervals: tuple = (0,)          # interval indices the oracle certifies against
    anchor_t: int = 0                # the interval the linear model is anchored at
    snapshot_mode: bool = False      # certify at nameplate, the operating point the paper uses
    anchor_ts: tuple = ()            # if set, certify once per anchor and keep the best
    regime: str = "rated"            # rated | shapes | banded  (see set_regime)
    beta: float = 0.0
    _pv_shape = None
    _ld_shape = None
    _site_of: dict | None = None

    # -------------------------------------------------- native-load bookkeeping
    def _capture_native(self):
        """Nameplate kW/kvar of the feeder's OWN loads, in `Loads` walk order.

        Called on the pristine circuit, before any request is declared, so the collection holds
        exactly the native loads and the index alignment is stable across reloads.
        """
        c = self._ctx
        names, kw, kvar = [], [], []
        i = c.Loads.First()
        while i:
            names.append(c.Loads.Name().lower())
            kw.append(c.Loads.kW())
            kvar.append(c.Loads.kvar())
            i = c.Loads.Next()
        self.n_native = len(names)
        self.base_kw = np.asarray(kw)
        self.base_kvar = np.asarray(kvar)
        self.rows = year().rows_for(names)
        self._at_t = None

    def _set_interval(self, t: int):
        """Scale the native loads to interval `t`. Requests are declared after the natives, so
        walking the first `n_native` entries never touches a request.

        Under `snapshot_mode` the multipliers are ignored and every native load sits at its
        nameplate -- the paper's own operating point, reachable from this same oracle so that a
        snapshot-certified accept-set can be handed straight to `verify_year_multi`.
        """
        if self._at_t == t:
            return
        y = year()
        if self.snapshot_mode:
            mp, mq = self.base_kw, self.base_kvar
        else:
            mp = y.P[self.rows, t] * self.base_kw
            mq = y.Q[self.rows, t] * self.base_kvar
        c = self._ctx
        j, i = 0, c.Loads.First()
        while i and j < self.n_native:
            c.Loads.kW(mp[j])
            c.Loads.kvar(mq[j])
            j += 1
            i = c.Loads.Next()
        self._at_t = t

    def _stage(self, projects):
        """As exp101, except that a request is switched by its kW rather than by `enabled=`.

        Toggling `enabled` makes OpenDSS rebuild Y, which on this 3,443-node feeder costs 12.2 ms
        against 0.5 ms for writing sixteen kW values -- and the year-round loop switches accept-
        sets tens of thousands of times. A constant-power load at kW=0, kvar=0 injects nothing,
        so the two are the same circuit: checked over 25 random accept-sets, the two paths agree
        on vmin and vmax to 0.0 pu exactly, not approximately.
        """
        was = self._staged
        super()._stage(projects)
        if was is not projects:
            self._at_t = None            # a reload restored every native load to nameplate
            c = self._ctx
            for p in projects:
                c.Command(f"Load.{p.id}.enabled=yes")
                c.Loads.Name(p.id)
                c.Loads.kW(0.0)
            self._on = set()

    def _set(self, accepted_ids):
        """Membership as kW. Every request is rewritten each call: at sixteen requests the whole
        write costs less than the bookkeeping needed to avoid it."""
        want = set(accepted_ids)
        c = self._ctx
        for p in self._staged or ():
            c.Loads.Name(p.id)
            c.Loads.kW(p.p_mw * 1000.0 if p.id in want else 0.0)
        self._on = want

    def _fresh(self):
        c = super()._fresh()
        self._at_t = None
        return c

    # -------------------------------------------------- the feasibility test
    def _violation_now(self):
        c = self._ctx
        if not c.Solution.Converged():
            return float("inf"), None
        v = np.asarray(c.Circuit.AllBusMagPu())
        v = v[v > 0]
        if not len(v):
            return float("inf"), None
        vmin, vmax = float(v.min()), float(v.max())
        return (max(0.0, self.VMIN_PU - vmin) + max(0.0, vmax - self.VMAX_PU),
                (vmin, vmax))

    # -------------------------------------------------- how a request behaves in time
    def set_regime(self, regime: str, beta: float = 0.0,
                   pv_shape=None, ld_shape=None, site_of=None):
        """What the ACCEPTED requests do while the feeder's year runs underneath them.

        rated    every approved request sits at its full rated output at every interval. This is
                 the primary arm and it is not a simplification: an interconnection study rates a
                 request at nameplate and tests it against the worst network condition, which is
                 exactly this. It is also the only arm directly comparable with the snapshot,
                 whose requests are likewise constant.
        shapes   each leg follows a real profile from this dataset -- generation the site's own
                 measured photovoltaic output at 15-minute resolution, load one of the feeder's
                 own commercial demand shapes. The two peak at different hours, so a co-located
                 pair stops cancelling. This is the energy question rather than the capacity one.
        banded   the Section 6.2 carve-out as a constraint. A site with BOTH legs approved is
                 controllable and its net exchange is held within +/- beta x rating at every
                 interval, both extremes checked. A site with only one leg approved cannot honour
                 a net-exchange agreement -- there is nothing to import against -- so it falls
                 back to `shapes`. That asymmetry is not imposed; it is what a segregated process
                 physically is, and a single-signed track can never produce a controllable site.
        """
        self.regime, self.beta = regime, float(beta)
        self._pv_shape, self._ld_shape, self._site_of = pv_shape, ld_shape, site_of
        return self

    def _request_kw(self, p, t, controlled, direction):
        """Signed kW of one approved request at interval `t`, in kW (OpenDSS's sign convention:
        a negative load exports)."""
        rating = abs(p.p_mw) * 1000.0
        if self.regime == "rated":
            return p.p_mw * 1000.0
        if self.regime == "banded" and controlled:
            # the site net, carried on the generation leg; the load leg contributes nothing the
            # network can see once the agreement holds
            return direction * self.beta * rating if p.p_mw < 0 else 0.0
        shape = self._pv_shape if p.p_mw < 0 else self._ld_shape
        return p.p_mw * 1000.0 * float(shape[t])

    def _apply_requests(self, projects, accepted_ids, t, direction):
        """Set every LIVE request -- the approvals under test and the anchor baked into this
        feeder's base case -- to what it produces at interval `t`.

        The anchor has to be included or a sequential study would be scored with its first
        track's approvals frozen at rated output while the second track's follow the year. It
        also decides controllability: a site whose generation leg was approved in round one and
        whose load leg is approved in round two IS controllable from round two onward, which is
        the only way a segregated process ever reaches a controlled site.
        """
        by_id = {p.id: p for p in projects}
        live = [(rid, by_id[rid]) for rid in accepted_ids]
        live += [(p.id, p) for p in getattr(self, "_anchor", ())]
        ctrl = set()
        if self.regime == "banded" and self._site_of:
            legs: dict = {}
            for rid, p in live:
                legs.setdefault(self._site_of[rid], set()).add(p.p_mw < 0)
            ctrl = {s for s, sg in legs.items() if len(sg) == 2}
        c = self._ctx
        for rid, p in live:
            on_ctrl = bool(self._site_of) and self._site_of.get(rid) in ctrl
            c.Loads.Name(rid)
            c.Loads.kW(self._request_kw(p, t, on_ctrl, direction))
        return bool(ctrl)

    def _reset_state(self):
        """Bring the solver back to the empty queue before every verdict.

        THE ORACLE HAS TO BE A FUNCTION OF ITS ACCEPT-SET, AND WITHOUT THIS IT IS NOT. OpenDSS
        warm-starts each solve from the previous solution, so a verdict depends on what was
        solved before it. That is not a theoretical worry: enumerating seed 10 serially and in
        six processes -- the same 65,536 subsets, differing only in the order they are visited --
        disagreed on 480 of them. Replaying the disagreements showed why. Four of six sampled
        accept-sets that are comfortably feasible from a pristine circuit (vmin 0.964, vmax
        1.030) instead DIVERGED to between 1e8 and 1e17 pu when solved after a 500-subset walk,
        and `evaluate` scores a non-converged solve as infeasible, so the defect was silent.

        `Solution.InitSnap()` does not clear the poisoned state, and re-solving after a failure
        inherits it: both recovered 2 of 6. Solving the empty queue first -- always
        well-conditioned, and the same reference point at a given interval regardless of history
        -- recovered 6 of 6. It costs 36% per solve.
        """
        if self._on:
            keep = set(self._on)
            self._set(set())
            self._ctx.Solution.Solve()
            self._set(keep)

    def _check_at(self, projects, accepted_ids, t, reset=True):
        """The feasibility test at ONE interval, whatever the regime. Returns (violation, band).

        The band regime has two distinct dispatches to test; the extremes bound every admissible
        one, so two solves settle the interval. Every other regime is a single solve.
        """
        self._set_interval(int(t))
        worst_v, band = 0.0, None
        for direction in (-1.0, +1.0):
            any_ctrl = False
            if reset:
                self._reset_state()
            if self.regime != "rated":
                any_ctrl = self._apply_requests(projects, accepted_ids, int(t), direction)
            self._ctx.Solution.Solve()
            v, b = self._violation_now()
            if b is None:
                return float("inf"), None
            band = b if band is None else (min(band[0], b[0]), max(band[1], b[1]))
            worst_v = max(worst_v, v)
            if v > 0.0 or self.regime != "banded" or not any_ctrl:
                break
        return worst_v, band

    def evaluate(self, ctx, projects, accepted_ids):
        """Worst case over `self.intervals`, short-circuiting on the first rejection.

        Ordering matters for speed and not at all for the answer: most sets an optimizer proposes
        are infeasible, and a rejection costs one interval instead of all of them.
        """
        self._stage(projects)
        self._set(accepted_ids)
        by_id = {p.id: p for p in projects}
        mw = sum(abs(by_id[r].p_mw) for r in accepted_ids)
        worst = {"vmin": 2.0, "vmax": 0.0, "line_pct": 0.0, "trafo_pct": 0.0}
        for t in self.intervals:
            viol, band = self._check_at(projects, accepted_ids, t)
            if band is None:
                return False, 0.0, float("inf"), {"diverged": True, "t": int(t)}
            worst["vmin"] = min(worst["vmin"], band[0])
            worst["vmax"] = max(worst["vmax"], band[1])
            if viol > 0.0:
                worst["t"] = int(t)
                return False, 0.0, viol, worst
        return True, mw, 0.0, worst

    def build_gldf(self, ctx, buses):
        """The linear model is anchored at ONE interval, `anchor_t`.

        That is a warm start, not a claim: `certified_allocation` AC-verifies whatever the linear
        model proposes, and here AC-verification means every interval in `self.intervals`. A
        badly chosen anchor costs iterations, never correctness.
        """
        self._set_interval(int(self.anchor_t))
        self._reset_state()
        self._ctx.Solution.Solve()
        return super().build_gldf(ctx, buses)

    # -------------------------------------------------- the year-round check
    def verify_year_multi(self, projects, sets: dict, intervals=None, safe=False):
        """Every interval of the year against SEVERAL accept-sets.

        SET-MAJOR, and that is a correctness argument before it is a speed one. The warm-start
        poisoning that makes verdicts order-dependent comes from consecutive solves being far
        apart: interval-major jumps between unrelated accept-sets at every step, which is both
        slow (a distant warm start costs Newton iterations) and exactly the trajectory that
        diverges. Holding the accept-set fixed and stepping the interval walks a smooth path --
        adjacent quarter-hours differ by a few percent of load -- so each solve starts next to
        its answer.

        The empty-queue reset is therefore applied once per accept-set rather than once per
        verdict. `safe=True` restores the per-verdict reset; the two are checked against each
        other on a full year in `--check-verify`, and must return identical rejection counts.

        Identical accept-sets are solved once and shared: the alternation fixed points very often
        coincide with each other or with the joint optimum.
        """
        self._stage(projects)
        uniq: dict = {}
        for nm, ids in sets.items():
            uniq.setdefault(frozenset(ids), []).append(nm)
        keys = list(uniq)
        bad = {k: [] for k in keys}
        worst_v, worst_t = 0.0, None
        ts = list(range(N_INTERVALS) if intervals is None else intervals)
        for k in keys:
            self._set(set(k))
            self._reset_state()               # clear whatever the previous accept-set left
            for t in ts:
                viol, _band = self._check_at(projects, set(k), t, reset=safe)
                if viol > 0.0:
                    bad[k].append(int(t))
                    if viol > worst_v:
                        worst_v, worst_t = viol, int(t)
        per_arm = {nm: len(bad[k]) for k in keys for nm in uniq[k]}
        return per_arm, worst_t, worst_v

    def verify_year(self, projects, accepted_ids, intervals=None):
        """Every interval of the shipped year against a fixed accept-set.

        Returns (n_failing, worst_interval, band). This is the instrument that turns a
        critical-set certificate into a year-round one; at ~2.4 ms per interval a full sweep is
        about 75 s, which is affordable once per reported number and not once per oracle call.
        """
        self._stage(projects)
        self._set(accepted_ids)
        ts = range(N_INTERVALS) if intervals is None else intervals
        bad, worst_v, worst_t = [], 0.0, None
        for t in ts:
            self._set_interval(int(t))
            self._ctx.Solution.Solve()
            viol, band = self._violation_now()
            if viol > 0.0:
                bad.append(int(t))
                if viol > worst_v:
                    worst_v, worst_t = viol, int(t)
        return bad, worst_t, worst_v


def load(master: str, intervals, anchor_t=0) -> TimeSeriesDSSFeeder:
    f = TimeSeriesDSSFeeder(master)
    f._capture_native()
    f.intervals = tuple(int(t) for t in intervals)
    ts = (anchor_t,) if isinstance(anchor_t, (int, np.integer)) else tuple(int(t) for t in anchor_t)
    f.anchor_ts = ts
    f.anchor_t = int(ts[0])
    f.ctx = f.anchored_context(f.ctx, [], set())
    return f


# the shapes the non-rated regimes use, both from this dataset and this location
PV_SHAPE = "GSO_36.1090_-79.9153_15_180"     # the tilt/azimuth most of this feeder's own PV uses
LD_SHAPE = "com_kw_14231_pu"                 # a commercial demand shape from this feeder


def request_shapes(pv_name: str = PV_SHAPE, ld_name: str = LD_SHAPE):
    """Real 15-minute profiles for the two legs, from SMART-DS's own files.

    The generation shape is the measured photovoltaic output SMART-DS assigns to this feeder's
    own rooftop systems at this latitude; it peaks slightly above 1.0 because it is normalized to
    Pmpp rather than to inverter rating, which is the dataset's convention and is kept. The load
    shape is one of the feeder's own commercial demand profiles, the plausible partner for a
    co-located industrial request.
    """
    d = CACHE / "profiles"
    pv = np.loadtxt(d / f"{pv_name}.csv")
    ld = np.loadtxt(d / f"{ld_name}.csv")
    assert pv.size == ld.size == N_INTERVALS
    return pv, ld


def site_map(projects) -> dict:
    """request id -> co-location site. exp101's builder puts both legs of a pair at the same
    (bus, phase), which is what a point of common coupling is."""
    return {p.id: f"{p.bus_name}.{p.phase}" for p in projects}


def with_anchor(base: TimeSeriesDSSFeeder, anchor_projects, anchor_ids):
    """exp101's anchored copy, carrying the year bookkeeping across."""
    f2 = _r.with_anchor(base, anchor_projects, anchor_ids)
    f2.__class__ = TimeSeriesDSSFeeder
    for attr in ("n_native", "base_kw", "base_kvar", "rows", "intervals", "anchor_t",
                 "anchor_ts", "snapshot_mode", "regime", "beta",
                 "_pv_shape", "_ld_shape", "_site_of"):
        setattr(f2, attr, getattr(base, attr))
    f2._at_t = None
    return f2


# --------------------------------------------------------------------- interval selection
def seed_intervals(f, n_each: int = 2):
    """The intervals a fixed injection is most likely to break, before anything is known.

    Overvoltage binds when there is least native load to absorb export; undervoltage when there
    is most. Aggregate kW is a proxy for both and costs no power flow, so the loop starts from
    the annual extremes and the lazy-constraint step supplies whatever the proxy misses.
    """
    y = year()
    agg = (y.P[f.rows, :] * f.base_kw[:, None]).sum(axis=0)
    lo = np.argsort(agg)[:n_each]
    hi = np.argsort(agg)[-n_each:]
    return [int(t) for t in np.concatenate([lo, hi])], agg


def shaped_seed_intervals(agg, pv, ld, n_each: int = 2):
    """Extra starting intervals for the regimes where the REQUESTS also move.

    With requests held at rating, the intervals that bind are the annual load extremes and
    nothing else. Once a generation leg follows the sun that proxy is wrong in a specific way: at
    this feeder's annual minimum-load interval the photovoltaic output is exactly ZERO -- it is
    the small hours of an October night -- so the interval that looks worst for export is the one
    with no export. What binds instead is high output against low native load.

    Both directions have to be seeded, and on this feeder the one that actually binds is not the
    obvious one. The cuts the shaped arm kept adding were t=17358 and t=17362 -- day 180 at 19:30
    and 20:30, where photovoltaic output is 0.01 and 0.00 of rating, the site's own load is at
    0.967 and 0.972, and native load sits in the 98th percentile. The uncontrolled pair fails
    through its LOAD leg at the evening peak, not through its generation leg at midday, which is
    consistent with SMART-DS being undervoltage-limited throughout this corpus. Seeding only the
    export direction cost a cut per draw, and a cut is a full re-enumeration plus a year sweep.

    The lazy-constraint loop finds these on its own; seeding them just saves the cut.
    """
    a = agg / agg.max()
    p = pv / pv.max()
    d = ld / ld.max()
    out = set()
    for score in (p - a, p - d, p * (1.0 - a),      # export: output high, absorption low
                  a + d, d - p, a * d):             # import: site drawing while feeder is loaded
        out.update(int(t) for t in np.argsort(score)[-n_each:])
    return sorted(out)


# --------------------------------------------------------------------- the arms
def _certify(f, projects, n_jobs):
    """Certify `projects` against `f`'s whole interval set.

    With `f.anchor_ts` holding more than one interval the search is run once per anchor and the
    best result kept. That is not cherry-picking: every candidate is AC-verified against every
    interval in `f.intervals` before it can be returned, so both are valid certificates and the
    larger one is simply the better search. It matters because one anchor cannot serve both
    tracks -- overvoltage binds at minimum native load and undervoltage at maximum -- and a badly
    anchored linear model makes a track look smaller than it is, which would inflate the residual
    in the paper's favour.
    """
    f._staged = None
    f._keys = {p.bus: p.gldf_key for p in projects}
    anchors = getattr(f, "anchor_ts", None) or (f.anchor_t,)
    best_ids, best_kw = set(), 0.0
    for t in anchors:
        f.anchor_t = int(t)
        f._staged = None
        ids, kw = _seq.certify(f, projects, n_jobs)
        if kw > best_kw:
            best_ids, best_kw = ids, kw
    return best_ids, best_kw


def three_way_sets(f, projects, n_jobs):
    """exp109's `three_way_generic`, returning the accept-sets as well as the totals.

    The sets are what the year-round verification needs, and exp109's version discards them.
    Duplicated rather than added to shared machinery, whose return arity other runs depend on.
    """
    gen = [p for p in projects if p.p_mw < 0]
    ld = [p for p in projects if p.p_mw > 0]

    g_ids, g_kw = _certify(f, gen, n_jobs)
    l_ids, l_kw = _certify(f, ld, n_jobs)
    j_ids, joint_kw = _certify(f, projects, n_jobs)

    fg = with_anchor(f, gen, g_ids)
    l2_ids, l2_kw = _certify(fg, ld, n_jobs)
    fl = with_anchor(f, ld, l_ids)
    g2_ids, g2_kw = _certify(fl, gen, n_jobs)

    sets = {"gen_alone": g_ids, "load_alone": l_ids, "joint": j_ids,
            "seq_genfirst": g_ids | l2_ids, "seq_loadfirst": l_ids | g2_ids}
    return dict(gen_alone_kw=g_kw, load_alone_kw=l_kw, seg_parallel_kw=g_kw + l_kw,
                seq_genfirst_kw=g_kw + l2_kw, seq_loadfirst_kw=l_kw + g2_kw,
                best_sequential_kw=max(g_kw + l2_kw, l_kw + g2_kw),
                joint_kw=joint_kw), sets


def alternate_sets(f, projects, n_jobs, start="gen", max_rounds=None):
    """exp109's `alternate`, returning the fixed point's accept-set.

    Same loop, same order, same anchoring; only the return differs. Verified against exp109 on
    the snapshot oracle by `--self-test`.
    """
    max_rounds = _alt.MAX_ROUNDS if max_rounds is None else max_rounds
    gen = [p for p in projects if p.p_mw < 0]
    ld = [p for p in projects if p.p_mw > 0]
    tracks = [gen, ld] if start == "gen" else [ld, gen]
    pending = [list(tracks[0]), list(tracks[1])]
    accepted: list = []
    half_rounds, productive = 0, 0

    for _rnd in range(max_rounds):
        progressed = False
        for t in (0, 1):
            if not pending[t]:
                continue
            fa = with_anchor(f, accepted, {p.id for p in accepted}) if accepted else f
            ids, _kw = _certify(fa, pending[t], n_jobs)
            half_rounds += 1
            if ids:
                accepted.extend(p for p in pending[t] if p.id in ids)
                pending[t] = [p for p in pending[t] if p.id not in ids]
                productive += 1
                progressed = True
        if not progressed:
            break

    ids = {p.id for p in accepted}
    total = sum(abs(p.p_mw) for p in accepted) * 1000.0
    ok, _mw, _v, _i = f.evaluate(f.ctx, projects, ids)
    return total, ids, half_rounds, productive, bool(ok)


def draw_yearround(master, seed, k, size_kw, mismatch, n_jobs, intervals, anchor_t,
                   max_cuts=8, log=print, regime="rated", beta=0.0, shapes=None):
    """One draw, certified against every interval of the shipped year.

    Lazy constraints: solve every arm against the current critical interval set, verify each
    reported accept-set against all 35,040 intervals, and if any interval rejects any of them,
    add that interval and solve the draw again. All arms share one constraint set, so the
    comparison between them stays like-for-like at every iteration.
    """
    ivals = list(intervals)
    for cut in range(max_cuts + 1):
        f = load(master, ivals, anchor_t)
        projects = _r.build_colocated(f, k, seed, size_kw, size_kw * mismatch)
        if regime != "rated":
            pv, ld = shapes if shapes else request_shapes()
            f.set_regime(regime, beta, pv, ld, site_map(projects))
        r, sets = three_way_sets(f, projects, n_jobs)
        for st in ("gen", "load"):
            tot, ids, hr, prod, ok = alternate_sets(f, projects, n_jobs, start=st)
            r[f"alt_{st}first_kw"] = tot
            r[f"alt_{st}first_ok"] = ok
            r[f"alt_{st}first_half_rounds"] = hr
            sets[f"alt_{st}first"] = ids
        r["alt_best_kw"] = max(r["alt_genfirst_kw"], r["alt_loadfirst_kw"])

        # every reported number must survive the year, not just the joint one
        n_bad, worst_t, worst_v = f.verify_year_multi(projects, sets)
        if worst_t is None:
            r.update(seed=seed, k=len(projects), size_kw=size_kw, mismatch=mismatch,
                     regime=regime, beta=(beta if regime == "banded" else np.nan),
                     n_intervals=len(ivals), cuts=cut, intervals=";".join(map(str, ivals)))
            return r, f, projects, sets
        log(f"      cut {cut + 1}: year rejects "
            f"{ {a: n for a, n in n_bad.items() if n} }, worst t={worst_t} viol {worst_v:.5f}")
        ivals = sorted(set(ivals) | {worst_t})
    raise RuntimeError(f"seed {seed}: no year-round fixed point after {max_cuts} cuts")


# --------------------------------------------------------------------- exact enumeration
_EW: dict = {}


def _enum_init(spec):
    """Each worker builds its OWN feeder and its own DSS context.

    Forked children must not share the parent's context -- OpenDSS solution state is per-context
    and carries across solves -- so nothing is inherited but the read-only profile matrices,
    which is exactly what copy-on-write is for.
    """
    f = load(spec["master"], spec["intervals"], spec["anchor_t"])
    f.snapshot_mode = spec["snapshot_mode"]
    projects = _r.build_colocated(f, spec["k"], spec["seed"], spec["size_kw"],
                                  spec["size_kw"] * spec["mismatch"])
    if spec["regime"] != "rated":
        pv, ld = request_shapes()
        f.set_regime(spec["regime"], spec["beta"], pv, ld, site_map(projects))
    f._keys = {p.bus: p.gldf_key for p in projects}
    f._staged = None
    f._stage(projects)
    _EW["f"], _EW["projects"] = f, projects
    _EW["ids"] = [p.id for p in projects]


def _enum_chunk(args):
    lo, hi = args
    f, projects, ids = _EW["f"], _EW["projects"], _EW["ids"]
    k = len(ids)
    out = np.zeros(hi - lo, dtype=np.uint8)
    for m in range(lo, hi):
        acc = {ids[i] for i in range(k) if m >> i & 1}
        ok, _mw, _v, _i = f.evaluate(f.ctx, projects, acc)
        out[m - lo] = bool(ok)
    return lo, out


def enumerate_year(spec, n_jobs, chunk=1024):
    """AC feasibility of all 2^K subsets, at every interval in the constraint set.

    K is 16 here, so this is 65,536 subsets -- affordable only because switching an accept-set
    costs 0.5 ms rather than 12, and because an infeasible subset is settled by the first
    interval that rejects it. It removes the objection both referees lead with: with the whole
    lattice enumerated, JOINT and the segregated baselines stop being two heuristic lower bounds
    whose difference inherits no guarantee, and become exact optima of the same problem.
    """
    import multiprocessing as mp
    n = 1 << spec["k"]
    feas = np.zeros(n, dtype=np.uint8)
    if n_jobs <= 1 or n <= 256:
        _enum_init(spec)
        _lo, out = _enum_chunk((0, n))
        return out
    bounds = [(lo, min(lo + chunk, n)) for lo in range(0, n, chunk)]
    ctx = mp.get_context("fork")
    with ctx.Pool(n_jobs, initializer=_enum_init, initargs=(spec,)) as pool:
        for lo, out in pool.imap_unordered(_enum_chunk, bounds):
            feas[lo:lo + len(out)] = out
    return feas


def _seq_winner(projects, gen, ld, feas, first):
    """exact_sequential's argmax, returning the mask it lands on as well as the value.

    exp113 returns only the value, which is enough to report a number and not enough to verify
    it. The baselines are read off a lattice enumerated against the constraint set -- a
    relaxation of the year -- so a baseline can be too high, and a baseline that is too high
    makes the residual too small. Recovering the mask lets the year decide.
    """
    _x = __import__("113_exact_enumeration")
    n = len(feas)
    gb, lb = _x._bits_of(projects, "gen"), _x._bits_of(projects, "load")
    fb, sb = (gb, lb) if first == "gen" else (lb, gb)
    _kw, anchors = _x._optimal_masks(gen, ld, feas, fb)
    sub = _x._submasks(sb, n)
    ok = feas.astype(bool)
    tot = gen + ld
    best, bm = -np.inf, 0
    for a in anchors:
        cand = a | sub
        j = int(cand[np.argmax(np.where(ok[cand], tot[cand], -1.0))])
        if tot[j] > best:
            best, bm = float(tot[j]), j
    return best, bm


def _alt_winner(projects, gen, ld, feas, start, max_nodes=4096):
    """exact_alternation, returning the fixed point's mask alongside its value."""
    _x = __import__("113_exact_enumeration")
    n = len(feas)
    gb, lb = _x._bits_of(projects, "gen"), _x._bits_of(projects, "load")
    order = (gb, lb) if start == "gen" else (lb, gb)
    ok = feas.astype(bool)
    tot = gen + ld
    seen: set = set()
    best, bmask = 0.0, 0
    frontier = [(0, 0)]
    nodes = 0
    while frontier:
        acc, h = frontier.pop()
        if (acc, h % 2) in seen:
            continue
        seen.add((acc, h % 2))
        if float(tot[acc]) > best:
            best, bmask = float(tot[acc]), acc
        if h >= 2 * _alt.MAX_ROUNDS:
            continue
        pending = order[h % 2] & ~acc
        if not pending:
            if order[(h + 1) % 2] & ~acc:
                frontier.append((acc, h + 1))
            continue
        if nodes >= max_nodes:
            continue
        nodes += 1
        cand = acc | _x._submasks(pending, n)
        good = cand[ok[cand]]
        if not len(good):
            frontier.append((acc, h + 1))
            continue
        top = float(tot[good].max())
        if top <= float(tot[acc]) + 1e-9:
            frontier.append((acc, h + 1))
            continue
        for child in good[tot[good] >= top - 1e-9]:
            frontier.append((int(child), h + 1))
    return best, bmask


def exact_draw(master, seed, k, size_kw, mismatch, n_jobs, intervals, anchor_t,
               regime="rated", beta=0.0, snapshot_mode=False, max_cuts=8, log=print):
    """One draw with every quantity in Eq. (1) replaced by the exact optimum of its own problem,
    still certified against the whole year by the same lazy-constraint loop.

    The sequential and alternating baselines are read off the enumeration rather than re-solved:
    asking whether a second-track set is certifiable against a base case holding the first
    track's approvals is asking whether their union is feasible, which the enumeration has
    already answered for every union. Both are maximized over every tied optimum of the earlier
    track, so the segregated baselines get their best case and the residual its most conservative
    value.

    EVERY REPORTED QUANTITY IS VERIFIED AGAINST THE WHOLE YEAR. This used to be three of them --
    the joint optimum and the two track optima -- on the argument that only JOINT's overstatement
    could inflate the residual, and that the segregated baselines, read off a lattice enumerated
    against the constraint set, could only come out too HIGH and so make the residual too SMALL.
    That reasoning is sound and the bias is harmless while the residual is large. It stops being
    harmless the moment the residual approaches zero, which is precisely where the mismatch arms
    land: at a 2:1 leg ratio under the year, R1 collapses with the sequential total sitting
    exactly on JOINT, which is the signature the bias would forge if it were operating. So the
    sequential and alternating winners are recovered as masks and put to the year as well.
    """
    _x = __import__("113_exact_enumeration")
    ivals = list(intervals)
    spec = dict(master=master, seed=seed, k=k, size_kw=size_kw, mismatch=mismatch,
                regime=regime, beta=beta, snapshot_mode=snapshot_mode, anchor_t=anchor_t)
    f = load(master, ivals, anchor_t)
    f.snapshot_mode = snapshot_mode
    projects = _r.build_colocated(f, k, seed, size_kw, size_kw * mismatch)
    if regime != "rated":
        pv, ld = request_shapes()
        f.set_regime(regime, beta, pv, ld, site_map(projects))
    f._keys = {p.bus: p.gldf_key for p in projects}
    gen, ld_kw = _x.sign_totals(projects)
    tot = gen + ld_kw

    for cut in range(max_cuts + 1):
        spec["intervals"] = ivals
        feas = enumerate_year(spec, n_jobs)
        ok = feas.astype(bool)
        gbits, lbits = _x._bits_of(projects, "gen"), _x._bits_of(projects, "load")
        joint_mask = int(np.argmax(np.where(ok, tot, -1.0)))
        g_kw, _gm = _x._optimal_masks(gen, ld_kw, feas, gbits)
        l_kw, _lm = _x._optimal_masks(gen, ld_kw, feas, lbits)
        sq_g, sqg_m = _seq_winner(projects, gen, ld_kw, feas, "gen")
        sq_l, sql_m = _seq_winner(projects, gen, ld_kw, feas, "load")
        alt_g, altg_m = _alt_winner(projects, gen, ld_kw, feas, "gen")
        alt_l, altl_m = _alt_winner(projects, gen, ld_kw, feas, "load")

        def ids_of(mask):
            return {p.id for i, p in enumerate(projects) if mask >> i & 1}

        # EVERY reported quantity is verified against the whole year, not just the three whose
        # overstatement would inflate the residual. The segregated baselines are read off a
        # relaxation, so they can only be too high and would make the residual too SMALL -- a
        # bias that is harmless when the residual is large and decisive when it is near zero,
        # which is exactly the regime the mismatch arms land in.
        sets = {"joint": ids_of(joint_mask),
                "gen_alone": ids_of(int(np.argmax(np.where(ok & ((np.arange(len(ok)) & ~gbits) == 0),
                                                            tot, -1.0)))),
                "load_alone": ids_of(int(np.argmax(np.where(ok & ((np.arange(len(ok)) & ~lbits) == 0),
                                                             tot, -1.0)))),
                "seq_genfirst": ids_of(sqg_m), "seq_loadfirst": ids_of(sql_m),
                "alt_genfirst": ids_of(altg_m), "alt_loadfirst": ids_of(altl_m)}
        if snapshot_mode:
            break
        n_bad, worst_t, worst_v = f.verify_year_multi(projects, sets)
        if worst_t is None:
            break
        log(f"      cut {cut + 1}: year rejects "
            f"{ {a: n for a, n in n_bad.items() if n} }, worst t={worst_t} viol {worst_v:.5f}")
        ivals = sorted(set(ivals) | {worst_t})
    r = dict(gen_alone_kw=g_kw, load_alone_kw=l_kw, seg_parallel_kw=g_kw + l_kw,
             seq_genfirst_kw=sq_g, seq_loadfirst_kw=sq_l,
             best_sequential_kw=max(sq_g, sq_l),
             alt_genfirst_kw=alt_g, alt_loadfirst_kw=alt_l, alt_best_kw=max(alt_g, alt_l),
             joint_kw=float(tot[joint_mask]),
             n_feasible=int(ok.sum()), n_subsets=len(ok),
             seed=seed, k=len(projects), size_kw=size_kw, mismatch=mismatch,
             regime=("exact_snapshot" if snapshot_mode else f"exact_{regime}"),
             beta=(beta if regime == "banded" else np.nan),
             n_intervals=len(ivals), cuts=cut, intervals=";".join(map(str, ivals)))
    return r


def replay_snapshot(master, seed, k, size_kw, mismatch, n_jobs):
    """Certify exactly as the paper does, then hand the result to the year.

    This is the SMART-DS analogue of exp104's Swiss replay, and it is the number a utility would
    actually feel: not "how would the optimum change" but "what happens to the decision the
    snapshot study already made". Certification here is single-interval at nameplate, so it
    reproduces the published `m80_alt_s20_smartds` row for the same seed; only the verification
    is new.
    """
    f = load(master, [0])
    f.snapshot_mode = True
    projects = _r.build_colocated(f, k, seed, size_kw, size_kw * mismatch)
    r, sets = three_way_sets(f, projects, n_jobs)
    for st in ("gen", "load"):
        tot, ids, _hr, _prod, _ok = alternate_sets(f, projects, n_jobs, start=st)
        r[f"alt_{st}first_kw"] = tot
        sets[f"alt_{st}first"] = ids
    r["alt_best_kw"] = max(r["alt_genfirst_kw"], r["alt_loadfirst_kw"])

    f.snapshot_mode = False                       # same circuit, now driven by the shipped year
    f._at_t = None
    n_bad, worst_t, _v = f.verify_year_multi(projects, sets)
    for nm, n in n_bad.items():
        r[f"reject_{nm}"] = n
        r[f"reject_{nm}_pct"] = 100.0 * n / N_INTERVALS
    r.update(seed=seed, k=len(projects), size_kw=size_kw, mismatch=mismatch,
             regime="snapshot_replay", worst_t=(worst_t if worst_t is not None else -1))
    return r


def residuals(r: dict) -> dict:
    """Eq. (1)'s residual and the alternating one, in exp109's columns so the two runs stack."""
    joint = r["joint_kw"]
    res_1 = max(0.0, joint - r["best_sequential_kw"])
    res_a = max(0.0, joint - r["alt_best_kw"])
    return dict(best_alternating_kw=r["alt_best_kw"],
                residual_singlepass_kw=res_1, residual_alt_kw=res_a,
                residual_singlepass_pct=(100 * res_1 / joint if joint > 0 else np.nan),
                residual_alt_pct=(100 * res_a / joint if joint > 0 else np.nan),
                recovered_by_alternation_pct=(100 * (res_1 - res_a) / res_1
                                              if res_1 > 1e-9 else np.nan))


def self_test(master, n_jobs=4):
    """Two equalities that have to hold, or nothing below means anything.

    1. With every multiplier forced to 1.0 the time-varying oracle IS exp101's snapshot oracle:
       the timeseries model differs from the paper's only by the `yearly=` comment.
    2. `alternate_sets` reproduces exp109's `alternate` total on that same snapshot, so the local
       copy has not drifted from the shared implementation it duplicates.
    """
    y = year()
    f = load(master, intervals=[0])
    P0, Q0 = y.P, y.Q
    y.P, y.Q = np.ones_like(P0), np.ones_like(Q0)
    f._at_t = None
    try:
        projects = _r.build_colocated(f, 8, 0, 100.0, 100.0)
        acc = {p.id for p in projects[:5]}
        f._keys = {p.bus: p.gldf_key for p in projects}
        f._staged = None
        ok1, _m, _v, i1 = f.evaluate(f.ctx, projects, acc)
        g = _r.load(master)
        g._keys = dict(f._keys)
        g._staged = None
        ok2, _m, _v, i2 = g.evaluate(g.ctx, projects, acc)
        # 1e-6 pu, not 1e-9: this oracle now solves from a fixed reference state and exp101's
        # solves from wherever its previous call left the solver, so the two land on the same
        # operating point to within Newton tolerance rather than bit-for-bit. The gap measured
        # here is 1.5e-7 pu, four orders below the 0.94/1.10 limits it is compared against.
        d = max(abs(i1["vmin"] - i2["vmin"]), abs(i1["vmax"] - i2["vmax"]))
        assert ok1 == ok2 and d < 1e-6, (i1, i2)
        print(f"  [1] ones-year == exp101 snapshot: vmin {i1['vmin']:.6f} "
              f"vmax {i1['vmax']:.6f}, agree to {d:.1e} pu   PASS")

        fam = _alt.get_family("smartds_colocated", n_jobs, master=master)
        gf = fam.make_feeder()
        gp = _r.build_colocated(gf, 8, 0, 100.0, 100.0)
        ref = _alt.alternate(fam, gf, gp, n_jobs, start="gen")[0]
        mine = alternate_sets(f, projects, n_jobs, start="gen")[0]
        assert abs(ref - mine) < 1e-6, (ref, mine)
        print(f"  [2] alternate_sets == exp109 alternate: {mine:.1f} kW   PASS")
    finally:
        y.P, y.Q = P0, Q0
        f._at_t = None

    # [3] the one that caught a real defect: is a verdict a function of the accept-set alone?
    g = load(master, [0])
    g.snapshot_mode = True
    pr = _r.build_colocated(g, 16, 10, 100.0, 100.0)
    g._keys = {p.bus: p.gldf_key for p in pr}
    ids = [p.id for p in pr]
    probe = [15360, 15361, 15372, 15373, 15374, 15375]
    fresh, walked = [], []
    for m in probe:
        acc = {ids[i] for i in range(len(ids)) if m >> i & 1}
        g._staged = None
        fresh.append(g.evaluate(g.ctx, pr, acc)[0])
    g._staged = None
    g._stage(pr)
    for pm in range(max(0, probe[0] - 500), probe[0]):
        g.evaluate(g.ctx, pr, {ids[i] for i in range(len(ids)) if pm >> i & 1})
    for m in probe:
        acc = {ids[i] for i in range(len(ids)) if m >> i & 1}
        walked.append(g.evaluate(g.ctx, pr, acc)[0])
    assert fresh == walked, list(zip(probe, fresh, walked))
    print(f"  [3] verdicts survive a 500-subset walk: {sum(fresh)}/{len(probe)} feasible "
          f"either way   PASS")


def check_verify(master, seed, k, size_kw, mismatch, n_jobs, regime="rated"):
    """Does the set-major sweep return the same year as the per-verdict-reset sweep?

    The fast path resets the solver once per accept-set instead of once per verdict, on the
    argument that stepping the interval with a fixed accept-set is a smooth trajectory. That is
    an argument; this is the measurement. Both sweeps run the same accept-sets over the same
    35,040 intervals and must agree on every rejection count.
    """
    f = load(master, [0])
    f.snapshot_mode = True
    projects = _r.build_colocated(f, k, seed, size_kw, size_kw * mismatch)
    if regime != "rated":
        pv, ld = request_shapes()
        f.set_regime(regime, 0.0, pv, ld, site_map(projects))
    r, sets = three_way_sets(f, projects, n_jobs)
    f.snapshot_mode = False
    f._at_t = None

    t0 = time.time()
    fast, ft, _fv = f.verify_year_multi(projects, sets, safe=False)
    t_fast = time.time() - t0
    f._at_t = None
    t0 = time.time()
    slow, st, _sv = f.verify_year_multi(projects, sets, safe=True)
    t_slow = time.time() - t0

    print(f"\n  {'arm':>16} {'set-major':>10} {'per-verdict':>12}")
    print("  " + "-" * 42)
    for nm in sorted(fast):
        flag = "" if fast[nm] == slow[nm] else "   <-- DIFFERS"
        print(f"  {nm:>16} {fast[nm]:>10} {slow[nm]:>12}{flag}")
    ok = fast == slow and ft == st
    print(f"\n  worst interval: {ft} vs {st}")
    print(f"  timing: set-major {t_fast:.0f}s, per-verdict {t_slow:.0f}s "
          f"({t_slow / max(t_fast, 1e-9):.1f}x)")
    print(f"  {'PASS -- the fast sweep is exact' if ok else 'FAIL -- use safe=True'}\n")
    return ok


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--feeder", default=FEEDER)
    ap.add_argument("--k", type=int, default=16)
    ap.add_argument("--size-kw", type=float, default=100.0)
    ap.add_argument("--mismatch", type=float, default=1.0)
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2, 3])
    ap.add_argument("--n-jobs", type=int, default=4)
    ap.add_argument("--seed-intervals", type=int, default=2,
                    help="annual load extremes per direction to start the constraint set with")
    ap.add_argument("--anchor", choices=["minload", "maxload", "median", "both"], default="both",
                    help="interval(s) the linear model is anchored at. A warm start, not a "
                         "claim -- every candidate is AC-verified at every interval regardless. "
                         "`both` runs each search twice and keeps the better certificate, which "
                         "removes the risk that one anchor understates the track it does not "
                         "suit and inflates the residual.")
    ap.add_argument("--max-cuts", type=int, default=8)
    ap.add_argument("--regime", choices=["rated", "shapes", "banded"], default="rated",
                    help="rated: approvals at nameplate at every interval (standard "
                         "interconnection practice, and the arm comparable with the snapshot); "
                         "shapes: each leg follows a real SMART-DS profile; "
                         "banded: Section 6.2's net-export control as a constraint")
    ap.add_argument("--betas", type=float, nargs="+", default=[0.0],
                    help="net-exchange band as a fraction of rating (regime=banded)")
    ap.add_argument("--exact", action="store_true",
                    help="replace every quantity in Eq. (1) with the exact optimum of its own "
                         "problem by enumerating all 2^K subsets, instead of searching for it")
    ap.add_argument("--exact-snapshot", action="store_true",
                    help="the same enumeration at the paper's snapshot, for the like-for-like "
                         "comparison the year-round exact run needs")
    ap.add_argument("--replay", action="store_true",
                    help="certify at the snapshot exactly as the paper does, then report how "
                         "much of the year rejects the decision it made")
    ap.add_argument("--check-verify", action="store_true",
                    help="run the year sweep both ways and require identical rejection counts")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--out", default="d1_smartds_timeseries.csv")
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()

    master = str(CACHE / a.feeder / "Master.dss")
    if a.self_test:
        print("\n  SELF-TEST\n")
        self_test(master, a.n_jobs)
        return
    if a.check_verify:
        print("\n  YEAR-SWEEP EQUIVALENCE CHECK\n")
        raise SystemExit(0 if check_verify(master, a.seeds[0], a.k, a.size_kw, a.mismatch,
                                           a.n_jobs, a.regime) else 1)

    probe = load(master, intervals=[0])
    ivals, agg = seed_intervals(probe, a.seed_intervals)
    if a.regime != "rated":
        _pv, _ld = request_shapes()
        ivals = sorted(set(ivals) | set(shaped_seed_intervals(agg, _pv, _ld)))
    lo, hi = int(agg.argmin()), int(agg.argmax())
    anchor_t = {"minload": (lo,), "maxload": (hi,),
                "median": (int(np.argsort(agg)[len(agg) // 2]),),
                "both": (lo, hi)}[a.anchor]

    print("\n  D1 -- SMART-DS AGAINST ITS OWN YEAR, CERTIFIED AT EVERY INTERVAL\n")
    print(f"  {a.feeder}: {probe.n_native} native loads, {len(probe.order)} nodes.")
    print(f"  The snapshot the paper certifies against is the NON-COINCIDENT peak: every load at")
    print(f"  its own annual maximum at once, {probe.base_kw.sum():,.0f} kW. The feeder's true")
    print(f"  aggregate runs {agg.min():,.0f} - {agg.max():,.0f} kW "
          f"(median {np.median(agg):,.0f}), so the snapshot overstates native load by")
    print(f"  {probe.base_kw.sum() / agg.max():.1f}x at the annual peak and "
          f"{probe.base_kw.sum() / np.median(agg):.1f}x at the median.\n")
    print(f"  Load absorbs export and opposes import, so that error FLATTERS the generation")
    print(f"  track and PENALIZES the load track. The residual is built from both.\n")
    print(f"  K={a.k} co-located pairs at {a.size_kw:.0f} kW"
          + (f" (load leg x{a.mismatch:.2f})" if a.mismatch != 1.0 else "")
          + f"; constraint set starts at {ivals}, GLDF anchored at t={anchor_t}.\n")

    if a.exact or a.exact_snapshot:
        snap = bool(a.exact_snapshot)
        print(f"  EXACT: all 2^{a.k} = {1 << a.k:,} subsets enumerated by AC power flow, "
              f"{'at the paper snapshot' if snap else 'at every certified interval'}.\n")
        rows = []
        for seed in a.seeds:
            t0 = time.time()
            r = exact_draw(master, seed, a.k, a.size_kw, a.mismatch, a.n_jobs, ivals,
                           anchor_t[0], regime=a.regime, beta=a.betas[0],
                           snapshot_mode=snap, max_cuts=a.max_cuts,
                           log=lambda s: print(s, flush=True))
            r.update(residuals(r), feeder=a.feeder, dt=time.time() - t0)
            rows.append(r)
            print(f"    seed {seed}: feasible {r['n_feasible']:,}/{r['n_subsets']:,} | "
                  f"gen {r['gen_alone_kw']:.0f} load {r['load_alone_kw']:.0f} "
                  f"seq {r['best_sequential_kw']:.0f} alt {r['alt_best_kw']:.0f} "
                  f"JOINT {r['joint_kw']:.0f} | R1 {r['residual_singlepass_pct']:.1f}% "
                  f"R* {r['residual_alt_pct']:.1f}% | {r['cuts']} cuts [{r['dt']:.0f}s]",
                  flush=True)
            if a.save:
                OUT.mkdir(exist_ok=True)
                pd.DataFrame(rows).to_csv(OUT / a.out, index=False)
        d = pd.DataFrame(rows)
        print(f"\n  MEDIAN OVER {len(d)} DRAWS -- exact, no optimizer anywhere\n")
        print(f"    gen alone        {d.gen_alone_kw.median():8.0f} kW")
        print(f"    load alone       {d.load_alone_kw.median():8.0f} kW")
        print(f"    best sequential  {d.best_sequential_kw.median():8.0f} kW")
        print(f"    best alternating {d.alt_best_kw.median():8.0f} kW")
        print(f"    JOINT            {d.joint_kw.median():8.0f} kW")
        print(f"    R1               {d.residual_singlepass_pct.median():8.1f} %")
        print(f"    R*               {d.residual_alt_pct.median():8.1f} %   "
              f"positive in {int((d.residual_alt_kw > 1e-6).sum())}/{len(d)} draws")
        if a.save:
            d.to_csv(OUT / a.out, index=False)
            print(f"\n  saved -> outputs/{a.out}\n")
        return

    if a.replay:
        print("  REPLAY: certified at the snapshot, then handed to the shipped year.\n")
        rows = []
        for seed in a.seeds:
            t0 = time.time()
            r = replay_snapshot(master, seed, a.k, a.size_kw, a.mismatch, a.n_jobs)
            r.update(residuals(r), feeder=a.feeder, dt=time.time() - t0)
            rows.append(r)
            print(f"    seed {seed}: JOINT {r['joint_kw']:.0f} kW rejected at "
                  f"{r['reject_joint_pct']:.1f}% of intervals; best sequential "
                  f"{r['best_sequential_kw']:.0f} kW at {r['reject_seq_genfirst_pct']:.1f}% / "
                  f"{r['reject_seq_loadfirst_pct']:.1f}%  [{r['dt']:.0f}s]", flush=True)
            if a.save:
                OUT.mkdir(exist_ok=True)
                pd.DataFrame(rows).to_csv(OUT / a.out, index=False)
        d = pd.DataFrame(rows)
        print(f"\n  MEDIAN OVER {len(d)} DRAWS -- share of the 35,040 intervals that reject a"
              f"\n  snapshot-certified accept-set\n")
        for nm in ("gen_alone", "load_alone", "seq_genfirst", "seq_loadfirst",
                   "alt_genfirst", "alt_loadfirst", "joint"):
            col = f"reject_{nm}_pct"
            if col in d:
                print(f"    {nm:>14}  {d[col].median():6.1f} %   "
                      f"(worst draw {d[col].max():.1f} %, clean in "
                      f"{int((d[col] == 0).sum())}/{len(d)} draws)")
        if a.save:
            d.to_csv(OUT / a.out, index=False)
            print(f"\n  saved -> outputs/{a.out}\n")
        return

    shapes = request_shapes() if a.regime != "rated" else None
    if shapes is not None:
        pv, ld = shapes
        print(f"  regime={a.regime}: generation follows {PV_SHAPE} (zero at "
              f"{100 * (pv == 0).mean():.0f}% of intervals), load follows {LD_SHAPE}.\n")
    arms = a.betas if a.regime == "banded" else [0.0]

    rows = []
    for beta in arms:
        if a.regime == "banded":
            print(f"  beta = {beta:g}:", flush=True)
        for seed in a.seeds:
            t0 = time.time()
            r, f, projects, sets = draw_yearround(
                master, seed, a.k, a.size_kw, a.mismatch, a.n_jobs, ivals, anchor_t,
                max_cuts=a.max_cuts, log=lambda s: print(s, flush=True),
                regime=a.regime, beta=beta, shapes=shapes)
            r.update(residuals(r))
            r.update(feeder=a.feeder, family="smartds_colocated_timeseries",
                     dt=time.time() - t0)
            rows.append(r)
            print(f"    seed {seed}: gen {r['gen_alone_kw']:.0f} "
                  f"load {r['load_alone_kw']:.0f} "
                  f"seq {r['best_sequential_kw']:.0f} alt {r['alt_best_kw']:.0f} "
                  f"JOINT {r['joint_kw']:.0f} kW | R1 {r['residual_singlepass_pct']:.1f}% "
                  f"R* {r['residual_alt_pct']:.1f}% | {r['cuts']} cuts, "
                  f"{r['n_intervals']} intervals [{r['dt']:.0f}s]", flush=True)
            if a.save:
                OUT.mkdir(exist_ok=True)
                pd.DataFrame(rows).to_csv(OUT / a.out, index=False)

    d = pd.DataFrame(rows)
    print(f"\n  MEDIAN OVER {len(a.seeds)} DRAWS, every number valid at all "
          f"{N_INTERVALS} intervals\n")
    hdr = (f"  {'arm':>10} {'gen':>6} {'load':>6} {'seq':>6} {'alt':>6} {'JOINT':>7} "
           f"{'R1 %':>7} {'R* %':>7} {'R*>0':>6} {'cuts':>5}")
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for beta, g in d.groupby("beta", dropna=False):
        nm = a.regime if a.regime != "banded" else f"beta {beta:g}"
        print(f"  {nm:>10} {g.gen_alone_kw.median():>6.0f} {g.load_alone_kw.median():>6.0f} "
              f"{g.best_sequential_kw.median():>6.0f} {g.alt_best_kw.median():>6.0f} "
              f"{g.joint_kw.median():>7.0f} {g.residual_singlepass_pct.median():>7.1f} "
              f"{g.residual_alt_pct.median():>7.1f} "
              f"{int((g.residual_alt_kw > 1e-6).sum()):>3}/{len(g):<2} "
              f"{g.cuts.max():>5.0f}")

    if a.save:
        OUT.mkdir(exist_ok=True)
        d.to_csv(OUT / a.out, index=False)
        print(f"\n  saved -> outputs/{a.out}\n")


if __name__ == "__main__":
    main()
