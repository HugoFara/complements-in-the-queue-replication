"""M81 -- A CERTIFICATE FOR STRICT SIMULTANEITY (and a free optimality probe)

WHY
---
M80 (exp109) showed that multi-round alternation recovers most of the single-pass residual: a
re-study loop, needing no tariff reform, reaches the joint optimum in 10 of 13 draws that had a
residual. What survives alternation is the paper's real claim, and it needs to be established
by something better than a difference of two heuristic searches -- which is precisely the
objection raised against Eq. (1).

This file supplies that. At the ALTERNATING FIXED POINT of a draw, take every request the fixed
point did not seat and ask, by DIRECT AC EVALUATION only:

  1. does any leftover request fit on its own?        -> if yes, alternation stalled prematurely
                                                         (a search artifact, not a physics fact)
  2. does any SAME-SIGN pair fit, neither alone?      -> also reachable by a single-signed round,
                                                         so also not simultaneity
  3. does any MIXED gen+load pair fit, neither alone? -> STRICT SIMULTANEITY. No single-signed
                                                         round can ever reach it, at any number
                                                         of rounds, because each half is
                                                         infeasible at every intermediate base.

Case 3 is a CERTIFICATE, not an estimate: it is decided by AC power flow on an explicit pair, so
it holds regardless of how good any optimizer is. That is the epistemic upgrade -- the surviving
residual stops being "the gap between two searches" and becomes "here is a specific pair that is
jointly feasible while neither half is".

THE FREE OPTIMALITY PROBE
-------------------------
On draws where alternation already reached the joint value, the fixed point IS the certifier's
joint optimum. Finding an addable single or pair there would mean the joint certifier left
capacity on the table -- a direct, cheap measurement of the optimizer gap that Eq. (1) is
accused of hiding. Zero addable sets on those draws is evidence the joint search is tight.

Run:
  uv run python experiments/110_simultaneity_certificate.py --save
  uv run python experiments/110_simultaneity_certificate.py --families enwl_n13f4 --seeds 1
"""
from __future__ import annotations

import argparse
import itertools
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

_alt = __import__("109_alternating_rounds")

OUT = Path(__file__).resolve().parents[1] / "outputs"


def fixed_point_accept_set(fam, f, projects, n_jobs, start, max_rounds=_alt.MAX_ROUNDS):
    """exp109's alternating loop, returning the ACCEPT-SET itself rather than just its total."""
    gen = [p for p in projects if p.p_mw < 0]
    ld = [p for p in projects if p.p_mw > 0]
    tracks = [gen, ld] if start == "gen" else [ld, gen]
    pending = [list(tracks[0]), list(tracks[1])]
    accepted: list = []
    for _rnd in range(max_rounds):
        progressed = False
        for t in (0, 1):
            if not pending[t]:
                continue
            fa = fam.with_anchor(f, accepted, {p.id for p in accepted}) if accepted else f
            fam.reset(fa, pending[t])
            ids, _kw = fam.certify(fa, pending[t], n_jobs)
            if ids:
                accepted.extend([p for p in pending[t] if p.id in ids])
                pending[t] = [p for p in pending[t] if p.id not in ids]
                progressed = True
        if not progressed:
            break
    return accepted


def certificate(fam, f, projects, acc_ids):
    """The three questions, by direct AC evaluation against the pristine feeder."""
    def feas(extra_ids):
        fam.reset(f, projects)
        ok, _mw, _vio, _info = f.evaluate(f.ctx, projects, acc_ids | extra_ids)
        return bool(ok)

    leftover = [p for p in projects if p.id not in acc_ids]
    singles = [p for p in leftover if feas({p.id})]
    single_ids = {p.id for p in singles}

    same, mixed = [], []
    for a, b in itertools.combinations(leftover, 2):
        if a.id in single_ids or b.id in single_ids:
            continue                      # a pair containing an addable single proves nothing
        if not feas({a.id, b.id}):
            continue
        (same if (a.p_mw < 0) == (b.p_mw < 0) else mixed).append((a.id, b.id))

    return dict(n_leftover=len(leftover),
                n_leftover_gen=sum(1 for p in leftover if p.p_mw < 0),
                n_leftover_load=sum(1 for p in leftover if p.p_mw > 0),
                n_addable_singles=len(singles), n_same_sign_pairs=len(same),
                n_mixed_pairs=len(mixed),
                example_mixed_pair=(f"{mixed[0][0]}+{mixed[0][1]}" if mixed else ""),
                # the pairs themselves, for callers that need kW rather than a count
                # (exp120). Popped before this dict reaches a CSV row.
                _mixed_pairs=mixed)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--families", nargs="+",
                    default=["ckt7", "enwl_n13f4", "enwl_n2f1", "ckt24", "spur"])
    ap.add_argument("--k", type=int, default=None)
    ap.add_argument("--size-kw", type=float, default=100.0)
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2, 3])
    ap.add_argument("--n-jobs", type=int, default=6)
    ap.add_argument("--mat", default=None, help="Swiss 55-node .mat")
    ap.add_argument("--master", default=None, help="SMART-DS Master.dss")
    ap.add_argument("--placement", choices=["random", "colocated"], default="random")
    ap.add_argument("--out", default="m81_simultaneity_certificate.csv")
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()

    print("\n  M81 -- STRICT-SIMULTANEITY CERTIFICATE AT THE ALTERNATING FIXED POINT\n")
    print("  singles / same-sign pairs addable  -> alternation stalled early (search artifact)")
    print("  MIXED pairs addable, neither half  -> strict simultaneity, unreachable by any")
    print("                                        number of single-signed rounds\n")

    default_k = {"ckt7": 17, "ckt24": 24, "spur": 12, "swiss55": 48,
                 "smartds_colocated": 16, "smartds_random": 16}
    default_size = {"spur": 500.0, "swiss55": 500.0}
    rows = []
    for fname in a.families:
        fam = _alt.get_family(fname, a.n_jobs, mat=a.mat, master=a.master,
                              placement=a.placement)
        k = a.k if a.k else default_k.get(fname, 24)
        seeds = [0] if fname == "spur" else a.seeds
        size_used = default_size.get(fname, a.size_kw)
        f = fam.make_feeder()
        print(f"  {fam.desc}")
        hdr = (f"  {'seed':>4} | {'fixpt kW':>9} {'left':>5} | {'singles':>8} {'same-sign':>10} "
               f"{'MIXED':>7} | {'verdict':>22}")
        print(hdr)
        print("  " + "-" * (len(hdr) - 2))
        for seed in seeds:
            t0 = time.time()
            projects = fam.make_projects(f, seed, k, size_used)
            best, best_ids = -1.0, set()
            for start in ("gen", "load"):
                acc = fixed_point_accept_set(fam, f, projects, a.n_jobs, start)
                tot = sum(abs(p.p_mw) for p in acc) * 1000.0
                if tot > best:
                    best, best_ids = tot, {p.id for p in acc}
            c = certificate(fam, f, projects, best_ids)
            c.pop("_mixed_pairs")
            if c["n_addable_singles"] or c["n_same_sign_pairs"]:
                verdict = "STALLED EARLY"
            elif c["n_mixed_pairs"]:
                verdict = "strict simultaneity"
            else:
                verdict = "dead end at depth 2"
            c.update(family=fname, seed=seed, k=k, size_kw=size_used,
                     placement=a.placement,
                     fixed_point_kw=best, verdict=verdict, dt=time.time() - t0)
            rows.append(c)
            print(f"  {seed:>4d} | {best:>9.0f} {c['n_leftover']:>5d} | "
                  f"{c['n_addable_singles']:>8d} {c['n_same_sign_pairs']:>10d} "
                  f"{c['n_mixed_pairs']:>7d} | {verdict:>22}", flush=True)
            if a.save:
                OUT.mkdir(exist_ok=True)
                pd.DataFrame(rows).to_csv(OUT / a.out, index=False)
        print()

    d = pd.DataFrame(rows)
    print("=" * 78)
    stalled = int((d.n_addable_singles + d.n_same_sign_pairs > 0).sum())
    print(f"\n  draws where alternation stalled prematurely : {stalled} / {len(d)}")
    print(f"  draws with a strict-simultaneity certificate: "
          f"{int((d.n_mixed_pairs > 0).sum())} / {len(d)}")
    print(f"  total certified mixed pairs found           : {int(d.n_mixed_pairs.sum())}")
    print("\n  A stalled draw would mean the alternating fixed point is not a fixed point of the")
    print("  physics, only of the search. A mixed-pair certificate is decided by AC power flow")
    print("  on an explicit pair, so it does not depend on any optimizer being optimal.\n")
    if a.save:
        print(f"  saved -> outputs/{a.out}\n")


if __name__ == "__main__":
    main()
