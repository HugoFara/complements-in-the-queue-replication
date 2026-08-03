"""Instrumentation for the one item `docs/paper.md` Section 7 still lists as
open after M19: does `nsga2_search_continuous`'s (M7) underperformance
against binary `nsga2_search` at equal budget close with a larger search
budget, or is it a convergence-operator ceiling that more generations/
population don't fix? M7 found continuous NSGA-II reaching 231.1 kW on
EULV at (pop=40, gen=40) -- worse than binary's 245.4 kW, and worse than
the plain binary greedy baseline's 236.8 kW -- diagnosed as `FloatRandomSampling`
starting genes near 0.5 on average, far from the near-bang-bang (mostly-0-
or-1) corners this problem's optima tend to sit at. A follow-up at 2x the
budget (pop=80, gen=80 -- 4x the evaluations) did not finish within 590s
(binary's own full run takes ~155s) -- left as an open question, not
resolved. M12 found the same inherited gap on `nsga2_search_continuous_fair`.

This script does not answer the question -- it is the harness to actually
answer it once run (see the module's closing docstring note: "we will run
later"). `allocation.search` gained a `callback` passthrough (pure
instrumentation, no change to what is searched -- see its module docstring
and `test_callback_is_notified_once_per_generation_and_does_not_change_the_result`)
so a *single* long run can be sampled at every generation, instead of
restarting from scratch at each budget level the way M7's own one-off
80/80 check did. Two variables are swept independently: `n_gen` (via the
callback's running-best-so-far curve from one run, cheap to extend) and
`pop_size` (needs a fresh run each time, more expensive -- a small,
explicit list, not a dense grid).

What "closing the question" means, concretely: if the continuous curve's
running-best converges to (or past) binary NSGA-II's own same-budget curve,
or to the continuous LP's exact optimum, before the budget runs out, the
answer is "yes, budget alone closes it." If the curve visibly plateaus well
below those references while still consuming most of the generation budget,
the answer is "no, this needs a different operator (e.g. a corner-seeded
initial sampling), not just more of the same one" -- the follow-up M7
itself already named as the next concrete step, not attempted here.

This script does not pick a verdict for you: it prints the raw curves, the
deterministic ceilings (continuous greedy baseline, continuous LP optimum),
and a first-half-vs-second-half improvement split (still-improving vs.
plateaued), and leaves the read to whoever runs it.

Run (small, fast sanity check of the harness itself, not a real answer):
    uv run python experiments/44_continuous_convergence.py --network eulv \\
        --n-gen-max 40 --pop-sizes 40

Run (the actual budget-vs-convergence study -- expensive, see the module
docstring's compute-cost discussion; run in the background):
    uv run python experiments/44_continuous_convergence.py --network eulv \\
        --n-gen-max 320 --pop-sizes 40,160 --n-jobs -1 --fair --save
"""

from __future__ import annotations

import argparse
import random
import time
from pathlib import Path

from gridsynth import import_opendss
from pymoo.core.callback import Callback

from allocation import AllocationProblem, Request, greedy_baseline, nsga2_search
from allocation.baseline import continuous_greedy_baseline
from allocation.convex import lindistflow_search
from allocation.search import nsga2_search_continuous, nsga2_search_continuous_fair

REPO = Path(__file__).resolve().parents[1]
CACHE = REPO / "cache" / "enwl_full"
OUT = REPO / "outputs"

PV_KW_LO, PV_KW_HI = 3.0, 10.0  # same band as experiments 24-31/36, for comparability

# Same networks/candidate-pool sizes as experiments/31_continuous_sizing.py
# (M7), so any curve here lands on the same footing as its already-published
# numbers -- (pop_size, n_gen) here are *maximums* swept up to, not fixed
# defaults, since that is the entire point of this script.
NETWORKS = {
    "eulv": dict(master=REPO / "data" / "opendss" / "eulv" / "Master_snapshot.dss", k=40),
    "enwl": dict(master=CACHE / "network_15" / "Feeder_4" / "Master_snapshot.dss", k=100),
}


def build_requests(load_buses: list[int], k: int, seed: int) -> list[Request]:
    rng = random.Random(seed)
    buses = rng.sample(load_buses, k)
    return [Request(id=f"pv{i}", bus=bus, p_mw=-rng.uniform(PV_KW_LO, PV_KW_HI) / 1000.0, queue_order=i)
            for i, bus in enumerate(buses)]


class _ConvergenceLog(Callback):
    """Records, once per generation, the best fully-feasible (`reinforcement_
    cost_proxy == 0`) accommodated_mw found *so far* in this run -- a running
    max, not a per-generation snapshot: NSGA-II's tracked population is a
    fixed-size window, and a good feasible individual is not guaranteed to
    still be present at every later generation just because it appeared once
    (see search.py's own `_SizingProblem`/`_FairSizingProblem`: `F[:, 0]` is
    `-accommodated_mw`, `F[:, 1]` is `reinforcement_cost_proxy` in both --
    the third column `_FairSizingProblem` adds (queue_fairness_violations)
    is ignored here, this is about capacity convergence, not fairness).
    Pure instrumentation -- does not touch `algorithm`'s state.
    """

    def __init__(self):
        super().__init__()
        self.best_mw: list[float] = []
        self.n_feasible: list[int] = []

    def notify(self, algorithm) -> None:
        F = algorithm.pop.get("F")
        feasible = F[:, 1] <= 1e-9
        gen_best = float(-F[feasible, 0].min()) if feasible.any() else 0.0
        running_best = max(self.best_mw[-1], gen_best) if self.best_mw else gen_best
        self.best_mw.append(running_best)
        self.n_feasible.append(int(feasible.sum()))


def print_curve(label: str, log: _ConvergenceLog, pop_size: int, log_every: int, dt: float) -> None:
    n_gen = len(log.best_mw)
    print(f"  -- {label} (pop_size={pop_size}, n_gen={n_gen}, {dt:.0f}s) --")
    checkpoints = list(range(log_every, n_gen, log_every)) + [n_gen]
    for g in checkpoints:
        print(f"    gen {g:4d}: best={log.best_mw[g-1]*1000:8.1f} kW   "
              f"feasible {log.n_feasible[g-1]:3d}/{pop_size}")
    if n_gen >= 2:
        mid = n_gen // 2
        first_half = log.best_mw[mid - 1] - log.best_mw[0]
        second_half = log.best_mw[-1] - log.best_mw[mid - 1]
        print(f"    improvement: first half of budget {first_half*1000:+.1f} kW, "
              f"second half {second_half*1000:+.1f} kW"
              f"{'  (still climbing near the end)' if second_half > 0.1 * first_half else '  (flattening out)'}")


def rows_from_log(rows: list[dict], network: str, method: str, pop_size: int, seed: int,
                   log: _ConvergenceLog) -> None:
    for g, (mw, nf) in enumerate(zip(log.best_mw, log.n_feasible), start=1):
        rows.append(dict(network=network, method=method, pop_size=pop_size, seed=seed,
                          generation=g, best_kw=mw * 1000, n_feasible=nf))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--network", choices=NETWORKS, default="eulv")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--k", type=int, default=None, help="override the default candidate-pool size")
    ap.add_argument("--n-gen-max", type=int, default=160,
                     help="generation budget each pop_size is run to (M7's own partial check got to "
                          "80 without finishing in time -- this defaults higher but is fully CLI-tunable)")
    ap.add_argument("--pop-sizes", type=str, default="40",
                     help="comma-separated list of population sizes to sweep, e.g. 40,160")
    ap.add_argument("--log-every", type=int, default=10, help="checkpoint interval for the printed table")
    ap.add_argument("--n-jobs", type=int, default=1,
                     help="M10 parallelization; -1 uses all cores. Check RAM headroom before scaling "
                          "this up on a large pop_size/n_gen-max run.")
    ap.add_argument("--skip-binary-reference", action="store_true",
                     help="skip the same-budget binary nsga2_search curve (cheaper, but no direct "
                          "same-generation-axis comparison)")
    ap.add_argument("--fair", action="store_true",
                     help="also sweep nsga2_search_continuous_fair (M12) at the largest pop_size, "
                          "checking whether its own inherited convergence gap (M12) responds to budget too")
    ap.add_argument("--save", action="store_true", help="write every (pop_size, generation) row to a CSV")
    args = ap.parse_args()

    cfg = NETWORKS[args.network]
    master = cfg["master"]
    if not master.exists():
        raise SystemExit(f"{master} not found -- for enwl, run experiments/28_m3_scale_report.py "
                          "first to populate the cache (or point --network at eulv)")
    k = args.k or cfg["k"]
    pop_sizes = [int(p) for p in args.pop_sizes.split(",")]
    n_gen_max = args.n_gen_max

    net = import_opendss(str(master))
    load_buses = net.load.bus.tolist()
    requests = build_requests(load_buses, k, args.seed)

    def make_problem() -> AllocationProblem:
        # M12 already caught this exact pitfall: `AllocationProblem`'s M10
        # per-instance cache carries over between search() calls that share
        # one instance, making a later call look free rather than measuring
        # its real cost. A fresh instance per search call here (base `net`
        # itself is never mutated by evaluate_sizes -- it deepcopies before
        # solving -- so sharing it across instances is safe) is the fix.
        return AllocationProblem(net=net, requests=requests)

    print(f"Continuous NSGA-II convergence-vs-budget sweep -- {args.network} "
          f"({len(net.bus)} buses, {len(net.load)} loads), {k} candidate PV requests, seed={args.seed}\n"
          f"pop_sizes={pop_sizes}, n_gen_max={n_gen_max}, n_jobs={args.n_jobs}\n")

    # 0. deterministic reference ceilings -- cheap, computed once ------------
    print("== 0. deterministic reference points (no search budget involved) ==")
    binary_base = greedy_baseline(make_problem())
    continuous_base = continuous_greedy_baseline(make_problem())
    binary_lp = lindistflow_search(make_problem())
    continuous_lp = lindistflow_search(make_problem(), continuous=True)
    print(f"  binary greedy baseline:      {binary_base.accommodated_mw*1000:8.1f} kW")
    print(f"  continuous greedy baseline:  {continuous_base.accommodated_mw*1000:8.1f} kW")
    print(f"  binary LinDistFlow MILP:     {binary_lp.result.accommodated_mw*1000:8.1f} kW")
    print(f"  continuous LinDistFlow LP:   {continuous_lp.result.accommodated_mw*1000:8.1f} kW  "
          f"(feasible={continuous_lp.result.feasible}) -- the {{0,1}}⊂[0,1] ceiling continuous "
          f"NSGA-II can never beat but should be able to approach")
    print()

    rows: list[dict] = []

    # 1. continuous NSGA-II, one long run per pop_size, sampled every generation
    print("== 1. nsga2_search_continuous: running-best-so-far vs. generation ==")
    for pop_size in pop_sizes:
        log = _ConvergenceLog()
        t0 = time.time()
        nsga2_search_continuous(make_problem(), pop_size=pop_size, n_gen=n_gen_max, seed=args.seed,
                                 n_jobs=args.n_jobs, callback=log)
        dt = time.time() - t0
        print_curve("continuous", log, pop_size, args.log_every, dt)
        rows_from_log(rows, args.network, "continuous", pop_size, args.seed, log)
    print()

    # 2. same-budget binary reference, same generation axis ------------------
    if not args.skip_binary_reference:
        print("== 2. nsga2_search (binary): same pop_size/n_gen budget, for direct comparison ==")
        for pop_size in pop_sizes:
            log = _ConvergenceLog()
            t0 = time.time()
            nsga2_search(make_problem(), pop_size=pop_size, n_gen=n_gen_max, seed=args.seed,
                          n_jobs=args.n_jobs, callback=log)
            dt = time.time() - t0
            print_curve("binary", log, pop_size, args.log_every, dt)
            rows_from_log(rows, args.network, "binary", pop_size, args.seed, log)
        print()

    # 3. optional: does the fairness-constrained variant's own inherited gap
    #    (M12) respond to budget the same way, at the largest pop_size swept?
    if args.fair:
        pop_size = max(pop_sizes)
        print(f"== 3. nsga2_search_continuous_fair: same question, at pop_size={pop_size} ==")
        log = _ConvergenceLog()
        t0 = time.time()
        nsga2_search_continuous_fair(make_problem(), pop_size=pop_size, n_gen=n_gen_max, seed=args.seed,
                                      n_jobs=args.n_jobs, callback=log)
        dt = time.time() - t0
        print_curve("continuous_fair", log, pop_size, args.log_every, dt)
        rows_from_log(rows, args.network, "continuous_fair", pop_size, args.seed, log)
        print()

    if args.save:
        OUT.mkdir(exist_ok=True)
        import csv
        out_path = OUT / f"continuous_nsga2_convergence_{args.network}.csv"
        with open(out_path, "w", newline="") as f:
            fieldnames = sorted({key for r in rows for key in r.keys()})
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(rows)
        print(f"saved {out_path} ({len(rows)} rows)")


if __name__ == "__main__":
    main()
