"""M70 -- THE SEGREGATION RESIDUAL ON REAL UK LV FEEDERS (ENWL), the independent-family test.

Every residual number in the brief so far is on an EPRI feeder: Ckt7 (exp91/93/95) and Ckt24
(exp88/96/97), same US utility corpus, same single-snapshot family. A reviewer will ask whether
the predictive rule -- residual > 0 iff gen and load can each independently drive the SAME active
constraint (a two-sided voltage band almost always qualifies; a one-sided thermal bound only when
the binding element carries reversible flow) -- survives on a feeder from a DIFFERENT family with
a DIFFERENT standard. This file runs the three-way segregation + residual (exp91/93's machinery,
verbatim) on the ENWL feeders vendored since v4: real UK LV networks under EN 50160 limits
(0.94-1.10 pu), the conductor corpus's own feeders.

PREDICTION (from the corrected invariant): ENWL LV feeders are R-dominated, so PV export is
OVERVOLTAGE-limited -- a two-sided voltage band -> two-way coupling -> a positive residual. The
smoke test confirmed the antecedent: on n2f1 and n13f4 the PV-solo optimum pins vmax near 1.10
while lines sit at 45-94%, and load alone hosts ~0 (the non-worsening vmin clamp on a depressed
baseline). If the residual is positive here, the rule holds on an independent family; if it
collapses, we need to know before a referee does.

TWO ENWL feeders span the baseline regimes exp78 chose them for:
  * n2f1  -- clean, baseline INSIDE the limits (vmin 0.966), the "normal" feeder.
  * n13f4 -- depressed, baseline vmin 0.817 far OUTSIDE 0.94, the non-worsening clamp load-bearing.

Everything else is exp93's three-way test: parallel (each track vs bare baseline), sequential
(approve one track, add it to the base case, study the other; both orders), joint (co-study all).
    RESIDUAL := joint - max(seq_pvfirst, seq_ldfirst) = capacity NO study order recovers.
The sequential anchor is the base Feeder's own `anchored_context` (exp70), wrapped back into a
staged-oracle Feeder that keeps the SHIPPED limits -- so the approved PV's voltage relief does not
become a false floor for the load track (the same care exp92's ckt24_with_anchor takes).

Each cell also reports WHAT BINDS at the PV-solo optimum (vmax / line / trafo vs its limit), so
the regime is classified, not assumed -- that is the datum §1's rule turns on.

Run:
  .venv/bin/python experiments/98_residual_enwl.py --save
  .venv/bin/python experiments/98_residual_enwl.py --feeders enwl_n2f1 --sizes-kw 100 --save
"""
from __future__ import annotations

import argparse
import random
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

_x = __import__("78_corpus_law")                  # Feeder, load_feeder
_c = __import__("68_ckt7_gldf_headline")          # Project
_seq = __import__("91_sequential_segregation")    # certify (feeder-agnostic)

OUT = Path(__file__).resolve().parents[1] / "outputs"

FEEDERS = ["enwl_n2f1", "enwl_n13f4"]
SIZES_KW = [100.0]
K = 24


def build_mixed(f, k, seed, load_frac, size_kw):
    """K monodisperse requests: buses uniform over the load-bus pool, signs by load_frac.
    Separate bus / sign RNG streams so the mix never moves a request's bus (exp83's draw)."""
    rng_b = random.Random(seed)
    buses = [rng_b.choice(f.load_buses) for _ in range(k)]
    rng_s = random.Random(seed + 10_000)
    idx = list(range(k))
    rng_s.shuffle(idx)
    is_load = set(idx[:int(round(load_frac * k))])
    return [_c.Project(id=f"r{i}", bus=int(b),
                       p_mw=(size_kw / 1000.0) if i in is_load else (-size_kw / 1000.0))
            for i, b in enumerate(buses)]


def enwl_with_anchor(base_f, anchor_projects, anchor_ids):
    """A staged-oracle Feeder whose BASE CASE already contains the first track's accepted set as
    permanent in-service (negative-for-PV) loads -- exp91's feeder_with_anchor, but on the base
    Feeder and via its own `anchored_context`, and keeping base_f's SHIPPED limits so the approved
    PV's relief is not read back as a higher floor for the load track."""
    ctx2 = base_f.anchored_context(base_f.ctx, anchor_projects, anchor_ids)
    f2 = _x.Feeder.from_ctx(base_f.name + "+anchor", ctx2, base_f.sizer,
                            base_f.VMIN_PU, base_f.VMAX_PU, base_f.LOADING_MAX_PCT)
    f2._proj = None
    return f2


def binds(f, projects, ids):
    """What is at its limit at this accept-set's AC solution (for regime classification)."""
    f._proj = None
    ok, _, _, info = f.evaluate(f.ctx, projects, ids)
    return dict(ok=bool(ok), vmax=info.get("vmax"), vmin=info.get("vmin"),
                line_pct=info.get("line_pct"), trafo_pct=info.get("trafo_pct"))


def three_way(f, projects, n_jobs):
    """exp93's residual on one ENWL queue: parallel, sequential (both orders), joint."""
    pv = [p for p in projects if p.p_mw < 0]
    ld = [p for p in projects if p.p_mw > 0]

    f._proj = None
    pv_ids, pv_kw = _seq.certify(f, pv, n_jobs)
    f._proj = None
    ld_ids, ld_kw = _seq.certify(f, ld, n_jobs)
    seg_par = pv_kw + ld_kw

    f._proj = None
    _j, joint = _seq.certify(f, projects, n_jobs)

    f_pv = enwl_with_anchor(f, pv, pv_ids)               # base + accepted PV -> voltage relief
    _l2, ld2 = _seq.certify(f_pv, ld, n_jobs)
    seq_pv1 = pv_kw + ld2

    f_ld = enwl_with_anchor(f, ld, ld_ids)               # base + accepted load
    _p2, pv2 = _seq.certify(f_ld, pv, n_jobs)
    seq_ld1 = ld_kw + pv2

    best_seq = max(seq_pv1, seq_ld1)
    reg = binds(f, pv, pv_ids)                            # what binds at the PV-solo optimum
    return dict(pv_alone_kw=pv_kw, load_alone_kw=ld_kw, seg_parallel_kw=seg_par,
                seq_pvfirst_kw=seq_pv1, seq_loadfirst_kw=seq_ld1, best_sequential_kw=best_seq,
                joint_kw=joint,
                pvsolo_vmax=reg["vmax"], pvsolo_line_pct=reg["line_pct"],
                pvsolo_trafo_pct=reg["trafo_pct"],
                cost_vs_parallel_pct=(100 * (joint / seg_par - 1) if seg_par > 0 else np.nan),
                residual_vs_sequential_pct=(100 * (joint / best_seq - 1) if best_seq > 0 else np.nan))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--feeders", nargs="+", default=FEEDERS)
    ap.add_argument("--sizes-kw", type=float, nargs="+", default=SIZES_KW)
    ap.add_argument("--k", type=int, default=K)
    ap.add_argument("--load-frac", type=float, default=0.5)
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2, 3])
    ap.add_argument("--n-jobs", type=int, default=8)
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()

    print("\n  M70 -- SEGREGATION RESIDUAL ON REAL UK LV FEEDERS (ENWL, EN 50160 0.94-1.10)\n")
    print(f"  K={a.k}, load {100*a.load_frac:.0f}%, monodisperse; residual = joint - best study order.")
    print("  PREDICTION: PV overvoltage-limited (two-sided band) -> two-way -> positive residual.\n")

    hdr = (f"  {'feeder':>11} {'size':>5} | {'PVsolo':>7} {'LDsolo':>7} {'JOINT':>7} "
           f"{'vmax@pv':>8} {'line%':>6} | {'vs par':>7} {'residual: med':>13} {'min':>5} {'max':>5}")
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))

    rows = []
    for name in a.feeders:
        _ctx, f, _rel, _mk, desc = _x.load_feeder(name, a.n_jobs)
        for s in a.sizes_kw:
            per = []
            for seed in a.seeds:
                t0 = time.time()
                projects = build_mixed(f, a.k, seed, a.load_frac, s)
                r = three_way(f, projects, a.n_jobs)
                r.update(feeder=name, size_kw=s, k=a.k, seed=seed, load_frac=a.load_frac,
                         dt=time.time() - t0)
                rows.append(r)
                per.append(r)
                if a.save:
                    OUT.mkdir(exist_ok=True)
                    pd.DataFrame(rows).to_csv(OUT / "m70_residual_enwl.csv", index=False)

            res = np.array([p["residual_vs_sequential_pct"] for p in per], float)
            res = res[~np.isnan(res)]
            parg = np.array([p["cost_vs_parallel_pct"] for p in per], float)
            parg = parg[~np.isnan(parg)]
            pvsolo = np.median([p["pv_alone_kw"] for p in per])
            ldsolo = np.median([p["load_alone_kw"] for p in per])
            joint = np.median([p["joint_kw"] for p in per])
            vmax = np.median([p["pvsolo_vmax"] for p in per])
            line = np.median([p["pvsolo_line_pct"] for p in per])
            if len(res):
                pstr = (f"{np.median(parg):>+6.0f}%" if len(parg) else f"{'--':>7}")
                print(f"  {name:>11} {s:>4.0f} | {pvsolo:>7.0f} {ldsolo:>7.0f} {joint:>7.0f} "
                      f"{vmax:>8.4f} {line:>5.1f} | {pstr:>7} "
                      f"{np.median(res):>+12.1f}% {np.min(res):>+4.1f} {np.max(res):>+4.1f}")

    d = pd.DataFrame(rows)
    if a.save:
        OUT.mkdir(exist_ok=True)
        d.to_csv(OUT / "m70_residual_enwl.csv", index=False)

    print("\n" + "=" * 96)
    print("  Ckt7 (exp91/93): residual median +20%. Ckt24 (exp96): 0 while thermal binds, up to")
    print("  +43% once coarse-PV oversubscription makes overvoltage bind. If ENWL (a different")
    print("  family, EN 50160) also shows a positive residual with vmax binding, the rule --")
    print("  residual <=> the active constraint admits gen AND load as independent aggressors --")
    print("  holds on an independent feeder.\n")
    if a.save:
        print("  saved -> outputs/m70_residual_enwl.csv")


if __name__ == "__main__":
    main()
