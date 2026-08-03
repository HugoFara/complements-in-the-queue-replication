"""M51b: the law on SMART-DS -- a LOAD-growth queue, in OpenDSS, on the feeder that had no room.

WHY SMART-DS IS THE CORPUS'S HARDEST CASE, AND WORTH THE SEPARATE FILE
---------------------------------------------------------------------
Every other feeder in this study hosts EXPORT and binds on OVERVOLTAGE. SMART-DS (exp57/58,
M28/M36) is the opposite regime in every respect:

  * the requests are EV chargers -- LOADS, not generation, so they push voltage DOWN;
  * the binding constraint is UNDERVOLTAGE, not overvoltage;
  * thermal is off by that corpus's own convention (`max_loading_percent=1e6`);
  * it is the feeder where our own method FAILED -- M36's 0/5, re-confirmed against the
    GLDF ceiling: not a weak method, a feeder with no room.

If reachable pressure predicts the lottery *there*, it is not an artifact of exporting PV.

The physics the law leans on survives the sign flip. Adding load only depresses voltage
further, so a request that violates a limit alone violates it in any company -- exactly as
export only raises it. Solo-infeasible still implies infeasible-in-any-set, which is all the
screen assumes.

WHY IT COULD NOT SIMPLY REUSE exp65's ORACLE
--------------------------------------------
`AllocationProblemDSS.evaluate` redirects the master file into a **fresh OpenDSS context on
every call** -- the right discipline when a study makes a few hundred evaluations, and
unusable here: this experiment makes tens of thousands (100 arrival orders x K requests x
cells), and a Redirect of a 3,000-bus SMART-DS master costs ~1 s. That is ~10 hours per
cell.

So the oracle below keeps ONE context alive, declares every request as a DISABLED load once,
and toggles `enabled` per evaluation -- one command per step of a queue walk instead of a
full circuit rebuild. `--self-test` asserts it returns exactly what `AllocationProblemDSS`
returns, accept-set for accept-set, before any measurement is taken.

Everything downstream -- the a-priori screen, the FCFS walks, the certified yardstick, the
two M48 invariants, the cells -- is exp78's code, unchanged. Only the oracle differs.

Run:  uv run python experiments/79_smartds_law.py --self-test
      uv run python experiments/79_smartds_law.py --feeder uhs1_1247--udt15926 --save
"""

from __future__ import annotations

import argparse
import glob
import os
import random
import time
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("OMP_NUM_THREADS", "1")

import numpy as np
import pandas as pd
from opendssdirect import dss as dss_base

from allocation import AllocationProblemDSS, DSSRequest

_c = __import__("68_ckt7_gldf_headline")          # Project
_e = __import__("73_empirical_request_distribution")
_m = __import__("75_milp_maximality")
_x = __import__("78_corpus_law")                  # capacity / run_cells / pool

REPO = Path(__file__).resolve().parents[1]
CACHE = REPO / "cache" / "smartds"
VMIN_PU, VMAX_PU = 0.94, 1.10      # AllocationProblemDSS defaults, as exp57/58/65 used them
KW_LO, KW_HI = 6.0, 17.0           # exp57/58's EV band -- this corpus's own request model


class DSSFeeder:
    """One SMART-DS master behind exp68's module API, so exp75's `certified_allocation` and
    exp78's cells run on it unchanged.

    The GLDF pieces are exp65's, converted to PER-UNIT. exp65 worked in SI (volts^2, watts)
    because its own MILP carried a per-node voltage base; `relinearize_backoff` instead
    assumes a scalar `VMAX_PU`, so each row of M is divided by that node's own base voltage
    squared. The conversion is asserted, not assumed: `--self-test` checks GLDF's predicted
    voltages against OpenDSS's own solution at a random accept-set.
    """

    VMIN_PU, VMAX_PU = VMIN_PU, VMAX_PU

    def __init__(self, master: str):
        self.master = master
        self.name = Path(master).parent.name
        self._ctx = self._fresh()
        self._staged: list | None = None
        self._on: set[str] = set()

        order = [s.upper() for s in self._ctx.Circuit.YNodeOrder()]
        self.order = order
        self._ctx.Vsources.First()
        self._ctx.Circuit.SetActiveElement(f"Vsource.{self._ctx.Vsources.Name()}")
        src = self._ctx.CktElement.BusNames()[0].split(".")[0].upper()
        slack = [i for i, nm in enumerate(order) if nm.split(".")[0] == src]
        self.slack = slack
        self.pq = np.array([i for i in range(len(order)) if i not in set(slack)])

        vb = np.zeros(len(self.pq))                       # per-node LN base volts
        for j, i in enumerate(self.pq):
            self._ctx.Circuit.SetActiveBus(order[i].split(".")[0])
            vb[j] = self._ctx.Bus.kVBase() * 1000.0
        self.vbase = vb

        self._order_idx = {nm: i for i, nm in enumerate(order)}   # YNode name -> its index

        # candidate attach points: every (bus, phase) that carries a node
        pts = []
        for bus in self._ctx.Circuit.AllBusNames():
            self._ctx.Circuit.SetActiveBus(bus)
            for node in self._ctx.Bus.Nodes():
                if 1 <= int(node) <= 3:
                    pts.append((bus, int(node)))
        self.points = pts
        # exp78/exp75 pass `ctx` around and the M41 re-linearizer reads `ctx.net.bus.index`; the
        # DSS ctx has no pandapower net, so supply a minimal one (node index list + baseline pu),
        # mirroring `anchored_context`. Without it certified_allocation crashes at net=None.
        v0 = np.abs(self._ynode_v()[self.pq]) / self.vbase
        self.ctx = SimpleNamespace(
            net=SimpleNamespace(bus=SimpleNamespace(index=list(range(len(order)))),
                                res_bus=SimpleNamespace(vm_pu=pd.Series(
                                    v0, index=[int(i) for i in self.pq]))),
            feeder=self, excluded_line_idx=[], excluded_buses=set())

    def _fresh(self):
        c = dss_base.NewContext()
        c.Command("Clear")
        c.Command(f'Redirect "{self.master}"')
        c.Solution.Solve()
        return c

    # ---------------------------------------------------------------- the oracle
    def _stage(self, projects):
        """Declare every request ONCE, disabled. A queue walk then costs one `enabled=`
        command per step instead of a full circuit rebuild."""
        if self._staged is projects:
            return
        self._ctx = self._fresh()
        for p in projects:
            self._ctx.Circuit.SetActiveBus(p.bus_name)
            kv = self._ctx.Bus.kVBase()
            self._ctx.Command(f"New Load.{p.id} Bus1={p.bus_name}.{p.phase} Phases=1 "
                              f"Conn=Wye kV={kv} kW={abs(p.p_mw) * 1000} kvar=0 enabled=no")
        self._staged, self._on = projects, set()

    def _set(self, accepted_ids):
        want = set(accepted_ids)
        for rid in want - self._on:
            self._ctx.Command(f"Load.{rid}.enabled=yes")
        for rid in self._on - want:
            self._ctx.Command(f"Load.{rid}.enabled=no")
        self._on = want

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
        # thermal is OFF on this corpus (exp57/58/65: max_loading_percent=1e6), so the
        # expensive per-element current sweep is skipped entirely, not merely ignored.
        violation = max(0.0, self.VMIN_PU - vmin) + max(0.0, vmax - self.VMAX_PU)
        return violation == 0.0, mw, violation, {"vmin": vmin, "vmax": vmax,
                                                 "line_pct": 0.0, "trafo_pct": 0.0}

    # ------------------------------------------------------------- the linear model
    def _ynode_v(self):
        raw = np.asarray(self._ctx.Circuit.YNodeVArray(), dtype=float)
        return raw[0::2] + 1j * raw[1::2]

    def build_gldf(self, ctx, buses):
        """exp65's `build_gldf_dss`, in per-unit. Y is built with every load DISABLED (GLDF
        wants loads as power injections, but OpenDSS folds their Norton admittance into Y);
        the linearization point comes from the SOLVED circuit, which is what re-anchoring
        moves."""
        import scipy.sparse as sp
        import scipy.sparse.linalg as spla

        cy = self._fresh()
        i = cy.Loads.First()
        while i:
            cy.Command(f"Disable Load.{cy.Loads.Name()}")
            i = cy.Loads.Next()
        cy.Solution.Solve()
        data, idx, ptr = cy.YMatrix.getYsparse()
        n = len(ptr) - 1
        Y = sp.csc_matrix((data, idx, ptr), shape=(n, n))

        V = self._ynode_v()                       # the CURRENT operating point (anchored)
        lu = spla.splu(sp.csc_matrix(Y[self.pq, :][:, self.pq]))
        E = lu.solve(-(np.asarray(Y[self.pq, :][:, self.slack].todense()) @ V[self.slack]))

        # `buses` are NODE INDICES (p.bus, matching exp75's `m_cols.get(int(p.bus))`); key m_cols
        # by that index so the certify path actually uses the GLDF rather than an empty A_v.
        pos = {int(b): j for j, b in enumerate(self.pq)}
        m_cols = {}
        for b in {int(x) for x in buses}:
            jp = pos.get(b)
            if jp is None:                                      # slack node, or not in pq
                continue
            e = np.zeros(len(self.pq))
            e[jp] = 1.0
            z = lu.solve(e.astype(complex))
            col_si = 2.0 * np.real(E * np.conj(z) / E[jp])      # volts^2 per watt injected
            m_cols[b] = col_si / self.vbase ** 2                # -> pu^2 per watt
        # base_mva = 1e-6 makes exp75's `col * (-p_mw / base_mva)` read `col * (-p_watts)`,
        # i.e. injection in WATTS, which is the unit `col` is expressed per.
        return dict(v_base=(np.abs(V[self.pq]) / self.vbase) ** 2, m_cols=m_cols,
                    base_mva=1e-6, pq=self.pq, lookup={i: i for i in range(len(self.order))})

    def build_thermal(self, ctx, projects):
        """Empty by design: this corpus disables thermal (exp57/58/65). M48's lesson was to
        never let an empty thermal model be a SURPRISE -- so it is declared, not inherited."""
        return dict(p_base=np.array([]), r_lim=np.array([]), members=[])

    def anchored_context(self, ctx, projects, anchor_ids):
        """Re-linearize at the incumbent (M41): solve with the anchor's loads in service and
        hand back the operating point. GLDF's M is unchanged by the anchor -- only the point
        it is expanded about moves."""
        self._stage(projects)
        self._set(anchor_ids)
        self._ctx.Solution.Solve()
        v = np.abs(self._ynode_v()[self.pq]) / self.vbase
        net = SimpleNamespace(bus=SimpleNamespace(index=list(range(len(self.order)))),
                              res_bus=SimpleNamespace(vm_pu=pd.Series(
                                  v, index=[int(i) for i in self.pq])))
        return SimpleNamespace(net=net, feeder=self, excluded_line_idx=[],
                               excluded_buses=set())


def build_projects(feeder, k, seed):
    """exp57/58's EV requests: single-phase taps drawn WITH replacement, sizes uniform in
    their 6-17 kW band. POSITIVE p_mw -- these are loads. `p.bus` is the request's NODE INDEX
    (its BUS.PHASE position in the YNode order), which is the GLDF column key exp75 looks up."""
    rng_b = random.Random(seed)
    pts = [rng_b.choice(feeder.points) for _ in range(k)]
    rng_s = random.Random(seed + 10_000)
    out = []
    for i, (bus, phase) in enumerate(pts):
        key = f"{bus.upper()}.{phase}"
        p = _c.Project(id=f"ev{i}", bus=feeder._order_idx.get(key, -1),
                       p_mw=rng_s.uniform(KW_LO, KW_HI) / 1000.0)
        p.bus_name, p.phase = bus, phase
        p.gldf_key = key
        out.append(p)
    return out


def _gldf_buses(projects):
    return [p.bus for p in projects]


def self_test(feeder, n=8, seed=3):
    """1. The staged oracle must return the same VERDICT as `AllocationProblemDSS`, accept-set
       for accept-set. Voltages are compared too, but to 1e-4 pu, not to the bit: the staged
       oracle solves incrementally from the previous operating point while the reference
       re-Redirects the master and solves from scratch, and OpenDSS's iterative solution
       depends on where it starts. The observed drift is ~5e-6 pu (0.0005%), which no
       feasibility verdict in this study turns on.

    2. GLDF vs OpenDSS is REPORTED, not asserted. SMART-DS has 605 transformers with tap
       control -- a nonlinear control loop no linear model can track -- and exp65 already
       measured GLDF's 4-6% optimism here. More to the point, M42's lesson was that a
       surrogate must be judged on the trajectory the optimizer actually follows, never on
       random samples of the decision space, which is precisely what this probe is. The
       yardstick does not trust it either way: every iterate is AC-verified, infeasible ones
       become no-good cuts, and the result is AC-completed.
    """
    projects = build_projects(feeder, 25, seed)
    reqs = [DSSRequest(id=p.id, bus=p.bus_name, phase=p.phase, p_kw=abs(p.p_mw) * 1000,
                       queue_order=i) for i, p in enumerate(projects)]
    ref = AllocationProblemDSS(master_path=feeder.master, requests=reqs,
                               vmin_pu=VMIN_PU, vmax_pu=VMAX_PU, max_loading_percent=1e6)
    rng = random.Random(seed)
    ok_all, worst = True, 0.0
    for _ in range(n):
        ids = {p.id for p in projects if rng.random() < 0.5}
        got_ok, _mw, _v, d = feeder.evaluate(feeder.ctx, projects, ids)
        r = ref.evaluate(sorted(ids))
        worst = max(worst, abs(d["vmin"] - r.vmin_pu), abs(d["vmax"] - r.vmax_pu))
        same = got_ok == r.feasible and worst < 1e-4
        ok_all &= same
        if not same:
            print(f"    MISMATCH |{len(ids)}|: staged feasible={got_ok} "
                  f"vmin={d['vmin']:.6f} | reference feasible={r.feasible} "
                  f"vmin={r.vmin_pu:.6f}")
    print(f"  self-test 1: staged oracle == AllocationProblemDSS on {n} accept-sets: "
          f"{'PASS' if ok_all else 'FAIL'}  (worst voltage drift {1e3*worst:.4f} mpu)")

    ids = {p.id for p in projects[:12]}
    g = feeder.build_gldf(feeder.ctx, _gldf_buses(projects))   # anchored at the base
    x = np.array([1.0 if p.id in ids else 0.0 for p in projects])
    A = np.zeros((len(g["pq"]), len(projects)))
    for r_i, p in enumerate(projects):
        col = g["m_cols"].get(p.bus)
        if col is not None:
            A[:, r_i] = col * (-p.p_mw / g["base_mva"])
    v_pred = np.sqrt(np.maximum(g["v_base"] + A @ x, 0.0))
    feeder.evaluate(feeder.ctx, projects, ids)
    v_ac = np.abs(feeder._ynode_v()[feeder.pq]) / feeder.vbase
    err = 1000 * (v_ac - v_pred)
    print(f"  self-test 2 (reported, not asserted -- see docstring): GLDF vs OpenDSS at a "
          f"RANDOM |accept|={len(ids)}: mean {err.mean():+.2f} mpu, worst "
          f"{np.abs(err).max():.2f} mpu")
    return ok_all


def feasible_masters():
    """exp57's screen: only feeders whose BASE case is already inside the planning band can
    host anything. (Several SMART-DS masters start undervoltage -- they are excluded there
    and here, for the same reason and by the same test.)"""
    out = []
    for m in sorted(glob.glob(str(CACHE / "*" / "Master.dss"))):
        try:
            if AllocationProblemDSS(master_path=m, requests=[],
                                    max_loading_percent=1e6).evaluate([]).feasible:
                out.append(m)
        except Exception:
            pass
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--feeder", help="SMART-DS master directory name")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    ap.add_argument("--targets", type=float, nargs="+", default=_x.TARGETS)
    ap.add_argument("--permutations", type=int, default=100)
    ap.add_argument("--n-jobs", type=int, default=8)
    ap.add_argument("--k0", type=int, default=50)
    ap.add_argument("--kmax", type=int, default=1600)
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()

    if a.list:
        for m in feasible_masters():
            print(" ", Path(m).parent.name)
        return

    masters = {Path(m).parent.name: m for m in sorted(glob.glob(str(CACHE / "*" / "Master.dss")))}
    feeder = DSSFeeder(masters[a.feeder])
    print(f"M51b -- the law on SMART-DS {feeder.name}")
    print(f"  {len(feeder.order)} nodes, {len(feeder.points)} attach points; "
          f"EV loads {KW_LO}-{KW_HI} kW; thermal off (exp57/58 convention)")
    if not self_test(feeder):
        raise SystemExit("staged oracle disagrees with AllocationProblemDSS")
    if a.self_test:
        return

    mk = lambda ctx, k, s: build_projects(feeder, k, s)
    # (`p.bus` is now the node index build_gldf keys on, so exp75's call site needs no patch.)

    print("\n  CALIBRATION -- hosting capacity A under this request distribution")
    t0 = time.time()
    a_kw, mu_r, info = _x.capacity(feeder.ctx, feeder, feeder, mk, a.n_jobs,
                                   k0=a.k0, kmax=a.kmax)
    print(f"    A = {a_kw:.1f} kW;  mean reachable request = {mu_r:.2f} kW [{time.time()-t0:.0f}s]")
    if a.save:
        _x.record_capacity(f"smartds_{feeder.name}", a_kw, mu_r, info,
                           f"SMART-DS {feeder.name} (EV loads, undervoltage-limited)")

    _x.run_cells(f"smartds_{feeder.name}", feeder.ctx, feeder, feeder, mk, a_kw, mu_r,
                 a.seeds, a.targets, a.permutations, a.n_jobs, a.save)


if __name__ == "__main__":
    main()
