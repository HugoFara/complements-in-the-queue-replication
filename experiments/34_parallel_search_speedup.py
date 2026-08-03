"""M10 of the joint-allocation roadmap: parallelize the constraint-oracle
evaluation NSGA-II pays for.

ROADMAP.md's "Hard parts / risks" section has, since M0, named the
constraint-oracle cost as the honest scaling risk: every candidate subset
costs one `pandapower.runpp` solve, and a GA over subsets is many solves
(1,640 for M1's EULV run, 900 for M3's ENWL run) -- the reason `pop_size`/
`n_gen` were sized down for the larger M3 feeder rather than left at M1's
defaults. `docs/paper.md`'s own Section 7 lists "cache/warm-start/parallelize
the constraint-oracle solves" as future-work item (1), the first of the four
items it names.

Two independent things landed this milestone:

- `AllocationProblem.evaluate_sizes` now memoizes by its normalized accept-
  set (`src/allocation/problem.py`) -- checked, not assumed, by instrumenting
  a real EULV `nsga2_search` run first: 7.4% of its 1,640 `runpp` calls were
  exact repeats of an already-evaluated accept-set (NSGA-II's own duplicate
  elimination only dedupes *within* one generation; an elite individual
  carries its cached fitness forward without re-evaluation, but crossover/
  mutation can still regenerate a genotype tried and discarded earlier).
- `nsga2_search`/`nsga2_search_fair`/`nsga2_search_continuous` all gained an
  `n_jobs` parameter (`src/allocation/search.py`) that parallelizes each
  generation's population evaluation across worker processes via pymoo's
  `StarmapParallelization`, with `n_jobs=1` (the default) reproducing the
  exact pre-M10 sequential code path bit-for-bit.

This experiment measures both, on the same two validation networks every
prior milestone's engine-level claims were checked on (EULV, the M3 ENWL
feeder), and -- the correctness question a speed optimization actually
needs answered -- verifies `n_jobs=1` and `n_jobs>1` return the identical
accept-set from the identical seed.

Run:  uv run python experiments/34_parallel_search_speedup.py
"""

from __future__ import annotations

import argparse
import os
import random
import time
from pathlib import Path

from gridsynth import import_opendss

from allocation import AllocationProblem, Request, nsga2_search

REPO = Path(__file__).resolve().parents[1]
CACHE = REPO / "cache" / "enwl_full"

PV_KW_LO, PV_KW_HI = 3.0, 10.0  # same band as experiments 24/25/26/28-33

NETWORKS = {
    "eulv": dict(master=REPO / "data" / "opendss" / "eulv" / "Master_snapshot.dss",
                 k=40, pop_size=40, n_gen=40),
    "enwl": dict(master=CACHE / "network_15" / "Feeder_4" / "Master_snapshot.dss",
                 k=100, pop_size=30, n_gen=30),
}


def build_requests(load_buses: list[int], k: int, seed: int) -> list[Request]:
    rng = random.Random(seed)
    buses = rng.sample(load_buses, k)
    return [Request(id=f"pv{i}", bus=bus, p_mw=-rng.uniform(PV_KW_LO, PV_KW_HI) / 1000.0, queue_order=i)
            for i, bus in enumerate(buses)]


def run_network(name: str, seed: int, n_jobs: int) -> None:
    cfg = NETWORKS[name]
    master = cfg["master"]
    if not master.exists():
        raise SystemExit(f"{master} not found -- for enwl, run experiments/28_m3_scale_report.py first")

    net = import_opendss(str(master))
    load_buses = net.load.bus.tolist()
    requests = build_requests(load_buses, cfg["k"], seed)

    print(f"== {name}: {cfg['k']} requests, pop_size={cfg['pop_size']}, n_gen={cfg['n_gen']} "
          f"({cfg['pop_size'] * cfg['n_gen']} solves) ==")

    problem_seq = AllocationProblem(net=net, requests=requests)
    t0 = time.time()
    sequential = nsga2_search(problem_seq, pop_size=cfg["pop_size"], n_gen=cfg["n_gen"], seed=seed, n_jobs=1)
    dt_seq = time.time() - t0
    seq_best = max((r for r in sequential if r.feasible), key=lambda r: r.accommodated_mw)
    nominal_solves = cfg["pop_size"] * cfg["n_gen"]
    distinct = len(problem_seq._cache)
    hits = nominal_solves - distinct  # within-search-phase repeats the cache caught
    print(f"  n_jobs=1:        {dt_seq:6.1f}s  best={seq_best.accommodated_mw*1000:7.1f} kW  "
          f"{distinct}/{nominal_solves} distinct accept-sets -> {hits} cache hits ({hits/nominal_solves*100:.1f}%)")

    problem_par = AllocationProblem(net=net, requests=requests)
    t0 = time.time()
    parallel = nsga2_search(problem_par, pop_size=cfg["pop_size"], n_gen=cfg["n_gen"], seed=seed, n_jobs=n_jobs)
    dt_par = time.time() - t0
    par_best = max((r for r in parallel if r.feasible), key=lambda r: r.accommodated_mw)
    print(f"  n_jobs={n_jobs:<9d} {dt_par:6.1f}s  best={par_best.accommodated_mw*1000:7.1f} kW  "
          f"cache entries={len(problem_par._cache)} (per-worker cache isn't shared back to the main process)")

    speedup = dt_seq / dt_par if dt_par > 0 else float("inf")
    identical = {r.accepted_ids for r in sequential} == {r.accepted_ids for r in parallel}
    print(f"  speedup: {speedup:.1f}x")
    print(f"  n_jobs=1 and n_jobs={n_jobs} returned the identical accept-set population: {identical}")
    if not identical:
        print("  ** MISMATCH ** -- parallelization changed the result; this would be a correctness bug.")
    print()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--network", choices=[*NETWORKS, "both"], default="both")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--n-jobs", type=int, default=os.cpu_count())
    args = ap.parse_args()

    print(f"M10: parallelizing NSGA-II's constraint-oracle evaluation ({os.cpu_count()} CPUs available, "
          f"n_jobs={args.n_jobs})\n")

    names = list(NETWORKS) if args.network == "both" else [args.network]
    for name in names:
        run_network(name, args.seed, args.n_jobs)


if __name__ == "__main__":
    main()
