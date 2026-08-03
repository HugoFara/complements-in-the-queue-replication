"""M31: does Ckt24's utility-scale positive result (M30) generalize to a second
real feeder, or was it a one-feeder fluke?

[[allocation-honest-baseline-pivot]] (docs/logs/ckt24.md): EPRI's Ckt24 beat the
honest FCFS baseline on 6/6 tested instances by the widest margins in the whole
project, closing (on one feeder) the "real utility-scale positive network" gap.
That is existence proof, not a base rate -- one feeder could be a fluke of its
own load-allocation unevenness. This experiment tests a SECOND real, utility-scale
EPRI feeder (Ckt7, a different location/topology/voltage class -- 115kV -> 12.47kV,
vs. Ckt24's 230kV -> 34.5kV) with the identical methodology, to see whether the
Ckt24 result repeats.

BASELINE FEASIBILITY -- ONE DISCLOSED EXCLUSION, SIMPLER THAN Ckt24 (full detail
in `data/opendss/ckt7/PROVENANCE.md`): no transformer is overloaded at Ckt7's
baseline (0/161, unlike Ckt24's 37/843) and there is no structural substation-level
overload needing a non-worsening rule (unlike Ckt24's substation transformer at
111%) -- Ckt7 needs neither of Ckt24's second/third adaptations. It needs only:
11 of 1,148 lines (~1%) are individually overloaded (115-238%) at baseline, all
small secondary "service" (`s_*`) drop lines -- the same EPRI allocation-factor-
vs-nameplate mismatch mechanism as Ckt24's transformers, manifesting on lines
instead. Traced via a line-graph traversal (remove the 11 bad lines, find each
one's downstream island): 657 of 906 loads by COUNT (fine-grained secondary
modeling means many buses per customer) but only 4.7 of 76.6 MW (~6.2%) by
magnitude -- comparable to Ckt24's 7.8% exclusion. Those loads' buses are dropped
from the candidate pool, and every line inside one of the 11 pockets is dropped
from the line-loading check (900 of 1,148 lines -- again a large COUNT from fine
secondary detail, not a large fraction of load). After exclusion the baseline is
exactly feasible: max remaining line loading 56.2%, max transformer loading
61.2%, vmin 0.962 pu.

SEARCH BUDGET -- M30's lesson applied proactively was NOT sufficient by itself,
a SECOND, DISTINCT budget dimension mattered here. Starting from M30's corrected
budget (pop=60/gen=100, one seeded search) was itself the mistake this time: on
Ckt7, that budget found only 2/6 instances positive, 4/6 at EXACTLY 0.0% gain --
not because M30's "scale pop/gen with gene count" lesson didn't apply (Ckt7's
gene count, 150-300, is the same or smaller than M30's), but because Ckt7's
fitness landscape turned out to be more seed-sensitive: a single seeded NSGA-II
run can land in a local optimum that exactly reproduces FIFO and never escapes it,
even at a much larger pop/gen budget. Confirmed directly (not assumed): re-running
the single most suspicious instance (MODERATE seed=0, exactly 0.0%) at pop=120/
gen=200 with 4 INDEPENDENT search-seed restarts found +49.9% on restart 0, but
restarts 1 and 3 found EXACTLY 0.0% again and restart 3 found an intermediate
+20.7% -- i.e. budget size alone (pop x gen) does not fix this; restart DIVERSITY
does. The full experiment was then re-run at pop=120/gen=200 (one restart), which
recovered gains on 4/6 instances but left 2/6 (both STRESS) at exactly 0.0% again
-- a second, targeted check (2 more independent search-seed restarts on just those
2 instances) recovered real gains there too (+40.5%, +9.8%). Net effect: **every
instance checked with more than one independent search-seed restart eventually
found a positive gain; none has been confirmed as a genuine, robust zero.** The 4
instances reported below from a single search-seed restart (all 3 MODERATE, one
STRESS) are therefore LOWER BOUNDS on their true achievable gain, not final
numbers -- flagged explicitly in the RESULT section below, not smoothed over.
CLI defaults (pop=120, gen=200, search-seeds=3) reflect the budget this analysis
now recommends, not the one that produced every number actually reported (mixed
seed-counts per instance, disclosed per-row).

PERFORMANCE: two changes, checked empirically before relying on them, neither
changes what is searched (same seed, same operators, same per-individual fitness),
only how fast a fixed evaluation sequence computes:
  - `check_connectivity=False` in `RUNPP_KWARGS`: pandapower's connectivity check
    re-verifies the network's topology is still connected on every `runpp` call --
    redundant here, since every trial adds only loads, never removes or
    reconnects anything, so topology never actually changes. Benchmarked on Ckt7
    (20-load trials, n=15): 85.3 -> 63.3 ms/eval, ~26% faster, all still converge.
    `bfsw` (backward/forward sweep, in principle cheaper than Newton-Raphson on a
    radial network) was also tried and REJECTED: it failed to converge at all on
    this network's near-zero-impedance connector stubs (0/15 in the same
    benchmark) -- not a free win here. `init="results"` warm-starting from the
    already-solved base network was tried too and was slightly SLOWER (77.0 ms),
    not faster -- also not used.
  - `n_jobs` (mirrors `allocation.search.nsga2_search`'s M10 pattern exactly:
    `multiprocessing.Pool` + pymoo's `StarmapParallelization`, one worker per
    process, each with its own `copy.deepcopy` of the network): parallelizes
    NSGA-II's one-`runpp`-per-individual evaluation across CPU cores. Checked
    before defaulting it on: this network pickles to ~2 MB, so even 16 parallel
    worker copies is ~30 MB total -- not a resource-usage concern on a 60 GB
    machine (the earlier project lesson to check RAM headroom before scaling
    worker pools, applied and cleared, not skipped).

REQUEST MODEL: identical to M30 for direct comparability -- PV export requests,
uniform [10, 100] kW, MODERATE (N=150) / STRESS (N=300) penetration scenarios,
buses sampled with replacement from the 199 candidate buses that remain after
exclusion (Ckt7's candidate pool is much smaller than Ckt24's ~3,650 -- a
meaningfully different network, not a rerun of the same one).

RESULT (2026-07-11, mixed search-seed counts per instance, disclosed per row --
`outputs/m31_ckt7_utility_scale.csv` holds the single-seed pass only, the extra
restarts on the two STRESS instances were ad hoc and are recorded here, not in
that file):

| Scenario | Seed 0 | Seed 1 | Seed 2 | search seeds tried |
|---|---:|---:|---:|---|
| MODERATE | +49.9% | +6.8% | +10.5% | 1 each -- LOWER BOUNDS |
| STRESS | +40.5% | +9.8% | +61.4% | 3, 3, 1 -- first two cross-validated |

**6/6 instances positive** -- every instance checked with more than one
independent search-seed restart eventually found a real gain (STRESS seed 0:
0.0% -> +23.7% -> +40.5%; STRESS seed 1: 0.0% -> 0.0% -> +9.8%), and no instance
has been confirmed as a genuine zero. This DOES generalize M30's Ckt24 finding --
a second real, utility-scale EPRI feeder also lands in the positive regime -- but
with a real, disclosed difference: Ckt24 was reliable with ONE seeded search once
the budget was scaled to gene count; Ckt7 needs BOTH a larger budget AND multiple
independent restarts to reliably surface its gain, a genuinely different (noisier)
fitness landscape. The 4 single-seed numbers above (all MODERATE, STRESS seed 2)
are honestly reported as lower bounds, not final figures -- work paused here
(2026-07-11, user had limited compute-time availability) with `--seeds 3` as the
documented next step, not a claim that 3 seeds was independently verified
sufficient (unlike M30, where 1 restart was shown sufficient after a direct check).

PERFORMANCE NOTE ON THIS PAUSE: the optimizations above (`check_connectivity=False`,
`n_jobs`) turned what was estimated as a 2-4 hour blocking wait into ~38 minutes
per confirmatory check -- the reason resuming this later at `--search-seeds 3` is
now a ~1-2 hour job, not a multi-hour one, on a machine with `n_jobs=6` (chosen to
leave the other 10 cores free, not `os.cpu_count()`, per explicit instruction not
to saturate a machine still needed for other work).
"""

from __future__ import annotations

import argparse
import copy
import multiprocessing
import random
import statistics
import time
from contextlib import nullcontext
from dataclasses import dataclass, field

import networkx as nx
import numpy as np
import pandapower as pp
import pandapower.topology as top
from pymoo.algorithms.moo.nsga2 import NSGA2
from pymoo.core.problem import ElementwiseProblem
from pymoo.operators.crossover.pntx import TwoPointCrossover
from pymoo.operators.mutation.bitflip import BitflipMutation
from pymoo.operators.sampling.rnd import BinaryRandomSampling
from pymoo.optimize import minimize
from pymoo.parallelization import StarmapParallelization

from gridsynth import import_opendss

VMIN_PU, VMAX_PU, LOADING_MAX_PCT = 0.94, 1.05, 100.0
RUNPP_KWARGS = dict(max_iteration=50, init="flat", tolerance_mva=1e-4, check_connectivity=False)
SCENARIOS = {"MODERATE": 150, "STRESS": 300}
REQ_LO_MW, REQ_HI_MW = 0.010, 0.100
MASTER_PATH = "data/opendss/ckt7/Master_ckt7.dss"


def _pool(n_jobs: int):
    """Mirrors `allocation.search._pool` exactly -- a no-op context for
    `n_jobs == 1` (the exact sequential code path), a `multiprocessing.Pool`
    otherwise (`n_jobs <= 0` uses `os.cpu_count()` workers)."""
    if n_jobs == 1:
        return nullcontext(None)
    return multiprocessing.Pool(processes=n_jobs if n_jobs > 0 else None)


def _runner(pool) -> StarmapParallelization | None:
    return StarmapParallelization(pool.starmap) if pool is not None else None


@dataclass
class Project:
    id: str
    bus: int
    p_mw: float  # negative: export


@dataclass
class NetworkContext:
    """Bundles the base network plus the one disclosed exclusion (module
    docstring) that `evaluate` needs on every call."""

    net: pp.pandapowerNet
    excluded_line_idx: list
    excluded_buses: set = field(default_factory=set)


def build_network() -> NetworkContext:
    """Import Ckt7, solve baseline, and compute the one disclosed exclusion --
    see module docstring and data/opendss/ckt7/PROVENANCE.md for why it's
    needed. Unlike Ckt24 (exp60), the overload here is on LINES (secondary
    service drops), not transformers, so the traversal removes the bad lines
    from the graph first, then finds each one's downstream island."""
    net = import_opendss(MASTER_PATH)
    pp.runpp(net, **RUNPP_KWARGS)

    lp = net.res_line.loading_percent
    bad_line_idx = list(lp[lp > 100].index)

    g = top.create_nxgraph(net, include_trafos=True, include_lines=True, include_impedances=False)
    for idx in bad_line_idx:
        fb, tb = net.line.at[idx, "from_bus"], net.line.at[idx, "to_bus"]
        if g.has_edge(fb, tb):
            g.remove_edge(fb, tb)

    src = net.ext_grid.bus.iloc[0]
    excluded_buses: set[int] = set()
    for idx in bad_line_idx:
        tb = net.line.at[idx, "to_bus"]
        comp = nx.node_connected_component(g, tb) if tb in g else {tb}
        if src not in comp:
            excluded_buses |= comp

    excluded_line_idx = [
        i for i, row in net.line.iterrows()
        if row["from_bus"] in excluded_buses or row["to_bus"] in excluded_buses
    ]

    return NetworkContext(net=net, excluded_line_idx=excluded_line_idx, excluded_buses=excluded_buses)


def build_projects(ctx: NetworkContext, k, seed) -> list[Project]:
    candidate_buses = [b for b in ctx.net.load.bus.unique() if b not in ctx.excluded_buses]
    rng = random.Random(seed)
    projects = []
    for i in range(k):
        bus = rng.choice(candidate_buses)
        p_kw = rng.uniform(REQ_LO_MW, REQ_HI_MW) * 1000
        projects.append(Project(id=f"p{i}", bus=bus, p_mw=-p_kw / 1000))
    return projects


def evaluate(ctx: NetworkContext, projects, accepted_ids: set[str]) -> tuple[bool, float, float]:
    """Custom oracle (not `allocation.AllocationProblem`, since the excluded
    pocket needs the line-drop rule documented in the module docstring). Returns
    (feasible, accommodated_mw, violation_magnitude) -- violation is the soft
    penalty the NSGA-II search below optimizes against, 0.0 exactly when
    feasible."""
    net = copy.deepcopy(ctx.net)
    by_id = {p.id: p for p in projects}
    for rid in accepted_ids:
        p = by_id[rid]
        pp.create_load(net, bus=p.bus, p_mw=p.p_mw, q_mvar=0.0, name=rid)

    try:
        pp.runpp(net, **RUNPP_KWARGS)
    except pp.LoadflowNotConverged:
        return False, 0.0, float("inf")

    vmin = float(net.res_bus.vm_pu.min())
    vmax = float(net.res_bus.vm_pu.max())
    other_line_pct = net.res_line.loading_percent.drop(ctx.excluded_line_idx)
    line_pct = float(other_line_pct.max()) if len(other_line_pct) else 0.0
    trafo_pct = float(net.res_trafo.loading_percent.max()) if len(net.trafo) else 0.0

    over_v = max(0.0, vmax - VMAX_PU)
    under_v = max(0.0, VMIN_PU - vmin)
    over_line = max(0.0, line_pct - LOADING_MAX_PCT)
    over_trafo = max(0.0, trafo_pct - LOADING_MAX_PCT)

    violation = over_v + under_v + over_line + over_trafo
    feasible = violation == 0.0
    accommodated_mw = sum(abs(by_id[rid].p_mw) for rid in accepted_ids)
    return feasible, accommodated_mw, violation


def skip_continue_baseline(ctx: NetworkContext, projects) -> tuple[set[str], float]:
    """The honest FCFS comparator (allocation.baseline.skip_continue_baseline's
    logic, reimplemented locally per this project's convention of self-contained
    experiment scripts): walk the queue in order, skip an infeasible request and
    keep going, never stop dead."""
    accepted: set[str] = set()
    accepted_mw = 0.0
    for p in projects:
        trial = accepted | {p.id}
        ok, mw, _ = evaluate(ctx, projects, trial)
        if ok:
            accepted, accepted_mw = trial, mw
    return accepted, accepted_mw


class _Ckt7SubsetProblem(ElementwiseProblem):
    def __init__(self, ctx: NetworkContext, projects, elementwise_runner=None):
        kwargs = {"elementwise_runner": elementwise_runner} if elementwise_runner is not None else {}
        super().__init__(n_var=len(projects), n_obj=2, xl=0, xu=1, vtype=bool, **kwargs)
        self.ctx = ctx
        self.projects = projects

    def _evaluate(self, x, out, *args, **kwargs):
        accepted = {p.id for p, bit in zip(self.projects, x) if bit}
        _, mw, violation = evaluate(self.ctx, self.projects, accepted)
        out["F"] = [-mw, violation]


def joint_search(ctx: NetworkContext, projects, pop, gen, seed,
                  seed_ids: set[str] | None = None, n_jobs: int = 1) -> tuple[set[str], float]:
    """Seeded NSGA-II -- the M28/M30 lesson, applied from the start this time
    (see module docstring's SEARCH BUDGET note). `n_jobs` parallelizes across
    CPU cores (module docstring's PERFORMANCE note); `n_jobs=1` (default) is
    the exact sequential path, bit-identical to before this option existed."""
    with _pool(n_jobs) as pool:
        problem = _Ckt7SubsetProblem(ctx, projects, elementwise_runner=_runner(pool))
        n_var = len(projects)
        if seed_ids is not None:
            rng = np.random.default_rng(seed)
            x0 = rng.random((pop, n_var)) < 0.5
            x0[0] = [p.id in seed_ids for p in projects]
            sampling = x0
        else:
            sampling = BinaryRandomSampling()
        algorithm = NSGA2(pop_size=pop, sampling=sampling,
                           crossover=TwoPointCrossover(), mutation=BitflipMutation(),
                           eliminate_duplicates=True)
        res = minimize(problem, algorithm, ("n_gen", gen), seed=seed, verbose=False)
    best_mw, best_ids = 0.0, set()
    for x in res.pop.get("X"):
        accepted = {p.id for p, bit in zip(projects, x) if bit}
        ok, mw, _ = evaluate(ctx, projects, accepted)
        if ok and mw > best_mw:
            best_mw, best_ids = mw, accepted
    return best_ids, best_mw


def run_instance(ctx: NetworkContext, scenario, seed, pop, gen, search_seeds, n_jobs: int = 1) -> dict:
    k = SCENARIOS[scenario]
    projects = build_projects(ctx, k, seed)
    t0 = time.time()
    fifo_ids, fifo_mw = skip_continue_baseline(ctx, projects)

    best_mw = 0.0
    for s in range(search_seeds):
        _, mw = joint_search(ctx, projects, pop, gen, seed=s, seed_ids=fifo_ids, n_jobs=n_jobs)
        best_mw = max(best_mw, mw)

    gain_pct = (best_mw / fifo_mw - 1) * 100 if fifo_mw > 0 else float("nan")
    return dict(scenario=scenario, seed=seed, fifo_kw=fifo_mw * 1000, fifo_n=len(fifo_ids),
                joint_kw=best_mw * 1000, gain_pct=gain_pct, dt=time.time() - t0)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seeds", type=int, default=3, help="instance draws per scenario")
    ap.add_argument("--pop", type=int, default=120)
    ap.add_argument("--gen", type=int, default=200)
    ap.add_argument("--search-seeds", type=int, default=3,
                     help="NSGA-II restarts per instance; best kept -- 3 is this analysis's recommendation, "
                          "not yet independently verified sufficient the way M30's 1 restart was")
    ap.add_argument("--n-jobs", type=int, default=6,
                     help="parallel workers for NSGA-II (<=0: os.cpu_count()) -- default leaves other cores free")
    ap.add_argument("--save", action="store_true")
    args = ap.parse_args()

    print("M31: Ckt7 (real 115kV->12.47kV, 1,255-bus, 76.6MW feeder) -- does M30's Ckt24 result generalize?\n")
    print("Importing + diagnosing Ckt7...")
    ctx = build_network()
    print(f"  excluded lines (pre-broken service-drop pockets): {len(ctx.excluded_line_idx)}")
    print(f"  excluded buses (their downstream loads): {len(ctx.excluded_buses)}")
    n_candidates = len([b for b in ctx.net.load.bus.unique() if b not in ctx.excluded_buses])
    print(f"  remaining candidate buses: {n_candidates}\n")

    ok0, _, viol0 = evaluate(ctx, [], set())
    print(f"  empty accept-set: feasible={ok0} (violation={viol0:.4f})")
    assert ok0, "baseline still infeasible after exclusion -- fix before trusting any result below"
    print()

    rows = []
    for scenario in SCENARIOS:
        gains = []
        for seed in range(args.seeds):
            row = run_instance(ctx, scenario, seed, args.pop, args.gen, args.search_seeds, n_jobs=args.n_jobs)
            rows.append(row)
            gains.append(row["gain_pct"])
            print(f"  {scenario:8s} seed={seed}  FCFS={row['fifo_kw']:8.1f}kW({row['fifo_n']:3d})  "
                  f"joint={row['joint_kw']:8.1f}kW  gain={row['gain_pct']:+6.1f}%  {row['dt']:.0f}s")
        print(f"    -> median {statistics.median(gains):+.1f}% (range {min(gains):+.1f}% to {max(gains):+.1f}%)\n")

    if args.save and rows:
        import csv
        from pathlib import Path
        out = Path(__file__).resolve().parents[1] / "outputs"
        out.mkdir(exist_ok=True)
        path = out / "m31_ckt7_utility_scale.csv"
        fields = sorted({k for r in rows for k in r.keys()})
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader(); w.writerows(rows)
        print(f"saved {path}")


if __name__ == "__main__":
    main()
