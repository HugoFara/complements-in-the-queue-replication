"""M27: the regime map on a voltage-limited US corpus -- and the strawman collapse.

This experiment set out to find the "joint helps" half of the regime map on the
corpus the M22-M26 rule points at: SMART-DS urban-suburban feeders, genuinely
North-American (single-phase laterals) and voltage-limited (long laterals
clearing the 0.94 pu floor only thinly). It found something more important, and
more deflationary.

Two search-INDEPENDENT findings (they compare two deterministic baselines, so no
metaheuristic budget confound):

1. **The strawman collapse.** Experiments 39/40 reported the project's largest
   margins (+192%, +1367%) using `greedy_baseline_dss`, which STOPS DEAD at the
   first infeasible request -- and on these feeders that first block is often a
   single individually-infeasible early request that freezes the whole queue
   (e.g. udt14717: stop-dead seats 2/40). Against the honest
   `skip_continue_baseline_dss` (skip that one, keep walking), the SAME feeder
   seats ~38/40. The spectacular margins were measuring joint-vs-strawman; they
   collapse against a real FCFS baseline.

2. **Skip-continue is near-optimal on large real feeders.** On these 700-5,000-bus
   feeders, demand requests spread across thousands of buses barely interact, so
   skip-continue seats ~84% of all requested kW just by skipping the few that
   don't fit. That leaves little "which subset" room for a joint search -- the
   opposite of EULV, a small feeder where export requests globally saturate the
   voltage and skip-continue seats only ~20%. Voltage-limiting is necessary but
   not sufficient for a joint advantage; strong request coupling (small or
   globally-saturated feeders) is what actually creates it.

`nsga2_search_dss` (the convex/MILP route is IEEE13-only on this oracle) is run
on a size-spanning subset only, at experiment 40's sequential budget. It ties or
LOSES to skip-continue here -- partly a real "little joint room" effect (skip
already seats 84%, so the ceiling is close), partly NSGA-II under-searching at
this budget (an honest limitation: this oracle has no fast optimal search to rule
the second out). The strawman-collapse finding (1) does NOT depend on search
quality.

Feeders are fetched once from OEDI (anonymous S3) into cache/smartds/ and screened
for baseline voltage-feasibility (thermal excluded -- SMART-DS's snapshot already
exceeds a minority of its own line ratings, per experiment 40).

Run:  uv run python experiments/57_smartds_voltage_regime.py [--k 40] [--nsga-subset 5] [--save]
"""

from __future__ import annotations

import argparse
import csv
import glob
import os
import random
import time
from pathlib import Path

import opendssdirect as dss_base

from allocation import (
    AllocationProblemDSS,
    DSSRequest,
    greedy_baseline_dss,
    nsga2_search_dss,
    skip_continue_baseline_dss,
)

REPO = Path(__file__).resolve().parents[1]
CACHE = REPO / "cache" / "smartds"
OUT = REPO / "outputs"

KW_LO, KW_HI = 6.0, 17.0   # EV/demand band (matches experiment 40): positive p_kw = undervoltage stress
TIE_TOL_KW = 1e-2


def screen_feasible(masters: list[str]) -> list[str]:
    """Keep only the baseline voltage-feasible feeders (empty subset feasible,
    thermal excluded)."""
    feasible = []
    for m in masters:
        name = os.path.basename(os.path.dirname(m))
        try:
            base = AllocationProblemDSS(master_path=m, requests=[], max_loading_percent=1e6).evaluate([])
            if base.feasible:
                feasible.append(m)
                print(f"  {name:24s} vmin={base.vmin_pu:.4f}  FEASIBLE")
            else:
                print(f"  {name:24s} vmin={base.vmin_pu:.4f}  infeasible (excluded)")
        except Exception as e:
            print(f"  {name:24s} ERROR {str(e)[:50]}")
    return feasible


def candidate_attach_points(master_path: str, exclude: set[str]) -> list[tuple[str, int]]:
    ctx = dss_base.NewContext()
    ctx.Command("Clear")
    ctx.Command(f'Redirect "{master_path}"')
    ctx.Solution.Solve()
    points = []
    for name in ctx.Circuit.AllBusNames():
        if name in exclude:
            continue
        ctx.Circuit.SetActiveBus(name)
        for node in ctx.Bus.Nodes():
            points.append((name, node))
    return points


def build_requests(points, k, seed) -> list[DSSRequest]:
    rng = random.Random(seed)
    sample = rng.sample(points, min(k, len(points)))
    return [DSSRequest(id=f"ev{i}", bus=b, phase=ph, p_kw=rng.uniform(KW_LO, KW_HI), queue_order=i)
            for i, (b, ph) in enumerate(sample)]


def run_feeder(master: str, k: int, seed: int, pop: int, gen: int, run_nsga: bool) -> dict:
    name = os.path.basename(os.path.dirname(master))
    t0 = time.time()
    source_bus = f"{name.split('--')[1]}-{name.split('--')[0]}x"  # circuit's own source bus
    points = candidate_attach_points(master, {source_bus})
    n_bus = 0  # informational: number of distinct buses touched (attach-point buses)
    requests = build_requests(points, k, seed)
    problem = AllocationProblemDSS(master_path=master, requests=requests, max_loading_percent=1e6)
    total_req_kw = sum(abs(r.p_kw) for r in requests)

    # search-INDEPENDENT: the two deterministic FCFS baselines
    stop_dead = greedy_baseline_dss(problem)
    skip = skip_continue_baseline_dss(problem)
    stop_kw = stop_dead.accommodated_mw * 1000
    skip_kw = skip.accommodated_mw * 1000
    n_skip = len(skip.accepted_ids)
    bound = n_skip < k

    # the strawman collapse: how much of the old +192%/+1367%-style margin was
    # really stop-dead giving away capacity a real FCFS walk keeps.
    strawman_pct = (skip_kw / stop_kw - 1) * 100 if stop_kw > 0 else float("inf")

    row = dict(name=name, k=k, total_req_kw=total_req_kw, n_attach=len(points),
               stop_dead_kw=stop_kw, stop_dead_acc=len(stop_dead.accepted_ids),
               skip_kw=skip_kw, skip_acc=n_skip, bound=bound,
               skip_seats_frac=skip_kw / total_req_kw if total_req_kw else float("nan"),
               strawman_collapse_pct=strawman_pct,
               req_over_skip=total_req_kw / skip_kw if skip_kw > 0 else float("inf"))

    if run_nsga:
        results = nsga2_search_dss(problem, pop_size=pop, n_gen=gen, seed=seed)
        feas = [r for r in results if r.feasible]
        joint = max(feas, key=lambda r: r.accommodated_mw) if feas else None
        joint_kw = joint.accommodated_mw * 1000 if joint else float("nan")
        gain = joint_kw - skip_kw if joint else float("nan")
        row.update(joint_kw=joint_kw, joint_acc=len(joint.accepted_ids) if joint else 0,
                   joint_vs_skip_kw=gain,
                   joint_vs_skip_pct=(gain / skip_kw * 100) if joint and skip_kw > 0 else float("nan"))
    row["dt"] = time.time() - t0
    return row


def main() -> None:
    import statistics
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--k", type=int, default=40)
    ap.add_argument("--pop", type=int, default=20)
    ap.add_argument("--gen", type=int, default=20)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--nsga-subset", type=int, default=5,
                     help="run nsga2_search_dss on this many size-spanning feeders (0 = none)")
    ap.add_argument("--max-feeders", type=int, default=0)
    ap.add_argument("--save", action="store_true")
    args = ap.parse_args()

    masters = sorted(glob.glob(str(CACHE / "uhs*" / "Master.dss")))
    print(f"M27: voltage-limited SMART-DS regime map -- screening {len(masters)} urban-suburban feeders\n")
    feasible = screen_feasible(masters)
    if args.max_feeders:
        feasible = feasible[: args.max_feeders]

    # choose the NSGA-II subset: spread across the feeder-size (attach-point) range
    nsga_masters: set[str] = set()
    if args.nsga_subset:
        sized = sorted(feasible, key=lambda m: os.path.getsize(str(Path(m).parent / "Lines.dss"))
                       if (Path(m).parent / "Lines.dss").exists() else 0)
        idxs = sorted(set(int(round(i)) for i in
                          [j * (len(sized) - 1) / max(args.nsga_subset - 1, 1) for j in range(args.nsga_subset)]))
        nsga_masters = {sized[i] for i in idxs}

    print(f"\n{len(feasible)} baseline-feasible voltage-limited feeders; stop-dead vs skip-continue on all,"
          f" nsga2_search_dss on {len(nsga_masters)} (K={args.k})\n")

    rows = []
    t_all = time.time()
    for m in feasible:
        row = run_feeder(m, args.k, args.seed, args.pop, args.gen, run_nsga=(m in nsga_masters))
        rows.append(row)
        jstr = (f"  joint={row['joint_kw']:7.1f}({row['joint_acc']:2d}) "
                f"vs-skip={row['joint_vs_skip_pct']:+5.1f}%") if "joint_kw" in row else ""
        print(f"  {row['name']:24s} stop-dead={row['stop_dead_kw']:6.1f}({row['stop_dead_acc']:2d})  "
              f"skip={row['skip_kw']:7.1f}({row['skip_acc']:2d}/{row['k']})  "
              f"strawman-collapse={row['strawman_collapse_pct']:+7.0f}%  "
              f"skip-seats={row['skip_seats_frac']*100:3.0f}%{jstr}  {row['dt']:.0f}s")

    print(f"\ntotal {time.time()-t_all:.0f}s\n" + "=" * 70)
    n = len(rows)
    n_bound = sum(1 for r in rows if r["bound"])
    collapses = [r["strawman_collapse_pct"] for r in rows if r["strawman_collapse_pct"] != float("inf")]
    print("FINDING 1 -- the strawman collapse (search-INDEPENDENT: stop-dead vs skip-continue):")
    print(f"  On {n} voltage-limited US feeders, switching the old stop-dead baseline to an honest")
    print(f"  skip-continue FCFS walk recovers a median +{statistics.median(collapses):.0f}% more capacity")
    print(f"  (range +{min(collapses):.0f}% to +{max(collapses):.0f}%). The +192%/+1367% margins")
    print(f"  experiments 39/40 reported were measuring joint-vs-STRAWMAN; against a real FCFS")
    print(f"  baseline that capacity was never actually lost. skip-continue seats a median")
    print(f"  {statistics.median(r['skip_seats_frac'] for r in rows)*100:.0f}% of all requested kW.")

    nsga_rows = [r for r in rows if "joint_kw" in r]
    if nsga_rows:
        gains = [r["joint_vs_skip_pct"] for r in nsga_rows if r["joint_kw"] == r["joint_kw"]]
        n_beat = sum(1 for g in gains if g > 0.5)
        print(f"\nFINDING 2 -- joint (NSGA-II) does NOT beat honest FCFS on these large feeders:")
        print(f"  on {len(nsga_rows)} size-spanning feeders, joint beats skip-continue on {n_beat}/{len(nsga_rows)};")
        print(f"  median joint-vs-skip = {statistics.median(gains):+.1f}%. Skip-continue already seats ~"
              f"{statistics.median(r['skip_seats_frac'] for r in nsga_rows)*100:.0f}% of requested kW, so the")
        print(f"  joint ceiling is close; and NSGA-II under-searches at this budget (no fast optimal")
        print(f"  route exists for this oracle). Either way: no demonstrable joint advantage here.")
    print(f"\n  INTERPRETATION: voltage-limiting is necessary but NOT sufficient for a joint advantage.")
    print(f"  It needs strong request COUPLING (small/globally-saturated feeders, e.g. EULV +46%),")
    print(f"  which these large real feeders -- demand spread over thousands of buses -- do not provide.")

    if args.save and rows:
        OUT.mkdir(exist_ok=True)
        path = OUT / "m27_smartds_voltage_regime.csv"
        allfields = sorted({k for r in rows for k in r.keys()})
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=allfields)
            w.writeheader(); w.writerows(rows)
        print(f"\nsaved {path}")


if __name__ == "__main__":
    main()
