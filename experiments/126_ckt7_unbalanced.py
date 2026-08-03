"""M100 -- CKT7 ON ITS NATIVE UNBALANCED MODEL, the last balanced EPRI cell

WHY
---
Three feeders have now been re-solved on the models they ship with, and the balanced
positive-sequence treatment failed differently on each:

  n13f4   R* 48.6% -> 0.0%    the balanced solve was CARRYING the residual
  n2f1    R*  0.0% -> 50.0%   the balanced solve was HIDING one
  Ckt24   R* 61.8% -> 100%    the balanced solve was UNDERSTATING one

So "balanced" is not a conservative approximation with a known sign, and every cell the
manuscript solves that way is an open question rather than a disclosed simplification.
Ckt7 is the last EPRI feeder still solved balanced, and it matters for a specific claim:
Ckt7 is a ZERO-R* cell under both placements, and the paper uses it as evidence that
co-location is necessary but not sufficient. If phase unbalance manufactures an irreducible
residual on Ckt7 the way it did on n2f1, that argument needs re-making; if it does not, the
argument survives a test it has not yet had.

WHAT DIFFERS FROM CKT24
-----------------------
Ckt7's constraint set is the simpler one and is taken verbatim from exp68: the 0.94-1.05 pu
band, every line and transformer at or below 100%, and the islands behind the eleven
pre-broken secondary service lines excluded (data/opendss/ckt7/PROVENANCE.md). There is no
substation non-worsening rule -- that is a Ckt24 disclosure and does not apply here -- and no
RegControl, so contexts need no per-process DataPath.

Two things do carry over and are imported rather than re-written: the pooled DSS contexts
(exp125), because the oracle otherwise leaks 40 MB per staging, and exp101's cached GLDF
factorization. The cache is safe at a non-default setpoint: `col_si` is
`2 Re(E conj(z) / E[jp])`, which is invariant to a uniform scaling of E, and the source
voltage enters E only as such a scaling.

THE SETPOINT
------------
The paper's Ckt7 cell runs the source at 1.04 pu, not the 1.05 the shipped master declares,
so `Vsource.source.pu` is set after each redirect. Reported per run so it cannot drift.

Run:
  uv run python experiments/126_ckt7_unbalanced.py --save
  uv run python experiments/126_ckt7_unbalanced.py --seeds 0 --no-certificate
"""
from __future__ import annotations

import argparse
import math
import sys
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
_m99 = __import__("125_ckt24_unbalanced")           # pooled contexts, thermal-row conventions

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "outputs"
MASTER = str(REPO / "data" / "opendss" / "ckt7" / "Master_ckt7.dss")

VMIN_PU, VMAX_PU, LOADING_MAX_PCT = 0.94, 1.05, 100.0
TOL_PU, TOL_PCT = 1e-6, 1e-6
SETPOINT = 1.04

_COPY = ("master", "name", "order", "slack", "pq", "vbase", "points", "_order_idx",
         "VMIN_PU", "VMAX_PU", "setpoint", "excl_buses", "excl_lines", "line_check",
         "trafo_check", "line_amps", "trafo_kva", "v_mask", "adj", "src_bus", "_gcache")


class Ckt7DSSFeeder(_sd.MixedDSSFeeder):
    """EPRI Ckt7's shipped three-phase model behind the Feeder API the balanced arm uses."""

    def __init__(self, master=MASTER, setpoint=SETPOINT, vmin=VMIN_PU, vmax=VMAX_PU):
        self.setpoint = float(setpoint)
        super().__init__(master)
        self.VMIN_PU, self.VMAX_PU = float(vmin), float(vmax)
        c = self._ctx
        c.Solution.Solve()

        c.Vsources.First()
        c.Circuit.SetActiveElement(f"Vsource.{c.Vsources.Name()}")
        self.src_bus = c.CktElement.BusNames()[0].split(".")[0].upper()

        # --- lines: ratings, endpoints, and which are over 100% at baseline
        lines, over = [], []
        for nm in list(c.Lines.AllNames()):
            c.Lines.Name(nm)                       # the setter; SetActiveElement does NOT move it
            na = float(c.Lines.NormAmps())
            c.Circuit.SetActiveElement(f"Line.{nm}")
            b = [x.split(".")[0].upper() for x in c.CktElement.BusNames()]
            nc = int(c.CktElement.NumConductors())
            cur = np.asarray(c.CktElement.CurrentsMagAng(), dtype=float)[0:2 * nc:2]
            pct = 100.0 * float(cur.max()) / na if na else 0.0
            lines.append((nm, b[0], b[1], na, nc))
            if pct > 100.0:
                over.append((nm, b[0], b[1]))

        # --- transformers
        xf = {}
        for nm in list(c.Transformers.AllNames()):
            c.Transformers.Name(nm)
            xf[nm] = float(c.Transformers.kVA())

        # --- the pockets: exp68's rule, cut each over-limit line and keep the side the
        #     source cannot reach
        g = nx.Graph()
        for nm, a, b, _na, _nc in lines:
            g.add_edge(a, b, key=nm)
        for nm in list(c.Transformers.AllNames()):
            c.Circuit.SetActiveElement(f"Transformer.{nm}")
            bs = [x.split(".")[0].upper() for x in c.CktElement.BusNames()]
            g.add_edge(bs[0], bs[1], key=f"xf:{nm}")
        for nm, a, b in over:
            if g.has_edge(a, b):
                g.remove_edge(a, b)
        self.excl_buses = set()
        for nm, a, b in over:
            if b in g:
                comp = nx.node_connected_component(g, b)
                if self.src_bus not in comp:
                    self.excl_buses |= comp
        self.excl_lines = {nm for nm, a, b, _na, _nc in lines
                           if a in self.excl_buses or b in self.excl_buses}

        self.line_check = [(nm, na, nc) for nm, _a, _b, na, nc in lines
                           if nm not in self.excl_lines and na > 0]
        self.trafo_check = [(nm, kva) for nm, kva in xf.items() if kva > 0]
        self.line_amps = {nm: (na, nc) for nm, _a, _b, na, nc in lines}
        self.trafo_kva = dict(xf)

        nodes = [n.upper() for n in c.Circuit.AllNodeNames()]
        self.v_mask = np.array([n.split(".")[0] not in self.excl_buses
                                and n.split(".")[0] != self.src_bus for n in nodes])

        # --- candidate attach points: load buses outside the pockets
        pts = {}
        for nm in list(c.Loads.AllNames()):
            c.Circuit.SetActiveElement(f"Load.{nm}")
            bn = c.CktElement.BusNames()[0]
            bus = bn.split(".")[0].upper()
            if bus in self.excl_buses:
                continue
            ph = [int(x) for x in bn.split(".")[1:] if x.isdigit() and 1 <= int(x) <= 3]
            pts.setdefault(bus, set()).update(ph or [1])
        self.points = [(b, p) for b in sorted(pts) for p in sorted(pts[b])]

        # --- radial adjacency for the thermal rows
        self.adj = {}
        for nm, a, b, _na, _nc in lines:
            self.adj.setdefault(a, []).append((b, "Line", nm))
            self.adj.setdefault(b, []).append((a, "Line", nm))
        for nm in xf:
            c.Circuit.SetActiveElement(f"Transformer.{nm}")
            bs = [x.split(".")[0].upper() for x in c.CktElement.BusNames()]
            self.adj.setdefault(bs[0], []).append((bs[1], "Transformer", nm))
            self.adj.setdefault(bs[1], []).append((bs[0], "Transformer", nm))

        self._gcache = None
        self.ctx = SimpleNamespace(net=self.ctx.net, feeder=self,
                                   excluded_line_idx=[], excluded_buses=set())

    # ---------------------------------------------------------------- context
    def _fresh(self):
        c = getattr(self, "_ctx", None)
        if c is None:
            c = _m99._acquire_ctx()
            weakref.finalize(self, _m99._CTX_FREE.append, c)
        c = _m99._reload(c, self.master)
        sp = getattr(self, "setpoint", None)
        if sp is not None:                          # the paper's cell is 1.04, not the 1.05 shipped
            c.Vsources.First()
            c.Command(f"Vsource.{c.Vsources.Name()}.pu={sp}")
            c.Solution.Solve()
        return c

    # ---------------------------------------------------------------- oracle
    def _elem_s(self, full_name):
        c = self._ctx
        c.Circuit.SetActiveElement(full_name)
        p = np.asarray(c.CktElement.Powers(), dtype=float)
        per = 2 * c.CktElement.NumConductors()
        return max(float(np.hypot(p[t * per:(t + 1) * per][0::2].sum(),
                                  p[t * per:(t + 1) * per][1::2].sum()))
                   for t in range(c.CktElement.NumTerminals()))

    def _elem_pq(self, full_name, upstream_bus):
        c = self._ctx
        c.Circuit.SetActiveElement(full_name)
        t = 0 if c.CktElement.BusNames()[0].split(".")[0].upper() == upstream_bus else 1
        p = np.asarray(c.CktElement.Powers(), dtype=float)
        per = 2 * c.CktElement.NumConductors()
        seg = p[t * per:(t + 1) * per]
        return float(seg[0::2].sum()), float(seg[1::2].sum())

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
        for nm, kva in self.trafo_check:
            trafo_pct = max(trafo_pct, 100.0 * self._elem_s(f"Transformer.{nm}") / kva)

        violation = (max(0.0, self.VMIN_PU - vmin - TOL_PU)
                     + max(0.0, vmax - self.VMAX_PU - TOL_PU)
                     + max(0.0, line_pct - LOADING_MAX_PCT - TOL_PCT) / 100.0
                     + max(0.0, trafo_pct - LOADING_MAX_PCT - TOL_PCT) / 100.0)
        return violation == 0.0, mw, violation, {
            "vmin": vmin, "vmax": vmax, "vmin_all": float(v[v > 0].min()),
            "line_pct": line_pct, "trafo_pct": trafo_pct}

    # ---------------------------------------------------------- linear model
    def build_thermal(self, ctx, projects):
        """exp68's construction, on the DSS model. Same conventions as exp125, minus the
        substation row -- Ckt7 has no non-worsening element."""
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
                    pb, qb = self._elem_pq(f"{kind}.{nm}", par)
                    s_b = math.hypot(pb, qb)
                    if kind == "Transformer":
                        smax = self.trafo_kva.get(nm, 0.0)
                    else:
                        na, nc = self.line_amps.get(nm, (0.0, 0))
                        self._ctx.Circuit.SetActiveElement(f"Line.{nm}")
                        cur = np.asarray(self._ctx.CktElement.CurrentsMagAng(),
                                         dtype=float)[0:2 * nc:2]
                        pct = 100.0 * float(cur.max()) / na if na else 0.0
                        smax = (s_b / (pct / 100.0)) if pct > 1.0 else float("inf")
                    if not np.isfinite(smax) or smax ** 2 <= qb ** 2:
                        bus = par
                        continue
                    key_of[key] = len(p_base)
                    p_base.append(pb / 1000.0)
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
        return dict(p_base=p_base, r_lim=np.sqrt(np.maximum(s_max ** 2 - q_base ** 2, 0.0)),
                    members=members)

    def _parent_step(self, bus, seen):
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
    f2 = Ckt7DSSFeeder.__new__(Ckt7DSSFeeder)
    for a in _COPY:
        setattr(f2, a, getattr(base, a, None))
    f2._depth_cache = base._depth()
    f2._anchor = [p for p in anchor_projects if p.id in anchor_ids]
    f2._ctx = f2._fresh()
    f2._staged, f2._on = None, set()
    f2.ctx = SimpleNamespace(net=base.ctx.net, feeder=f2,
                             excluded_line_idx=[], excluded_buses=set())
    f2._keys = {}
    return f2


def make_family(f, desc, mismatch=1.0):
    return _alt.Family(
        "ckt7_unbalanced", lambda: f,
        lambda ff, seed, k, size: _sd.build_colocated(ff, k, seed, size, size * mismatch),
        with_anchor, _seq.certify, desc, reset=_alt._reset_dss)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--k", type=int, default=17)
    ap.add_argument("--size-kw", type=float, default=100.0)
    ap.add_argument("--seeds", type=int, nargs="+", default=list(range(20)))
    ap.add_argument("--n-jobs", type=int, default=4)
    ap.add_argument("--setpoint", type=float, default=SETPOINT)
    ap.add_argument("--mismatch", type=float, default=1.0)
    ap.add_argument("--no-certificate", action="store_true")
    ap.add_argument("--out", default="m100_ckt7_unbalanced.csv")
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()

    print("\n  M100 -- CKT7 SOLVED UNBALANCED, ON ITS SHIPPED OPENDSS MODEL\n")
    t0 = time.time()
    f = Ckt7DSSFeeder(MASTER, setpoint=a.setpoint)
    print(f"  built in {time.time() - t0:.1f}s: {len(f.order)} nodes, "
          f"{len(f.line_check)} checked lines, {len(f.trafo_check)} transformers")
    print(f"  source at {a.setpoint:.2f} pu (shipped master declares 1.05)")
    ok, _mw, viol, info = f.evaluate(f.ctx, [], set())
    print(f"  excluded buses {len(f.excl_buses)}, excluded lines {len(f.excl_lines)}")
    print(f"  candidate (bus,phase) points: {len(f.points)}")
    print(f"  baseline vmin {info['vmin']:.4f} vmax {info['vmax']:.4f} "
          f"(all nodes {info['vmin_all']:.4f})")
    print(f"           worst line {info['line_pct']:.2f}%, transformer {info['trafo_pct']:.2f}%")
    print(f"  baseline feasible: {ok} (violation {viol:.6f})\n")

    fam = make_family(f, f"EPRI Ckt7 UNBALANCED (OpenDSS-native) @{a.setpoint:.2f}, "
                         f"co-located x{a.mismatch:.2f}", mismatch=a.mismatch)
    rows = []
    hdr = (f"  {'seed':>4} | {'gen':>6} {'load':>6} {'seq':>6} {'JOINT':>6} {'alt':>6} | "
           f"{'R1%':>6} {'R*%':>6} | {'mixed':>5} {'cert%':>6} | {'s':>4}")
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
        rstar, r1 = joint - best, joint - w["best_sequential_kw"]

        n_mixed, cert_kw = 0, 0.0
        if not a.no_certificate:
            cc = _cert.certificate(fam, f, projects, best_ids)
            mixed = cc.pop("_mixed_pairs")
            n_mixed = cc["n_mixed_pairs"]
            _t, cert_kw = __import__("120_certified_share").certified_disjoint_pairs(
                fam, f, projects, best_ids, mixed)
        share = (100.0 * cert_kw / rstar) if rstar > 1e-9 else np.nan

        rows.append(dict(feeder="ckt7", engine="opendss_unbalanced", seed=seed,
                         placement="colocated", k=a.k, size_kw=a.size_kw,
                         mismatch=a.mismatch, setpoint=a.setpoint, **w,
                         alternation_kw=best, r1_kw=r1, rstar_kw=rstar,
                         r1_pct=100 * r1 / joint if joint else np.nan,
                         rstar_pct=100 * rstar / joint if joint else np.nan,
                         n_mixed_pairs=n_mixed, certified_kw=cert_kw,
                         certified_share_pct=share, dt=time.time() - t0))
        print(f"  {seed:>4d} | {w['gen_alone_kw']:>6.0f} {w['load_alone_kw']:>6.0f} "
              f"{w['best_sequential_kw']:>6.0f} {joint:>6.0f} {best:>6.0f} | "
              f"{100 * r1 / joint if joint else 0:>6.1f} "
              f"{100 * rstar / joint if joint else 0:>6.1f} | "
              f"{n_mixed:>5d} {share:>6.1f} | {time.time() - t0:>4.0f}", flush=True)
        if a.save:
            OUT.mkdir(exist_ok=True)
            pd.DataFrame(rows).to_csv(OUT / a.out, index=False)

    d = pd.DataFrame(rows)
    print("\n" + "=" * 80)
    print(f"\n  R1 {d.r1_pct.median():.1f}%   R* {d.rstar_pct.median():.1f}%   "
          f"R*>0 in {int((d.rstar_kw > 1e-9).sum())}/{len(d)}")
    print("\n  BALANCED arm, same cell: R1 31.2%, R* 0.0% in 20/20.")
    print("  A nonzero R* here would make phase unbalance sufficient to create the")
    print("  irreducible residual on a feeder co-location alone does not.\n")
    if a.save:
        OUT.mkdir(exist_ok=True)
        d.to_csv(OUT / a.out, index=False)
        print(f"  saved -> outputs/{a.out}\n")


if __name__ == "__main__":
    main()
