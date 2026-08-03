"""M55 -- IS COMPLEMENTARITY PHYSICS, OR IS IT A FEEDER PINNED AT ITS LIMIT?

M54 (exp82) settled the plumbing question and raised a harder one.

  SETTLED. The ext_grid IS in exp68's voltage-constraint set (`net.res_bus.vm_pu.max()`
  over every bus, slack included), and its setpoint is exactly 1.0500 -- so "Ckt7's
  baseline vmax sits exactly on the limit" was a SETPOINT read as a physical fact. But
  the defect is INERT: the slack sits AT the limit, never above it, so it never flips a
  verdict. Seatability is bit-identical with it excluded (199/199 @ 20 kW, 7/199 @ 100 kW
  either way), and the GLDF never saw it at all (exp68 drops the slack row from Ybus).
  It masked our DIAGNOSTICS, not our results: with the slack counted, 196/199 probe buses
  report exactly ZERO dvmax/dP, because the reported vmax is pinned by a bus nothing can
  move. Excluding it, the locational spread is 518,444x. This experiment excludes it.

  ALSO SETTLED, AND AGAINST US. §0 claimed a 100 kW PV request is seatable at NO bus.
  It is seatable at 7 of 199 (3.5%). exp81 drew 17 requests at random and missed all
  seven -- p ~ 0.55, luck, not physics. That sentence was false and is retracted.

  OPEN, AND THE REASON THIS EXPERIMENT EXISTS. Excluding the slack, Ckt7's baseline vmax
  is 1.048808 -- headroom to the 1.05 limit of 0.0012 pu. The source is modelled at 1.05,
  the very top of ANSI Range A. So Ckt7 as configured has essentially ZERO PV headroom,
  and the complementarity result may be a tautology: "on a feeder with no voltage headroom,
  you need load to make room." A real utility would not run a feeder there.

  THE TEST. Sweep the source setpoint 1.05 -> 1.00. At each, re-measure (a) the headroom,
  (b) solo seatability by request size, and (c) the E2 complementarity cell: certified
  achievable kW vs the PV/load mix. If the mix multiplies achievable capacity ONLY at
  1.05, complementarity is an artefact of the pin and we retract §0. If it survives at
  1.03-1.04 -- where the feeder has real headroom and PV is feasible on its own -- then
  complementarity is physics, and the case for co-studying generation and load stands on
  a feeder a utility would actually operate.

Usage:
  python experiments/83_setpoint_sweep.py --quick        # (a)+(b) only, no MILP
  python experiments/83_setpoint_sweep.py --save         # the full sweep
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

_c = __import__("68_ckt7_gldf_headline")
_m = __import__("75_milp_maximality")
_x = __import__("78_corpus_law")

OUT = Path(__file__).resolve().parents[1] / "outputs"
SETPOINTS = [1.05, 1.04, 1.03, 1.02, 1.00]
SIZES_KW = [20, 50, 75, 100, 150]
LOAD_FRACTIONS = [0.0, 0.15, 0.3, 0.5, 0.7, 1.0]


class SlackFreeFeeder(_x.Feeder):
    """exp78's staged-oracle Feeder, with the ONE fix M54 says is owed: the ext_grid bus
    is dropped from the voltage-constraint set. No interconnection request can move the
    slack -- constraining it is meaningless, and (M54) it censors every sensitivity we
    read off vmax. The verdicts are unchanged (M54/D3 proves it); the diagnostics are not."""

    @classmethod
    def at_setpoint(cls, vm_pu, vmin, vmax, loading):
        ctx = _c.build_network()
        ctx.net.ext_grid.loc[:, "vm_pu"] = vm_pu     # <-- the only thing that moves
        pp.runpp(ctx.net, **_c.RUNPP_KWARGS)
        f = cls.from_ctx(f"ckt7@{vm_pu:.2f}", ctx, None, vmin, vmax, loading)
        f.slack = int(ctx.net.ext_grid.bus.iloc[0])
        f.setpoint = vm_pu
        return f

    def evaluate(self, ctx, projects, accepted_ids):
        net, rows = self._staged(projects)
        net.load.loc[list(rows.values()), "in_service"] = [r in accepted_ids for r in rows]
        by_id = {p.id: p for p in projects}
        try:
            pp.runpp(net, **_x.RUNPP_KWARGS)
        except pp.LoadflowNotConverged:
            return False, 0.0, float("inf"), {"diverged": True}
        vm = self._checked_vm(net, also=(self.slack,))     # <-- the fix (+ M100's, see exp78)
        vmin, vmax = float(vm.min()), float(vm.max())
        lp = net.res_line.loading_percent.drop(self.excl_lines, errors="ignore")
        line_pct = float(lp.max()) if len(lp) else 0.0
        trafo_pct = float(net.res_trafo.loading_percent.max()) if len(net.trafo) else 0.0
        violation = (max(0.0, vmax - self.VMAX_PU) + max(0.0, self.VMIN_PU - vmin)
                     + max(0.0, line_pct - self.LOADING_MAX_PCT)
                     + max(0.0, trafo_pct - self.LOADING_MAX_PCT))
        mw = sum(abs(by_id[rid].p_mw) for rid in accepted_ids)
        return violation == 0.0, mw, violation, {
            "vmax": vmax, "vmin": vmin, "line_pct": line_pct, "trafo_pct": trafo_pct}


def headroom(f):
    net = f.ctx.net
    vm = net.res_bus.vm_pu.drop(index=f.slack)
    return float(vm.max()), f.VMAX_PU - float(vm.max()), float(vm.min())


def seatable(f, size_kw, sign=-1):
    """How many candidate buses can host ONE request of this size, alone?"""
    n = 0
    for b in f.load_buses:
        p = [_c.Project(id="probe", bus=int(b), p_mw=sign * size_kw / 1000.0)]
        f._proj = None                                   # force a restage (bus changed)
        ok, _, _, _ = f.evaluate(f.ctx, p, {"probe"})
        n += int(ok)
    return n


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--setpoints", type=float, nargs="+", default=SETPOINTS)
    ap.add_argument("--quick", action="store_true", help="headroom + seatability only")
    ap.add_argument("--size-kw", type=float, default=100.0)
    ap.add_argument("--k", type=int, default=17)
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    ap.add_argument("--n-jobs", type=int, default=8)
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()

    print("\n  M55 -- COMPLEMENTARITY vs THE SOURCE SETPOINT  (Ckt7, slack excluded)\n")

    # ---------------------------------------------------------------- (a) + (b)
    print("  (a) HEADROOM, and (b) SOLO SEATABILITY of a PV request  [buses = 199]\n")
    hdr = "  " + f"{'source':>7} {'base vmax':>10} {'headroom':>9} {'base vmin':>10} | " + \
          " ".join(f"{int(s):>5}kW" for s in SIZES_KW)
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))

    feeders = {}
    for sp in a.setpoints:
        f = SlackFreeFeeder.at_setpoint(sp, _c.VMIN_PU, _c.VMAX_PU, _c.LOADING_MAX_PCT)
        feeders[sp] = f
        bmax, hr, bmin = headroom(f)
        cells = [seatable(f, s) for s in SIZES_KW]
        print(f"  {sp:>7.2f} {bmax:>10.6f} {hr:>+9.5f} {bmin:>10.6f} | "
              + " ".join(f"{c:>4}/199" if c < 100 else f"{c:>3}/199" for c in cells))

    print("\n    headroom = VMAX_PU - baseline vmax, EXCLUDING the slack.")
    print("    If PV only becomes solo-feasible once the source is backed off, then §0's")
    print("    'PV is infeasible, load rescues it' was a statement about the SETPOINT.\n")

    if a.quick:
        return

    # ---------------------------------------------------------------- (c)
    print("\n  (c) THE COMPLEMENTARITY CELL -- certified achievable kW vs the PV/load mix")
    print(f"      ({int(a.size_kw)} kW requests, K={a.k}, median over {len(a.seeds)} seeds)\n")

    hdr = "  " + f"{'source':>7} |" + "".join(f"{int(100*lf):>7}%" for lf in LOAD_FRACTIONS) \
          + f" |{'mix gain':>10}"
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))

    rows = []
    for sp in a.setpoints:
        f = feeders[sp]
        line = []
        for lf in LOAD_FRACTIONS:
            ys = []
            for seed in a.seeds:
                projects = _build_mixed(f, a.k, seed, lf, a.size_kw)
                t0 = time.time()
                _ids, mw, _i = _m.certified_allocation(f.ctx, projects, f, f,
                                                       n_jobs=a.n_jobs, verbose=False)
                ys.append(mw * 1000)
                rows.append(dict(setpoint=sp, load_frac=lf, seed=seed, size_kw=a.size_kw,
                                 k=a.k, achievable_kw=mw * 1000, dt=time.time() - t0))
            line.append(float(np.median(ys)))
        allpv = line[0]
        best = max(line)
        gain = "inf" if allpv <= 0 < best else (f"{100*(best/allpv-1):+.0f}%" if allpv > 0 else "0%")
        print(f"  {sp:>7.2f} |" + "".join(f"{v:>7.0f}" for v in line) + f" |{gain:>10}")

    print("\n    'mix gain' = best mix vs the all-PV queue, at the same K and size.")
    print("    If the gain collapses toward 0% as the source is backed off, complementarity")
    print("    was the pin. If it survives at 1.03-1.04, it is physics.\n")

    if a.save:
        OUT.mkdir(exist_ok=True)
        df = pd.DataFrame(rows)
        df.to_csv(OUT / "m55_setpoint_ckt7.csv", index=False)
        print(f"  saved -> outputs/m55_setpoint_ckt7.csv")


def _build_mixed(f, k, seed, load_frac, size_kw):
    """exp81's draw verbatim: the bus stream and the sign stream are separate RNGs, so
    changing the mix never moves a request -- only its sign."""
    rng_b = random.Random(seed)
    buses = [rng_b.choice(f.load_buses) for _ in range(k)]
    rng_s = random.Random(seed + 10_000)
    idx = list(range(k))
    rng_s.shuffle(idx)
    is_load = set(idx[:int(round(load_frac * k))])
    return [_c.Project(id=f"r{i}", bus=int(b),
                       p_mw=(size_kw / 1000.0) if i in is_load else (-size_kw / 1000.0))
            for i, b in enumerate(buses)]


if __name__ == "__main__":
    main()
