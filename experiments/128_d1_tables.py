"""D1's tables, rebuilt from the saved CSVs so every number in the revision plan has a
provenance trail rather than a transcript.

Three comparisons, in the order the argument needs them:

  1. published vs exact AT THE SNAPSHOT -- the control. Agreement means the paper's SMART-DS
     numbers are exact optima rather than search artifacts, which is what both referees'
     leading objection asks for, and it means anything that moves under the year is physics.
  2. exact snapshot vs exact year -- the finding, with no optimizer on either side. The
     search-based year arm is shown alongside because the rest of the corpus has K far too
     large to enumerate, so the comparable number has to come through the MILP path.
  3. replay -- what share of the year rejects the decision a snapshot study already made,
     per arm. Not how the optimum moves; whether the plan is still legal.

Then whatever regime and mismatch arms exist: `shapes` is uncontrolled co-location, `banded`
is Section 6.2's net-export control as a constraint, and the `_mm` arms drop the leg mismatch
below 1.0 so the joint side stops being arithmetic.

Run:  uv run python experiments/128_d1_tables.py
"""
from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path(__file__).resolve().parents[1] / "outputs"
COLS = ["gen_alone_kw", "load_alone_kw", "best_sequential_kw", "best_alternating_kw",
        "joint_kw", "residual_singlepass_pct", "residual_alt_pct"]


def get(name):
    p = OUT / f"{name}.csv"
    return pd.read_csv(p) if p.exists() else None


def norm(d):
    """`residuals()` already writes best_alternating_kw, so a blind rename of alt_best_kw would
    produce two columns of that name and turn every lookup into a DataFrame."""
    if "best_alternating_kw" not in d.columns and "alt_best_kw" in d.columns:
        d = d.rename(columns={"alt_best_kw": "best_alternating_kw"})
    return d


def med(d, c):
    return d[c].median() if d is not None and c in d else np.nan


def main():
    pub = get("m80_alt_s20_smartds")
    snap = get("d1_exact_snapshot")
    yr = get("d1_exact_year")
    srch = get("d1_year_search")
    rep = get("d1_replay")

    print("\n  1. CONTROL -- exact enumeration at the paper's own snapshot\n")
    hdr = f"  {'arm':>22} {'n':>3} {'gen':>6} {'load':>6} {'seq':>6} {'alt':>6} {'JOINT':>7} {'R1 %':>7} {'R* %':>7}"
    print(hdr, "\n  " + "-" * (len(hdr) - 2))
    for nm, d in (("published (search)", pub), ("exact enumeration", snap)):
        if d is None:
            continue
        d = norm(d)
        print(f"  {nm:>22} {len(d):>3} " + " ".join(
            f"{med(d, c):>6.0f}" if "pct" not in c else f"{med(d, c):>7.1f}" for c in COLS))
    if pub is not None and snap is not None:
        m = pub.merge(snap, on="seed", suffixes=("_pub", "_ex"))
        # tolerance, not `==`: both sides accumulate kW as sums of p_mw * 1000, so equal
        # answers differ in the last bits (max observed 2.3e-13 kW). Strict equality reports
        # spurious mismatches.
        same = int((np.abs(m.joint_kw_pub - m.joint_kw_ex) < 1e-6).sum())
        sameq = int((np.abs(m.best_sequential_kw_pub - m.best_sequential_kw_ex) < 1e-6).sum())
        print(f"\n     JOINT identical in {same}/{len(m)} draws; "
              f"best sequential identical in {sameq}/{len(m)}.")
        d = (m.residual_singlepass_pct_ex - m.residual_singlepass_pct_pub)
        print(f"     R1 exact - R1 published: median {d.median():+.1f} pp, "
              f"range {d.min():+.1f} to {d.max():+.1f} pp")

    print("\n\n  2. THE FINDING -- the same enumeration, certified at every interval of the year\n")
    print(hdr, "\n  " + "-" * (len(hdr) - 2))
    for nm, d in (("exact @ snapshot", snap), ("exact @ full year", yr),
                  ("search @ full year", srch)):
        if d is None:
            continue
        d = norm(d)
        print(f"  {nm:>22} {len(d):>3} " + " ".join(
            f"{med(d, c):>6.0f}" if "pct" not in c else f"{med(d, c):>7.1f}" for c in COLS))
    if snap is not None and yr is not None:
        m = snap.merge(yr, on="seed", suffixes=("_s", "_y"))
        for c, lbl in (("gen_alone_kw", "generation track"), ("load_alone_kw", "load track"),
                       ("best_sequential_kw", "best sequential"), ("joint_kw", "JOINT")):
            dd = m[f"{c}_y"] - m[f"{c}_s"]
            print(f"     {lbl:>18}: {dd.median():+7.0f} kW median "
                  f"({int((dd < 0).sum())} draws fall, {int((dd == 0).sum())} unchanged, "
                  f"{int((dd > 0).sum())} rise)")
        dr = m.residual_singlepass_pct_y - m.residual_singlepass_pct_s
        print(f"     {'R1':>18}: {dr.median():+7.1f} pp median "
              f"({int((dr > 0).sum())}/{len(m)} draws rise)")

    if rep is not None:
        print("\n\n  3. REPLAY -- share of the 35,040 intervals that reject a snapshot-certified "
              "accept-set\n")
        print(f"  {'arm':>16} {'median %':>9} {'worst %':>8} {'clean draws':>12}")
        print("  " + "-" * 48)
        for nm in ("gen_alone", "load_alone", "seq_genfirst", "seq_loadfirst",
                   "alt_genfirst", "alt_loadfirst", "joint"):
            c = f"reject_{nm}_pct"
            if c in rep:
                print(f"  {nm:>16} {rep[c].median():>9.1f} {rep[c].max():>8.1f} "
                      f"{int((rep[c] == 0).sum()):>7}/{len(rep):<4}")

    for nm in ("d1_exact_shapes", "d1_exact_banded_b00", "d1_exact_banded_b02",
               "d1_exact_banded_b05", "d1_exact_snapshot_mm075", "d1_exact_year_mm075",
               "d1_exact_snapshot_mm050", "d1_exact_year_mm050"):
        d = get(nm)
        if d is None:
            continue
        d = norm(d)
        print(f"\n  {nm}: n={len(d)} " + " ".join(
            f"{c.split('_')[0]}={med(d, c):.0f}" for c in COLS[:5])
            + f"  R1={med(d, 'residual_singlepass_pct'):.1f}% "
              f"R*={med(d, 'residual_alt_pct'):.1f}%")
    print()


if __name__ == "__main__":
    main()
