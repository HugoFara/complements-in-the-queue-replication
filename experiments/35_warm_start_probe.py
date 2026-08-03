"""M11 of the joint-allocation roadmap: investigate warm-starting the AC solve.

ROADMAP.md's "Hard parts / risks" section has, since M0, suggested three
things for the constraint-oracle cost: "cache converged states, warm-start,
and parallelize the evaluation across workers." M5 sidestepped the whole
question on radial networks with a MILP; M10 did the other two -- caching
repeated accept-sets and parallelizing NSGA-II's per-generation evaluation
across worker processes. That left one piece of the original suggestion
untouched: warm-starting `pandapower.runpp`'s own Newton-Raphson solve, i.e.
seeding each candidate's initial voltage guess from an already-converged
*nearby* state (`init="results"`) instead of the default flat 1.0-pu start.

This experiment checks that idea directly, three ways, on both validation
networks (EULV, the M3 ENWL feeder):

1. Iteration count at realistic candidate sizes -- does a warm start actually
   need fewer Newton-Raphson iterations than a flat start?
2. A near-neighbor case -- warm-starting a "child" candidate (a handful of
   requests swapped) from its "parent"'s already-solved state, the situation
   NSGA-II's own mutation/crossover actually produces every generation.
3. A rigorously ordering-controlled wall-clock A/B benchmark -- alternating
   which variant runs first each trial, since an earlier, non-alternating
   version of this probe showed a spurious ~2x "warm" advantage on EULV that
   disappeared once trial order was controlled for (a cold-cache/ordering
   artifact, not a real effect -- see docs/joint-allocation-scope.md's M11).

Run:  uv run python experiments/35_warm_start_probe.py
"""

from __future__ import annotations

import copy
import random
import statistics as st
import time
from pathlib import Path

import pandapower as pp

from gridsynth import import_opendss

REPO = Path(__file__).resolve().parents[1]
CACHE = REPO / "cache" / "enwl_full"

PV_KW_LO, PV_KW_HI = 3.0, 10.0  # same band as experiments 24/25/26/28-34

NETWORKS = {
    "eulv": dict(master=REPO / "data" / "opendss" / "eulv" / "Master_snapshot.dss", k=40),
    "enwl": dict(master=CACHE / "network_15" / "Feeder_4" / "Master_snapshot.dss", k=50),
}


def make_candidate(base_net, load_buses, seed: int, k: int):
    rng = random.Random(seed)
    buses = rng.sample(load_buses, k)
    net = copy.deepcopy(base_net)
    for i, bus in enumerate(buses):
        pp.create_load(net, bus=bus, p_mw=-rng.uniform(PV_KW_LO, PV_KW_HI) / 1000.0, q_mvar=0.0, name=f"r{i}")
    return net


def probe_iteration_counts(name: str, master: Path, k: int) -> None:
    net_unsolved = import_opendss(str(master))
    net_solved = copy.deepcopy(net_unsolved)
    pp.runpp(net_solved)
    load_buses = net_unsolved.load.bus.tolist()

    print(f"== {name}: iteration count, flat vs. warm start, at growing candidate size ==")
    for size in (1, k // 2, k):
        warm = make_candidate(net_solved, load_buses, seed=0, k=size)
        pp.runpp(warm, init="results")
        flat = make_candidate(net_unsolved, load_buses, seed=0, k=size)
        pp.runpp(flat, init="flat")
        print(f"  {size:3d} requests: warm={warm['_ppc']['iterations']} iters   "
              f"flat={flat['_ppc']['iterations']} iters")

    # Near-neighbor case: a "child" that differs from an already-solved
    # "parent" by only 2 of 40 requests -- what NSGA-II mutation/crossover
    # actually produces, unlike the "0 requests -> k requests" case above.
    # parent = first 40 of a fixed 50-request pool, deterministic by seed.
    rng = random.Random(1)
    pool_buses = rng.sample(load_buses, min(50, len(load_buses)))
    pool_sizes = [rng.uniform(PV_KW_LO, PV_KW_HI) / 1000.0 for _ in pool_buses]

    def build(base_net, ids):
        n = copy.deepcopy(base_net)
        for i in ids:
            pp.create_load(n, bus=pool_buses[i], p_mw=-pool_sizes[i], q_mvar=0.0, name=f"r{i}")
        return n

    n_parent = min(40, len(pool_buses) - 2)
    parent_ids = list(range(n_parent))
    parent = build(net_unsolved, parent_ids)
    pp.runpp(parent, init="flat")
    child_ids = parent_ids[2:] + [n_parent, n_parent + 1]  # drop 2, add 2

    child_from_parent = build(parent, child_ids)
    pp.runpp(child_from_parent, init="results")
    child_flat = build(net_unsolved, child_ids)
    pp.runpp(child_flat, init="flat")
    print(f"  near-neighbor child (2-of-{n_parent} requests swapped from a solved parent): "
          f"warm={child_from_parent['_ppc']['iterations']} iters   flat={child_flat['_ppc']['iterations']} iters")
    print()


def probe_wall_clock(name: str, master: Path, k: int, n_trials: int = 30) -> None:
    net_unsolved = import_opendss(str(master))
    net_solved = copy.deepcopy(net_unsolved)
    pp.runpp(net_solved)
    load_buses = net_unsolved.load.bus.tolist()

    warm_times, flat_times = [], []
    for seed in range(n_trials):
        # Alternate which variant runs first each trial -- an earlier,
        # non-alternating version of this probe showed a spurious ~2x "warm"
        # advantage that vanished once ordering bias was controlled for.
        order = (0, 1) if seed % 2 == 0 else (1, 0)
        for which in order:
            if which == 0:
                n = make_candidate(net_solved, load_buses, seed, k)
                t0 = time.perf_counter(); pp.runpp(n, init="results"); warm_times.append(time.perf_counter() - t0)
            else:
                n = make_candidate(net_unsolved, load_buses, seed, k)
                t0 = time.perf_counter(); pp.runpp(n, init="flat"); flat_times.append(time.perf_counter() - t0)

    print(f"== {name}: wall-clock A/B, k={k}, n_trials={n_trials}, order alternated per trial ==")
    print(f"  warm: mean={st.mean(warm_times) * 1000:6.2f}ms  median={st.median(warm_times) * 1000:6.2f}ms  "
          f"stdev={st.stdev(warm_times) * 1000:6.2f}ms")
    print(f"  flat: mean={st.mean(flat_times) * 1000:6.2f}ms  median={st.median(flat_times) * 1000:6.2f}ms  "
          f"stdev={st.stdev(flat_times) * 1000:6.2f}ms")
    print(f"  speedup (mean flat/warm): {st.mean(flat_times) / st.mean(warm_times):.2f}x")
    print()


def main() -> None:
    print("M11: does warm-starting runpp's AC solve pay off on these networks?\n")
    for name, cfg in NETWORKS.items():
        if not cfg["master"].exists():
            raise SystemExit(f"{cfg['master']} not found -- for enwl, run experiments/28_m3_scale_report.py first")
        probe_iteration_counts(name, cfg["master"], cfg["k"])
    for name, cfg in NETWORKS.items():
        probe_wall_clock(name, cfg["master"], cfg["k"])


if __name__ == "__main__":
    main()
