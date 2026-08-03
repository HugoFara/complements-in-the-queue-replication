"""M73 -- THE SEGREGATION RESIDUAL ON SMART-DS, the second independent family (OpenDSS-native).

ENWL (exp98) answered "non-EPRI" but is still one LV family. SMART-DS is a genuinely different
feeder family: US split-phase secondaries and single-phase laterals that pandapower's
symmetrical-component solver cannot represent at all (issue #873), so it runs on the OpenDSS-
NATIVE oracle (`AllocationProblemDSS` / exp79's `DSSFeeder`), not the pandapower `Feeder`. And its
regime is the opposite of every other feeder here: the requests are EV chargers (LOADS, pushing
voltage DOWN), the binding constraint is UNDERVOLTAGE, and thermal is off by the corpus's own
convention. If the residual rule survives the sign flip AND the engine change, it is not an
artifact of pandapower or of PV export.

SMART-DS's native queue is pure EV load -> monotone -> residual structurally 0 (nothing is only-
jointly-feasible when every request pushes the same way). So this file builds a MIXED PV+EV queue
on it: EV load (positive, undervoltage aggressor) plus PV export (negative, overvoltage aggressor).
The two-sided voltage band (0.94-1.10) is then the active constraint.

RESULT, and it is instructive. Under RANDOM placement the residual is ~0 up to K=64 -- but not
because the rule fails. On a 3443-node feeder a random PV and a random load land on DIFFERENT
laterals, electrically far apart, so they cannot be complements; a sequential order recovers the
whole parallel gap. Under CO-LOCATED placement (a PV and a load at the SAME site -- an industrial
load with on-site solar, the exact DOE/FERC co-located-interconnection case) each request alone
stresses one voltage limit while together they cancel, so the pair is only jointly feasible and no
study order recovers it: residual **+60% to +129% (median +100%)**. So on a large feeder the
residual is a CO-LOCATION effect -- which is precisely the deliberate load+generation co-location
the reform concerns, and it is the voltage analogue of exp99's reverse-flow spur, here on a US
split-phase feeder through the OpenDSS-native oracle. Two-way coupling survives the engine change
AND the undervoltage sign flip.

MACHINERY. `MixedDSSFeeder` extends exp79's `DSSFeeder` with two changes: requests stage with
SIGNED kW (so a negative-kW OpenDSS load exports), and the GLDF columns are keyed by project index
(matching exp75's `m_cols.get(int(p.bus))`). `with_anchor` bakes an accepted first track in as
permanent enabled loads -- the DSS-native sequential anchor, the analogue of exp98's
`enwl_with_anchor`. The exp75 re-linearizer needs `ctx.net.bus.index`, which the DSS ctx lacks
(net=None); `anchored_context([], {})` supplies a populated base ctx (a compatibility gap that
post-dates exp79 and only surfaces on the certify path, not its self-test). `certify` and the
three-way structure are then exp91/exp98's, unchanged.

Run:
  .venv/bin/python experiments/101_residual_smartds.py --feeder uhs2_1247--udt10746 --save
"""
from __future__ import annotations

import argparse
import glob
import os
import random
import sys
import time
import weakref
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("OMP_NUM_THREADS", "1")

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

_law = __import__("79_smartds_law")               # DSSFeeder
_c = __import__("68_ckt7_gldf_headline")          # Project
_seq = __import__("91_sequential_segregation")    # certify (feeder-agnostic)

# A DSS context is never freed: `NewContext()` costs tens of megabytes that a dropped
# reference does not give back, while `Clear` + `Redirect` on an existing one is flat. The
# oracle builds a context per staging and per anchored copy, and `alternate` builds an
# anchored copy per half-round, so a twenty-draw run on this feeder reached 19 GB. Contexts
# are therefore pooled and reloaded. Verified equivalent: a reloaded circuit is the same
# circuit, and `--self-test` still passes.
_CTX_FREE: list = []


def _acquire_ctx():
    return _CTX_FREE.pop() if _CTX_FREE else _law.dss_base.NewContext()


def _reload_ctx(c, master):
    c.Command("Clear")
    c.Command(f'Redirect "{master}"')
    c.Solution.Solve()
    return c


REPO = Path(__file__).resolve().parents[1]
CACHE = REPO / "cache" / "smartds"
OUT = REPO / "outputs"


class MixedDSSFeeder(_law.DSSFeeder):
    """exp79's DSSFeeder, but requests carry SIGNED kW (PV exports) and GLDF columns are keyed by
    project index. `_anchor` (optional) is a set of permanent, always-enabled base loads."""

    def _decl(self, p, enabled):
        self._ctx.Circuit.SetActiveBus(p.bus_name)
        kv = self._ctx.Bus.kVBase()
        self._ctx.Command(f"New Load.{p.id} Bus1={p.bus_name}.{p.phase} Phases=1 Conn=Wye "
                          f"kV={kv} kW={p.p_mw * 1000} kvar=0 "
                          f"enabled={'yes' if enabled else 'no'}")

    def _fresh(self):
        """Reload this feeder's OWN context; take one from the pool only the first time."""
        c = getattr(self, "_ctx", None)
        if c is None:
            c = _acquire_ctx()
            weakref.finalize(self, _CTX_FREE.append, c)
        return _reload_ctx(c, self.master)

    def _stage(self, projects):
        if self._staged is projects:
            return
        self._ctx = self._fresh()
        for p in getattr(self, "_anchor", ()):        # permanent base (the sequential anchor)
            self._decl(p, True)
        for p in projects:                            # the decision variables, toggled per solve
            self._decl(p, False)
        self._staged, self._on = projects, set()

    def _gldf_base(self):
        """Y, its factorization and E, built ONCE and shared with every anchored copy.

        It used to be rebuilt per call, on a context allocated per call -- the dominant term
        in both the runtime and the leak. It is also redundant. M41 established that
        re-linearizing RE-ANCHORS but cannot RE-SLOPE: Y is taken with every load disabled,
        and the source voltage is fixed by the Vsource, so Y, its LU and E do not move with
        the anchor. Only `v_base`, read from the current solved state below, does. Caching is
        exact rather than an approximation, and `--self-test` checks it against OpenDSS.
        """
        if getattr(self, "_gcache", None) is not None:
            return self._gcache
        import scipy.sparse as sp
        import scipy.sparse.linalg as spla
        cy = _reload_ctx(_acquire_ctx(), self.master)
        i = cy.Loads.First()
        while i:
            cy.Command(f"Disable Load.{cy.Loads.Name()}"); i = cy.Loads.Next()
        cy.Solution.Solve()
        data, idx, ptr = cy.YMatrix.getYsparse()
        n = len(ptr) - 1
        Y = sp.csc_matrix((data, idx, ptr), shape=(n, n))
        raw = np.asarray(cy.Circuit.YNodeVArray(), dtype=float)
        Vy = raw[0::2] + 1j * raw[1::2]
        lu = spla.splu(sp.csc_matrix(Y[self.pq, :][:, self.pq]))
        E = lu.solve(-(np.asarray(Y[self.pq, :][:, self.slack].todense()) @ Vy[self.slack]))
        _CTX_FREE.append(cy)
        self._gcache = dict(lu=lu, E=E, cols={},
                            pos={int(b): j for j, b in enumerate(self.pq)})
        return self._gcache

    def build_gldf(self, ctx, buses):
        """exp79's build_gldf math, but m_cols keyed by project INDEX (`buses` = [p.bus]); the
        gldf_key per index comes from self._keys. Columns are memoized by NODE index."""
        g = self._gldf_base()
        m_cols = {}
        for b in buses:
            bi = int(b)
            key = self._keys[bi].upper()
            if key not in self.order:
                continue
            jp = g["pos"].get(self.order.index(key))
            if jp is None:
                continue
            col = g["cols"].get(jp)
            if col is None:
                e = np.zeros(len(self.pq)); e[jp] = 1.0
                z = g["lu"].solve(e.astype(complex))
                col = (2.0 * np.real(g["E"] * np.conj(z) / g["E"][jp])) / self.vbase ** 2
                g["cols"][jp] = col
            m_cols[bi] = col
        V = self._ynode_v()                             # the anchored operating point
        return dict(v_base=(np.abs(V[self.pq]) / self.vbase) ** 2, m_cols=m_cols,
                    base_mva=1e-6, pq=self.pq, lookup={i: i for i in range(len(self.order))})


def load(master: str) -> MixedDSSFeeder:
    f = MixedDSSFeeder(master)
    f.ctx = f.anchored_context(f.ctx, [], set())      # populate ctx.net for exp75's relinearizer
    return f


def with_anchor(base: MixedDSSFeeder, anchor_projects, anchor_ids) -> MixedDSSFeeder:
    """A MixedDSSFeeder on the same master with the accepted first track baked in as permanent
    enabled loads -- the DSS-native sequential anchor (keeps base's cached structure, no re-scan)."""
    f2 = MixedDSSFeeder.__new__(MixedDSSFeeder)
    for attr in ("master", "name", "order", "slack", "pq", "vbase", "points", "_gcache"):
        setattr(f2, attr, getattr(base, attr, None))
    f2._anchor = [p for p in anchor_projects if p.id in anchor_ids]
    f2._ctx = f2._fresh()
    f2._staged, f2._on = None, set()
    f2.ctx = SimpleNamespace(net=None, feeder=f2)
    f2.ctx = f2.anchored_context(f2.ctx, [], set())
    f2._keys = {}
    return f2


def _mk(i, bus, phase, kw, tag):
    p = _c.Project(id=f"{tag}{i}", bus=i, p_mw=kw / 1000.0)
    p.bus_name, p.phase = bus, phase
    p.gldf_key = f"{bus.upper()}.{phase}"
    return p


def build_mixed(f, k, seed, load_frac, pv_kw, ev_kw):
    """K mixed requests at RANDOM (bus, phase) attach points: load_frac EV load (+, undervoltage
    aggressor), the rest PV export (-, overvoltage aggressor). Separate bus/sign RNG streams. On a
    3443-node feeder random PV and load land on different laterals, so complements are electrically
    far apart -- the residual stays ~0 (a scale/placement effect, not a physics difference)."""
    rng_b = random.Random(seed)
    pts = [rng_b.choice(f.points) for _ in range(k)]
    rng_s = random.Random(seed + 10_000)
    idx = list(range(k)); rng_s.shuffle(idx)
    is_load = set(idx[:int(round(load_frac * k))])
    return [_mk(i, bus, phase, (ev_kw if i in is_load else -pv_kw), ("ev" if i in is_load else "pv"))
            for i, (bus, phase) in enumerate(pts)]


def build_colocated(f, k, seed, pv_kw, ev_kw):
    """K requests = k/2 CO-LOCATED PV+load pairs: at each of k/2 attach points, one PV and one
    load at the SAME bus.phase -- an industrial site with on-site solar, the DOE/FERC co-located-
    interconnection scenario. Each alone stresses one voltage limit; together they cancel, so the
    pair is only jointly feasible and NO study order recovers it -> the two-way residual."""
    rng = random.Random(seed)
    pts = [rng.choice(f.points) for _ in range(max(1, k // 2))]
    out = []
    for bus, phase in pts:
        out.append(_mk(len(out), bus, phase, -pv_kw, "pv"))
        out.append(_mk(len(out), bus, phase, ev_kw, "ev"))
    return out


def three_way(f, projects, n_jobs):
    pv = [p for p in projects if p.p_mw < 0]
    ld = [p for p in projects if p.p_mw > 0]
    f._staged = None; f._keys = {p.bus: p.gldf_key for p in pv}
    pv_ids, pv_kw = _seq.certify(f, pv, n_jobs)
    f._staged = None; f._keys = {p.bus: p.gldf_key for p in ld}
    ld_ids, ld_kw = _seq.certify(f, ld, n_jobs)
    f._staged = None; f._keys = {p.bus: p.gldf_key for p in projects}
    _j, joint = _seq.certify(f, projects, n_jobs)
    fpv = with_anchor(f, pv, pv_ids); fpv._keys = {p.bus: p.gldf_key for p in ld}
    _l, ld2 = _seq.certify(fpv, ld, n_jobs)
    fld = with_anchor(f, ld, ld_ids); fld._keys = {p.bus: p.gldf_key for p in pv}
    _p, pv2 = _seq.certify(fld, pv, n_jobs)
    best_seq = max(pv_kw + ld2, ld_kw + pv2)
    return dict(pv_alone_kw=pv_kw, load_alone_kw=ld_kw, seg_parallel_kw=pv_kw + ld_kw,
                seq_pvfirst_kw=pv_kw + ld2, seq_loadfirst_kw=ld_kw + pv2, best_sequential_kw=best_seq,
                joint_kw=joint,
                cost_vs_parallel_pct=(100 * (joint / (pv_kw + ld_kw) - 1) if (pv_kw + ld_kw) > 0 else np.nan),
                residual_vs_sequential_pct=(100 * (joint / best_seq - 1) if best_seq > 0 else np.nan))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--feeder", default="uhs2_1247--udt10746", help="SMART-DS master dir name")
    ap.add_argument("--ks", type=int, nargs="+", default=[16, 24, 32])
    ap.add_argument("--pv-kw", type=float, default=100.0)
    ap.add_argument("--ev-kw", type=float, default=100.0)
    ap.add_argument("--load-frac", type=float, default=0.5)
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    ap.add_argument("--n-jobs", type=int, default=6)
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()

    master = str(CACHE / a.feeder / "Master.dss")
    f = load(master)
    _ok0, _, _, d0 = f.evaluate(f.ctx, [], set())
    print(f"\n  M73 -- SEGREGATION RESIDUAL ON SMART-DS {f.name} (OpenDSS-native, undervoltage regime)")
    print(f"  {len(f.order)} nodes; baseline vmin {d0['vmin']:.4f} vmax {d0['vmax']:.4f} "
          f"(limits {f.VMIN_PU}-{f.VMAX_PU}); mixed PV {a.pv_kw:.0f} / EV {a.ev_kw:.0f} kW\n")
    print("  RANDOM placement: complements land on different laterals of a 3443-node feeder ->")
    print("  residual ~0 (a study order recovers all). CO-LOCATED (PV+load same site, the DOE/FERC")
    print("  scenario): each alone stresses one voltage limit, together they cancel -> residual on.\n")

    hdr = (f"  {'placement':>10} {'K':>4} | {'PVsolo':>6} {'LDsolo':>6} {'JOINT':>6} {'bestSEQ':>7} | "
           f"{'vs par':>7} {'residual: med':>13} {'min':>5} {'max':>5}")
    print(hdr); print("  " + "-" * (len(hdr) - 2))

    rows = []
    builders = [("random", build_mixed), ("colocated", build_colocated)]
    for placement, builder in builders:
        for K in a.ks:
            per = []
            for seed in a.seeds:
                t0 = time.time()
                if placement == "random":
                    projects = builder(f, K, seed, a.load_frac, a.pv_kw, a.ev_kw)
                else:
                    projects = builder(f, K, seed, a.pv_kw, a.ev_kw)
                r = three_way(f, projects, a.n_jobs)
                r.update(feeder=a.feeder, placement=placement, k=len(projects), seed=seed,
                         pv_kw=a.pv_kw, ev_kw=a.ev_kw, dt=time.time() - t0)
                rows.append(r); per.append(r)
                if a.save:
                    OUT.mkdir(exist_ok=True)
                    pd.DataFrame(rows).to_csv(OUT / "m73_residual_smartds.csv", index=False)
            res = np.array([p["residual_vs_sequential_pct"] for p in per], float); res = res[~np.isnan(res)]
            parg = np.array([p["cost_vs_parallel_pct"] for p in per], float); parg = parg[~np.isnan(parg)]
            pvs = np.median([p["pv_alone_kw"] for p in per]); lds = np.median([p["load_alone_kw"] for p in per])
            jt = np.median([p["joint_kw"] for p in per]); bs = np.median([p["best_sequential_kw"] for p in per])
            kk = int(np.median([p["k"] for p in per]))
            if len(res):
                print(f"  {placement:>10} {kk:>4} | {pvs:>6.0f} {lds:>6.0f} {jt:>6.0f} {bs:>7.0f} | "
                      f"{np.median(parg):>+6.0f}% {np.median(res):>+12.1f}% {np.min(res):>+4.1f} {np.max(res):>+4.1f}")
        print()

    if a.save:
        OUT.mkdir(exist_ok=True)
        pd.DataFrame(rows).to_csv(OUT / "m73_residual_smartds.csv", index=False)
        print("  saved -> outputs/m73_residual_smartds.csv")
    print("  RANDOM ~0, CO-LOCATED +60-129%: on a large feeder the residual is a co-location")
    print("  effect -- which is exactly the deliberate load+generation co-location the reform targets.")


if __name__ == "__main__":
    main()
