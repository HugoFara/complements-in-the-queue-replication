"""M79 -- DOES CKT24 STAY UNDER-SUBSCRIBED AT FINE GRANULARITY?

Section 5.4 of the paper claims that on Ckt24 fine 30 kW requests "stay thermal-limited
(residual 0) past 4 MW requested". That sentence was carried over from exp96's design note,
which asserted it about the OVERSUBSCRIPTION ratio (PV_requested / PV_solo stays ~1.0), not
about the residual, and the archived scale sweep (exp97 -> m69) stops at 3 MW total. Neither
file measures a 30 kW residual past 4 MW, so this one does.

The mechanism under test is the oversubscription gate of Section 5.4: a queue that fits solo
has nothing for a joint study to recover, because a generation-first sequential order already
seats everything the generation track can hold. Ckt24 at 30 kW is the case where the feeder
simply hosts more as requests are added, so the ratio never climbs and the residual should
stay near zero however large the total gets.

What it finds: the ratio does stay flat (1.02-1.10x out to 6 MW requested, never approaching
the 2.5x that turns Ckt24 two-way), and the residual is small but NOT identically zero --
below 2.6% throughout, exactly zero in 4 of 12 draws, median 0.9%. "Residual 0" was too
strong; "never oversubscribed, so the residual stays negligible" is what the data support.

Same three-way measurement as exp96 (parallel, both sequential orders, joint), same feeder
at Ckt24's only feasible setpoint, so these rows are directly comparable with m68/m69.

Run:
  .venv/bin/python experiments/108_ckt24_fine_scale.py --save
  .venv/bin/python experiments/108_ckt24_fine_scale.py --totals-kw 4000 --seeds 0
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

_seg = __import__("88_ckt24_segregation")                  # Ckt24Feeder, build_mixed
_over = __import__("96_residual_oversubscription_ckt24")   # three_way

OUT = Path(__file__).resolve().parents[1] / "outputs"

SIZE_KW = 30.0
TOTALS_KW = [4000.0, 5000.0, 6000.0]
SETPOINT = 1.05          # FORCED -- Ckt24's only feasible setpoint (exp88)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--setpoint", type=float, default=SETPOINT)
    ap.add_argument("--size-kw", type=float, default=SIZE_KW)
    ap.add_argument("--totals-kw", type=float, nargs="+", default=TOTALS_KW)
    ap.add_argument("--load-frac", type=float, default=0.5)
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2, 3])
    ap.add_argument("--n-jobs", type=int, default=6)
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()

    print("\n  M79 -- FINE-GRANULARITY RESIDUAL vs TOTAL REQUESTED  (Ckt24 @%.2f, %.0f kW)\n"
          % (a.setpoint, a.size_kw))
    print("  K = total/size; the question is whether the generation track ever oversubscribes.")
    print("  If it does not, the residual should stay ~0 however large the total.\n")

    f = _seg.Ckt24Feeder.at_setpoint(a.setpoint)

    hdr = (f"  {'total':>7} {'K':>4} {'seed':>4} | {'PV solo':>8} {'oversub':>8} | "
           f"{'SEG-par':>8} {'bestSEQ':>8} {'JOINT':>8} | {'residual':>9}")
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))

    rows = []
    for total in a.totals_kw:
        K = int(round(total / a.size_kw))
        for seed in a.seeds:
            t0 = time.time()
            projects = _seg.build_mixed(f, K, seed, a.load_frac, a.size_kw)
            pv_req = sum(abs(p.p_mw) for p in projects if p.p_mw < 0) * 1000
            r = _over.three_way(f, projects, a.n_jobs)
            j, b = r["joint_kw"], r["best_sequential_kw"]
            r.update(total_kw=total, k=K, seed=seed, size_kw=a.size_kw,
                     setpoint=a.setpoint, load_frac=a.load_frac,
                     pv_requested_kw=pv_req,
                     oversub_ratio=(pv_req / r["pv_alone_kw"] if r["pv_alone_kw"] else float("inf")),
                     residual_kw=j - b,
                     residual_frac_of_joint_pct=(100 * (j - b) / j if j > 0 else float("nan")),
                     dt=time.time() - t0)
            rows.append(r)
            print(f"  {total:>7.0f} {K:>4d} {seed:>4d} | {r['pv_alone_kw']:>8.0f} "
                  f"{r['oversub_ratio']:>7.2f}x | {r['seg_parallel_kw']:>8.0f} {b:>8.0f} "
                  f"{j:>8.0f} | {r['residual_frac_of_joint_pct']:>8.2f}%", flush=True)
            if a.save:
                OUT.mkdir(exist_ok=True)
                pd.DataFrame(rows).to_csv(OUT / "m79_ckt24_fine_scale.csv", index=False)

    d = pd.DataFrame(rows)
    res = d.residual_frac_of_joint_pct
    print("\n" + "=" * (len(hdr) - 2))
    print(f"  oversubscription {d.oversub_ratio.min():.2f}-{d.oversub_ratio.max():.2f}x "
          f"-- the generation track never fills, so there is little for a joint study to add.")
    print(f"  residual: median {res.median():.2f}%, max {res.max():.2f}%, "
          f"exactly zero in {int((res <= 1e-9).sum())}/{len(res)} draws.")
    print("  Contrast the 100 kW cells (m68), where 2.5x oversubscription makes the same")
    print("  feeder two-way and the residual reaches 30% of joint.\n")
    if a.save:
        print("  saved -> outputs/m79_ckt24_fine_scale.csv")


if __name__ == "__main__":
    main()
