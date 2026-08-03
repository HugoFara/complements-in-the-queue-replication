"""M22: the honest corpus headline -- tie rate vs. a *real* FCFS baseline.

The joint-allocation note's Section 4.8 reports the search "beats the baseline"
on 120/121 ENWL feeders, but that headline hides two problems this experiment
exists to correct, both raised as blocking review issues:

1. **The baseline is a strawman.** `greedy_baseline` stops dead at the first
   infeasible request -- but real first-come-first-served interconnection
   studies skip an infeasible project and keep walking the queue. On a
   thermal-limited feeder (91% of this corpus), a later request on an
   unstressed part of the feeder genuinely still fits, so stop-dead
   *understates* what the status quo already achieves. This re-runs every
   comparison against `skip_continue_baseline` (a pure binary skip-and-continue
   walk) as the PRIMARY comparator, keeping stop-dead only as a contrast.

2. **The 116/121 "ties" are not foregrounded, and their cause is not
   diagnosed.** This reports #exact-ties / #real-gains (with magnitudes) /
   #regressions up front, and -- the load-bearing diagnostic -- splits ties
   into *unbound* (the request set never stresses the feeder: skip-continue
   accepts all K requests, so "which subset" is a non-question) vs. *genuinely
   tied* (the feeder binds, but joint search finds nothing the skip-continue
   walk didn't already get). If most ties are unbound, the honest headline is
   not "joint search rarely helps" but "the fixed request set rarely binds" --
   which is precisely what M23 (request-distribution sensitivity) then sweeps.

Reuses Section 4.8's exact corpus, per-feeder seeds, and request construction
so the numbers are directly comparable. Also saves a per-feeder CSV with the
regime features (request/headroom ratio, baseline saturation, binding type)
that M24's regime characterization regresses on.

Run:  uv run python experiments/52_honest_corpus_baseline.py [--max-feeders N] [--save]
"""

from __future__ import annotations

import argparse
import csv
import random
import time
from pathlib import Path

from gridsynth import import_opendss

from allocation import (
    AllocationProblem,
    Request,
    greedy_baseline,
    lindistflow_search_iterative,
    skip_continue_baseline,
)

REPO = Path(__file__).resolve().parents[1]
CACHE = REPO / "cache" / "enwl_full"
OUT = REPO / "outputs"

PV_KW_LO, PV_KW_HI = 3.0, 10.0  # same band as experiments 24-32, for comparability
SEED = 0
MAX_REQUESTS = 50
MIN_LOADS = 3
TIE_TOL_KW = 1e-3  # kW; below this two accommodated-MW figures are "the same"


def discover_feeders() -> list[Path]:
    masters = list(CACHE.glob("network_*/Feeder_*/Master_snapshot.dss"))
    return sorted(masters, key=lambda p: (int(p.parent.parent.name.split("_")[1]),
                                           int(p.parent.name.split("_")[1])))


def build_requests(load_buses: list[int], k: int, seed: int) -> list[Request]:
    rng = random.Random(seed)
    buses = rng.sample(load_buses, k)
    return [Request(id=f"pv{i}", bus=bus, p_mw=-rng.uniform(PV_KW_LO, PV_KW_HI) / 1000.0, queue_order=i)
            for i, bus in enumerate(buses)]


def binding_constraint(result, problem: AllocationProblem) -> str:
    over_v = (result.vmax_pu - 1.0) / (problem.vmax_pu - 1.0)
    under_v = (1.0 - result.vmin_pu) / (1.0 - problem.vmin_pu)
    volt_frac = max(over_v, under_v, 0.0)
    therm_frac = max(result.max_line_loading_percent, result.max_trafo_loading_percent) / problem.max_loading_percent
    return "voltage" if volt_frac >= therm_frac else "thermal"


def run_feeder(master: Path, feeder_seed: int) -> dict:
    name = f"{master.parent.parent.name}/{master.parent.name}"
    t0 = time.time()
    row: dict = {"name": name}
    try:
        net = import_opendss(str(master))
    except Exception as e:
        row.update(status=f"import_failed: {e}", dt=time.time() - t0)
        return row
    row["n_bus"] = len(net.bus)

    base_problem = AllocationProblem(net=net, requests=[])
    if not base_problem.evaluate([]).feasible:
        row.update(status="baseline_infeasible", dt=time.time() - t0)
        return row

    load_buses = net.load.bus.tolist()
    if len(load_buses) < MIN_LOADS:
        row.update(status="too_few_loads", n_load=len(load_buses), dt=time.time() - t0)
        return row

    k = min(MAX_REQUESTS, len(load_buses))
    requests = build_requests(load_buses, k, feeder_seed)
    problem = AllocationProblem(net=net, requests=requests)
    total_requested_kw = sum(abs(r.p_mw) for r in requests) * 1000

    stop_dead = greedy_baseline(problem)
    skip = skip_continue_baseline(problem)
    try:
        joint = lindistflow_search_iterative(problem).result
    except Exception as e:
        row.update(status=f"convex_failed: {e}", n_load=len(load_buses), k=k,
                   dt=time.time() - t0)
        return row

    stop_kw = stop_dead.accommodated_mw * 1000
    skip_kw = skip.accommodated_mw * 1000
    joint_kw = joint.accommodated_mw * 1000
    n_skip = len(skip.accepted_ids)

    bound = n_skip < k  # skip-continue rejected at least one request => the request set binds
    gain_vs_skip = joint_kw - skip_kw
    gain_vs_stop = joint_kw - stop_kw

    if not joint.feasible:
        cls = "search_infeasible"
    elif not bound:
        cls = "unbound_tie"  # nothing binds; joint == skip == all-K, a non-question
    elif gain_vs_skip > TIE_TOL_KW:
        cls = "real_gain"
    elif gain_vs_skip < -TIE_TOL_KW:
        cls = "regression"
    else:
        cls = "bound_tie"  # feeder binds, but joint finds nothing skip-continue missed

    row.update(
        status="ok", n_load=len(load_buses), k=k, seed=feeder_seed,
        total_requested_kw=total_requested_kw,
        stop_dead_kw=stop_kw, stop_dead_acc=len(stop_dead.accepted_ids),
        skip_kw=skip_kw, skip_acc=n_skip,
        joint_kw=joint_kw, joint_feasible=joint.feasible,
        gain_vs_skip_kw=gain_vs_skip, gain_vs_stop_kw=gain_vs_stop,
        bound=bound, classification=cls,
        binding=binding_constraint(joint, problem),
        # regime features (M24): how loaded is the request set vs. what fits,
        # and how much of the queue the honest baseline already served.
        req_over_skip=total_requested_kw / skip_kw if skip_kw > 0 else float("inf"),
        skip_saturation=n_skip / k,
        dt=time.time() - t0,
    )
    return row


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--max-feeders", type=int, default=0)
    ap.add_argument("--save", action="store_true")
    args = ap.parse_args()

    feeders = discover_feeders()
    if args.max_feeders:
        feeders = feeders[: args.max_feeders]
    print(f"M22: honest corpus baseline -- {len(feeders)} ENWL feeders, "
          f"skip-and-continue as the PRIMARY comparator\n")

    rows = []
    t_all = time.time()
    for idx, master in enumerate(feeders):
        row = run_feeder(master, SEED + idx)
        rows.append(row)
        if row["status"] == "ok":
            print(f"{row['name']:24s} bus={row['n_bus']:5d} k={row['k']:3d}  "
                  f"stop-dead={row['stop_dead_kw']:7.1f}  skip={row['skip_kw']:7.1f}({row['skip_acc']:2d}/{row['k']})  "
                  f"joint={row['joint_kw']:7.1f}  "
                  f"dJoint-skip={row['gain_vs_skip_kw']:+6.1f}  [{row['classification']}]")
        else:
            print(f"{row['name']:24s} {row['status']}")

    ok = [r for r in rows if r["status"] == "ok"]
    print(f"\ntotal: {len(rows)} feeders in {time.time() - t_all:.1f}s; {len(ok)} baseline-feasible + evaluated\n")

    if ok:
        from collections import Counter
        cls = Counter(r["classification"] for r in ok)
        n = len(ok)
        print("=" * 66)
        print("HEADLINE (vs. skip-and-continue FCFS, the real status quo):")
        print("=" * 66)
        gains = sorted((r["gain_vs_skip_kw"] for r in ok if r["classification"] == "real_gain"), reverse=True)
        regs = sorted(r["gain_vs_skip_kw"] for r in ok if r["classification"] == "regression")
        n_unbound = cls["unbound_tie"]
        n_bound_tie = cls["bound_tie"]
        n_tie = n_unbound + n_bound_tie
        print(f"  exact ties:   {n_tie}/{n} ({100*n_tie/n:.0f}%)  "
              f"= {n_unbound} UNBOUND (request set never binds) + {n_bound_tie} bound-but-tied")
        print(f"  real gains:   {len(gains)}/{n} ({100*len(gains)/n:.0f}%)  magnitudes kW: "
              f"{[round(x,1) for x in gains]}")
        print(f"  regressions:  {len(regs)}/{n}  magnitudes kW: {[round(x,1) for x in regs]}")
        if cls["search_infeasible"]:
            print(f"  search infeasible: {cls['search_infeasible']}")
        print()
        print("  CONTRAST -- the same feeders vs. stop-dead greedy_baseline (Section 4.8's comparator):")
        beats_stop = sum(1 for r in ok if r["gain_vs_stop_kw"] > TIE_TOL_KW)
        ties_stop = sum(1 for r in ok if abs(r["gain_vs_stop_kw"]) <= TIE_TOL_KW)
        reg_stop = sum(1 for r in ok if r["gain_vs_stop_kw"] < -TIE_TOL_KW)
        print(f"    joint > stop-dead: {beats_stop}   ties: {ties_stop}   regressions: {reg_stop}")
        # how much of the "gain vs stop-dead" is really just skip-continue's own gain?
        skip_beats_stop = sum(1 for r in ok if r["skip_kw"] - r["stop_dead_kw"] > TIE_TOL_KW)
        print(f"    (of which skip-continue ALONE already beats stop-dead on {skip_beats_stop} feeders "
              f"-- capacity the strawman baseline gave away for free)")
        print()
        print("  DIAGNOSIS -- why the tie rate is high:")
        print(f"    {n_unbound}/{n} ({100*n_unbound/n:.0f}%) feeders are UNBOUND: the fixed 3-10 kWp x{{K<=50}} "
              f"request set")
        print(f"    never forces a rejection, so every method accepts all K. The joint problem")
        print(f"    only exists on the {n - n_unbound} BOUND feeders; among those, real gains are")
        bound_rows = [r for r in ok if r["bound"] and r["classification"] != "search_infeasible"]
        if bound_rows:
            print(f"    {len(gains)}/{len(bound_rows)} ({100*len(gains)/len(bound_rows):.0f}% of bound feeders).")

    if args.save and ok:
        OUT.mkdir(exist_ok=True)
        path = OUT / "m22_honest_corpus.csv"
        fieldnames = sorted({k for r in rows for k in r.keys()})
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(rows)
        print(f"\nsaved {path}")


if __name__ == "__main__":
    main()
