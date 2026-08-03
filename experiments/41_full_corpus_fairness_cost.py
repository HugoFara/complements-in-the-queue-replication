"""M17 of the joint-allocation roadmap: re-run fairness (M9) and
reinforcement-cost (M6) across the full ENWL corpus, not just the two
networks each was checked on originally.

`docs/paper.md` Section 7 named this as the first of three still-open items
after M16: "re-running the fairness and reinforcement-cost validations
across the full 121-feeder corpus the way the core search-beats-baseline
claim already was, now more tractable given this note's own speed work."
M8 (`experiments/32_multi_feeder_validation.py`) already ran the core
search-beats-baseline claim and a voltage-vs-thermal binding classification
across all 121 baseline-feasible feeders of ENWL's 128-feeder corpus, using
`lindistflow_search_iterative` (the loss-corrected MILP) as the fast primary
tool, NSGA-II only as a spot-check -- exactly because NSGA-II alone across
~100 feeders of this scale would take hours, not a session. M6 and M9 never
got that treatment: both were checked on exactly two networks (EULV,
`network_15/Feeder_4`), same as everything M8 itself set out to generalize.

**Reusing M8's exact corpus, seeds, and requests.** `discover_feeders` and
`build_requests` here are byte-for-byte copies of M8's -- same
`network_*/Feeder_*` sort order, same `PV_KW_LO/HI = 3-10`, same
`feeder_seed = SEED + idx` per feeder -- so every feeder in this run draws
the identical candidate-request set M8 already classified as voltage- or
thermal-limited (`outputs/m8_multi_feeder.csv`, from a prior M8 run: 121
baseline-feasible feeders, 110 thermal-limited / 11 voltage-limited -- itself
already a striking mismatch against Sandia's 216-feeder split of 72%
voltage/26% thermal, noted here for cross-reference, not re-litigated).
Recomputing baseline-feasibility and binding classification directly here
(cheap: one `runpp` per feeder) rather than trusting the stale CSV file, but
expecting -- and checking -- that it reproduces the same 121/110/11 split.

**Tool choice, same reasoning as M8.** `nsga2_search_fair` (M9) is the exact
3-objective AC oracle but pays one `runpp` per individual per generation --
not viable across 121 feeders in a session. `lindistflow_search_fair` (M12)
is the convex-search analogue: a single-shot MILP with a hard cap on
`queue_fairness_violations`, AC-reverified every time. Single-shot, not the
loss-corrected iterative variant M8 used for its headline capacity numbers
(there is no fairness-aware iterative variant) -- so both the free
(`lindistflow_search`) and zero-violation (`lindistflow_search_fair(...,
max_violations=0)`) calls here carry the same known lossless-model bias
identified in M5/M8 (underestimates accommodated MW on large feeders,
worse as trunk-edge current grows). That bias is *shared* by both calls on
a given feeder, which is what makes their difference -- "what does zero
queue-jumps cost, in kW, relative to the free MILP optimum" -- still a fair
internal comparison, even though neither number alone is as tight as M8's
own iterative-search headline figures. `nsga2_search_fair` is spot-checked
on a handful of feeders spanning the size range to confirm this, the same
role M8's own NSGA-II spot-check played for the plain search.

Reinforcement cost needs no such caveat: `reinforced_greedy_baseline` (M6)
is already a cheap greedy walk (one `runpp`/`price_reinforcement` call per
queued request), not NSGA-II-scale, so it runs directly across the full
corpus at full accuracy.

Run:  uv run python experiments/41_full_corpus_fairness_cost.py [--max-feeders N]
                                                                  [--nsga2-spotcheck N] [--save]
"""

from __future__ import annotations

import argparse
import random
import time
from pathlib import Path

from gridsynth import import_opendss

from allocation import (
    AllocationProblem,
    Request,
    greedy_baseline,
    lindistflow_search,
    lindistflow_search_fair,
    nsga2_search_fair,
    queue_fairness_violations,
)
from allocation.baseline import reinforced_greedy_baseline

REPO = Path(__file__).resolve().parents[1]
CACHE = REPO / "cache" / "enwl_full"
OUT = REPO / "outputs"

PV_KW_LO, PV_KW_HI = 3.0, 10.0  # same band as experiments 24-32, for comparability
SEED = 0
MAX_REQUESTS = 50
MIN_LOADS = 3
MILP_TIME_LIMIT_S = 20.0  # tighter than the 60s default -- two MILP solves/feeder x 121 feeders


def discover_feeders() -> list[Path]:
    """Identical to M8's -- every cached ``Master_snapshot.dss``, sorted by
    (network, feeder) number, so feeder-index-based seeds line up exactly."""
    masters = list(CACHE.glob("network_*/Feeder_*/Master_snapshot.dss"))
    return sorted(masters, key=lambda p: (int(p.parent.parent.name.split("_")[1]),
                                           int(p.parent.name.split("_")[1])))


def build_requests(load_buses: list[int], k: int, seed: int) -> list[Request]:
    """Identical to M8's -- see that file's own docstring for why a per-feeder
    seed (not one shared seed) is required for distinct draws across the corpus."""
    rng = random.Random(seed)
    buses = rng.sample(load_buses, k)
    return [Request(id=f"pv{i}", bus=bus, p_mw=-rng.uniform(PV_KW_LO, PV_KW_HI) / 1000.0, queue_order=i)
            for i, bus in enumerate(buses)]


def binding_constraint(result, problem: AllocationProblem) -> str:
    """Same voltage-vs-thermal classification as M8's own helper."""
    over_v = (result.vmax_pu - 1.0) / (problem.vmax_pu - 1.0)
    under_v = (1.0 - result.vmin_pu) / (1.0 - problem.vmin_pu)
    volt_frac = max(over_v, under_v, 0.0)
    therm_frac = max(result.max_line_loading_percent, result.max_trafo_loading_percent) / problem.max_loading_percent
    return "voltage" if volt_frac >= therm_frac else "thermal"


def run_feeder(master: Path, feeder_seed: int) -> dict:
    name = f"{master.parent.parent.name}/{master.parent.name}"
    t0 = time.time()
    row: dict = {"name": name}
    try:
        net = import_opendss(str(master))
    except Exception as e:
        row.update(status=f"import_failed: {e}", dt=time.time() - t0)
        return row
    row["n_bus"] = len(net.bus)

    base_problem = AllocationProblem(net=net, requests=[])
    if not base_problem.evaluate([]).feasible:
        row.update(status="baseline_infeasible", n_load=len(net.load), dt=time.time() - t0)
        return row

    load_buses = net.load.bus.tolist()
    if len(load_buses) < MIN_LOADS:
        row.update(status="too_few_loads", n_load=len(load_buses), dt=time.time() - t0)
        return row

    k = min(MAX_REQUESTS, len(load_buses))
    requests = build_requests(load_buses, k, feeder_seed)
    problem = AllocationProblem(net=net, requests=requests)
    row.update(n_load=len(load_buses), k=k, seed=feeder_seed)

    # -- reinforcement cost (M6), full accuracy, cheap enough for the whole corpus --
    greedy = greedy_baseline(problem)
    reinforced = reinforced_greedy_baseline(problem)
    row.update(
        greedy_mw=greedy.accommodated_mw * 1000,
        reinforced_mw=reinforced.result.accommodated_mw * 1000,
        reinforced_gain_kw=(reinforced.result.accommodated_mw - greedy.accommodated_mw) * 1000,
        reinforced_cost_eur=reinforced.total_cost_eur,
    )

    # -- fairness cost (M9), single-shot MILP both sides (see this file's docstring) --
    try:
        free = lindistflow_search(problem, time_limit_s=MILP_TIME_LIMIT_S)
        fair0 = lindistflow_search_fair(problem, max_violations=0, time_limit_s=MILP_TIME_LIMIT_S)
    except NotImplementedError as e:
        row.update(status="ok_baseline_only (convex failed: %s)" % e, dt=time.time() - t0)
        return row

    binding = binding_constraint(free.result, problem)
    free_mw = free.result.accommodated_mw if free.result.feasible else float("nan")
    fair0_mw = fair0.result.accommodated_mw if fair0.result.feasible else float("nan")
    fair0_violations = queue_fairness_violations(requests, fair0.result.accepted_ids) if fair0.result.feasible else -1
    row.update(
        status="ok", binding=binding,
        free_mw=free_mw * 1000, fair0_mw=fair0_mw * 1000,
        fairness_cost_kw=(free_mw - fair0_mw) * 1000 if free.result.feasible and fair0.result.feasible else float("nan"),
        fair0_violations=fair0_violations,
        dt=time.time() - t0,
    )
    return row


def spotcheck_nsga2_fair(rows: list[dict], n: int) -> None:
    """Same role as M8's plain-search spot-check: pick n feeders spanning the
    observed size range and confirm `nsga2_search_fair`'s exact-AC zero-
    violation point agrees with `lindistflow_search_fair(0)`'s single-shot
    MILP answer, not just assumed to generalize from EULV/ENWL-Feeder_4."""
    ok = sorted((r for r in rows if r["status"] == "ok"), key=lambda r: r["n_bus"])
    if not ok:
        return
    idxs = sorted(set(int(round(i)) for i in
                       [j * (len(ok) - 1) / max(n - 1, 1) for j in range(n)]))
    picked = [ok[i] for i in idxs]

    print(f"\n== nsga2_search_fair spot-check ({len(picked)} feeders spanning the size range) ==")
    for row in picked:
        master = CACHE / row["name"] / "Master_snapshot.dss"
        net = import_opendss(str(master))
        requests = build_requests(net.load.bus.tolist(), row["k"], row["seed"])
        problem = AllocationProblem(net=net, requests=requests)
        pop, gen = (30, 30) if row["n_bus"] > 1000 else (40, 40)
        t0 = time.time()
        results = nsga2_search_fair(problem, pop_size=pop, n_gen=gen, seed=SEED)
        dt = time.time() - t0
        feasible = [r for r in results if r.feasible]
        zero_v = [r for r in feasible if queue_fairness_violations(requests, r.accepted_ids) == 0]
        best_zero_mw = max((r.accommodated_mw for r in zero_v), default=float("nan")) * 1000
        gap = best_zero_mw - row["fair0_mw"]
        print(f"  {row['name']:25s} bus={row['n_bus']:5d}  "
              f"milp_fair0={row['fair0_mw']:7.1f} kW  nsga2_fair0={best_zero_mw:7.1f} kW  "
              f"(nsga2-milp={gap:+.1f} kW)  time={dt:.1f}s")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--max-feeders", type=int, default=0, help="0 = every cached feeder (128)")
    ap.add_argument("--nsga2-spotcheck", type=int, default=6)
    ap.add_argument("--save", action="store_true")
    args = ap.parse_args()

    feeders = discover_feeders()
    if args.max_feeders:
        feeders = feeders[: args.max_feeders]
    print(f"M17: fairness + reinforcement-cost across {len(feeders)} ENWL feeders "
          f"(M8's own 121/128-feeder baseline-feasible corpus)\n")

    rows = []
    t_all = time.time()
    for idx, master in enumerate(feeders):
        row = run_feeder(master, SEED + idx)
        rows.append(row)
        if row["status"] == "ok":
            print(f"{row['name']:25s} bus={row['n_bus']:5d} k={row['k']:3d} binding={row['binding']:8s}  "
                  f"reinforce: {row['reinforced_gain_kw']:+7.1f} kW / EUR{row['reinforced_cost_eur']:,.0f}  "
                  f"fair0: -{row['fairness_cost_kw']:6.1f} kW ({row['fair0_violations']} viol. allowed=0)  "
                  f"{row['dt']:.1f}s")
        else:
            print(f"{row['name']:25s} {row['status']}")

    print(f"\ntotal: {len(rows)} feeders scanned in {time.time() - t_all:.1f}s")

    ok = [r for r in rows if r["status"] == "ok"]
    baseline_infeasible = [r for r in rows if r["status"] == "baseline_infeasible"]
    too_few = [r for r in rows if r["status"] == "too_few_loads"]
    convex_failed = [r for r in rows if r["status"].startswith("ok_baseline_only")]
    print(f"  baseline-feasible + evaluated: {len(ok)}/{len(rows)}")
    print(f"  baseline-infeasible (excluded): {len(baseline_infeasible)}")
    print(f"  too few loads (excluded): {len(too_few)}")
    print(f"  convex model failed (excluded from fairness comparison): {len(convex_failed)}")

    if ok:
        n_voltage = sum(1 for r in ok if r["binding"] == "voltage")
        n_thermal = len(ok) - n_voltage
        print(f"\n  binding constraint: voltage-limited {n_voltage}/{len(ok)}, thermal-limited {n_thermal}/{len(ok)}")

        # -- M6 pattern check: does reinforcement recover capacity mainly on thermal-limited feeders? --
        def _avg(xs):
            xs = list(xs)
            return sum(xs) / len(xs) if xs else float("nan")

        volt_rows = [r for r in ok if r["binding"] == "voltage"]
        therm_rows = [r for r in ok if r["binding"] == "thermal"]
        print("\n  == M6 pattern (voltage-limited feeders get ~nothing from reinforcement; "
              "thermal-limited feeders do) ==")
        print(f"    voltage-limited ({len(volt_rows)}): avg reinforcement gain "
              f"{_avg(r['reinforced_gain_kw'] for r in volt_rows):+.1f} kW, "
              f"{sum(1 for r in volt_rows if r['reinforced_gain_kw'] > 1e-6)}/{len(volt_rows)} feeders gained anything")
        print(f"    thermal-limited ({len(therm_rows)}): avg reinforcement gain "
              f"{_avg(r['reinforced_gain_kw'] for r in therm_rows):+.1f} kW, "
              f"{sum(1 for r in therm_rows if r['reinforced_gain_kw'] > 1e-6)}/{len(therm_rows)} feeders gained anything")

        # -- M9 pattern check: does zero-queue-jump fairness cost capacity, and how often is it free? --
        fair_ok = [r for r in ok if r["fairness_cost_kw"] == r["fairness_cost_kw"]]  # drop NaNs (infeasible MILP side)
        free_fairness = [r for r in fair_ok if r["fairness_cost_kw"] <= 1e-6]
        print(f"\n  == M9 pattern (does demanding zero queue-jumps cost capacity?) ==")
        print(f"    {len(fair_ok)}/{len(ok)} feeders had both MILP sides feasible")
        print(f"    zero-cost fairness (free lunch): {len(free_fairness)}/{len(fair_ok)} feeders "
              f"({100 * len(free_fairness) / max(len(fair_ok), 1):.0f}%)")
        if len(fair_ok) > len(free_fairness):
            paying = [r for r in fair_ok if r["fairness_cost_kw"] > 1e-6]
            print(f"    among the {len(paying)} that do pay: avg cost "
                  f"{_avg(r['fairness_cost_kw'] for r in paying):.1f} kW, "
                  f"max {max(r['fairness_cost_kw'] for r in paying):.1f} kW "
                  f"({max(paying, key=lambda r: r['fairness_cost_kw'])['name']})")

    if args.nsga2_spotcheck and ok:
        spotcheck_nsga2_fair(rows, args.nsga2_spotcheck)

    if args.save:
        OUT.mkdir(exist_ok=True)
        import csv
        with open(OUT / "m17_full_corpus_fairness_cost.csv", "w", newline="") as f:
            fieldnames = sorted({k for r in rows for k in r.keys()})
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(rows)
        print(f"\nsaved {OUT / 'm17_full_corpus_fairness_cost.csv'}")


if __name__ == "__main__":
    main()
