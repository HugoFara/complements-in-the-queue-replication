"""M30: an honest FCFS-vs-joint-search stress test on a real, utility-scale feeder.

[[allocation-honest-baseline-pivot]] (docs/logs/smartds.md) left one gap open after
SMART-DS came back 0/5 positive: a real feeder at genuine utility scale (hundreds
to thousands of buses) that is ALSO strongly coupled enough to land in the
"joint search beats FCFS" regime. EULV/ENWL (this project's other validation
networks) are real but small (<5,000 buses, <1 MW). SMART-DS is utility-scale but
came back negative on every feeder tried. This experiment tests EPRI's Ckt24 --
one of EPRI's "Green Circuits", a real 34.5 kV feeder serving 3,891 customers,
~50.9 MW peak declared load, 6,058 buses after import -- the largest real network
in this project's corpus by a wide margin (bigger than any single SMART-DS feeder
tried in exp57/58).

BASELINE FEASIBILITY, THREE DISCLOSED ADAPTATIONS (full detail in
`data/opendss/ckt24/PROVENANCE.md`), the first two decided with Hugo before this
experiment was built, the third a mechanical extension of the same rule found
necessary while smoke-testing:

1. 37 of 843 transformers (4.4%) are individually overloaded (101-310%) on Ckt24's
   own declared load, serving 241 loads (~7.8% of total load) -- the same
   allocation-factor-vs-nameplate artifact already documented for EPRI's Ckt5
   (data/opendss/ckt5/PROVENANCE.md). Those 241 loads' buses are EXCLUDED from the
   candidate request pool, AND the 37 transformers themselves are dropped from the
   "every other transformer <=100%" feasibility check -- their overload has
   nothing to do with which new requests get accepted (their downstream buses are
   never offered as candidates), so leaving them in the check would make every
   accept-set infeasible regardless of what's actually being tested. Caught by a
   smoke test: FCFS accepted 0/N requests, ~210 pp violation on every single-
   request trial, independent of request size.
2. The substation transformer itself (45 MVA) is at 111.06% loading on the full
   declared peak (~51 MVA implied demand vs. 45 MVA rated) -- a modest, structural
   overload consistent with this being a real feeder EPRI selected because it runs
   close to capacity, not a severe break like the SMART-DS Greensboro feeder or
   Ckt5 (which hit vmin 0.887 pu / 188-206% loading and were ruled out entirely).
   Checked directly: PV EXPORT requests relieve this loading (111.06% -> 108.77%
   after 1 MW of exports spread over 200 sites), so accepting requests moves this
   element the RIGHT direction, unlike a thermal violation a request could worsen.
   Requires the substation transformer to stay AT OR BELOW its own baseline
   loading (111.06%) rather than the network's normal 100% cap.
3. One secondary-service line (100.15% loaded at baseline) turned out to sit
   immediately downstream of one of the 37 already-excluded transformers -- the
   same pocket, manifesting on a line segment instead of the transformer. Found
   by the same smoke-testing process as #1 (empty accept-set came back with a
   small nonzero violation instead of the expected 0.0). Every line whose
   endpoints fall inside an excluded transformer's downstream island is dropped
   from the line-loading check for the same reason as #1 -- a mechanical
   extension of the same already-approved exclusion, not a new judgment call.

Every OTHER line/transformer/bus in the network keeps the normal <=100% loading /
0.94-1.05 pu voltage-band rule, unchanged.

SEARCH BUDGET / SEEDING -- TWO ROUNDS, THE SECOND CAUGHT BY THE FIRST RESULT LOOKING
SUSPICIOUS, NOT ASSUMED CORRECT: per the M28 lesson (`nsga2_search_dss_seeded`),
generation 0 is seeded with skip-continue's own accept-set, so NSGA-II elitism
guarantees any measured gain is real, not a blind-initialization artifact. That
alone was NOT enough here. A first pass used pop=30/gen=40, one search seed, for
BOTH the 150-gene MODERATE and 300-gene STRESS scenarios -- and STRESS came back
suspiciously close to 0% gain on every instance (+0.1% median) while MODERATE, on
the same underlying candidate pool (STRESS's requests are MODERATE's first 150 plus
150 more -- same `random.Random(seed)` draws, so directly comparable), found real
gains (+10.4% median, up to +60.7%). Superset logic makes this a red flag: any
accept-set achievable in MODERATE's 150-candidate space is also achievable in
STRESS's 300-candidate space (just leave the extra 150 unselected), so STRESS's
TRUE optimum can only be >= MODERATE's, never less -- a fixed per-instance budget
that doesn't scale with gene count dilutes exploration on the larger space, exactly
the mechanism M7's continuous-NSGA-II negative result already documented once
(`docs/joint-allocation-scope.md`). Tested directly rather than assumed: re-ran the
single most suspicious instance (STRESS seed=1, which had found EXACTLY 0.0% gain)
at pop=60/gen=100 x 4 restarts -- gain jumped to +149.9% on the very first restart
(the other 3 restarts found nothing better, confirming one restart at this larger
budget is already enough, not merely lucky repetition). The whole experiment was
then re-run at pop=60/gen=100, ONE search seed, uniformly for both scenarios --
this module's CLI defaults now match that corrected budget, not the original
under-sized pass.

REQUEST MODEL: PV export requests (p_mw negative), uniform size in [10, 100] kW --
a small-commercial/large-residential mix plausible for a real 34.5 kV MV feeder
(EULV/ENWL's 3-10 kWp residential band would need thousands of requests to reach
a stress regime on a feeder this size). Buses sampled with replacement (N can
exceed the ~3,650 valid candidate buses) from every load bus NOT in the excluded
set. Two penetration scenarios, mirroring the Fontinele head-to-head's I3/I4
naming convention: MODERATE (N=150, ~8.25 MW requested) and STRESS (N=300,
~16.5 MW requested) -- both were smoke-tested to confirm they actually bind
(STRESS hits vmax=1.134 pu and 98.8% line loading at full-accept, well past the
1.05 pu / 100% limits).

RESULT (pop=60/gen=100, 3 seeds/scenario, `outputs/m30_ckt24_utility_scale.csv`):
6/6 instances positive, every one -- MODERATE +23.9%/+168.0%/+88.5% (median
+88.5%), STRESS +25.1%/+149.9%/+8.7% (median +25.1%). The largest, most
consistent joint-search-beats-FCFS margin found anywhere in this project, on
the largest real network in its corpus. Read alongside SMART-DS's 0/5 (M28)
and Fontinele's 33/69-bus win (M29): network SIZE alone doesn't predict which
regime a feeder lands in -- Ckt24's real, EPRI-allocated load is documented
(PROVENANCE.md) to be genuinely UNEVEN across the feeder (the same allocation-
factor-vs-nameplate mismatch that produced the 37 excluded transformers), so
headroom is unevenly distributed across candidate buses in a way a synthetic,
more uniformly-provisioned SMART-DS feeder apparently is not -- a plausible
mechanism, not yet independently confirmed on a second real, heterogeneous,
utility-scale feeder.
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
MASTER_PATH = "data/opendss/ckt24/master_ckt24.dss"


def _pool(n_jobs: int):
    """Mirrors `allocation.search._pool` exactly -- a no-op context for
    `n_jobs == 1` (the exact sequential code path), a `multiprocessing.Pool`
    otherwise (`n_jobs <= 0` uses `os.cpu_count()` workers). See exp61's module
    docstring PERFORMANCE note for the benchmark and RAM-headroom check behind
    this -- same network family, same conclusion applies here."""
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
    """Bundles the base network plus the three disclosed exclusions/adaptations
    (module docstring) that `evaluate` needs on every call."""

    net: pp.pandapowerNet
    sub_idx: int
    sub_baseline_pct: float
    bad_trafo_idx: list
    excluded_line_idx: list
    excluded_buses: set = field(default_factory=set)


def build_network() -> NetworkContext:
    """Import Ckt24, solve baseline, and compute the three disclosed exclusions
    -- see module docstring and data/opendss/ckt24/PROVENANCE.md for why each
    is needed."""
    net = import_opendss(MASTER_PATH)
    pp.runpp(net, **RUNPP_KWARGS)

    sub_idx = net.trafo[net.trafo.name == "subxfmr"].index[0]
    sub_baseline_pct = float(net.res_trafo.at[sub_idx, "loading_percent"])

    g = top.create_nxgraph(net, include_trafos=False, include_lines=True, include_impedances=False)
    lp = net.res_trafo.loading_percent
    bad_trafo_idx = [i for i in lp[lp > 100].index if i != sub_idx]
    excluded_buses: set[int] = set()
    for idx in bad_trafo_idx:
        lv_bus = net.trafo.at[idx, "lv_bus"]
        comp = nx.node_connected_component(g, lv_bus) if lv_bus in g else {lv_bus}
        excluded_buses |= comp

    excluded_line_idx = [
        i for i, row in net.line.iterrows()
        if row["from_bus"] in excluded_buses or row["to_bus"] in excluded_buses
    ]

    return NetworkContext(net=net, sub_idx=sub_idx, sub_baseline_pct=sub_baseline_pct,
                           bad_trafo_idx=bad_trafo_idx, excluded_line_idx=excluded_line_idx,
                           excluded_buses=excluded_buses)


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
    """Custom oracle (not `allocation.AllocationProblem`, since the substation
    transformer and the excluded pocket need the non-uniform rules documented in
    the module docstring). Returns (feasible, accommodated_mw, violation_magnitude)
    -- violation is the soft penalty the NSGA-II search below optimizes against,
    0.0 exactly when feasible."""
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
    other_trafo_pct = net.res_trafo.loading_percent.drop([ctx.sub_idx, *ctx.bad_trafo_idx])
    other_trafo_max = float(other_trafo_pct.max()) if len(other_trafo_pct) else 0.0
    sub_pct = float(net.res_trafo.at[ctx.sub_idx, "loading_percent"])

    over_v = max(0.0, vmax - VMAX_PU)
    under_v = max(0.0, VMIN_PU - vmin)
    over_line = max(0.0, line_pct - LOADING_MAX_PCT)
    over_other_trafo = max(0.0, other_trafo_max - LOADING_MAX_PCT)
    over_sub_trafo = max(0.0, sub_pct - ctx.sub_baseline_pct)

    violation = over_v + under_v + over_line + over_other_trafo + over_sub_trafo
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


class _Ckt24SubsetProblem(ElementwiseProblem):
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
    """Seeded NSGA-II -- see module docstring's SEARCH BUDGET / SEEDING note.
    `n_jobs` parallelizes across CPU cores (see exp61's PERFORMANCE note);
    `n_jobs=1` (default) is the exact sequential path used for this module's
    own reported M30 results."""
    with _pool(n_jobs) as pool:
        problem = _Ckt24SubsetProblem(ctx, projects, elementwise_runner=_runner(pool))
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
    ap.add_argument("--pop", type=int, default=60)
    ap.add_argument("--gen", type=int, default=100)
    ap.add_argument("--search-seeds", type=int, default=1, help="NSGA-II restarts per instance; best kept")
    ap.add_argument("--n-jobs", type=int, default=1, help="parallel workers for NSGA-II (<=0: os.cpu_count())")
    ap.add_argument("--save", action="store_true")
    args = ap.parse_args()

    print("M30: Ckt24 (real 34.5kV, 6,058-bus, 50.9MW feeder) -- honest FCFS vs. seeded joint search\n")
    print("Importing + diagnosing Ckt24...")
    ctx = build_network()
    print(f"  substation baseline loading: {ctx.sub_baseline_pct:.2f}% (non-worsening cap for this element)")
    print(f"  pre-broken small transformers (excluded both ways): {len(ctx.bad_trafo_idx)}")
    print(f"  excluded lines (same pockets): {len(ctx.excluded_line_idx)}")
    print(f"  excluded buses (their downstream loads): {len(ctx.excluded_buses)}\n")

    # Sanity check: the empty accept-set must be exactly feasible after all
    # three exclusions, or a later "0/N accepted" result would be ambiguous
    # between "genuinely no room" and "still-broken baseline" (exactly the
    # failure mode the first two smoke tests caught).
    ok0, _, viol0 = evaluate(ctx, [], set())
    print(f"  empty accept-set: feasible={ok0} (violation={viol0:.4f})")
    assert ok0, "baseline still infeasible after exclusions -- fix before trusting any result below"
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
        path = out / "m30_ckt24_utility_scale.csv"
        fields = sorted({k for r in rows for k in r.keys()})
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader(); w.writerows(rows)
        print(f"saved {path}")


if __name__ == "__main__":
    main()
