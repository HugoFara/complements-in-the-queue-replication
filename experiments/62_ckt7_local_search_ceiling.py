"""M32: is NSGA-II leaving room on the table on Ckt7, or is M31's noisy landscape
real regardless of algorithm?

Context ([[allocation-honest-baseline-pivot]] sixth check, exp61, DECISION.md
Sec. 4e): a single seeded NSGA-II restart on Ckt7 sometimes lands exactly on the
FCFS point and never escapes it, even at a large pop/gen budget -- fixed only by
trying multiple independent restarts. That is consistent with two very different
explanations: (a) NSGA-II's crossover/elitism machinery has a specific weakness on
this subset-selection encoding (premature convergence, weak diversity), or (b) the
fitness landscape genuinely has many deep local optima and ANY search method would
need many independent tries to escape them. Only (a) is really "NSGA-II is
brittle"; (b) would mean more restarts of *anything* keeps helping, which is a
property of the problem, not the algorithm.

METHOD: a structurally unrelated search -- randomized batch hill-climbing, no
crossover, no population, no GA machinery at all -- run with many independent
restarts (mix of random-init and one FCFS-seeded, mirroring NSGA-II's own
seeding) on the SAME instances (same `build_projects` seed) and the SAME
`evaluate` oracle as exp61, so results are directly comparable gain-for-gain.
Each restart does first-improving-batch bit-flip local search: shuffle all N
project indices, evaluate a batch of `n_jobs` single-bit flips in parallel,
accept the best one in that batch if it improves on the current (feasibility-
first, then higher accepted MW) state, else move to the next batch; a full pass
over all N indices with no accepted move means a local optimum -- restart.

This is deliberately a much simpler/cheaper method than NSGA-II. If it finds
comparable-or-better gains for comparable-or-less compute, that's evidence NSGA-II
itself is the bottleneck (interpretation (a)), and a smarter/more diverse search
operator would likely do even better than either. If cumulative best-found keeps
climbing across dozens of restarts with no sign of flattening, that's evidence
that "more compute helps" is a property of the LANDSCAPE (interpretation (b)),
not something specific to NSGA-II -- in which case chasing more restarts of any
method has a real, currently-uncharacterized ceiling.

This is explicitly a CHEAP PROBE, not a rerun of exp61's already-reported numbers:
budget is intentionally kept far below exp61's `--pop 120 --gen 200 --search-seeds
3` recommendation, sized instead to fit a run of well under an hour so it doesn't
repeat the earlier "commit to a multi-hour run blind" mistake.

RESULT (2026-07-11, `--restarts 10 --max-evals 1500 --n-jobs 6`, all 6 exp61
instances, same seeds -- FCFS baselines matched exp61's CSV exactly, confirming
these are the identical instances, not a redraw): the toggle-only version (no
swap move) reproduced NSGA-II's exact failure mode -- flat 0.0% across all
restarts on the one instance tried, never finding a single improving move from
the FCFS-seeded start. Adding the swap move (drop one accepted, add one
rejected -- see `_sample_move`) changed the outcome completely:

| Scenario/seed | FCFS kW | NSGA-II gain (exp61, best-of-N restarts) | Local-search gain (best-of-10) | Ratio | Restart curve |
|---|---:|---:|---:|---:|---|
| MODERATE 0 | 336.5 | +49.9% (1 restart) | **+154.7%** | 3.1x | climbs to restart 4, flat after |
| MODERATE 1 | 780.3 | +6.8% (1 restart) | **+17.9%** | 2.6x | climbs to restart 6, flat after |
| MODERATE 2 | 351.6 | +10.5% (1 restart) | **+153.3%** | 14.6x | climbs to restart 8, flat after |
| STRESS 0 | 413.2 | +40.5% (3 restarts) | **+163.8%** | 4.0x | flat from restart 1 (first try) |
| STRESS 1 | 798.5 | +9.8% (3 restarts) | **+19.6%** | 2.0x | flat from restart 1 (first try) |
| STRESS 2 | 497.6 | +61.4% (1 restart) | **+66.4%** | 1.1x | flat from restart 1 (first try) |

**6/6 instances: local search beats NSGA-II**, by 2-14.6x on 5/6 and marginally
(1.1x) on the one instance (STRESS seed=2) where NSGA-II already found its best
relative result. This used LESS compute than NSGA-II, not more: 10 restarts x
1500 evals = ~15,000 `evaluate()` calls per instance, vs. NSGA-II's ~24,000 for
a single pop=120/gen=200 restart (63% as much), or ~72,000 for the two STRESS
instances that needed 3 search-seed restarts (21% as much) -- so this is not
"we threw more compute at it," it is "a better move set found more with less."

Two findings, not one: (1) three of six curves (all STRESS) are perfectly FLAT
from the very first restart -- the FIFO-seeded start alone, with no random
restart diversity at all, already reaches the plateau local search ever finds
in this budget. That is real evidence AGAINST "this landscape just needs
unbounded restarts" (interpretation (b) in the module docstring): if restart
diversity were the active ingredient, these curves would climb like the other
three, not sit flat for 9 more tries. (2) NSGA-II gets the IDENTICAL FIFO seed
and still fails to find these moves from it -- ruling out "bad starting point"
as the explanation for NSGA-II's weakness. The only structural difference
between this method and NSGA-II is the move set: `TwoPointCrossover` +
`BitflipMutation` do not encode a direct swap, and evidently do not discover
one reliably through recombination either, at least not within the M31 budget.

CONCLUSION: on Ckt7, M31's "noisier landscape, needs bigger budget and more
restarts" framing was half right and half a red herring. The instance-to-
instance variability is real, but the root cause is not landscape ruggedness
demanding brute-force restarts -- it is that NSGA-II's specific operators are a
poor structural match for a capacity-constrained subset-selection encoding,
where the profitable move is a paired remove+add trade, not an independent bit
flip or a two-point crossover of two accept-patterns. M31's reported numbers
(including the ones already flagged as lower bounds) are still individually
correct and were honestly disclosed as conservative -- but this probe shows
"conservative" means understating the true achievable gain by as much as an
order of magnitude on some instances, not a small margin. Per user decision
(2026-07-11): documented as a finding, not acted on further -- no swap-aware
NSGA-II operator was implemented and M31's numbers were not rerun/replaced;
that remains a concrete, well-specified future improvement if this line of
work resumes (docs/logs/ckt7.md, M32).
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
import pandapower as pp
import pandapower.topology as top

from gridsynth import import_opendss

VMIN_PU, VMAX_PU, LOADING_MAX_PCT = 0.94, 1.05, 100.0
RUNPP_KWARGS = dict(max_iteration=50, init="flat", tolerance_mva=1e-4, check_connectivity=False)
SCENARIOS = {"MODERATE": 150, "STRESS": 300}
REQ_LO_MW, REQ_HI_MW = 0.010, 0.100
MASTER_PATH = "data/opendss/ckt7/Master_ckt7.dss"

# exp61's already-reported NSGA-II gains (module docstring, docs/logs/ckt7.md M31) --
# printed alongside this probe's results for direct comparison, not recomputed here.
NSGA2_GAIN_PCT = {
    ("MODERATE", 0): 49.9, ("MODERATE", 1): 6.8, ("MODERATE", 2): 10.5,
    ("STRESS", 0): 40.5, ("STRESS", 1): 9.8, ("STRESS", 2): 61.4,
}


def _pool(n_jobs: int):
    """Mirrors exp61's `_pool` exactly."""
    if n_jobs == 1:
        return nullcontext(None)
    return multiprocessing.Pool(processes=n_jobs if n_jobs > 0 else None)


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
    """Identical to exp61's `build_network` -- same exclusion, same network."""
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
    """Identical to exp61's `build_projects` -- same seed gives the same draw,
    so results below are directly comparable to exp61's per-instance rows."""
    candidate_buses = [b for b in ctx.net.load.bus.unique() if b not in ctx.excluded_buses]
    rng = random.Random(seed)
    projects = []
    for i in range(k):
        bus = rng.choice(candidate_buses)
        p_kw = rng.uniform(REQ_LO_MW, REQ_HI_MW) * 1000
        projects.append(Project(id=f"p{i}", bus=bus, p_mw=-p_kw / 1000))
    return projects


def evaluate(ctx: NetworkContext, projects, accepted_ids: set[str]) -> tuple[bool, float, float]:
    """Identical to exp61's `evaluate` -- same oracle, so gains are comparable."""
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
    """Identical to exp61's honest FCFS comparator."""
    accepted: set[str] = set()
    accepted_mw = 0.0
    for p in projects:
        trial = accepted | {p.id}
        ok, mw, _ = evaluate(ctx, projects, trial)
        if ok:
            accepted, accepted_mw = trial, mw
    return accepted, accepted_mw


def _sample_move(cur_ids: set[str], all_ids: list[str], rng) -> tuple:
    """Two move types: `("toggle", pid)` (add/remove one project -- needed to
    escape an infeasible random start, or to add when few are accepted) and
    `("swap", remove_pid, add_pid)` (drop one accepted, add one rejected --
    the move that actually matters for a capacity-constrained subset problem:
    a single ADD rarely helps once FCFS has already greedily filled headroom,
    since a REMOVE alone is never improving under a pure hill-climb. Swap is
    what lets the search trade a small accepted request for a larger rejected
    one, the combinatorial move NSGA-II's crossover implicitly explores)."""
    accepted = list(cur_ids)
    rejected = [i for i in all_ids if i not in cur_ids]
    if accepted and rejected and rng.random() < 0.6:
        return ("swap", rng.choice(accepted), rng.choice(rejected))
    return ("toggle", rng.choice(all_ids))


def _apply_move(ids: set[str], move: tuple) -> set[str]:
    nbr = set(ids)
    if move[0] == "toggle":
        pid = move[1]
        nbr.discard(pid) if pid in nbr else nbr.add(pid)
    else:
        _, rem, add = move
        nbr.discard(rem)
        nbr.add(add)
    return nbr


def _eval_move(ctx, projects, ids, move) -> tuple[bool, float, float]:
    """Module-level so it's picklable for `multiprocessing.Pool.starmap`."""
    return evaluate(ctx, projects, _apply_move(ids, move))


def _better(a: tuple[bool, float, float], b: tuple[bool, float, float]) -> bool:
    """Feasibility-first, then higher accepted MW (feasible) / lower violation
    (infeasible) -- a strict total preorder, no weighting or scaling needed."""
    af, amw, av = a
    bf, bmw, bv = b
    if af != bf:
        return af
    return amw > bmw if af else av < bv


def local_search_restart(ctx, projects, start_ids, pool, n_jobs, max_evals, rng):
    """Randomized batch hill climbing over the toggle+swap neighborhood (see
    `_sample_move`). Each round samples `n_jobs` candidate moves, evaluates
    them in parallel, and takes the best one if it improves on the current
    state; runs for a fixed number of rounds (`max_evals // n_jobs`) rather
    than stopping at a declared local optimum, so total cost per restart is
    predictable. Returns (best_feasible_mw, evals_used, n_moves_accepted)."""
    cur_ids = set(start_ids)
    all_ids = [p.id for p in projects]
    cur_state = evaluate(ctx, projects, cur_ids)
    evals = 1
    best_mw = cur_state[1] if cur_state[0] else 0.0
    n_moves = 0
    n_rounds = max(1, max_evals // n_jobs)
    for _ in range(n_rounds):
        moves = [_sample_move(cur_ids, all_ids, rng) for _ in range(n_jobs)]
        args = [(ctx, projects, cur_ids, mv) for mv in moves]
        results = pool.starmap(_eval_move, args) if pool is not None else [_eval_move(*a) for a in args]
        evals += len(moves)
        best_in_batch = max(results, key=lambda r: (r[0], r[1] if r[0] else -r[2]))
        if _better(best_in_batch, cur_state):
            cur_ids = _apply_move(cur_ids, moves[results.index(best_in_batch)])
            cur_state = best_in_batch
            if cur_state[0]:
                best_mw = max(best_mw, cur_state[1])
            n_moves += 1
    return best_mw, evals, n_moves


def run_probe(ctx, scenario, seed, n_restarts, max_evals, n_jobs) -> dict:
    k = SCENARIOS[scenario]
    projects = build_projects(ctx, k, seed)
    fifo_ids, fifo_mw = skip_continue_baseline(ctx, projects)

    rng = random.Random(1000 + seed)
    n = len(projects)
    curve = []  # (restart index, cumulative best mw)
    best_mw = 0.0
    t0 = time.time()
    with _pool(n_jobs) as pool:
        for r in range(n_restarts):
            start_ids = fifo_ids if r == 0 else {p.id for p in projects if rng.random() < 0.5}
            mw, evals, moves = local_search_restart(
                ctx, projects, start_ids, pool, n_jobs if n_jobs != 1 else 1, max_evals, rng
            )
            best_mw = max(best_mw, mw)
            curve.append(round((best_mw / fifo_mw - 1) * 100, 1) if fifo_mw > 0 else float("nan"))
    dt = time.time() - t0

    gain_pct = (best_mw / fifo_mw - 1) * 100 if fifo_mw > 0 else float("nan")
    return dict(scenario=scenario, seed=seed, fifo_kw=fifo_mw * 1000, joint_kw=best_mw * 1000,
                ls_gain_pct=gain_pct, nsga2_gain_pct=NSGA2_GAIN_PCT.get((scenario, seed), float("nan")),
                curve=curve, dt=dt)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--instances", type=str, default="MODERATE:0,MODERATE:1,MODERATE:2,STRESS:0,STRESS:1,STRESS:2",
                     help="comma-separated SCENARIO:seed pairs")
    ap.add_argument("--restarts", type=int, default=10, help="10 is what produced the RESULT table in the module docstring")
    ap.add_argument("--max-evals", type=int, default=1500, help="eval budget per restart")
    ap.add_argument("--n-jobs", type=int, default=6, help="leaves other cores free -- do not raise without checking headroom")
    ap.add_argument("--save", action="store_true")
    args = ap.parse_args()

    instances = []
    for tok in args.instances.split(","):
        scen, seed = tok.split(":")
        instances.append((scen, int(seed)))

    print("M32: Ckt7 local-search ceiling probe -- is NSGA-II the bottleneck, or is the landscape genuinely noisy?\n")
    print("Importing + diagnosing Ckt7...")
    ctx = build_network()
    ok0, _, viol0 = evaluate(ctx, [], set())
    assert ok0, "baseline infeasible -- fix before trusting anything below"
    print("  ready\n")

    rows = []
    for scenario, seed in instances:
        row = run_probe(ctx, scenario, seed, args.restarts, args.max_evals, args.n_jobs)
        rows.append(row)
        print(f"  {scenario:8s} seed={seed}  FCFS={row['fifo_kw']:8.1f}kW  "
              f"local-search={row['joint_kw']:8.1f}kW  ls_gain={row['ls_gain_pct']:+6.1f}%  "
              f"nsga2_gain={row['nsga2_gain_pct']:+6.1f}%  {row['dt']:.0f}s")
        print(f"    cumulative-best-vs-restart curve (%): {row['curve']}\n")

    if args.save and rows:
        import csv
        from pathlib import Path
        out = Path(__file__).resolve().parents[1] / "outputs"
        out.mkdir(exist_ok=True)
        path = out / "m32_ckt7_local_search_ceiling.csv"
        fields = sorted({k for r in rows for k in r.keys()})
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader(); w.writerows(rows)
        print(f"saved {path}")


if __name__ == "__main__":
    main()
