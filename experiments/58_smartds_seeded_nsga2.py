"""M28: does experiment 57's SMART-DS "joint loses 0/5" result survive a warm start?

Experiment 57 found `nsga2_search_dss` ties or loses to `skip_continue_baseline_dss`
on 5 size-spanning SMART-DS feeders (median -6%), and flagged an ambiguity it
could not separate: is that because skip-continue's ~95%-seated allocation IS
close to the true joint ceiling, or because `nsga2_search_dss`'s
`BinaryRandomSampling()` starts BLIND -- on a feeder with thousands of candidate
buses, a population of 20 random bitstrings has no realistic chance of
resembling a good accept-set, so a 20-generation budget can run out before the
search rediscovers what skip-continue already knows for free?

This experiment resolves it, directly. `nsga2_search_dss_seeded`
(src/allocation/search.py, M28) warm-starts generation 0 with the skip-continue
and stop-dead accept-sets themselves, at the EXACT SAME budget experiment 57
used (pop=20, gen=20), on the SAME 5 feeders and SAME request draws (identical
screening, k, and seed -- this file duplicates exp57's `screen_feasible`,
`candidate_attach_points`, and `build_requests` verbatim rather than importing
them, per this project's convention of self-contained numbered experiments).

Because NSGA-II is elitist, the seeded search's final population can never do
worse than the best of its own seeds (see
`test_nsga2_search_dss_seeded_never_worse_than_the_seed`). So:
  - if seeding still loses/ties on all 5 feeders, that is now REAL evidence of
    "no exploitable room" -- the search-budget confound is ruled out.
  - if seeding flips any feeder to a genuine win, experiment 57's "0/5" was
    partly a search-budget artifact, and real capacity exists in the joint
    search space that blind NSGA-II simply never found.

Run:  uv run python experiments/58_smartds_seeded_nsga2.py [--k 40] [--save]

Note: running all 5 feeders in one long-lived process OOM-killed at ~55 GB RSS
on the largest feeder (opendssdirect's per-evaluation `NewContext()` appears to
accumulate memory across the ~8,000 sequential evaluations two searches x 5
feeders costs here) -- rerun the largest feeder alone with `--only <name>` if
that happens; results are unaffected (`skip_continue_baseline_dss` /
`nsga2_search_dss(_seeded)` are pure functions of the request draw, so a fresh
process reproduces the exact same numbers, confirmed against experiment 57's
already-published unseeded figures).
"""

from __future__ import annotations

import argparse
import csv
import glob
import os
import random
import statistics
import time
from pathlib import Path

import opendssdirect as dss_base

from allocation import (
    AllocationProblemDSS,
    DSSRequest,
    greedy_baseline_dss,
    nsga2_search_dss,
    nsga2_search_dss_seeded,
    skip_continue_baseline_dss,
)

REPO = Path(__file__).resolve().parents[1]
CACHE = REPO / "cache" / "smartds"
OUT = REPO / "outputs"

KW_LO, KW_HI = 6.0, 17.0  # matches experiment 57/40's EV/demand band


def screen_feasible(masters: list[str]) -> list[str]:
    """Verbatim from experiment 57: keep only baseline voltage-feasible feeders."""
    feasible = []
    for m in masters:
        name = os.path.basename(os.path.dirname(m))
        try:
            base = AllocationProblemDSS(master_path=m, requests=[], max_loading_percent=1e6).evaluate([])
            if base.feasible:
                feasible.append(m)
        except Exception:
            pass
    return feasible


def candidate_attach_points(master_path: str, exclude: set[str]) -> list[tuple[str, int]]:
    """Verbatim from experiment 57."""
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
    """Verbatim from experiment 57 -- same RNG, same draw, same request set."""
    rng = random.Random(seed)
    sample = rng.sample(points, min(k, len(points)))
    return [DSSRequest(id=f"ev{i}", bus=b, phase=ph, p_kw=rng.uniform(KW_LO, KW_HI), queue_order=i)
            for i, (b, ph) in enumerate(sample)]


def pick_nsga_subset(feasible: list[str], n: int) -> set[str]:
    """Verbatim selection logic from experiment 57's `main`: size-spanning by
    Lines.dss file size, evenly spaced indices -- reproduces the SAME 5
    feeders exp57 ran NSGA-II on, as long as the same `feasible` list and `n`
    are passed (both are, by construction, here)."""
    sized = sorted(feasible, key=lambda m: os.path.getsize(str(Path(m).parent / "Lines.dss"))
                   if (Path(m).parent / "Lines.dss").exists() else 0)
    idxs = sorted(set(int(round(i)) for i in
                      [j * (len(sized) - 1) / max(n - 1, 1) for j in range(n)]))
    return {sized[i] for i in idxs}


def run_feeder(master: str, k: int, seed: int, pop: int, gen: int) -> dict:
    name = os.path.basename(os.path.dirname(master))
    t0 = time.time()
    source_bus = f"{name.split('--')[1]}-{name.split('--')[0]}x"
    points = candidate_attach_points(master, {source_bus})
    requests = build_requests(points, k, seed)
    problem = AllocationProblemDSS(master_path=master, requests=requests, max_loading_percent=1e6)

    stop_dead = greedy_baseline_dss(problem)
    skip = skip_continue_baseline_dss(problem)
    skip_kw = skip.accommodated_mw * 1000

    unseeded = nsga2_search_dss(problem, pop_size=pop, n_gen=gen, seed=seed)
    unseeded_feas = [r for r in unseeded if r.feasible]
    unseeded_joint = max(unseeded_feas, key=lambda r: r.accommodated_mw) if unseeded_feas else None
    unseeded_kw = unseeded_joint.accommodated_mw * 1000 if unseeded_joint else float("nan")

    seeded = nsga2_search_dss_seeded(
        problem, seed_accept_sets=[skip.accepted_ids, stop_dead.accepted_ids],
        pop_size=pop, n_gen=gen, seed=seed)
    seeded_feas = [r for r in seeded if r.feasible]
    seeded_joint = max(seeded_feas, key=lambda r: r.accommodated_mw) if seeded_feas else None
    seeded_kw = seeded_joint.accommodated_mw * 1000 if seeded_joint else float("nan")

    return dict(
        name=name, k=k, skip_kw=skip_kw, skip_acc=len(skip.accepted_ids),
        unseeded_kw=unseeded_kw, unseeded_vs_skip_pct=(unseeded_kw / skip_kw - 1) * 100 if skip_kw else float("nan"),
        seeded_kw=seeded_kw, seeded_vs_skip_pct=(seeded_kw / skip_kw - 1) * 100 if skip_kw else float("nan"),
        seeded_vs_unseeded_pct=(seeded_kw / unseeded_kw - 1) * 100 if unseeded_kw == unseeded_kw and unseeded_kw > 0 else float("nan"),
        dt=time.time() - t0,
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--k", type=int, default=40)
    ap.add_argument("--pop", type=int, default=20)
    ap.add_argument("--gen", type=int, default=20)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--nsga-subset", type=int, default=5)
    ap.add_argument("--only", type=str, default=None,
                     help="run a single feeder whose name contains this substring "
                          "(isolates it in its own process -- OpenDSS's per-evaluation "
                          "NewContext appears to accumulate memory across thousands of "
                          "sequential evals in one long-lived process; running the "
                          "largest feeder alone avoids the OOM that hit the combined run)")
    ap.add_argument("--save", action="store_true")
    args = ap.parse_args()

    masters = sorted(glob.glob(str(CACHE / "uhs*" / "Master.dss")))
    print(f"M28: does seeding NSGA-II's generation 0 change experiment 57's SMART-DS 0/5 result?\n")
    feasible = screen_feasible(masters)
    nsga_masters = pick_nsga_subset(feasible, args.nsga_subset)
    if args.only:
        nsga_masters = {m for m in nsga_masters if args.only in m}
    print(f"{len(feasible)} baseline-feasible feeders; re-running {len(nsga_masters)} of the SAME "
          f"{args.nsga_subset} size-spanning feeders exp57 used, at the SAME budget "
          f"(pop={args.pop}, gen={args.gen}, K={args.k})\n")

    rows = []
    t_all = time.time()
    for m in sorted(nsga_masters):
        row = run_feeder(m, args.k, args.seed, args.pop, args.gen)
        rows.append(row)
        print(f"  {row['name']:24s} skip={row['skip_kw']:7.1f}({row['skip_acc']:2d}/{row['k']})  "
              f"unseeded={row['unseeded_kw']:7.1f} ({row['unseeded_vs_skip_pct']:+5.1f}% vs skip)  "
              f"seeded={row['seeded_kw']:7.1f} ({row['seeded_vs_skip_pct']:+5.1f}% vs skip, "
              f"{row['seeded_vs_unseeded_pct']:+5.1f}% vs unseeded)  {row['dt']:.0f}s")

    print(f"\ntotal {time.time()-t_all:.0f}s\n" + "=" * 70)
    n = len(rows)
    n_unseeded_beat = sum(1 for r in rows if r["unseeded_vs_skip_pct"] > 0.5)
    n_seeded_beat = sum(1 for r in rows if r["seeded_vs_skip_pct"] > 0.5)
    n_seeding_helped = sum(1 for r in rows if r["seeded_vs_unseeded_pct"] == r["seeded_vs_unseeded_pct"]
                            and r["seeded_vs_unseeded_pct"] > 0.5)
    n_seeding_ever_worse = sum(1 for r in rows if r["seeded_vs_skip_pct"] < -1e-6)

    print(f"unseeded (exp57 reproduction): beats skip-continue on {n_unseeded_beat}/{n}, "
          f"median {statistics.median(r['unseeded_vs_skip_pct'] for r in rows):+.1f}%")
    print(f"seeded (M28):                  beats skip-continue on {n_seeded_beat}/{n}, "
          f"median {statistics.median(r['seeded_vs_skip_pct'] for r in rows):+.1f}%")
    print(f"seeding ever WORSE than skip-continue's own value: {n_seeding_ever_worse}/{n} "
          f"(should be 0 -- elitism guarantee; a violation means a bug, not a finding)")
    print(f"seeding beat the unseeded search itself on {n_seeding_helped}/{n} feeders\n")

    if n_seeded_beat == 0:
        print("VERDICT: seeding did not change the outcome. The search-budget confound is ruled")
        print("out -- skip-continue really is at (or past) the joint ceiling on these feeders.")
        print("Experiment 57's finding 2 is now search-INDEPENDENT, not just search-observed.")
    else:
        print(f"VERDICT: seeding FLIPPED {n_seeded_beat} feeder(s) to a real win over skip-continue.")
        print("Experiment 57's '0/5' was partly a search-budget artifact: real capacity exists in")
        print("the joint search space that blind random-initialized NSGA-II never found.")

    if args.save and rows:
        OUT.mkdir(exist_ok=True)
        path = OUT / "m28_smartds_seeded_nsga2.csv"
        allfields = sorted({k for r in rows for k in r.keys()})
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=allfields)
            w.writeheader(); w.writerows(rows)
        print(f"\nsaved {path}")


if __name__ == "__main__":
    main()
