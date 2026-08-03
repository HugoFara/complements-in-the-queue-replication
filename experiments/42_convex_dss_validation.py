"""M18 of the joint-allocation roadmap: a convex/MILP route for
`AllocationProblemDSS` -- the gap `docs/paper.md` Section 7 named after M17:
"the convex/MILP route has no counterpart for `AllocationProblemDSS` --
LinDistFlow's radial relaxation doesn't apply to OpenDSS-solved single-
phase-lateral topology, so the fast route stays NSGA-II-only there."

`allocation.dss_convex` (new this milestone) builds one: a per-phase
*decoupled* LinDistFlow MILP, reading topology/impedances/existing
injections directly from a solved `opendssdirect` context rather than a
pandapower `net`. Getting an exact, angle-aware multiphase relaxation right
(tracking the real complex mutual coupling between phases) is genuine
research -- Section 7 already scoped that out for the meshed-network case,
and this milestone confirms empirically (not just by analogy) that it isn't
a small effect here either: dropping mutual coupling costs 3.4-4.5
percentage points of pu voltage on IEEE13's own head-end trunk line alone
(see `dss_convex._calibrate_baseline`'s docstring for the exact check), enough
that an uncalibrated version of this model is infeasible even at IEEE13's
own true (AC-feasible) baseline, before any candidate request is considered.
`dss_convex` fixes this with a one-time calibration against the real AC
baseline (not the angle-exact relaxation Section 7 scoped out) -- this
script is what validates that fix actually holds up under AC reverification,
and quantifies where it does and doesn't.

Four checks, in increasing order of what could go wrong, mirroring
experiments/29_convex_relaxation.py's own structure for the balanced/
pandapower case:

  1. **Baseline parity** -- with zero candidate requests, does the
     calibrated relaxed model's own voltage prediction match the real
     OpenDSS baseline solve? Should match by construction (that's what the
     calibration is *for*) -- checked directly, not assumed.
  2. **Accept-set parity** -- forward-evaluate the real greedy baseline's
     own accept-set through the relaxed model, the same near-boundary check
     experiment 29 runs for the pandapower case.
  3. **Head-to-head** -- solve time and AC-verified accommodated kW:
     greedy_baseline_dss, lindistflow_search_dss, and (unless skipped)
     nsga2_search_dss, same network/seed -- does the MILP beat the serial
     baseline, and how far short of NSGA-II's exact-AC search does the
     decoupling approximation leave it?
  4. **Calibration drift** -- the calibration is measured once, at zero
     accepted requests, then held fixed; this checks how much the relaxed
     model's own believed vmin at the *chosen* MILP accept-set has drifted
     from what the same accept-set's real AC reverification reports, i.e.
     how much the "requests are small next to existing load" assumption
     that calibration rests on has actually cost, on this feeder, at this
     request scale.

Scoped to IEEE13 only, not IEEE123 or SMART-DS: IEEE123 has regulators
interspersed mid-feeder (not just at the head, like IEEE13's Reg1/2/3) and
two normally-open tie switches, either of which this module's `root_bus`-
rooted, no-open-switches extraction isn't built to handle -- a real
extension, not attempted here.

Run:  uv run python experiments/42_convex_dss_validation.py
      uv run python experiments/42_convex_dss_validation.py --skip-nsga2
"""

from __future__ import annotations

import argparse
import random
import time
from pathlib import Path

import opendssdirect as dss_base

from allocation import (
    AllocationProblemDSS,
    DSSRequest,
    compare_to_baseline,
    greedy_baseline_dss,
    nsga2_search_dss,
)
from allocation.dss_convex import build_lindistflow_model_dss, evaluate_relaxed_dss, lindistflow_search_dss

REPO = Path(__file__).resolve().parents[1]
MASTER = REPO / "data" / "opendss" / "ieee13" / "IEEE13Nodeckt.dss"
ROOT_BUS = "650"
EXCLUDE_BUSES = {"sourcebus", ROOT_BUS}  # sourcebus: upstream of the delta-wye sub transformer,
                                          # not per-phase-representable; 650: the model's own root,
                                          # no accept variable can live on a fixed-voltage node
K, KW_LO, KW_HI = 38, 50.0, 150.0
POP_SIZE, N_GEN = 40, 40


def candidate_attach_points(master_path: Path, exclude_buses: set[str]) -> list[tuple[str, int]]:
    """Identical helper to experiments/39's own -- every (bus, phase) pair
    actually present on the solved circuit."""
    ctx = dss_base.NewContext()
    ctx.Command("Clear")
    ctx.Command(f'Redirect "{master_path}"')
    ctx.Solution.Solve()
    points = []
    for name in ctx.Circuit.AllBusNames():
        if name in exclude_buses:
            continue
        ctx.Circuit.SetActiveBus(name)
        for node in ctx.Bus.Nodes():
            points.append((name, node))
    return points


def build_requests(points: list[tuple[str, int]], k: int, seed: int) -> list[DSSRequest]:
    rng = random.Random(seed)
    sample = rng.sample(points, k)
    return [DSSRequest(id=f"ev{i}", bus=bus, phase=phase, p_kw=rng.uniform(KW_LO, KW_HI), queue_order=i)
            for i, (bus, phase) in enumerate(sample)]


def fmt(r) -> str:
    return f"vmin={r.vmin_pu:.5f} vmax={r.vmax_pu:.5f}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--k", type=int, default=None)
    ap.add_argument("--skip-nsga2", action="store_true", help="skip the (slower) NSGA-II comparison")
    args = ap.parse_args()

    points = candidate_attach_points(MASTER, EXCLUDE_BUSES)
    k = min(args.k or K, len(points))
    requests = build_requests(points, k, args.seed)
    problem = AllocationProblemDSS(master_path=str(MASTER), requests=requests, max_loading_percent=1e6)
    # thermal excluded here for the same reason experiments/39 excludes it:
    # IEEE13's vendored master declares no line ampacities (uniform 400A
    # default), not a meaningful constraint on this network as published.

    print(f"LinDistFlow-DSS MILP vs. NSGA-II-DSS -- IEEE13, {k} single-phase-tap "
          f"candidate requests, seed={args.seed}, root_bus={ROOT_BUS!r}\n")

    model = build_lindistflow_model_dss(problem, root_bus=ROOT_BUS)
    for phase, tree in sorted(model.trees.items()):
        print(f"  phase {phase} tree: {len(tree.order)} buses "
              f"(regulator edges: {sum(1 for j in tree.order if tree.kind[j] == 'regulator')})")
    print()

    # 1. baseline parity ------------------------------------------------------
    empty_relaxed = evaluate_relaxed_dss(model, [])
    empty_actual = problem.evaluate([])
    print("== 1. baseline parity (zero candidate requests: calibrated relaxed model vs. real OpenDSS solve) ==")
    print(f"  relaxed : {fmt(empty_relaxed)}")
    print(f"  actual  : {fmt(empty_actual)}")
    print(f"  gap     : {abs(empty_relaxed.vmin_pu - empty_actual.vmin_pu):.6f} pu (vmin), "
          f"{abs(empty_relaxed.vmax_pu - empty_actual.vmax_pu):.6f} pu (vmax)\n")

    # 2. accept-set parity at the greedy baseline's near-boundary point ------
    baseline = greedy_baseline_dss(problem)
    baseline_relaxed = evaluate_relaxed_dss(model, baseline.accepted_ids)
    print("== 2. accept-set parity (greedy baseline's accept-set: relaxed vs. real) ==")
    print(f"  relaxed : {fmt(baseline_relaxed)}")
    print(f"  actual  : {fmt(baseline)}  accommodated={baseline.accommodated_mw*1000:.1f} kW  "
          f"feasible={baseline.feasible}")
    print(f"  gap     : {abs(baseline_relaxed.vmin_pu - baseline.vmin_pu):.6f} pu (vmin)\n")

    # 3. head-to-head -----------------------------------------------------------
    print("== 3. head-to-head (AC-verified accommodated kW, wall time) ==")
    print(f"  greedy baseline    : {baseline.accommodated_mw*1000:7.1f} kW  "
          f"({len(baseline.accepted_ids)}/{k} accepted)")

    t0 = time.time()
    conv = lindistflow_search_dss(problem, root_bus=ROOT_BUS)
    conv_dt = time.time() - t0
    gap_vs_baseline = (conv.result.accommodated_mw - baseline.accommodated_mw) * 1000
    print(f"  LinDistFlow-DSS MILP: {conv.result.accommodated_mw*1000:7.1f} kW  "
          f"({len(conv.result.accepted_ids)}/{k} accepted)  "
          f"feasible={conv.result.feasible}  time={conv_dt:.2f}s  "
          f"({gap_vs_baseline:+.1f} kW vs. greedy)")
    print(f"    relaxed model's own belief about its chosen set: {fmt(conv.relaxed)}")
    print(f"    AC-verified reality for that same set           : {fmt(conv.result)}")

    if not args.skip_nsga2:
        t0 = time.time()
        results = nsga2_search_dss(problem, pop_size=POP_SIZE, n_gen=N_GEN, seed=args.seed)
        nsga_dt = time.time() - t0
        cmp = compare_to_baseline(results, baseline)
        best = cmp.best_feasible_search
        if best is not None:
            speedup = nsga_dt / conv_dt if conv_dt > 0 else float("inf")
            print(f"  NSGA-II-DSS        : {best.accommodated_mw*1000:7.1f} kW  "
                  f"({len(best.accepted_ids)}/{k} accepted)  "
                  f"feasible={best.feasible}  time={nsga_dt:.2f}s  dominates_baseline={cmp.dominates}")
            print(f"\n  LinDistFlow-DSS MILP was {speedup:.0f}x faster than NSGA-II-DSS "
                  f"({conv_dt:.2f}s vs {nsga_dt:.2f}s), reaching "
                  f"{conv.result.accommodated_mw/best.accommodated_mw*100:.1f}% of its accommodated kW -- "
                  "the shortfall is the per-phase decoupling approximation (dropped mutual coupling), "
                  "not a search-budget artifact: the MILP's own optimum is exact given its model, unlike "
                  "NSGA-II's population/generation-bounded search.")
        else:
            print("  NSGA-II-DSS        : no feasible individual found")
    print()

    # 4. calibration drift at the MILP's own chosen accept-set -----------------
    print("== 4. calibration drift (relaxed belief vs. AC reality, at the MILP's *own* chosen accept-set) ==")
    drift_vmin = conv.relaxed.vmin_pu - conv.result.vmin_pu
    print(f"  relaxed vmin={conv.relaxed.vmin_pu:.5f} pu vs. AC vmin={conv.result.vmin_pu:.5f} pu "
          f"-> drift={drift_vmin:+.5f} pu")
    print("  (baseline calibration was measured at zero accepted requests; this is what it costs "
          "once real requests are actually accepted on top of it)")


if __name__ == "__main__":
    main()
