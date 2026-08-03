"""M99 -- CKT24 ON ITS NATIVE UNBALANCED MODEL

WHY
---
exp122 solved the two ENWL feeders unbalanced and the answer was not a bounded caveat. On
n13f4 the irreducible residual VANISHED (48.6% -> 0.0%, positive in 0/20); on n2f1 it was
CREATED (0.0% -> 50.0%, positive in 20/20, 83.3% certificate-backed). A balanced
positive-sequence solve does not err in a known direction on an unbalanced feeder: it can
hide the mechanism or manufacture it, feeder by feeder.

That turns the manuscript's "Balanced power flow on the unbalanced feeders" limitation from
a disclosure into an open question about every cell it covers -- and it covers Ckt24, which
after n13f4's retraction is one of only two cells still carrying an irreducible residual.
So Ckt24's R* has to be re-measured on the model EPRI actually ships, in the engine that
model was written for.

Ckt24 is the right test for a second reason: it is the only cell in the corpus whose thermal
limits are REAL. ENWL specifies no NormAmps at all (exp122 drops line thermal for that
reason) and the SMART-DS corpus disables ampacity outright. Ckt24 ships wire data, line
codes with NormAmps, and 843 rated transformers, and at baseline it sits hard against them:
94.7% on the worst non-excluded line, 99.8% on the worst non-excluded transformer.

THE CONSTRAINT SET, AND HOW IT MAPS TO THE BALANCED ARM
-------------------------------------------------------
exp60 discloses three exclusions on this feeder, each forced by a baseline defect no request
causes. All three are RECOMPUTED here natively rather than carried over, because the two
engines disagree about which elements are broken:

  the substation transformer     balanced 111.06% of 45 MVA   unbalanced 118.68%
  pre-broken small transformers  balanced 37                  unbalanced 45
  their downstream pockets       balanced 241 loads           unbalanced 273 loads

The rules themselves are exp60's, unchanged: every other transformer and line must stay at
or below 100%; the substation transformer must not exceed its OWN baseline loading (PV export
relieves it, so it moves the right way); the pre-broken transformers and every line inside
their downstream islands are dropped from the check.

ONE EXTENSION, DISCLOSED. The balanced arm applies the voltage band to every bus. Solved
unbalanced, Ckt24's minimum node voltage is 0.9396 pu -- below the 0.94 floor -- and it sits
INSIDE one of the excluded pockets, on a secondary already disqualified from carrying a
request. Over the non-pocket nodes the baseline is 0.9705-1.0480 pu, comfortably compliant.
The pocket exclusion is therefore extended from the thermal check to the voltage check, for
exp60's own stated reason: those buses are never offered as candidates, and leaving them in
would make every accept-set infeasible for a violation no request caused. `vmin_all` is
recorded per draw so the extension stays auditable.

The slack node is dropped from the voltage check (M54): the source sits at its 1.05 pu
setpoint -- the same setpoint the balanced arm uses, and the one the shipped master declares
-- and no request moves it.

REQUEST SIZE IS NOT NEUTRAL HERE, AND THAT IS THE POINT
-------------------------------------------------------
Ckt24's candidate pool is 4,055 LV secondary points at 0.240 or 0.277 kV LN (plus a single
MV point), fed by services rated 195-299 A, off transformers of which 702 of 843 are at or
below 100 kVA. So the balanced arm's 100 kW single-phase request draws 417 A on a service
rated 195-299 A: it cannot be served alone, and the shipped feeder is right to say so.

The BALANCED solve does not see that. A positive-sequence model spreads the same 100 kW over
three phases, 139 A each, comfortably inside every rating -- which is exactly why the
balanced arm reports 200-600 kW of generation feasible alone and this one reports zero. At
100 kW the unbalanced cell is therefore saturated: R* is 100% because nothing whatever is
feasible alone, which is a true statement about a request nobody would submit.

The informative band is 25-50 kW, where a service CAN carry one leg, and `--size-kw` sweeps
it. `--mismatch` sweeps the other objection: at 1.0 the two legs cancel exactly and a pair is
a no-op, so joint feasibility is arithmetic rather than earned.

Run:
  uv run python experiments/125_ckt24_unbalanced.py --save
  uv run python experiments/125_ckt24_unbalanced.py --seeds 0 1 --no-certificate
"""
from __future__ import annotations

import argparse
import itertools
import math
import os
import sys
import tempfile
import time
import weakref
from pathlib import Path
from types import SimpleNamespace

import networkx as nx
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

_sd = __import__("101_residual_smartds")
_seq = __import__("91_sequential_segregation")
_alt = __import__("109_alternating_rounds")
_cert = __import__("110_simultaneity_certificate")

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "outputs"
MASTER = str(REPO / "data" / "opendss" / "ckt24" / "master_ckt24.dss")

VMIN_PU, VMAX_PU, LOADING_MAX_PCT = 0.94, 1.05, 100.0

# The substation's non-worsening reference is an EQUALITY at the baseline, and a co-located
# pair perturbs the injection by exactly zero -- so whether the set passes is decided by the
# last bits of the solve. Two separate effects had to be sized before picking a tolerance.
#
# 1. A single all-24 accept-set moves the substation by +7e-8 % of 45 MVA (0.03 VA), which a
#    strict `violation == 0` reads as infeasible.
# 2. Worse, the verdict is HISTORY-DEPENDENT. OpenDSS carries solution state across
#    Solution.Solve() calls within a context, and this feeder has a substation RegControl.
#    Measured directly: the same all-24 set on seeds 1, 2 and 4 is feasible on a freshly
#    staged circuit, infeasible after twenty-four other solves, and feasible again once
#    re-staged. The substation reading drifts by up to 4e-4 percentage points -- 0.17 kVA on
#    45 MVA -- which is invisible physically and fatal to a zero-tolerance equality. Node
#    voltages do not drift at all (vmax identical to six decimals); it is the feeder-wide
#    power sum that moves.
#
# So the loading tolerance is set 25x above the measured drift and still 4.5 kVA on a 45 MVA
# transformer, i.e. nothing. Without it, whether a knife-edge set passes depends on how many
# other sets were evaluated first, which is not a property an oracle may have.
TOL_PU, TOL_PCT = 1e-6, 1e-2

# Ckt24's substation RegControl ships `debugtrace=yes`, so every DSS context opens the same
# trace file and the second one dies with "Bad file number". Each context therefore gets its
# own DataPath, and debugtrace is switched off immediately after the redirect.
_TRACE_ROOT = Path(tempfile.mkdtemp(prefix="ckt24_dss_"))
_CTX_SEQ = itertools.count()

# A DSS context is never freed. Measured on this feeder: `NewContext()` costs 40 MB that a
# dropped reference does not give back, while `Clear` + `Redirect` on an EXISTING context is
# flat to within a megabyte over any number of reloads. exp101's oracle calls `_fresh()` per
# staging and per anchored copy, which on a 6,058-bus circuit reached 29 GB in four minutes.
# So contexts are pooled and reloaded, never accumulated.
_CTX_FREE: list = []
_CTX_DIR: dict = {}


def _acquire_ctx():
    from opendssdirect import dss as dss_base
    if _CTX_FREE:
        return _CTX_FREE.pop()
    c = dss_base.NewContext()
    _CTX_DIR[id(c)] = next(_CTX_SEQ)          # a SLOT, resolved to a path per process
    return c


def _reload(c, master):
    # The DataPath is keyed by (pid, slot), not slot alone: the certify path forks a worker
    # pool, and a forked child inherits both the parent's contexts and its open trace-file
    # handles. Sharing a directory across processes reproduces the same "Bad file number".
    d = _TRACE_ROOT / f"p{os.getpid()}_{_CTX_DIR[id(c)]}"
    d.mkdir(parents=True, exist_ok=True)
    c.Command("Clear")
    c.Command(f"Set DataPath={d}")
    c.Command(f'Redirect "{master}"')
    for nm in c.RegControls.AllNames():
        c.Command(f"RegControl.{nm}.debugtrace=no")
    c.Solution.Solve()
    return c

_COPY = ("master", "name", "order", "slack", "pq", "vbase", "points", "_order_idx",
         "VMIN_PU", "VMAX_PU", "sub_name", "sub_base_kva", "sub_base_s", "bad_trafos",
         "excl_buses", "excl_lines", "line_check", "trafo_check", "line_amps", "trafo_kva",
         "v_mask", "adj", "src_bus", "_gcache")


class Ckt24DSSFeeder(_sd.MixedDSSFeeder):
    """EPRI Ckt24's shipped three-phase model behind the same Feeder API the balanced arm uses.

    Adds what the SMART-DS feeder has no need of: a real thermal check (both lines against
    NormAmps and transformers against their kVA), exp60's three exclusions recomputed on the
    unbalanced solution, and a candidate pool restricted to load buses outside the excluded
    pockets.
    """

    def __init__(self, master=MASTER, vmin=VMIN_PU, vmax=VMAX_PU):
        super().__init__(master)
        self.VMIN_PU, self.VMAX_PU = float(vmin), float(vmax)
        c = self._ctx
        c.Solution.Solve()

        c.Vsources.First()
        c.Circuit.SetActiveElement(f"Vsource.{c.Vsources.Name()}")
        self.src_bus = c.CktElement.BusNames()[0].split(".")[0].upper()

        # --- transformers: identify the substation, then the pre-broken ones
        xf = {}
        for nm in self._each("Transformers"):
            c.Transformers.Name(nm)
            rated = float(c.Transformers.kVA())
            s = self._elem_s(f"Transformer.{nm}")
            xf[nm] = (100.0 * s / rated if rated else 0.0, rated,
                      [b.split(".")[0].upper() for b in c.CktElement.BusNames()], s)
        self.sub_name = max(xf, key=lambda k: xf[k][1])
        self.sub_base_kva = xf[self.sub_name][1]
        self.sub_base_s = xf[self.sub_name][3]
        self.bad_trafos = {k for k, v in xf.items() if v[0] > 100.0 and k != self.sub_name}

        # --- the pockets: connected components of the LINE graph below each broken transformer
        g = nx.Graph()
        lines = []
        for nm in self._each("Lines"):
            # `Lines.Name` is the SETTER that moves the Lines interface. Circuit.SetActiveElement
            # does NOT move it: reading NormAmps after SetActiveElement returns OpenDSS's 400 A
            # default for every line, which silently rates the whole feeder wrong.
            c.Lines.Name(nm)
            na = float(c.Lines.NormAmps())
            c.Circuit.SetActiveElement(f"Line.{nm}")
            b = [x.split(".")[0].upper() for x in c.CktElement.BusNames()]
            g.add_edge(b[0], b[1])
            lines.append((nm, b[0], b[1], na, int(c.CktElement.NumConductors())))
        self.excl_buses = set()
        for k in self.bad_trafos:
            lv = xf[k][2][1]
            self.excl_buses |= nx.node_connected_component(g, lv) if lv in g else {lv}
        self.excl_lines = {nm for nm, a, b, _na, _nc in lines
                           if a in self.excl_buses or b in self.excl_buses}

        # --- what evaluate() actually sweeps, resolved once
        self.line_check = [(nm, na, nc) for nm, _a, _b, na, nc in lines
                           if nm not in self.excl_lines and na > 0]
        self.trafo_check = [(nm, xf[nm][1]) for nm in xf
                            if nm != self.sub_name and nm not in self.bad_trafos]
        self.line_amps = {nm: (na, nc) for nm, _a, _b, na, nc in lines}
        self.trafo_kva = {nm: xf[nm][1] for nm in xf}

        nodes = [n.upper() for n in c.Circuit.AllNodeNames()]
        self.v_mask = np.array([n.split(".")[0] not in self.excl_buses
                                and n.split(".")[0] != self.src_bus for n in nodes])

        # --- candidate attach points: load buses outside the pockets (exp60's pool)
        pts = {}
        for nm in self._each("Loads"):
            c.Circuit.SetActiveElement(f"Load.{nm}")
            bn = c.CktElement.BusNames()[0]
            bus = bn.split(".")[0].upper()
            if bus in self.excl_buses:
                continue
            ph = [int(x) for x in bn.split(".")[1:] if x.isdigit() and 1 <= int(x) <= 3]
            pts.setdefault(bus, set()).update(ph or [1])
        self.points = [(b, p) for b in sorted(pts) for p in sorted(pts[b])]

        # --- radial adjacency for the thermal model (lines AND transformers, unlike the pockets)
        self.adj = {}
        for nm, a, b, _na, _nc in lines:
            self.adj.setdefault(a, []).append((b, "Line", nm))
            self.adj.setdefault(b, []).append((a, "Line", nm))
        for nm, (_p, _r, bus, _s) in xf.items():
            self.adj.setdefault(bus[0], []).append((bus[1], "Transformer", nm))
            self.adj.setdefault(bus[1], []).append((bus[0], "Transformer", nm))

        self._gcache = None
        self.ctx = SimpleNamespace(net=self.ctx.net, feeder=self,
                                   excluded_line_idx=[], excluded_buses=set())

    # ------------------------------------------------------------------ helpers
    def _each(self, iface):
        """Names of every element of a class, without leaving an iterator half-consumed."""
        return list(getattr(self._ctx, iface).AllNames())

    def _elem_s(self, full_name):
        """Worst-terminal apparent power (kVA) of the active element."""
        c = self._ctx
        c.Circuit.SetActiveElement(full_name)
        p = np.asarray(c.CktElement.Powers(), dtype=float)
        per = 2 * c.CktElement.NumConductors()
        return max(float(np.hypot(p[t * per:(t + 1) * per][0::2].sum(),
                                  p[t * per:(t + 1) * per][1::2].sum()))
                   for t in range(c.CktElement.NumTerminals()))

    def _elem_pq(self, full_name, upstream_bus):
        """(kW, kvar) into the element at the terminal facing `upstream_bus`."""
        c = self._ctx
        c.Circuit.SetActiveElement(full_name)
        t = 0 if c.CktElement.BusNames()[0].split(".")[0].upper() == upstream_bus else 1
        p = np.asarray(c.CktElement.Powers(), dtype=float)
        per = 2 * c.CktElement.NumConductors()
        seg = p[t * per:(t + 1) * per]
        return float(seg[0::2].sum()), float(seg[1::2].sum())

    def _fresh(self):
        """Reload this feeder's OWN context in place; take one from the pool only the first
        time. Returns the same object it was handed, so `self._ctx = self._fresh()` is a
        reload rather than an allocation."""
        c = getattr(self, "_ctx", None)
        if c is None:
            c = _acquire_ctx()
            weakref.finalize(self, _CTX_FREE.append, c)
        return _reload(c, self.master)

    # ------------------------------------------------------------------ the oracle
    def evaluate(self, ctx, projects, accepted_ids):
        self._stage(projects)
        self._set(accepted_ids)
        c = self._ctx
        c.Solution.Solve()
        by_id = {p.id: p for p in projects}
        mw = sum(abs(by_id[r].p_mw) for r in accepted_ids)
        if not c.Solution.Converged():
            return False, 0.0, float("inf"), {"diverged": True}

        v = np.asarray(c.Circuit.AllBusMagPu())
        keep = self.v_mask & (v > 0)
        if not keep.any():
            return False, 0.0, float("inf"), {"diverged": True}
        vv = v[keep]
        vmin, vmax = float(vv.min()), float(vv.max())

        line_pct = 0.0
        for nm, na, nc in self.line_check:
            c.Circuit.SetActiveElement(f"Line.{nm}")
            cur = np.asarray(c.CktElement.CurrentsMagAng(), dtype=float)[0:2 * nc:2]
            line_pct = max(line_pct, 100.0 * float(cur.max()) / na)

        trafo_pct = 0.0
        for nm, rated in self.trafo_check:
            if rated:
                trafo_pct = max(trafo_pct, 100.0 * self._elem_s(f"Transformer.{nm}") / rated)

        sub_s = self._elem_s(f"Transformer.{self.sub_name}")
        sub_pct = 100.0 * sub_s / self.sub_base_kva

        sub_base_pct = 100.0 * self.sub_base_s / self.sub_base_kva
        violation = (max(0.0, self.VMIN_PU - vmin - TOL_PU)
                     + max(0.0, vmax - self.VMAX_PU - TOL_PU)
                     + max(0.0, line_pct - LOADING_MAX_PCT - TOL_PCT) / 100.0
                     + max(0.0, trafo_pct - LOADING_MAX_PCT - TOL_PCT) / 100.0
                     + max(0.0, sub_pct - sub_base_pct - TOL_PCT) / 100.0)
        return violation == 0.0, mw, violation, {
            "vmin": vmin, "vmax": vmax, "vmin_all": float(v[v > 0].min()),
            "line_pct": line_pct, "trafo_pct": trafo_pct, "sub_pct": sub_pct}

    # ------------------------------------------------------------- the linear model
    def _gldf_base(self):
        """Y, its factorization and E, all built ONCE.

        exp101 rebuilds a loads-disabled context and re-factorizes a 7,521-node Y on every
        MILP iteration. On this feeder that is both the memory leak above and the dominant
        cost -- and it is redundant: M41 established that re-linearizing RE-ANCHORS but cannot
        RE-SLOPE, because pandapower and OpenDSS alike keep PQ loads in Sbus rather than
        Ybus. Y is taken with every load disabled, and the source voltage is fixed by the
        Vsource, so Y, its LU and E are all invariant. Only `v_base` -- read from the CURRENT
        solved state in `build_gldf` -- moves with the anchor. Caching is therefore exact,
        not an approximation.
        """
        if getattr(self, "_gcache", None) is not None:
            return self._gcache
        import scipy.sparse as sp
        import scipy.sparse.linalg as spla

        cy = _reload(_acquire_ctx(), self.master)
        i = cy.Loads.First()
        while i:
            cy.Command(f"Disable Load.{cy.Loads.Name()}")
            i = cy.Loads.Next()
        cy.Solution.Solve()
        data, idx, ptr = cy.YMatrix.getYsparse()
        n = len(ptr) - 1
        Y = sp.csc_matrix((data, idx, ptr), shape=(n, n))
        raw = np.asarray(cy.Circuit.YNodeVArray(), dtype=float)
        V = raw[0::2] + 1j * raw[1::2]
        lu = spla.splu(sp.csc_matrix(Y[self.pq, :][:, self.pq]))
        E = lu.solve(-(np.asarray(Y[self.pq, :][:, self.slack].todense()) @ V[self.slack]))
        _CTX_FREE.append(cy)
        self._gcache = dict(lu=lu, E=E, cols={},
                            pos={int(b): j for j, b in enumerate(self.pq)})
        return self._gcache

    def build_gldf(self, ctx, buses):
        """exp101's columns, memoized by NODE index and re-anchored per call."""
        g = self._gldf_base()
        m_cols = {}
        for b in buses:
            bi = int(b)
            ni = self._order_idx.get(self._keys[bi].upper())
            jp = None if ni is None else g["pos"].get(ni)
            if jp is None:
                continue
            col = g["cols"].get(jp)
            if col is None:
                e = np.zeros(len(self.pq))
                e[jp] = 1.0
                z = g["lu"].solve(e.astype(complex))
                col = (2.0 * np.real(g["E"] * np.conj(z) / g["E"][jp])) / self.vbase ** 2
                g["cols"][jp] = col
            m_cols[bi] = col
        V = self._ynode_v()                       # the anchored operating point
        return dict(v_base=(np.abs(V[self.pq]) / self.vbase) ** 2, m_cols=m_cols,
                    base_mva=1e-6, pq=self.pq, lookup={i: i for i in range(len(self.order))})

    def build_thermal(self, ctx, projects):
        """exp68's construction on the DSS model: one row per branch on the path from the
        source to a request, S_max calibrated from that branch's OWN flow and rating.

        Ckt24 is radial (5,222 lines over 6,000 buses form a forest), so an injection moves
        only the branches between it and the source; every other branch keeps a baseline
        already verified clean. Rows are built at the CURRENT operating point, which the
        M41 re-linearizer has just moved to the incumbent.

        The substation row is deliberately loose. Its S_max is the shipped baseline apparent
        power rather than the 45 MVA nameplate, which matches the AC oracle's non-worsening
        rule in kind but not in tightness: with p_base near 51 MW and r_lim near 52 MW the
        row barely binds, so the linear model does not really police that element. This
        costs nothing here -- the oracle does police it, every accepted set is AC-verified
        against it, and PV export moves it the relieving way -- but it is search guidance
        that is weaker than it looks, and should not be mistaken for the constraint itself.
        """
        key_of, p_base, q_base, s_max, members = {}, [], [], [], []
        for r_i, proj in enumerate(projects):
            bus = proj.bus_name.upper()
            seen = {bus}
            while True:
                step = self._parent_step(bus, seen)
                if step is None:
                    break
                par, kind, nm = step
                seen.add(par)
                key = (kind, nm)
                if key not in key_of:
                    if kind == "Line" and nm in self.excl_lines:
                        bus = par
                        continue
                    if kind == "Transformer" and nm in self.bad_trafos:
                        bus = par
                        continue
                    pb, qb = self._elem_pq(f"{kind}.{nm}", par)
                    s_b = math.hypot(pb, qb)
                    if kind == "Transformer":
                        # the substation is bound by its own baseline, not its nameplate --
                        # exp60's non-worsening rule, carried into the linear model
                        smax = (self.sub_base_s if nm == self.sub_name
                                else self.trafo_kva.get(nm, 0.0))
                    else:
                        na, nc = self.line_amps.get(nm, (0.0, 0))
                        self._ctx.Circuit.SetActiveElement(f"Line.{nm}")
                        cur = np.asarray(self._ctx.CktElement.CurrentsMagAng(),
                                         dtype=float)[0:2 * nc:2]
                        pct = 100.0 * float(cur.max()) / na if na else 0.0
                        # M35's calibration: OpenDSS rates by CURRENT at the ACTUAL voltage,
                        # so a nominal kVA from NormAmps does not reconcile with the check
                        smax = (s_b / (pct / 100.0)) if pct > 1.0 else float("inf")
                    if not np.isfinite(smax) or smax ** 2 <= qb ** 2:
                        bus = par
                        continue
                    key_of[key] = len(p_base)
                    p_base.append(pb / 1000.0)          # kW -> MW, exp68's units
                    q_base.append(qb / 1000.0)
                    s_max.append(smax / 1000.0)
                    members.append([])
                j = key_of.get(key)
                if j is not None:
                    members[j].append(r_i)
                bus = par

        p_base = np.array(p_base)
        q_base = np.array(q_base)
        s_max = np.array(s_max)
        r_lim = np.sqrt(np.maximum(s_max ** 2 - q_base ** 2, 0.0))
        return dict(p_base=p_base, r_lim=r_lim, members=members)

    def _parent_step(self, bus, seen):
        """One hop toward the source. Radial, so the neighbour on the source side is the one
        whose own distance to the source is smaller; `_depth` caches that walk."""
        d = self._depth()
        here = d.get(bus)
        if here is None or here == 0:
            return None
        for nb, kind, nm in self.adj.get(bus, ()):
            if d.get(nb, 1 << 30) < here and nb not in seen:
                return nb, kind, nm
        return None

    def _depth(self):
        if getattr(self, "_depth_cache", None) is None:
            d, stack = {self.src_bus: 0}, [self.src_bus]
            while stack:
                u = stack.pop()
                for v, _k, _n in self.adj.get(u, ()):
                    if v not in d:
                        d[v] = d[u] + 1
                        stack.append(v)
            self._depth_cache = d
        return self._depth_cache


def with_anchor(base, anchor_projects, anchor_ids):
    """exp101's DSS-native anchor, carrying this subclass's exclusion state."""
    f2 = Ckt24DSSFeeder.__new__(Ckt24DSSFeeder)
    for a in _COPY:
        setattr(f2, a, getattr(base, a))
    f2._depth_cache = base._depth()
    f2._anchor = [p for p in anchor_projects if p.id in anchor_ids]
    f2._ctx = f2._fresh()
    f2._staged, f2._on = None, set()
    f2.ctx = SimpleNamespace(net=base.ctx.net, feeder=f2,
                             excluded_line_idx=[], excluded_buses=set())
    f2._keys = {}
    return f2


def make_family(f, desc, mismatch=1.0):
    """`mismatch` scales the LOAD leg against the generation leg.

    At 1.0 the two legs of a co-located pair cancel exactly, and a referee can fairly call
    the resulting pair a no-op the network never sees. Below 1.0 the pair leaves a net
    injection, so joint feasibility stops being arithmetic and has to be earned.
    """
    return _alt.Family(
        "ckt24_unbalanced", lambda: f,
        lambda ff, seed, k, size: _sd.build_colocated(ff, k, seed, size, size * mismatch),
        with_anchor, _seq.certify, desc, reset=_alt._reset_dss)


def report_baseline(f):
    ok, _mw, viol, info = f.evaluate(f.ctx, [], set())
    print(f"  substation {f.sub_name} ({f.sub_base_kva:.0f} kVA): "
          f"{info['sub_pct']:.2f}%   (balanced arm: 111.06%)")
    print(f"  pre-broken transformers excluded: {len(f.bad_trafos)}   (balanced arm: 37)")
    print(f"  excluded buses {len(f.excl_buses)}, excluded lines {len(f.excl_lines)}")
    print(f"  candidate (bus,phase) attach points: {len(f.points)}")
    print(f"  baseline  vmin {info['vmin']:.4f}  vmax {info['vmax']:.4f}  "
          f"(all nodes: {info['vmin_all']:.4f})")
    print(f"            worst non-excluded line {info['line_pct']:.2f}%, "
          f"transformer {info['trafo_pct']:.2f}%")
    print(f"  baseline feasible: {ok}  (violation {viol:.6f})\n")
    return info


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--k", type=int, default=24)
    ap.add_argument("--size-kw", type=float, default=100.0)
    ap.add_argument("--seeds", type=int, nargs="+", default=list(range(20)))
    ap.add_argument("--n-jobs", type=int, default=6)
    ap.add_argument("--vmin", type=float, default=VMIN_PU)
    ap.add_argument("--vmax", type=float, default=VMAX_PU)
    ap.add_argument("--mismatch", type=float, default=1.0,
                    help="load leg as a fraction of the generation leg; "
                         "1.0 is the exactly-cancelling co-located pair")
    ap.add_argument("--no-certificate", action="store_true")
    ap.add_argument("--out", default="m99_ckt24_unbalanced.csv")
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()

    print("\n  M99 -- CKT24 SOLVED UNBALANCED, ON ITS SHIPPED OPENDSS MODEL\n")
    print("  exp60's three exclusions recomputed natively, not carried over: the two engines")
    print("  disagree about which elements are broken at baseline.\n")

    t0 = time.time()
    f = Ckt24DSSFeeder(MASTER, vmin=a.vmin, vmax=a.vmax)
    print(f"  built in {time.time() - t0:.1f}s: {len(f.order)} nodes, "
          f"{len(f.line_check)} checked lines, {len(f.trafo_check)} checked transformers\n")
    base = report_baseline(f)

    fam = make_family(f, f"EPRI Ckt24 UNBALANCED (OpenDSS-native), co-located "
                         f"x{a.mismatch:.2f}, {a.vmin:.2f}-{a.vmax:.2f} pu, sub non-worsening",
                      mismatch=a.mismatch)
    rows = []
    hdr = (f"  {'seed':>4} | {'gen':>6} {'load':>6} {'seq':>6} {'JOINT':>6} {'alt':>6} | "
           f"{'R1%':>6} {'R*%':>6} | {'mixed':>5} {'cert%':>6} | {'s':>5}")
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

        rows.append(dict(feeder="ckt24", engine="opendss_unbalanced", seed=seed,
                         placement="colocated", k=a.k, size_kw=a.size_kw, mismatch=a.mismatch,
                         base_vmin=base["vmin"], base_vmax=base["vmax"],
                         base_sub_pct=base["sub_pct"], **w,
                         alternation_kw=best, r1_kw=r1, rstar_kw=rstar,
                         r1_pct=100 * r1 / joint if joint else np.nan,
                         rstar_pct=100 * rstar / joint if joint else np.nan,
                         n_mixed_pairs=n_mixed, certified_kw=cert_kw,
                         certified_share_pct=share, dt=time.time() - t0))
        print(f"  {seed:>4d} | {w['gen_alone_kw']:>6.0f} {w['load_alone_kw']:>6.0f} "
              f"{w['best_sequential_kw']:>6.0f} {joint:>6.0f} {best:>6.0f} | "
              f"{100 * r1 / joint if joint else 0:>6.1f} "
              f"{100 * rstar / joint if joint else 0:>6.1f} | "
              f"{n_mixed:>5d} {share:>6.1f} | {time.time() - t0:>5.0f}", flush=True)
        if a.save:
            OUT.mkdir(exist_ok=True)
            pd.DataFrame(rows).to_csv(OUT / a.out, index=False)

    d = pd.DataFrame(rows)
    print("\n" + "=" * 82)
    print("\n  SUMMARY -- median over draws\n")
    print(f"    joint {d.joint_kw.median():.0f} kW   R1 {d.r1_pct.median():.1f}%   "
          f"R* {d.rstar_pct.median():.1f}%   R*>0 in {int((d.rstar_kw > 1e-9).sum())}/{len(d)}")
    s = d.certified_share_pct.dropna()
    if len(s):
        print(f"    certified share of R*: median {s.median():.1f}%, min {s.min():.1f}%")
    print("\n  BALANCED arm, same cell (m82_colo_s20 / m94_certified_share):")
    print("    R* median 61.8%, certified share median 100%")
    print("\n  ENWL showed the balanced solve can hide the mechanism (n13f4, 48.6% -> 0.0%)")
    print("  or manufacture it (n2f1, 0.0% -> 50.0%). This decides which happened here.\n")
    if a.save:
        OUT.mkdir(exist_ok=True)
        d.to_csv(OUT / a.out, index=False)
        print(f"  saved -> outputs/{a.out}\n")


if __name__ == "__main__":
    main()
