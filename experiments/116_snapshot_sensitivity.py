"""M90 -- DOES THE RESIDUAL SURVIVE THE OTHER SNAPSHOT?

THE OBJECTION
-------------
Every certification in the paper is taken at one snapshot: the feeder's shipped peak-load
operating point. Standard hosting-capacity practice screens PV at MINIMUM DAYTIME LOAD, because
that is when reverse flow and voltage rise are worst; peak load is the *lenient* snapshot for a
generation request and the *binding* one for a load request. So the snapshot choice is not
neutral between the two tracks, and a referee is entitled to ask whether a residual measured at
peak load is an artifact of screening generation at its easiest hour.

WHAT THIS FILE DOES
-------------------
Re-runs exp113's exact enumeration on Ckt7 with the feeder's own native load scaled by alpha,
holding EVERYTHING else fixed: same requests, same buses, same book limits, same excluded
islands. The candidate bus pool comes from `f.load_buses`, which is a topology fact, so the
request draw at a given seed is bit-identical across alphas and the only thing that moves is
the operating point the queue is certified against.

  alpha = 1.0    the paper's snapshot -- shipped peak load
  alpha = 0.3    minimum daytime load, the conventional PV screening point

Since the enumeration is exact, the answer carries no optimizer caveat. Three outcomes are
possible and all three are informative:

  the residual survives     -> the snapshot choice is not load-bearing, and the paper says so
  the residual grows        -> peak load was the conservative snapshot; magnitudes are floors
  the residual collapses    -> the headline is a peak-load artifact and must be scoped to it

Run:
  uv run python experiments/116_snapshot_sensitivity.py --alphas 1.0 0.3 --seeds 0 1 2 3 --save
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandapower as pp
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

_c = __import__("68_ckt7_gldf_headline")
_s = __import__("83_setpoint_sweep")
_alt = __import__("109_alternating_rounds")
_x = __import__("113_exact_enumeration")

OUT = Path(__file__).resolve().parents[1] / "outputs"


def feeder_at_load(alpha, setpoint=1.04):
    """Ckt7 with its native load scaled to `alpha` of shipped peak, limits unchanged.

    The excluded islands are taken from the SHIPPED case, not re-derived at alpha. They are a
    topology fact -- eleven pre-broken service lines -- and re-deriving them would silently
    change the candidate pool between snapshots, which is the one thing this comparison must
    hold fixed.
    """
    ctx = _c.build_network()
    ctx.net.load.loc[:, ["p_mw", "q_mvar"]] *= alpha
    ctx.net.ext_grid.loc[:, "vm_pu"] = setpoint
    pp.runpp(ctx.net, **_c.RUNPP_KWARGS)
    f = _s.SlackFreeFeeder.from_ctx(f"ckt7@{setpoint:.2f}x{alpha:g}", ctx, None,
                                    _c.VMIN_PU, _c.VMAX_PU, _c.LOADING_MAX_PCT)
    f.slack = int(ctx.net.ext_grid.bus.iloc[0])
    f.setpoint = setpoint
    return f


def family_at_load(alpha, setpoint=1.04):
    """exp109's ckt7 Family with the load-scaled feeder swapped in."""
    fam = _alt.get_family("ckt7", 1, placement="random")
    fam.make_feeder = lambda: feeder_at_load(alpha, setpoint)
    fam.desc = f"EPRI Ckt7 @{setpoint:.2f}, native load x{alpha:g}"
    return fam


def baseline_state(f):
    """What the snapshot itself looks like, before any request is added."""
    net = f.ctx.net
    # M100: report the voltages the band is actually tested against -- the excluded pockets
    # and the transmission-side source bus are out of the check set, so leaving them in here
    # made the reported baseline look 0.07 pu worse than the one `evaluate` scores.
    vm = f._checked_vm(net, also=(f.slack,))
    lp = net.res_line.loading_percent.drop(f.excl_lines, errors="ignore")
    return dict(base_load_mw=float(net.load.p_mw.sum()),
                base_vmin=float(vm.min()), base_vmax=float(vm.max()),
                base_line_pct=float(lp.max()) if len(lp) else 0.0)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--alphas", type=float, nargs="+", default=[1.0, 0.3])
    ap.add_argument("--k", type=int, default=17)
    ap.add_argument("--size-kw", type=float, default=100.0)
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2, 3])
    ap.add_argument("--setpoint", type=float, default=1.04)
    ap.add_argument("--max-nodes", type=int, default=4096)
    ap.add_argument("--n-jobs", type=int, default=8)
    ap.add_argument("--out", default="m90_snapshot_sensitivity.csv")
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()

    print("\n  M90 -- THE RESIDUAL AT A SECOND SNAPSHOT\n")
    print("  Exact enumeration, Ckt7, same requests at every native-load scale. alpha=1.0 is")
    print("  the paper's peak-load snapshot; alpha<1 approaches minimum daytime load, the")
    print("  conventional screening point for a generation request.\n")

    rows = []
    for alpha in a.alphas:
        fam = family_at_load(alpha, a.setpoint)
        f0 = fam.make_feeder()
        base = baseline_state(f0)
        print(f"  {fam.desc}:  native load {base['base_load_mw']:.2f} MW, "
              f"baseline v [{base['base_vmin']:.4f}, {base['base_vmax']:.4f}], "
              f"max line {base['base_line_pct']:.0f}%")
        for seed in a.seeds:
            f = fam.make_feeder()
            projects = _s._build_mixed(f, a.k, seed, 0.5, a.size_kw)
            print(f"    seed {seed}:", flush=True)
            try:
                r = _x.run_cell(fam, f, projects, a.n_jobs, [0.5], a.max_nodes, do_alt=True)
            except Exception as exc:          # one bad cell must not lose the others
                print(f"      FAILED: {type(exc).__name__}: {exc}\n", flush=True)
                continue
            r.update(alpha=alpha, seed=seed, size_kw=a.size_kw, **base)
            rows.append(r)
            print(f"      gen/load track  {r['exact_gen_track_kw']:.0f} / "
                  f"{r['exact_load_track_kw']:.0f} kW    "
                  f"best sequential {r['exact_best_sequential_kw']:.0f} kW    "
                  f"JOINT {r['exact_joint_kw']:.0f} kW")
            print(f"      EXACT R1 {r['exact_residual_kw']:.0f} kW "
                  f"({r['exact_residual_pct']:.1f}% of joint)   "
                  f"R* {r['exact_irreducible_pct']:.1f}%"
                  + ("  [node cap]" if r['alternation_capped'] else ""), flush=True)

    if not rows:
        raise SystemExit("no cells completed")
    d = pd.DataFrame(rows)

    print("\n  SUMMARY -- median over seeds\n")
    print(f"  {'alpha':>6} {'native MW':>10} {'gen kW':>8} {'load kW':>8} "
          f"{'seq kW':>8} {'joint kW':>9} {'R1 %':>7} {'R* %':>7}")
    for alpha, g in d.groupby("alpha"):
        print(f"  {alpha:>6.2f} {g.base_load_mw.iloc[0]:>10.2f} "
              f"{g.exact_gen_track_kw.median():>8.0f} {g.exact_load_track_kw.median():>8.0f} "
              f"{g.exact_best_sequential_kw.median():>8.0f} "
              f"{g.exact_joint_kw.median():>9.0f} "
              f"{g.exact_residual_pct.median():>7.1f} {g.exact_irreducible_pct.median():>7.1f}")

    ref = d[d.alpha == max(a.alphas)]
    print("\n  Read against the paper's snapshot: a residual that holds at both operating")
    print("  points is not a peak-load artifact. A generation track that GROWS as native load")
    print("  falls would be the surprise -- less load to absorb export, not more.\n")
    if len(ref):
        for alpha, g in d.groupby("alpha"):
            print(f"    alpha={alpha:g}:  R1 positive in {int((g.exact_residual_kw > 0).sum())}"
                  f"/{len(g)} draws, R* positive in "
                  f"{int((g.exact_irreducible_kw > 0).sum())}/{len(g)}")
    print()

    if a.save:
        OUT.mkdir(exist_ok=True)
        d.to_csv(OUT / a.out, index=False)
        print(f"  saved -> outputs/{a.out}\n")


if __name__ == "__main__":
    main()
