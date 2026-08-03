"""M94 -- HOW MUCH OF R* IS CERTIFICATE-BACKED STRICT SIMULTANEITY?

WHY
---
Both second-round referees converge on the same unrun computation, and both call it
outcome-deciding.

  AE reviewer, comment 2: "Sixteen pairs across the SMART-DS draws proves the mechanism
  exists; it does not show that 62.5% of joint capacity consists of it. Define and report
  the pair-certified share of R* per cell. If it's ~100%, the headline is bulletproof; if
  it's 40%, the reform-justifying number is 40%, and the rest is path dependence."

  Adversarial referee, comment 3(b): "Decompose each reported R* into (i) kW accounted for
  by exhibited mixed pairs meeting Proposition 1's hypothesis and (ii) remainder."

R* = JOINT - alternating is a difference of two searches. Only the part of it that an
explicit AC-feasible mixed pair accounts for is reform-relevant; the rest may be path
dependence of the alternation operator (a reason to study a queue well the first time) or
simply a deeper simultaneity our depth-2 certificate does not see.

WHAT THIS MEASURES
------------------
At the alternating fixed point of a draw, exp110's certificate already asks which leftover
requests are addable alone, in same-sign pairs, or only in MIXED pairs. This file converts
that from a COUNT of pairs into kW, and divides by R*. Two bounds, because the honest answer
is an interval:

  CERTIFIED (lower).  Greedily take disjoint certified mixed pairs in decreasing kW order,
  AC-verifying the cumulative union on top of the fixed point at every step and keeping only
  what holds. The result is an explicit, AC-feasible accept-set consisting of the fixed point
  plus strictly-simultaneous pairs. Its increment over the fixed point is capacity that IS
  strict simultaneity, certified pair by pair, appealing to no optimizer.

  IN-SOME-PAIR (upper).  Total kW of every leftover request appearing in at least one
  certified mixed pair. A leftover in NO mixed pair is not explained by depth-2 strict
  simultaneity at all, so this bounds the share from above.

The gap between them is pairs that are individually addable but not jointly addable -- real
simultaneity that competes with itself for the same headroom.

WHAT THE REMAINDER IS *NOT*
---------------------------
Unexplained kW is not thereby proven to be path dependence. The certificate is depth 2: a
jointly-feasible mixed TRIPLE with no feasible sub-pair would be strict simultaneity this
file scores as unexplained. `n_addable_singles` and `n_same_sign_pairs` are reported per
draw because those ARE positive evidence of premature stalling (a single-signed round could
have taken them), and they are the path-dependence indicator. Read the three columns
together; do not read "remainder" as "path dependence".

CELLS
-----
The three cells carrying an irreducible residual in the paper, at 20 draws each, matched
exactly to the alternation cells whose R* they explain:

  ckt24 colocated      k=24, 100 kW   (m82_colo_s20:      R* median 61.8%)
  smartds_colocated    k=16, 100 kW   (m80_alt_s20_smartds: R* median 62.5%)
  enwl_n13f4 rescale   k=24, 100 kW   (m88_hard_limits_s20: R* median 48.6%, positive 15/20)

The n13f4 RESCALE arm is the compliant operating point of exp114, on which neither referee
could find a certificate in the manuscript -- the AE reviewer calls it "the single most
consequential unrun computation in the paper", because it decides whether that 48.6%
supports the reform or undercuts the paper's own "only R* can justify merging tracks".

Run:
  uv run python experiments/120_certified_share.py --save
  uv run python experiments/120_certified_share.py --cells ckt24 --seeds 0 1 2 --n-jobs 8
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

_x = __import__("78_corpus_law")
_alt = __import__("109_alternating_rounds")
_cert = __import__("110_simultaneity_certificate")
_hard = __import__("114_n13f4_hard_limits")

OUT = Path(__file__).resolve().parents[1] / "outputs"
CACHE = Path(__file__).resolve().parents[1] / "cache" / "smartds"


# --------------------------------------------------------------------- cells
def cell_ckt24(n_jobs):
    fam = _alt.get_family("ckt24", n_jobs, placement="colocated")
    return fam, fam.make_feeder(), 24, 100.0


def cell_ckt24_50(n_jobs):
    """The paper's REFERENCE cell. `cell_ckt24` above is the 100 kW queue, which the paper
    reports as not servable alone -- a 100 kW single-phase request draws 417 A through a
    195-299 A service -- so anything measured there is scoped to a request size the feeder
    cannot carry. The reference cell is 50 kW and any claim about the headline 12.5% has to
    be made here."""
    fam = _alt.get_family("ckt24", n_jobs, placement="colocated")
    return fam, fam.make_feeder(), 24, 50.0


def cell_smartds(n_jobs, feeder="uhs2_1247--udt10746"):
    fam = _alt.get_family("smartds_colocated", n_jobs,
                          master=str(CACHE / feeder / "Master.dss"))
    return fam, fam.make_feeder(), 16, 100.0


def cell_n13f4_rescale(n_jobs, vmin=0.94, vmax=1.10, loading=100.0):
    """exp114's RESCALE arm: bisect the shipped baseline load to the largest scale meeting
    the book floor, then enforce book limits over the full candidate pool."""
    _ctx, f_rel, _rel, _mk, desc = _x.load_feeder("enwl_n13f4", n_jobs)
    f_scaled, alpha, achieved = _hard.build_rescaled(f_rel, vmin, vmax, loading)
    fam = _hard.family_for("enwl_n13f4_rescale", f_scaled, _hard.hard_with_anchor,
                           f"{desc} -- RESCALE x{alpha:.3f}, vmin {achieved:.3f}, book limits")
    return fam, f_scaled, 24, 100.0


CELLS = {"ckt24": cell_ckt24, "ckt24_50": cell_ckt24_50,
         "smartds": cell_smartds, "n13f4_rescale": cell_n13f4_rescale}


# ------------------------------------------------------------- the kW accounting
def certified_disjoint_pairs(fam, f, projects, acc_ids, mixed_pairs):
    """Greedy max-weight DISJOINT set of certified mixed pairs whose cumulative union stays
    AC-feasible on top of the fixed point.

    Every acceptance is AC-verified against the pristine feeder, so the returned set is a
    certificate: an explicit feasible allocation made of the fixed point plus pairs each of
    whose halves is individually infeasible.
    """
    by_id = {p.id: p for p in projects}
    kw = lambda pr: (abs(by_id[pr[0]].p_mw) + abs(by_id[pr[1]].p_mw)) * 1000.0
    order = sorted(mixed_pairs, key=kw, reverse=True)

    taken: set = set()
    used_ids: set = set()
    for pr in order:
        if pr[0] in used_ids or pr[1] in used_ids:
            continue                                  # disjointness
        trial = used_ids | {pr[0], pr[1]}
        fam.reset(f, projects)
        ok, _mw, _vio, _info = f.evaluate(f.ctx, projects, acc_ids | trial)
        if ok:
            used_ids = trial
            taken.add(pr)
    return taken, sum(abs(by_id[i].p_mw) for i in used_ids) * 1000.0


def run_cell(name, fam, f, k, size_kw, seeds, n_jobs):
    rows = []
    hdr = (f"  {'seed':>4} | {'JOINT':>7} {'alt':>7} {'R*':>7} | {'sing':>4} {'same':>4} "
           f"{'mixed':>5} | {'cert kW':>7} {'share%':>7} | {'inpair kW':>9} {'share%':>7}")
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for seed in seeds:
        t0 = time.time()
        projects = fam.make_projects(f, seed, k, size_kw)
        fam.reset(f, projects)
        w = _alt.three_way_generic(fam, f, projects, n_jobs)
        joint = w["joint_kw"]

        best, best_ids = -1.0, set()
        for start in ("gen", "load"):
            acc = _cert.fixed_point_accept_set(fam, f, projects, n_jobs, start)
            tot = sum(abs(p.p_mw) for p in acc) * 1000.0
            if tot > best:
                best, best_ids = tot, {p.id for p in acc}
        rstar = joint - best

        c = _cert.certificate(fam, f, projects, best_ids)
        mixed = c.pop("_mixed_pairs")
        by_id = {p.id: p for p in projects}
        in_pair = {i for pr in mixed for i in pr}
        in_pair_kw = sum(abs(by_id[i].p_mw) for i in in_pair) * 1000.0
        taken, cert_kw = certified_disjoint_pairs(fam, f, projects, best_ids, mixed)

        pct = lambda v: (100.0 * v / rstar) if rstar > 1e-9 else np.nan
        # exp121 needs the fixed point and the pairs themselves to linearize at, and
        # recomputing them costs another full pass of alternation per draw.
        row = dict(cell=name, seed=seed, k=k, size_kw=size_kw,
                   fixed_point_ids=";".join(sorted(best_ids)),
                   mixed_pairs=";".join(f"{u}+{v}" for u, v in mixed),
                   certified_pairs=";".join(f"{u}+{v}" for u, v in sorted(taken)),
                   joint_kw=joint, best_sequential_kw=w["best_sequential_kw"],
                   alternation_kw=best,
                   r1_kw=joint - w["best_sequential_kw"], rstar_kw=rstar,
                   rstar_pct=(100.0 * rstar / joint) if joint else np.nan,
                   n_addable_singles=c["n_addable_singles"],
                   n_same_sign_pairs=c["n_same_sign_pairs"],
                   n_mixed_pairs=c["n_mixed_pairs"],
                   n_disjoint_pairs_certified=len(taken),
                   certified_kw=cert_kw, certified_share_pct=pct(cert_kw),
                   in_pair_kw=in_pair_kw, in_pair_share_pct=pct(in_pair_kw),
                   stalled=bool(c["n_addable_singles"] or c["n_same_sign_pairs"]),
                   dt=time.time() - t0)
        rows.append(row)
        print(f"  {seed:>4d} | {joint:>7.0f} {best:>7.0f} {rstar:>7.0f} | "
              f"{c['n_addable_singles']:>4d} {c['n_same_sign_pairs']:>4d} "
              f"{c['n_mixed_pairs']:>5d} | {cert_kw:>7.0f} {pct(cert_kw):>7.1f} | "
              f"{in_pair_kw:>9.0f} {pct(in_pair_kw):>7.1f}", flush=True)
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cells", nargs="+", default=list(CELLS), choices=list(CELLS))
    ap.add_argument("--seeds", type=int, nargs="+", default=list(range(20)))
    ap.add_argument("--n-jobs", type=int, default=8)
    ap.add_argument("--out", default="m94_certified_share.csv")
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()

    print("\n  M94 -- THE CERTIFICATE-BACKED SHARE OF R*\n")
    print("  cert kW  : fixed point + a DISJOINT set of AC-verified mixed pairs, each half")
    print("             individually infeasible. A certificate, not a search result.")
    print("  inpair kW: every leftover in at least one certified mixed pair (upper bound).")
    print("  sing/same: addable singles or same-sign pairs -- a single-signed round could")
    print("             have taken these, so they are premature stalling, not simultaneity.\n")

    rows = []
    for name in a.cells:
        fam, f, k, size_kw = CELLS[name](a.n_jobs)
        print(f"  {fam.desc}   (k={k}, {size_kw:.0f} kW, {len(a.seeds)} draws)")
        rows += run_cell(name, fam, f, k, size_kw, a.seeds, a.n_jobs)
        if a.save:
            OUT.mkdir(exist_ok=True)
            pd.DataFrame(rows).to_csv(OUT / a.out, index=False)
        print()

    d = pd.DataFrame(rows)
    print("=" * 92)
    print("\n  SUMMARY -- median over draws\n")
    g = d.groupby("cell").agg(
        draws=("seed", "size"),
        rstar_pct=("rstar_pct", "median"),
        rstar_pos=("rstar_kw", lambda s: int((s > 1e-9).sum())),
        certified_share=("certified_share_pct", "median"),
        in_pair_share=("in_pair_share_pct", "median"),
        stalled=("stalled", "sum"))
    print(g.to_string())

    # certified_kw > R* would mean the fixed point plus certified pairs BEATS the joint
    # search -- an optimizer gap in the joint arm, not a share of anything.
    over = d[d.certified_kw > d.rstar_kw + 1e-9]
    if len(over):
        print(f"\n  WARNING: {len(over)} draw(s) where the certificate exceeds R*, i.e. the")
        print("  joint search left capacity on the table. Those shares are not interpretable")
        print("  as fractions; the joint arm needs re-running on them:")
        print(over[["cell", "seed", "joint_kw", "alternation_kw", "certified_kw"]]
              .to_string(index=False))
    print("\n  A cell whose certified share is ~100% has an R* that is strict simultaneity")
    print("  outright. A low certified share with a high in-pair share means the pairs exist")
    print("  but compete for the same headroom. A low share on BOTH, with stalled draws, is")
    print("  the referees' path-dependence reading and the headline would have to change.\n")
    if a.save:
        OUT.mkdir(exist_ok=True)
        d.to_csv(OUT / a.out, index=False)
        print(f"  saved -> outputs/{a.out}\n")


if __name__ == "__main__":
    main()
