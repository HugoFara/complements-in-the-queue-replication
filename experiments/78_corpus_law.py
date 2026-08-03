"""M51: does the effective-pressure law hold on the CORPUS? And a denominator a DNO can use.

WHAT M50 CLAIMED, ON TWO FEEDERS
--------------------------------
*A connection queue becomes a lottery when the requests that CAN be seated collectively
exceed what the feeder can host.* Requests that can never be seated (they violate a limit
on their own, on the empty feeder) inflate the queue's kW but are decided by physics, not
by arrival order -- so discount them:

    EFFECTIVE PRESSURE := (kW of requests that can be seated AT ALL) / (kW achievable)

M50 measured that on Ckt7 and Ckt24 (63 cells): pooled Spearman **+0.89** against the FCFS
capacity spread, versus +0.36 for raw pressure -- which on Ckt24 alone was **-0.07**, i.e.
nothing. Every cell above 1.02 was a lottery (32/32); below 1.01, none.

Two feeders is an observation, not a law. This runs it on the rest of the corpus.

M50's STATISTIC IS NOT WHAT M50 SAID IT WAS
-------------------------------------------
M50 divided R by `yardstick_kw` -- the certified optimum **of that queue** -- and pitched the
result as costing "K AC solves and nothing else". Both halves of that need correcting.

1. R/Y IS >= 1.00 BY CONSTRUCTION. Every request in this study is monotone: pure export only
   pushes voltage and flow further the same way (and SMART-DS's pure load only ever pushes
   voltage down). So a request that violates a limit ALONE violates it in any company, the
   optimum Y can only ever seat reachable requests, Y <= R always, and the ratio bottoms out
   at exactly 1.00 for every under-subscribed queue. That is not a bug -- it is the
   mechanism -- but it means M50's "threshold at 1.0-1.1" was reading a floor, not a knee.

2. IT NEEDED A MILP. Y is the certified optimum, which is the most expensive object in this
   entire project. A statistic that needs one is not something a DNO runs on a live queue.

Monotonicity, which caused the first problem, also solves the second. If every solo-feasible
request fits JOINTLY, then any prefix of any arrival order is a subset of that feasible set,
hence itself feasible, so skip-continue FCFS **accepts every one of them, in every order** --
the total is identical in all orders and the spread is exactly 1.00. Contrapositive:

    A LOTTERY REQUIRES A BLOCKED REQUEST.  If one FCFS walk seats every request that the
    screen says can be seated at all, no arrival order can do better or worse. Finding that
    out costs the screen (K AC solves) plus ONE walk (K more). No model. No MILP. No
    permutations. It is a certificate, not a correlation -- one direction is a theorem.

So this experiment measures four candidate predictors against the same 100-order spread, and
lets them compete:

    raw pressure    requested / achievable          M47's, falsified on Ckt24 (rho = -0.07)
    R / Y           reachable / achievable          M50's; needs the MILP; >= 1 by construction
    R / A           reachable / feeder CAPACITY     queue-independent denominator (below)
    R / W           reachable / ONE FCFS walk       the certificate; 2K AC solves

A is what the feeder can host under this request distribution -- one number per feeder from a
saturating probe queue. It is reported because it is the number a DNO already has (they call
it hosting capacity), and because its FAILURE here is informative: a queue is limited by the
buses its own requests happen to sit at, not by the feeder's best case. Measured on Ckt24: a
saturating menu certifies 2,984 kW at K=400 and still climbing, while that feeder's own
300-request queue can only achieve 2,113 kW. The right denominator is queue-specific.

THE CORPUS
----------
  Fontinele 33-bus / 69-bus   the head-to-head networks (M29) -- MV, thermally unbounded
  EULV                        the IEEE European LV feeder (the paper's surviving +3.0%)
  ENWL x3                     real UK LV networks, the conductor corpus's own feeders
  (SMART-DS is exp79: its baseline is 188% overloaded and one master will not converge in
   pandapower, which is why the paper's SMART-DS work uses the OpenDSS-native oracle.)

Queues are NOT swept blindly over K. Each feeder is calibrated once (A, and the mean
reachable kW per request), then K is chosen to place cells at target reachable pressures
0.5x .. 2.0x -- so every feeder is measured on both sides of its own threshold, which is
what M49's blind K grids could not guarantee (all 18 Ckt24 cells landed in 1.00-1.04).

Run:  uv run python experiments/78_corpus_law.py --feeder fontinele33 --save
      uv run python experiments/78_corpus_law.py --feeder ckt7 --calibrate-only --save
      uv run python experiments/78_corpus_law.py --pool
"""

from __future__ import annotations

import argparse
import copy
import multiprocessing as mp
import os
import random
import time
from pathlib import Path

os.environ.setdefault("OMP_NUM_THREADS", "1")

import numpy as np
import pandapower as pp
import pandapower.networks as pn
import pandas as pd
from scipy.stats import spearmanr

from gridsynth import import_opendss

_c = __import__("68_ckt7_gldf_headline")          # build_gldf / build_thermal / Project
_e = __import__("73_empirical_request_distribution")
_m = __import__("75_milp_maximality")             # certified_allocation, ac_complete, probes

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "outputs"
ENWL = REPO / "cache" / "enwl_full"
RUNPP_KWARGS = _c.RUNPP_KWARGS

TARGETS = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]   # target REACHABLE pressure R/A of each cell
K_FLOOR, K_CEIL = 25, 600
CALIB_SEED = 999                             # disjoint from the measurement draws (0..4)
REF_WALK_SEED = 500_000                      # disjoint from the measured orders AND from
                                             # exp75's warm starts (100_000+)


# ------------------------------------------------------------------ the corpus
def _enwl(net_name: str, fdr: str):
    return lambda: import_opendss(str(ENWL / net_name / fdr / "Master_snapshot.dss"))


def _uniform(lo, hi):
    return lambda rng, n: np.array([rng.uniform(lo, hi) for _ in range(n)])


def _empirical(rng, n):
    return _e.sample_kw(rng, n)


# WHY FONTINELE'S TWO NETWORKS ARE NOT IN THIS CORPUS (measured, not assumed)
# --------------------------------------------------------------------------
# Reachable pressure needs a FINITE denominator, and the IEEE 33/69-bus networks as cited do
# not have one. Both ship with `max_i_ka = 99999` -- Baran & Wu published no ratings and
# Fontinele et al. never specify any (M29 flagged this) -- so under a physical
# voltage-plus-thermal oracle nothing stops export being dumped at buses electrically close
# to the substation, where it barely moves any voltage. Measured: the certified allocation
# was still climbing 25-30% per doubling of the queue at 1,600 requests, reaching 53.8 MW on
# a 3.7 MW feeder. Their published results are bounded only by their own 15%-of-load NETWORK
# LOSS cap -- a study convention, not a network limit; no DNO refuses a connection because
# system losses passed 15%. So the law is not tested there, and that is a fact about the
# benchmark, not about the law. (The head-to-head result M29/M37 reports is unaffected: it
# compares methods under their rules, which is a different question from how much a feeder
# can host.)
#
# EACH REMAINING FEEDER KEEPS THE REQUEST MODEL ITS OWN EXPERIMENT ESTABLISHED. EULV gets
# exp53's rooftop PV band; the ENWL feeders have no request model of their own, so they get
# the real interconnection queue (M45). Ckt7/Ckt24 keep theirs. That the corpus spans
# several size distributions -- one heavy-tailed, one uniform -- makes it a harder test, not
# a sloppier one: a law about queues must not depend on the queue's size distribution.
FEEDERS = {
    # (loader, request sizer, description)
    "eulv": (lambda: import_opendss(str(REPO / "data" / "opendss" / "eulv" / "Master_snapshot.dss")),
             _uniform(3.0, 10.0), "IEEE European LV, exp53's rooftop PV band 3-10 kW"),
    # Five real UK LV feeders, chosen from the 128 for load count (they must be able to carry
    # a queue) and for baseline diversity: n2f1 sits comfortably inside the limits, n17f1
    # starts exactly ON the 0.94 floor, n13f4 far outside it at 0.817 pu, and n8f2 starts
    # with a line already at 118% -- the non-worsening clamp is doing real work on these.
    "enwl_n2f1": (_enwl("network_2", "Feeder_1"), _empirical,
                  "ENWL n2/f1 (2287 bus, 175 loads, vmin 0.966, line 66%)"),
    "enwl_n17f1": (_enwl("network_17", "Feeder_1"), _empirical,
                   "ENWL n17/f1 (2843 bus, 188 loads, vmin 0.941, line 72%)"),
    "enwl_n13f4": (_enwl("network_13", "Feeder_4"), _empirical,
                   "ENWL n13/f4 (785 bus, 173 loads, vmin 0.817, line 76%)"),
    "enwl_n8f2": (_enwl("network_8", "Feeder_2"), _empirical,
                  "ENWL n8/f2 (7023 bus, 302 loads, vmin 0.909, line 118%)"),
    "enwl_n5f3": (_enwl("network_5", "Feeder_3"), _empirical,
                  "ENWL n5/f3 (2903 bus, 156 loads, vmin 0.928, line 61%)"),
}


class Feeder:
    """One pandapower net behind exp68's module API + exp70's `anchored_context`, so that
    exp75's `certified_allocation` -- the M48 yardstick, with its warm starts, its AC
    completion and its two invariants -- runs unchanged on any feeder in the corpus.

    THE NON-WORSENING CLAMP. Half of this corpus starts outside the book limits: the
    Fontinele networks sit at vmin 0.909-0.913 pu and the ENWL feeders at 0.98, before a
    single request arrives. A hard 0.94 floor would make the EMPTY accept-set infeasible,
    every request would then be rejected by definition, achievable capacity would be zero,
    and the law would be measuring nothing. So the oracle forbids each limit being made
    WORSE than the baseline -- exp64's rule -- which reduces to the plain limit check
    wherever the baseline is already inside the limit (EULV, Ckt7, Ckt24).
    """

    @classmethod
    def from_ctx(cls, name, ctx, sizer, vmin, vmax, loading):
        """Wrap an EXISTING feeder context (Ckt7's, Ckt24's) so it gets the staged oracle --
        same network, same limits, same excluded islands, ~5x faster per AC solve. exp68's
        own oracle is left untouched: every published Ckt7/Ckt24 number came from it."""
        f = cls.__new__(cls)
        f.name, f.sizer = name, sizer
        f.VMIN_PU, f.VMAX_PU, f.LOADING_MAX_PCT = vmin, vmax, loading
        f.ctx = ctx
        # Ckt7 excludes the islands behind 11 pre-broken service lines, which sit above 100%
        # at BASELINE. Miss this and every accept-set is "infeasible".
        f.excl_lines = list(ctx.excluded_line_idx)
        # M100: the same islands must come out of the VOLTAGE check too. They sit 0.06-0.08 pu
        # below the rest of Ckt7 and own vmin whenever the check reaches them. This was masked
        # while the Vsource impedance went unmodelled and held the whole feeder 0.083 pu high.
        f.excl_buses = set(int(b) for b in ctx.excluded_buses)
        rep = ctx.net.get("opendss_import") or {}
        f.src_bus = None if rep.get("source_bus") is None else int(rep["source_bus"])
        f.base = dict(n_bus=len(ctx.net.bus), n_load=len(ctx.net.load))
        f.load_buses = sorted(int(b) for b in ctx.net.load.bus.unique()
                              if b not in ctx.excluded_buses)
        f._proj = f._enet = f._rows = None
        return f

    def __init__(self, name, net, sizer, vmin=0.94, vmax=1.10, loading=100.0):
        pp.runpp(net, **RUNPP_KWARGS)
        b_vmin = float(net.res_bus.vm_pu.min())
        b_vmax = float(net.res_bus.vm_pu.max())
        b_line = float(net.res_line.loading_percent.max()) if len(net.line) else 0.0
        b_trafo = float(net.res_trafo.loading_percent.max()) if len(net.trafo) else 0.0
        self.name = name
        self.sizer = sizer
        self.VMIN_PU = min(vmin, b_vmin)
        self.VMAX_PU = max(vmax, b_vmax)
        self.LOADING_MAX_PCT = max(loading, b_line, b_trafo)
        self.base = dict(n_bus=len(net.bus), n_load=len(net.load), vmin=b_vmin, vmax=b_vmax,
                         line_pct=b_line, trafo_pct=b_trafo)
        self.ctx = _c.NetworkContext(net=net, excluded_line_idx=[], excluded_buses=set())
        self.excl_lines = []
        self.excl_buses = set()
        self.src_bus = None
        self.load_buses = sorted(int(b) for b in net.load.bus.unique())
        self._proj = self._enet = self._rows = None

    # ---- exp68's oracle, with this feeder's limits ----
    def build_network(self):
        return self.ctx

    def _staged(self, projects):
        """A net with every request PRE-CREATED and out of service, so `evaluate` only has
        to flip `in_service`.

        exp68's oracle deep-copies the net and re-creates the accepted loads one by one on
        every call. That is O(|accepted|) dataframe writes per AC solve, hence O(K^2) per
        FCFS walk, and it dominates everything else: a single walk over 200 requests on a
        **33-bus** network took 4.6 s, of which the 200 power flows were under 1.5 s. The
        corpus needs tens of thousands of walks, so the oracle had to stop doing that.

        Identical physics -- an out-of-service load is absent from the power flow -- and the
        equivalence is asserted against the original oracle in `--self-test`.
        """
        if self._proj is not projects:                # identity: the walkers reuse one list
            net = copy.deepcopy(self.ctx.net)
            rows = {}
            for p in projects:
                rows[p.id] = pp.create_load(net, bus=p.bus, p_mw=p.p_mw, q_mvar=0.0,
                                            name=p.id, in_service=False)
            self._proj, self._enet, self._rows = projects, net, rows
        return self._enet, self._rows

    def _checked_vm(self, net, also=()):
        """Bus voltages the band actually applies to: everything except the excluded islands,
        the 115 kV source terminal, and whatever the caller pins (`also`)."""
        # getattr: subclasses that build themselves with __new__ (Ckt24Feeder) never set these,
        # and they must keep their own voltage check exactly as it was.
        drop = set(getattr(self, "excl_buses", ()) or ()) | set(also)
        src = getattr(self, "src_bus", None)
        if src is not None:
            drop.add(src)
        drop &= set(net.res_bus.index)
        return net.res_bus.vm_pu.drop(index=sorted(drop)) if drop else net.res_bus.vm_pu

    def evaluate(self, ctx, projects, accepted_ids):
        net, rows = self._staged(projects)
        net.load.loc[list(rows.values()), "in_service"] = [r in accepted_ids for r in rows]
        by_id = {p.id: p for p in projects}
        try:
            pp.runpp(net, **RUNPP_KWARGS)
        except pp.LoadflowNotConverged:
            return False, 0.0, float("inf"), {"diverged": True}
        vm = self._checked_vm(net)
        vmin = float(vm.min())
        vmax = float(vm.max())
        lp = net.res_line.loading_percent.drop(self.excl_lines, errors="ignore")
        line_pct = float(lp.max()) if len(lp) else 0.0
        trafo_pct = float(net.res_trafo.loading_percent.max()) if len(net.trafo) else 0.0
        violation = (max(0.0, vmax - self.VMAX_PU) + max(0.0, self.VMIN_PU - vmin)
                     + max(0.0, line_pct - self.LOADING_MAX_PCT)
                     + max(0.0, trafo_pct - self.LOADING_MAX_PCT))
        mw = sum(abs(by_id[rid].p_mw) for rid in accepted_ids)
        return violation == 0.0, mw, violation, {
            "vmax": vmax, "vmin": vmin, "line_pct": line_pct, "trafo_pct": trafo_pct}

    # ---- exp68's linear model, verbatim (both are pure pandapower) ----
    def build_gldf(self, ctx, buses):
        return _c.build_gldf(ctx, buses)

    def build_thermal(self, ctx, projects):
        return _c.build_thermal(ctx, projects)

    # ---- exp70's re-linearization hook ----
    def anchored_context(self, ctx, projects, anchor_ids):
        net = copy.deepcopy(ctx.net)
        by_id = {p.id: p for p in projects}
        for rid in anchor_ids:
            p = by_id[rid]
            pp.create_load(net, bus=p.bus, p_mw=p.p_mw, q_mvar=0.0, name=rid)
        pp.runpp(net, **RUNPP_KWARGS)
        return _c.NetworkContext(net=net, excluded_line_idx=ctx.excluded_line_idx,
                                 excluded_buses=ctx.excluded_buses)


def build_projects(feeder, k, seed):
    """M49's generator, feeder-agnostic: buses drawn WITH replacement from the load buses,
    sizes from THIS feeder's request model. Two independent RNG streams, so a change of
    size distribution never moves a bus."""
    rng_b = random.Random(seed)
    buses = [rng_b.choice(feeder.load_buses) for _ in range(k)]
    kws = feeder.sizer(random.Random(seed + 10_000), k)
    return [_c.Project(id=f"p{i}", bus=int(b), p_mw=-float(kw) / 1000.0)
            for i, (b, kw) in enumerate(zip(buses, kws))]


def self_test(feeder, n=12, seed=7):
    """The staged oracle must agree with exp68's deepcopy-and-recreate oracle, set for set."""
    projects = build_projects(feeder, 30, seed)
    rng = random.Random(seed)
    ok_all = True
    for _ in range(n):
        ids = {p.id for p in projects if rng.random() < 0.5}
        got = feeder.evaluate(feeder.ctx, projects, ids)
        # the reference: a fresh net, accepted loads created one by one
        net = copy.deepcopy(feeder.ctx.net)
        by_id = {p.id: p for p in projects}
        for rid in ids:
            p = by_id[rid]
            pp.create_load(net, bus=p.bus, p_mw=p.p_mw, q_mvar=0.0, name=rid)
        pp.runpp(net, **RUNPP_KWARGS)
        ref_v = (float(net.res_bus.vm_pu.min()), float(net.res_bus.vm_pu.max()))
        same = (abs(got[3]["vmin"] - ref_v[0]) < 1e-9 and abs(got[3]["vmax"] - ref_v[1]) < 1e-9)
        ok_all &= same
        if not same:
            print(f"    MISMATCH on {len(ids)} accepted: staged {got[3]['vmin']:.6f}/"
                  f"{got[3]['vmax']:.6f} vs reference {ref_v[0]:.6f}/{ref_v[1]:.6f}")
    print(f"  self-test: staged oracle == deepcopy oracle on {n} random accept-sets: "
          f"{'PASS' if ok_all else 'FAIL'}")
    return ok_all


# ------------------------------------------------------- the screen and the walks
_CTX = _PROJ = _MOD = None


def _solo(rid):
    ok, _, _, _ = _MOD.evaluate(_CTX, _PROJ, {rid})
    return rid, bool(ok)


def solo_feasible(ctx, projects, mod, n_jobs):
    """The a-priori screen: one AC solve per request, alone, on the base feeder."""
    global _CTX, _PROJ, _MOD
    _CTX, _PROJ, _MOD = ctx, projects, mod
    with mp.get_context("fork").Pool(n_jobs) as pool:
        got = pool.map(_solo, [p.id for p in projects])
    return {rid for rid, ok in got if ok}


def _walk_chunk(perm_seeds):
    mod, ctx, projects = _MOD, _CTX, _PROJ
    ids = [p.id for p in projects]
    k = len(projects)
    cache: dict[frozenset, tuple[bool, float]] = {}
    out = []
    for ps in perm_seeds:
        rng = random.Random(ps)
        order = list(range(k))
        rng.shuffle(order)
        acc, mw = frozenset(), 0.0
        for i in order:
            trial = acc | {ids[i]}
            hit = cache.get(trial)
            if hit is None:
                ok, m, _, _ = mod.evaluate(ctx, projects, set(trial))
                hit = (ok, m)
                cache[trial] = hit
            if hit[0]:
                acc, mw = trial, hit[1]
        seated = np.fromiter((ids[i] in acc for i in range(k)), dtype=bool, count=k)
        out.append((mw * 1000, len(acc), seated))
    return out


def fcfs_walks(ctx, projects, mod, permutations, n_jobs):
    global _CTX, _PROJ, _MOD
    _CTX, _PROJ, _MOD = ctx, projects, mod
    seeds = list(range(permutations))
    chunks = [seeds[i::n_jobs] for i in range(n_jobs)]
    with mp.get_context("fork").Pool(n_jobs) as pool:
        got = pool.map(_walk_chunk, [c for c in chunks if c])
    return [w for chunk in got for w in chunk]


# ---------------------------------------------------------------- calibration
def capacity(ctx, mod, rel, mk, n_jobs, k0=50, kmax=1600, tol=0.02, verbose=True):
    """A -- what this feeder can host under this request distribution. ONE number, computed
    once, independent of any queue we later measure.

    GROW THE PROBE TO SATURATION, NOT TO FIRST OVER-SUBSCRIPTION. The first version of this
    stopped as soon as the certified allocation had to reject something (seated < 90% of
    reachable). That underestimates A badly, and the error is not subtle: on the 33-bus
    network it returned A = 14.0 MW, and then a *measured cell* with 160 requests certified
    18.2 MW -- 30% more than the "capacity" it was being divided by. A longer queue is a
    richer MENU: more buses to choose from, more sizes to pack with, so the achievable total
    keeps climbing after the queue first becomes over-subscribed.

    So double K until the certified total stops climbing (< `tol` per doubling). A(K) is
    increasing and concave in K, and its limit is what a DNO means by hosting capacity: the
    most this feeder can absorb from an unlimited supply of requests of this kind.
    """
    k, prev, best, hist = k0, 0.0, 0.0, []
    while True:
        projects = mk(ctx, k, CALIB_SEED)
        feas = solo_feasible(ctx, projects, mod, n_jobs)
        by_id = {p.id: abs(p.p_mw) * 1000 for p in projects}
        reach = sum(by_id[i] for i in feas)
        ids, mw, info = _m.certified_allocation(ctx, projects, mod, rel, n_jobs=n_jobs,
                                                verbose=False, warm_starts=2, max_iter=8,
                                                time_limit=60.0)
        kw = mw * 1000
        gain = (kw / prev - 1.0) if prev > 0 else float("inf")
        best, mu_r = max(best, kw), reach / k
        hist.append(dict(k=k, reachable_kw=reach, certified_kw=kw, gain=gain))
        if verbose:
            print(f"    probe K={k:<4d}  reachable {len(feas):3d}/{k} ({reach:9.1f} kW)  "
                  f"certified {kw:9.1f} kW ({len(ids):3d} seated)"
                  f"   {'' if prev == 0 else f'+{100*gain:.1f}% on the last doubling'}")
        if k >= kmax or (prev > 0 and gain < tol):
            break
        prev = kw
        k *= 2
    return best, mu_r, dict(probe_k=k, probe_reachable_kw=reach, probe_hist=hist,
                            probe_last_gain=hist[-1]["gain"], **info)


def gini(x: np.ndarray) -> float:
    if x.sum() <= 0:
        return float("nan")
    s = np.sort(x)
    n = len(s)
    return float((2 * np.arange(1, n + 1) - n - 1).dot(s) / (n * s.sum()))


# ---------------------------------------------------------------------- main
def load_feeder(name, n_jobs):
    """(ctx, mod, rel, request-maker, description). Ckt7/Ckt24 keep their own modules and
    their own request generators, so a capacity computed here is directly comparable with
    M50's 63 already-measured cells."""
    if name == "ckt7":
        mod = __import__("68_ckt7_gldf_headline")
        rel = __import__("70_ckt7_relinearize")
        exp = __import__("71_arrival_order_lottery")
        mk = lambda ctx, k, s: exp.build_projects_x(ctx, k, s, replacement=True, empirical=True)
        return mod.build_network(), mod, rel, mk, "EPRI Ckt7 (M42/M49)"
    if name == "ckt24":
        mod = __import__("64_ckt24_gldf_bound")
        rel = __import__("69_ckt24_relinearize")
        exp = __import__("72_ckt24_arrival_order")
        mk = lambda ctx, k, s: exp.build_projects_x(ctx, k, s, empirical=True)
        return mod.build_network(), mod, rel, mk, "EPRI Ckt24 (M39/M49)"

    loader, sizer, desc = FEEDERS[name]
    f = Feeder(name, loader(), sizer)
    mk = lambda ctx, k, s: build_projects(f, k, s)
    return f.ctx, f, f, mk, desc


def run_cells(feeder, ctx, mod, rel, mk, a_kw, mu_r, seeds, targets, permutations, n_jobs,
              save):
    """The measurement. Every cell gets: the a-priori screen (K AC solves), the FCFS walks
    (model-free), and the certified yardstick (exp75, with both M48 invariants checked).

    Shared verbatim with exp79's OpenDSS corpus, so SMART-DS is measured by the same code
    that measured the pandapower feeders -- only the oracle underneath differs.
    """
    ks = sorted({max(K_FLOOR, min(K_CEIL, int(round(t * a_kw / mu_r)))) for t in targets})
    print(f"\n  CELLS: K = {ks}  (targets {targets} x A / mean-reachable), "
          f"draws {seeds}, {permutations} arrival orders each\n")

    rows, rates = [], []
    for k in ks:
        for seed in seeds:
            t0 = time.time()
            projects = mk(ctx, k, seed)
            sizes = np.array([abs(p.p_mw) * 1000 for p in projects])
            req_kw = float(sizes.sum())

            feas = solo_feasible(ctx, projects, mod, n_jobs)
            by_id = {p.id: abs(p.p_mw) * 1000 for p in projects}
            reach_kw = sum(by_id[i] for i in feas)

            # THE CERTIFICATE (see the module docstring): ONE reference walk, from a seed
            # disjoint from the measured orders, so it is never fitted to the sample whose
            # spread it is predicting.
            ref = _m.fcfs_accept_sets(ctx, projects, mod, [REF_WALK_SEED], n_jobs)[0]
            w_kw = sum(by_id[i] for i in ref)
            blocked = sorted(feas - set(ref))          # reachable, yet a walk rejected it

            walks = fcfs_walks(ctx, projects, mod, permutations, n_jobs)
            kws = np.array([w[0] for w in walks])
            ns = np.array([w[1] for w in walks])
            seat = np.stack([w[2] for w in walks])
            rate = seat.mean(axis=0)
            p5, p50, p95 = np.percentile(kws, [5, 50, 95])
            spread = p95 / p5 if p5 > 0 else float("inf")

            ids, mw, _inf = _m.certified_allocation(ctx, projects, mod, rel, n_jobs=n_jobs,
                                                    verbose=False)
            y_kw = mw * 1000
            addable = _m.maximality_probe(ctx, projects, mod, ids, n_jobs)
            beaten = bool(kws.max() > y_kw + 1e-6)

            rows.append(dict(
                feeder=feeder, k=k, seed=seed, requested_kw=req_kw, reachable_kw=reach_kw,
                capacity_kw=a_kw, yardstick_kw=y_kw,
                n_never_feasible=k - len(feas), never_feasible_kw=req_kw - reach_kw,
                pressure=req_kw / y_kw if y_kw else np.nan,
                eff_pressure_Y=reach_kw / y_kw if y_kw else np.nan,
                eff_pressure_A=reach_kw / a_kw,
                # the certificate: model-free, 2K AC solves, no permutations
                ref_walk_kw=w_kw, n_blocked=len(blocked),
                blocked_kw=sum(by_id[i] for i in blocked),
                contest_ratio=reach_kw / w_kw if w_kw else np.nan,
                max_request_kw=float(sizes.max()), top_share=float(sizes.max() / req_kw),
                gini=gini(sizes),
                contested=int(((rate > 0.05) & (rate < 0.95)).sum()),
                coinflip=int(((rate > 0.25) & (rate < 0.75)).sum()),
                biggest_seat_rate=float(rate[int(sizes.argmax())]),
                fcfs_p5=p5, fcfs_median=p50, fcfs_p95=p95, fcfs_max=float(kws.max()),
                spread=spread, median_pct=100 * p50 / y_kw if y_kw else np.nan,
                seated_frac_median=float(np.median(ns / k)),
                yardstick_maximal=not addable, yardstick_beaten=beaten,
                dt=time.time() - t0))
            for i, p in enumerate(projects):
                rates.append(dict(k=k, seed=seed, project=p.id, bus=p.bus,
                                  kw=float(sizes[i]), seat_rate=float(rate[i]),
                                  solo_feasible=p.id in feas))
            if save:
                OUT.mkdir(exist_ok=True)
                pd.DataFrame(rows).to_csv(OUT / f"m51_law_{feeder}.csv", index=False)
                pd.DataFrame(rates).to_csv(OUT / f"m51_seatrates_{feeder}.csv.gz", index=False)

            r = rows[-1]
            warn = ""
            if not r["yardstick_maximal"]:
                warn += f"  <-- NOT AC-MAXIMAL ({len(addable)} addable)"
            if r["yardstick_beaten"]:
                warn += "  <-- YARDSTICK BEATEN BY A WALK"
            print(f"  K={k:<4d} s={seed}  requested {req_kw:8.1f} kW  reachable {reach_kw:8.1f} "
                  f"({r['n_never_feasible']:3d} never)  achievable {y_kw:7.1f}"
                  f"   R/A {r['eff_pressure_A']:5.2f}x  R/Y {r['eff_pressure_Y']:5.2f}x"
                  f"  BLOCKED {r['n_blocked']:3d}"
                  f"   spread {spread:5.2f}x  coinflip {r['coinflip']:3d}"
                  f"  median {r['median_pct']:5.1f}%  [{r['dt']:.0f}s]{warn}")

    d = pd.DataFrame(rows)
    d["lottery"] = d.spread > 1.01
    print(f"\n  lottery in {int(d.lottery.sum())}/{len(d)} cells")
    for name, col in [("raw pressure  (requested / achievable)", d.pressure),
                      ("M50's R/Y     (reachable / achievable)", d.eff_pressure_Y),
                      ("R/A           (reachable / feeder CAPACITY)", d.eff_pressure_A),
                      ("contest ratio (reachable / ONE walk)", d.contest_ratio)]:
        if col.std() > 0:
            rr = spearmanr(col, d.spread)
            print(f"    Spearman(spread, {name:<42s}) = {rr.statistic:+.3f}  p={rr.pvalue:.4f}")

    print("\n  THE CERTIFICATE -- does ONE walk seat every reachable request? (2K AC solves)")
    print(f"    {'':22s} {'no lottery':>11s} {'lottery':>8s}")
    for lab, sub in [("blocked == 0  ->", d[d.n_blocked == 0]), ("blocked > 0   ->", d[d.n_blocked > 0])]:
        print(f"    {lab:22s} {int((~sub.lottery).sum()):11d} {int(sub.lottery.sum()):8d}")
    if save:
        print(f"\n  saved -> outputs/m51_law_{feeder}.csv")
    return d


def record_capacity(name, a_kw, mu_r, info, desc):
    """One row per feeder in outputs/m51_capacity.csv -- the law's denominator."""
    OUT.mkdir(exist_ok=True)
    path = OUT / "m51_capacity.csv"
    row = dict(feeder=name, description=desc, capacity_kw=a_kw, mean_reachable_kw=mu_r,
               probe_k=info["probe_k"], probe_reachable_kw=info["probe_reachable_kw"],
               probe_last_gain=info["probe_last_gain"])
    d = pd.read_csv(path) if path.exists() else pd.DataFrame()
    d = d[d.feeder != name] if len(d) else d
    d = pd.concat([d, pd.DataFrame([row])], ignore_index=True)
    d.to_csv(path, index=False)
    return row


def certify_m50(feeder, n_jobs, save):
    """Back-fill the certificate onto M50's 63 already-measured cells. They predate it, and
    it costs only the screen plus ONE reference walk (2K AC solves) -- no MILP, no re-run of
    the 200 arrival orders whose spread they already report."""
    ctx, mod, rel, mk, _desc = load_feeder(feeder, n_jobs)
    d = pd.read_csv(OUT / f"m50_eff_pressure_{feeder}.csv")
    out = []
    for r in d.itertuples():
        t0 = time.time()
        projects = mk(ctx, int(r.k), int(r.seed))
        by_id = {p.id: abs(p.p_mw) * 1000 for p in projects}
        feas = solo_feasible(ctx, projects, mod, n_jobs)
        ref = _m.fcfs_accept_sets(ctx, projects, mod, [REF_WALK_SEED], n_jobs)[0]
        blocked = sorted(feas - set(ref))
        w_kw = sum(by_id[i] for i in ref)
        out.append(dict(feeder=feeder, k=int(r.k), seed=int(r.seed),
                        ref_walk_kw=w_kw, n_blocked=len(blocked),
                        blocked_kw=sum(by_id[i] for i in blocked),
                        contest_ratio=r.reachable_kw / w_kw if w_kw else np.nan))
        print(f"  K={r.k:<4d} s={r.seed}  reachable {r.reachable_kw:8.1f} kW  one walk seats "
              f"{w_kw:8.1f}  BLOCKED {len(blocked):3d}  ratio "
              f"{out[-1]['contest_ratio']:5.2f}x   (spread {r.spread_p95_p5:5.2f}x)"
              f"  [{time.time()-t0:.0f}s]")
        if save:
            pd.DataFrame(out).to_csv(OUT / f"m51_certificate_{feeder}.csv", index=False)
    if save:
        print(f"\n  saved -> outputs/m51_certificate_{feeder}.csv")


COLS = ["feeder", "k", "seed", "requested_kw", "reachable_kw", "yardstick_kw", "pressure",
        "eff_pressure_Y", "eff_pressure_A", "contest_ratio", "n_blocked", "spread",
        "coinflip", "median_pct"]


def pool() -> None:
    """The corpus verdict: M50's two feeders + everything measured since, one table."""
    cap = pd.read_csv(OUT / "m51_capacity.csv").set_index("feeder")
    frames = []
    for f in ["ckt7", "ckt24"]:                       # M50's cells, re-scored
        p, c = OUT / f"m50_eff_pressure_{f}.csv", OUT / f"m51_certificate_{f}.csv"
        if p.exists() and f in cap.index and c.exists():
            d = pd.read_csv(p).merge(pd.read_csv(c), on=["feeder", "k", "seed"])
            d["eff_pressure_Y"] = d.eff_pressure
            d["eff_pressure_A"] = d.reachable_kw / cap.loc[f, "capacity_kw"]
            d["spread"] = d.spread_p95_p5 if "spread_p95_p5" in d else d.spread
            frames.append(d[COLS])
    for p in sorted(OUT.glob("m51_law_*.csv")):
        frames.append(pd.read_csv(p)[COLS])
    d = pd.concat(frames, ignore_index=True)
    d["lottery"] = d.spread > 1.01

    print(f"THE LAW ON THE CORPUS -- {len(d)} cells, {d.feeder.nunique()} feeders\n")
    print(f"  {'feeder':22s} {'cells':>5s} {'lottery':>9s}   {'rho(raw)':>9s} {'rho(R/Y)':>9s} "
          f"{'rho(R/A)':>9s} {'rho(R/W)':>9s}")
    for f, g in d.groupby("feeder", sort=False):
        rr = [spearmanr(g[c], g.spread).statistic if g[c].std() > 0 else float("nan")
              for c in ["pressure", "eff_pressure_Y", "eff_pressure_A", "contest_ratio"]]
        print(f"  {f:22s} {len(g):5d} {g.lottery.sum():4d}/{len(g):<4d}"
              f"  {rr[0]:+9.3f} {rr[1]:+9.3f} {rr[2]:+9.3f} {rr[3]:+9.3f}")
    print(f"  {'-'*22} {'-'*5} {'-'*9}   {'-'*9} {'-'*9} {'-'*9} {'-'*9}")
    for name, col in [("POOLED raw pressure (requested/achievable)", d.pressure),
                      ("POOLED M50's R/Y   (reachable/achievable)", d.eff_pressure_Y),
                      ("POOLED R/A         (reachable/feeder capacity)", d.eff_pressure_A),
                      ("POOLED R/W         (reachable/ONE walk)", d.contest_ratio)]:
        rr = spearmanr(col, d.spread)
        print(f"  {name:48s} rho = {rr.statistic:+.3f}   p = {rr.pvalue:.2e}")

    print("\n  THE CERTIFICATE -- one walk, 2K AC solves, no model, no permutations:")
    print(f"    {'':24s} {'no lottery':>11s} {'lottery':>9s}")
    for lab, s in [("every reachable seated", d[d.n_blocked == 0]),
                   ("some reachable BLOCKED", d[d.n_blocked > 0])]:
        print(f"    {lab:24s} {int((~s.lottery).sum()):11d} {int(s.lottery.sum()):9d}")

    # The SHARP test. The theorem does not merely say "no lottery" when nothing is blocked;
    # it says the spread is EXACTLY 1.000, because every arrival order accepts exactly the
    # solo-feasible set. Any cell that clears the certificate and still varies at all is a
    # counterexample to subset-closure in AC -- the one assumption the theorem rests on.
    safe = d[d.n_blocked == 0]
    if len(safe):
        viol = safe[safe.spread > 1.0001]
        print(f"\n    of the {len(safe)} certified-safe cells, worst spread seen: "
              f"{safe.spread.max():.5f}x  ({len(viol)} above 1.0001x)")
        if len(viol):
            print("    SUBSET-CLOSURE VIOLATED -- the theorem's assumption fails in AC:")
            for r in viol.itertuples():
                print(f"      {r.feeder} K={r.k} s={r.seed}: spread {r.spread:.4f}x")

    d.to_csv(OUT / "m51_pooled.csv", index=False)
    print(f"\n  saved -> outputs/m51_pooled.csv")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--feeder", choices=list(FEEDERS) + ["ckt7", "ckt24"])
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    ap.add_argument("--targets", type=float, nargs="+", default=TARGETS)
    ap.add_argument("--permutations", type=int, default=100)
    ap.add_argument("--n-jobs", type=int, default=8)
    ap.add_argument("--k0", type=int, default=50, help="first probe queue length")
    ap.add_argument("--kmax", type=int, default=1600, help="largest probe queue length")
    ap.add_argument("--calibrate-only", action="store_true",
                    help="just A and the mean reachable kW -- for Ckt7/Ckt24, whose cells "
                         "M50 already measured and which only need the new denominator")
    ap.add_argument("--certify-m50", action="store_true",
                    help="back-fill the certificate onto M50's cells (ckt7/ckt24)")
    ap.add_argument("--pool", action="store_true")
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()

    if a.pool:
        pool()
        return
    if a.certify_m50:
        certify_m50(a.feeder, a.n_jobs, a.save)
        return

    global _CTX, _PROJ, _MOD
    ctx, mod, rel, mk, desc = load_feeder(a.feeder, a.n_jobs)
    net = ctx.net
    print(f"M51 -- the law on {a.feeder}: {desc}")
    print(f"  {len(net.bus)} buses, {len(net.load)} loads, "
          f"baseline vmin {net.res_bus.vm_pu.min():.4f} vmax {net.res_bus.vm_pu.max():.4f}")
    if isinstance(mod, Feeder):
        print(f"  limits: vmin {mod.VMIN_PU:.4f}  vmax {mod.VMAX_PU:.4f}  "
              f"loading {mod.LOADING_MAX_PCT:.1f}%")
        if not self_test(mod):
            raise SystemExit("staged oracle disagrees with the reference oracle")

    print("\n  CALIBRATION -- the feeder's hosting capacity A under this request distribution")
    t0 = time.time()
    a_kw, mu_r, info = capacity(ctx, mod, rel, mk, a.n_jobs, k0=a.k0, kmax=a.kmax)
    print(f"    A = {a_kw:.1f} kW;  mean reachable request = {mu_r:.2f} kW  "
          f"[{time.time()-t0:.0f}s]")
    if a.save:
        record_capacity(a.feeder, a_kw, mu_r, info, desc)
        print(f"    saved -> outputs/m51_capacity.csv")
    if a.calibrate_only:
        return
    run_cells(a.feeder, ctx, mod, rel, mk, a_kw, mu_r, a.seeds, a.targets, a.permutations,
              a.n_jobs, a.save)


if __name__ == "__main__":
    main()
