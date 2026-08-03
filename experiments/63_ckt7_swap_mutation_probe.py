"""M33 (probe): does teaching NSGA-II the swap move close the gap that
experiments/62 (docs/logs/ckt7.md, M32) found -- a structurally simpler
swap-aware hill-climber beating M31's NSGA-II numbers on 6/6 Ckt7 instances,
by 2-14.6x on 5/6, using LESS compute than a single NSGA-II restart?

SUPERSEDED by M35: the GLDF-MILP retires NSGA-II regardless of its operators,
so this whole line (M32-M34) is a documented dead end. See docs/logs/ckt7.md.

MOTIVATION
----------
exp62 root-caused M31's noise to the search OPERATOR, not the landscape:
pymoo's stock TwoPointCrossover + BitflipMutation don't encode the "drop one
accepted request, add one rejected request" move this capacity-constrained
subset problem needs, and a hill-climber that has that move wins decisively.
Two ways to act on that:
  (a) teach NSGA-II the missing move (a custom mutation operator) and keep
      the existing multi-objective GA framework -- needed elsewhere anyway,
      `src/allocation/search.py`'s fairness variants are genuinely n_obj=3;
  (b) abandon NSGA-II for subset-selection problems, adopt a leaner
      heuristic (the exp62 hill-climber itself, or simulated annealing/
      iterated local search built the same way).

(a) is the cheaper hypothesis to test first: it isolates ONE variable
(mutation operator) while holding everything else (problem class,
TwoPointCrossover, population dynamics, selection) fixed. If a swap-aware
mutation operator alone closes most of the gap, NSGA-II is salvageable with
a small, targeted change. If it doesn't -- if the GA still underperforms
even with the right move available to it -- that is real evidence the
population/recombination dynamics themselves are the deeper problem, and (b)
becomes well-justified rather than a guess.

METHOD
------
`SwapMutation`: a per-individual mutation operator mirroring exp62's
`_sample_move` exactly -- with probability `swap_prob` (default 0.6, same
ratio exp62 used), pick one accepted (1) gene and one rejected (0) gene and
flip both together (a paired trade, cardinality-preserving); otherwise
toggle a single random gene (falls back to bitflip behavior, and is also
the only option when a row is all-1s or all-0s). Dropped into the exact
same `_Ckt7SubsetProblem` / `NSGA2` / `TwoPointCrossover` pipeline as
exp61 -- only `mutation=` changes.

Run head-to-head against stock `BitflipMutation` at the SAME (small) budget,
same seed, same instance, so any gap is attributable to the operator alone.

THIS IS A CHEAP PROBE, NOT A FULL RUN -- explicit user request ("short test
for now, more than full long runs"). Defaults (pop=40, gen=40, one search
seed) are well below exp61's recommended budget (pop=120, gen=200, 3 search
seeds) and below exp62's per-restart budget (1,500 evals ~= pop=40 x gen=40
= 1,600 evals here, so roughly one hill-climb restart's worth of compute).
Default instances are MODERATE seed=2 (largest observed LS/NSGA2 gap, 14.6x)
and STRESS seed=2 (smallest, 1.08x) -- the two ends of exp62's observed
range, chosen to be maximally informative from just 2 instances.

Reference numbers below (NSGA2_GAIN_PCT, LS_GAIN_PCT) are read verbatim from
exp61's `outputs/m31_ckt7_utility_scale.csv` / exp62's
`outputs/m32_ckt7_local_search_ceiling.csv` -- NOT recomputed here, just
printed alongside for direct comparison.

RESULT (2026-07-11, two escalating short probes, both well below exp61's
recommended budget -- explicit user request to keep this as short tests, not
a full re-run):

Probe 1 (pop=30, gen=30, 1 search seed, 900 evals/operator/instance): BOTH
operators landed at exactly +0.0% on both instances. Uninformative on its
own -- consistent with exp61's own note that single-seed short budgets often
land exactly at 0.0% on Ckt7 regardless of operator, i.e. 900 evals is likely
below the threshold where a single NSGA-II population can move off the
FCFS-seeded individual at all.

Probe 2 (pop=40, gen=60, 2 search seeds, 4,800 evals/operator/instance --
still ~5-15x less compute than exp61's 24,000-72,000): a clean, controlled
result, same seed/instance/everything except the mutation operator:

| Instance | bitflip | swap | ref: NSGA2 (M31, full budget) | ref: hill-climb (M32) |
|---|---:|---:|---:|---:|
| MODERATE seed=2 | +0.0% | **+3.2%** | +10.5% | +153.3% |
| STRESS seed=2   | +0.0% | **+29.4%** | +61.4% | +66.4% |

Bitflip stayed stuck exactly at the FCFS seed on both instances even with
more budget/restarts than probe 1. Swap moved off it both times -- on
STRESS:2 it already recovered about half of NSGA-II's own full-budget,
multi-restart result, using roughly a tenth of the compute.

CONCLUSION: this answers the motivating question directionally -- teach
NSGA-II the missing move, don't abandon it for a different heuristic. With
the mutation operator isolated as the only variable, swap converts "stuck at
zero" into real gains at a fraction of the usual compute, which is strong
evidence for "missing move" over "GA machinery is the wrong tool here."
It has NOT yet been shown to close the gap to the hill-climber's ceiling or
even match NSGA-II's own full-budget numbers -- swap has only been given a
fraction of the budget exp61 uses, and this was two short, uncommitted
probes on 2 of Ckt7's 6 instances, not a real re-run. Written up as-is, per
explicit user scoping decision, without further budget escalation or wiring
this operator into exp61/M31 for a full 6-instance re-run -- that remains
the natural next step if this resumes.

ESCALATION (M34, 2026-07-11, same day): user asked to escalate. Rather than
committing to the full 6-instance re-run in one shot, ran the natural
intermediate step first -- the SAME 2 instances (MODERATE:2, STRESS:2), but
at exp61's own full recommended budget (pop=120, gen=200, search-seeds=3 --
`--instances MODERATE:2,STRESS:2 --pop 120 --gen 200 --search-seeds 3
--n-jobs 6 --save`), 72,000 evals/operator/instance, ~15x probe 2's compute.
Machine had ample headroom (16 cores, 22GB free) for the ~1.5-2h run.

| Instance | bitflip (full budget) | swap (full budget) | delta | NSGA2 ref (M31, disclosed lower bound) | hill-climb ref (M32) |
|---|---:|---:|---:|---:|---:|
| MODERATE seed=2 | +54.3% | +72.5% | +18.2pp | +10.5% (1 restart) | +153.3% |
| STRESS seed=2   | +84.3% | +91.5% | +7.2pp  | +61.4% (1 restart) | +66.4% |

Two findings, one CORRECTING the probes, one CONFIRMING them:

1. The probes' "bitflip stuck at exactly 0%" story was itself a
   restart-budget artifact, not a property of the stock operator. At the
   full 3-restart budget bitflip alone reaches +54.3% / +84.3% -- consistent
   with M31's own disclosure that these two instances' reported numbers
   (+10.5%, +61.4%) were single-restart lower bounds, now confirmed directly
   rather than inferred.
2. The swap-vs-bitflip comparison survives escalation to full budget: with
   restart budget held EQUAL, swap still beats bitflip by +18.2pp and
   +7.2pp -- a real, reproducible operator effect layered on top of restart
   diversity, not a restatement of "just add restarts". On STRESS:2,
   swap-NSGA-II's +91.5% now EXCEEDS the swap-aware hill-climber's own
   reported ceiling (+66.4%, M32) -- the first result anywhere in this
   project where population-based search beats the simpler hill-climber
   outright. Does not generalize to MODERATE:2, where swap-NSGA-II (+72.5%)
   still trails the hill-climber's +153.3% by a wide margin.

Honest scope limits, unchanged in kind from the probes: still only 2 of
Ckt7's 6 instances (same seeds already probed, no new instance coverage);
both operators used identical 3-restart budgets so operator-vs-restart-
diversity is disentangled at only this one budget size. Full 6-instance
re-run at matched budget was offered as a scope option and explicitly not
taken this round -- still the natural next step if this resumes.

BOTTOM LINE: strengthens the pilot's directional finding while correcting
its framing. Not "bitflip is stuck at zero, swap fixes it" (a restart-budget
artifact) but "given equal, adequate restart budget, swap adds a real,
positive, reproducible margin on top of what bitflip alone finds (+7 to
+18pp on the 2 instances tested), enough on one of them to make NSGA-II beat
the previously-best method (the hill-climber) outright." Raw data:
`outputs/m33_ckt7_swap_mutation_probe.csv` (overwritten with these numbers;
probe 2's numbers are preserved in
`outputs/m33_ckt7_swap_mutation_probe.probe2.csv.bak` and in this docstring/
docs/logs/ckt7.md M33).
"""

from __future__ import annotations

import argparse
import copy
import multiprocessing
import random
import time
from contextlib import nullcontext
from dataclasses import dataclass, field

import networkx as nx
import numpy as np
import pandapower as pp
import pandapower.topology as top
from pymoo.algorithms.moo.nsga2 import NSGA2
from pymoo.core.mutation import Mutation
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

# exp61 (M31, stock NSGA-II) / exp62 (M32, swap-aware hill-climber) reported
# gains -- printed for reference, not recomputed.
NSGA2_GAIN_PCT = {
    ("MODERATE", 0): 49.9, ("MODERATE", 1): 6.8, ("MODERATE", 2): 10.5,
    ("STRESS", 0): 40.5, ("STRESS", 1): 9.8, ("STRESS", 2): 61.4,
}
LS_GAIN_PCT = {
    ("MODERATE", 0): 154.7, ("MODERATE", 1): 17.9, ("MODERATE", 2): 153.3,
    ("STRESS", 0): 163.8, ("STRESS", 1): 19.6, ("STRESS", 2): 66.4,
}


def _pool(n_jobs: int):
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
    net: pp.pandapowerNet
    excluded_line_idx: list
    excluded_buses: set = field(default_factory=set)


def build_network() -> NetworkContext:
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
    accepted: set[str] = set()
    accepted_mw = 0.0
    for p in projects:
        trial = accepted | {p.id}
        ok, mw, _ = evaluate(ctx, projects, trial)
        if ok:
            accepted, accepted_mw = trial, mw
    return accepted, accepted_mw


class SwapMutation(Mutation):
    """Mirrors exp62's `_sample_move` as a pymoo mutation operator: with
    probability `swap_prob`, trade one accepted (1) gene for one rejected
    (0) gene in a single paired move; otherwise toggle one random gene.
    Falls back to toggle when a row has no 1s or no 0s to trade."""

    def __init__(self, prob=1.0, swap_prob: float = 0.6, **kwargs):
        super().__init__(prob=prob, **kwargs)
        self.swap_prob = swap_prob

    def _do(self, problem, X, random_state=None, **kwargs):
        Xp = np.copy(X)
        for row in Xp:
            ones = np.flatnonzero(row)
            zeros = np.flatnonzero(~row)
            if random_state.random() < self.swap_prob and len(ones) and len(zeros):
                r = ones[random_state.integers(len(ones))]
                a = zeros[random_state.integers(len(zeros))]
                row[r] = False
                row[a] = True
            else:
                j = random_state.integers(len(row))
                row[j] = ~row[j]
        return Xp


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


def joint_search(ctx: NetworkContext, projects, pop, gen, seed, mutation: Mutation,
                  seed_ids: set[str] | None = None, n_jobs: int = 1) -> tuple[set[str], float]:
    """Identical to exp61's `joint_search` except `mutation` is now a
    parameter instead of a hardcoded `BitflipMutation()`."""
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
                           crossover=TwoPointCrossover(), mutation=mutation,
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
    fifo_ids, fifo_mw = skip_continue_baseline(ctx, projects)

    row = dict(scenario=scenario, seed=seed, fifo_kw=fifo_mw * 1000, fifo_n=len(fifo_ids),
               nsga2_ref_pct=NSGA2_GAIN_PCT.get((scenario, seed), float("nan")),
               ls_ref_pct=LS_GAIN_PCT.get((scenario, seed), float("nan")))

    for name, mutation in [("bitflip", BitflipMutation()), ("swap", SwapMutation())]:
        t0 = time.time()
        best_mw = 0.0
        for s in range(search_seeds):
            _, mw = joint_search(ctx, projects, pop, gen, seed=s, mutation=mutation,
                                  seed_ids=fifo_ids, n_jobs=n_jobs)
            best_mw = max(best_mw, mw)
        gain_pct = (best_mw / fifo_mw - 1) * 100 if fifo_mw > 0 else float("nan")
        row[f"{name}_kw"] = best_mw * 1000
        row[f"{name}_gain_pct"] = gain_pct
        row[f"{name}_dt"] = time.time() - t0

    return row


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--instances", default="MODERATE:2,STRESS:2",
                     help="comma-separated scenario:seed pairs -- default is the largest and "
                          "smallest exp62 LS/NSGA2 gap, most informative from just 2 instances")
    ap.add_argument("--pop", type=int, default=40, help="small on purpose -- this is a probe")
    ap.add_argument("--gen", type=int, default=40, help="small on purpose -- this is a probe")
    ap.add_argument("--search-seeds", type=int, default=1)
    ap.add_argument("--n-jobs", type=int, default=6,
                     help="parallel workers (<=0: os.cpu_count()) -- default leaves other cores free")
    ap.add_argument("--save", action="store_true")
    args = ap.parse_args()

    instances = []
    for tok in args.instances.split(","):
        scenario, seed = tok.split(":")
        instances.append((scenario.strip(), int(seed)))

    print("M33 probe: does a swap-aware NSGA-II mutation operator close exp62's gap?\n")
    print(f"budget: pop={args.pop} gen={args.gen} search_seeds={args.search_seeds} "
          f"({args.pop * args.gen * args.search_seeds} evals/operator/instance)\n")
    print("Importing Ckt7...")
    ctx = build_network()
    ok0, _, viol0 = evaluate(ctx, [], set())
    assert ok0, "baseline still infeasible after exclusion"
    print("  ok\n")

    rows = []
    for scenario, seed in instances:
        row = run_instance(ctx, scenario, seed, args.pop, args.gen, args.search_seeds, n_jobs=args.n_jobs)
        rows.append(row)
        print(f"{scenario:8s} seed={seed}  FCFS={row['fifo_kw']:8.1f}kW  "
              f"[reference: NSGA2(M31)={row['nsga2_ref_pct']:+6.1f}%  hill-climb(M32)={row['ls_ref_pct']:+6.1f}%]")
        print(f"  bitflip  gain={row['bitflip_gain_pct']:+6.1f}%  ({row['bitflip_dt']:.0f}s)")
        print(f"  swap     gain={row['swap_gain_pct']:+6.1f}%  ({row['swap_dt']:.0f}s)")
        delta = row['swap_gain_pct'] - row['bitflip_gain_pct']
        print(f"  -> swap operator delta at this budget: {delta:+.1f}pp\n")

    if args.save and rows:
        import csv
        from pathlib import Path
        out = Path(__file__).resolve().parents[1] / "outputs"
        out.mkdir(exist_ok=True)
        path = out / "m33_ckt7_swap_mutation_probe.csv"
        fields = sorted({k for r in rows for k in r.keys()})
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader(); w.writerows(rows)
        print(f"saved {path}")


if __name__ == "__main__":
    main()
