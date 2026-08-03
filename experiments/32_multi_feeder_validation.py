"""M8 of the joint-allocation roadmap: validate across more than one real-feeder
family.

Every prior milestone's engine-level claims (M1's search-beats-baseline bar,
M2's binding-constraint pattern, M5's convex-LP-vs-NSGA-II approximation
quality) were checked on exactly *two* real networks: EULV and one ENWL
feeder (`network_15/Feeder_4`, hand-picked in M3 as the largest of seven
candidates that happened to be baseline-feasible). `docs/paper.md`'s own
Section 7 flags this as the fourth concrete next step: "validate across more
than one real-feeder family, the way Part I validates conductor assignment
across 128 feeders rather than 4."

This runs the *same* method (greedy baseline vs. AC-reverified search) across
every feeder in ENWL's full "Low Voltage Network Solutions" corpus that is
baseline-feasible -- up to 128 real UK LV feeders across 25 physically
distinct networks (substations/areas), the exact corpus experiments 11/12
already validate conductor inference against, now pointed at the allocation
question instead.

**Tool choice, stated up front.** NSGA-II is the exact-AC-oracle search
(M1), but it pays one `runpp` per individual -- ROADMAP.md's own Hard Parts
section names this as the scaling bottleneck, and M3 already measured ~101s
for one feeder 5x EULV's size. Running it across ~100 feeders of that scale
would take hours, not a session. M5 built exactly the alternative this needs:
`lindistflow_search_iterative`, a MILP that is AC-reverified every round
(never trusted from the relaxed model alone) and was shown to reach 99.4-100%
of NSGA-II's accommodated MW at 55-86x the speed on the two networks checked
so far. This experiment uses it as the primary search for the full sweep,
and spot-checks NSGA-II agreement on a handful of feeders spanning the size
range -- extending M5's own 2-network approximation-quality check, not just
assuming it holds.

Run:  uv run python experiments/32_multi_feeder_validation.py [--max-feeders N]
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
    compare_to_baseline,
    greedy_baseline,
    lindistflow_search_iterative,
    nsga2_search,
)

REPO = Path(__file__).resolve().parents[1]
CACHE = REPO / "cache" / "enwl_full"
OUT = REPO / "outputs"

PV_KW_LO, PV_KW_HI = 3.0, 10.0  # same band as experiments 24-31, for comparability
SEED = 0
MAX_REQUESTS = 50
MIN_LOADS = 3  # below this, "which subset can we jointly host" isn't a meaningful question


def discover_feeders() -> list[Path]:
    """Every ``Master_snapshot.dss`` already cached by experiment 12's full-corpus
    fetch (25 networks, 128 feeders) -- no new download, this repo already trusts
    this corpus for conductor inference."""
    masters = list(CACHE.glob("network_*/Feeder_*/Master_snapshot.dss"))
    return sorted(masters, key=lambda p: (int(p.parent.parent.name.split("_")[1]),
                                           int(p.parent.name.split("_")[1])))


def build_requests(load_buses: list[int], k: int, seed: int) -> list[Request]:
    """Same construction as experiments 24-31: K rooftop-PV export requests at a
    seeded sample of real house buses."""
    rng = random.Random(seed)
    buses = rng.sample(load_buses, k)
    requests = []
    for i, bus in enumerate(buses):
        kw = rng.uniform(PV_KW_LO, PV_KW_HI)
        requests.append(Request(id=f"pv{i}", bus=bus, p_mw=-kw / 1000.0, queue_order=i))
    return requests


def binding_constraint(result, problem: AllocationProblem) -> tuple[str, float, float]:
    """Classify which limit is closer to its bound at ``result``, the same
    voltage-vs-thermal framing M2/M3 used ("52% of voltage headroom vs. 99.97%
    of thermal headroom") -- generalized to a ratio so it can be computed and
    compared across feeders with different vmin/vmax/loading bounds."""
    over_v = (result.vmax_pu - 1.0) / (problem.vmax_pu - 1.0)
    under_v = (1.0 - result.vmin_pu) / (1.0 - problem.vmin_pu)
    volt_frac = max(over_v, under_v, 0.0)
    therm_frac = max(result.max_line_loading_percent, result.max_trafo_loading_percent) / problem.max_loading_percent
    binding = "voltage" if volt_frac >= therm_frac else "thermal"
    return binding, volt_frac, therm_frac


def run_feeder(master: Path, feeder_seed: int) -> dict:
    """``feeder_seed`` must differ per feeder: CPython's ``random.sample`` only
    consumes as many PRNG words as ``k``'s and the population size's *bit
    lengths* require, so with a single shared seed, many differently-sized
    feeders that all hit the ``k=50`` request cap advance the generator's
    internal state identically and draw byte-identical PV size sequences --
    confirmed directly (not assumed) after several dozen feeders showed
    accommodated MW matching to 13 significant figures. A per-feeder seed
    avoids reusing the same draw across the corpus."""
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
    base_check = base_problem.evaluate([])
    if not base_check.feasible:
        row.update(status="baseline_infeasible", n_load=len(net.load), dt=time.time() - t0)
        return row

    load_buses = net.load.bus.tolist()
    if len(load_buses) < MIN_LOADS:
        row.update(status="too_few_loads", n_load=len(load_buses), dt=time.time() - t0)
        return row

    k = min(MAX_REQUESTS, len(load_buses))
    requests = build_requests(load_buses, k, feeder_seed)
    problem = AllocationProblem(net=net, requests=requests)
    baseline = greedy_baseline(problem)

    try:
        conv = lindistflow_search_iterative(problem)
        search_result = conv.result
    except Exception as e:
        row.update(status=f"ok_baseline_only (convex failed: {e})", n_load=len(load_buses), k=k,
                    baseline_mw=baseline.accommodated_mw * 1000, dt=time.time() - t0)
        return row

    dominates = search_result.feasible and search_result.accommodated_mw >= baseline.accommodated_mw - 1e-9
    binding, volt_frac, therm_frac = binding_constraint(search_result, problem)
    row.update(
        status="ok", n_load=len(load_buses), k=k, seed=feeder_seed,
        baseline_mw=baseline.accommodated_mw * 1000, search_mw=search_result.accommodated_mw * 1000,
        dominates=dominates, binding=binding, volt_frac=volt_frac, therm_frac=therm_frac,
        dt=time.time() - t0,
    )
    return row


def spotcheck_nsga2(rows: list[dict], n: int) -> list[dict]:
    """Pick ``n`` feeders spanning the observed size range among the ones that
    solved cleanly, and compare NSGA-II's best-feasible accommodated MW against
    `lindistflow_search_iterative`'s -- extending M5's 2-network approximation-
    quality check to more of this corpus."""
    ok = sorted((r for r in rows if r["status"] == "ok"), key=lambda r: r["n_bus"])
    if not ok:
        return []
    idxs = sorted(set(int(round(i)) for i in
                       [j * (len(ok) - 1) / max(n - 1, 1) for j in range(n)]))
    picked = [ok[i] for i in idxs]

    out = []
    for row in picked:
        master = CACHE / row["name"] / "Master_snapshot.dss"
        net = import_opendss(str(master))
        requests = build_requests(net.load.bus.tolist(), row["k"], row["seed"])
        problem = AllocationProblem(net=net, requests=requests)
        t0 = time.time()
        pop, gen = (30, 30) if row["n_bus"] > 1000 else (40, 40)
        results = nsga2_search(problem, pop_size=pop, n_gen=gen, seed=SEED)
        dt = time.time() - t0
        baseline = greedy_baseline(problem)
        cmp = compare_to_baseline(results, baseline)
        nsga2_mw = cmp.best_feasible_search.accommodated_mw * 1000 if cmp.best_feasible_search else float("nan")
        out.append(dict(name=row["name"], n_bus=row["n_bus"], convex_mw=row["search_mw"],
                         nsga2_mw=nsga2_mw, pop=pop, gen=gen, dt=dt))
        gap = nsga2_mw - row["search_mw"]
        print(f"  {row['name']:25s} bus={row['n_bus']:5d}  convex={row['search_mw']:7.1f} kW  "
              f"nsga2={nsga2_mw:7.1f} kW  (nsga2-convex={gap:+.1f} kW)  time={dt:.1f}s")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--max-feeders", type=int, default=0, help="0 = every cached feeder (128)")
    ap.add_argument("--nsga2-spotcheck", type=int, default=6)
    ap.add_argument("--save", action="store_true")
    args = ap.parse_args()

    feeders = discover_feeders()
    if args.max_feeders:
        feeders = feeders[: args.max_feeders]
    print(f"M8: multi-feeder validation -- {len(feeders)} ENWL feeders "
          f"(25 networks, ENWL's full 'Low Voltage Network Solutions' corpus)\n")

    rows = []
    t_all = time.time()
    for idx, master in enumerate(feeders):
        row = run_feeder(master, SEED + idx)
        rows.append(row)
        if row["status"] == "ok":
            gap = row["search_mw"] - row["baseline_mw"]
            print(f"{row['name']:25s} bus={row['n_bus']:5d} k={row['k']:3d}  "
                  f"baseline={row['baseline_mw']:7.1f} kW  search={row['search_mw']:7.1f} kW  "
                  f"({gap:+.1f} kW, {'dominates' if row['dominates'] else 'REGRESSION'}, "
                  f"binding={row['binding']})  {row['dt']:.1f}s")
        else:
            print(f"{row['name']:25s} {row['status']}")

    print(f"\ntotal: {len(rows)} feeders scanned in {time.time() - t_all:.1f}s")

    ok = [r for r in rows if r["status"] == "ok"]
    baseline_infeasible = [r for r in rows if r["status"] == "baseline_infeasible"]
    too_few = [r for r in rows if r["status"] == "too_few_loads"]
    convex_failed = [r for r in rows if r["status"].startswith("ok_baseline_only")]
    print(f"  baseline-feasible + evaluated: {len(ok)}/{len(rows)}")
    print(f"  baseline-infeasible (excluded, same trap as M2/M3's Ckt5/SMART-DS): {len(baseline_infeasible)}")
    print(f"  too few loads (<{MIN_LOADS}, excluded): {len(too_few)}")
    print(f"  convex model failed (non-radial or unsupported, excluded from search comparison): {len(convex_failed)}")

    if ok:
        n_dominates = sum(1 for r in ok if r["dominates"])
        n_regress = len(ok) - n_dominates
        n_voltage = sum(1 for r in ok if r["binding"] == "voltage")
        n_thermal = len(ok) - n_voltage
        print(f"\n  search dominates baseline: {n_dominates}/{len(ok)} "
              f"({100 * n_dominates / len(ok):.0f}%){f'  -- {n_regress} REGRESSION(S)' if n_regress else ''}")
        print(f"  binding constraint: voltage-limited {n_voltage}/{len(ok)} "
              f"({100 * n_voltage / len(ok):.0f}%), thermal-limited {n_thermal}/{len(ok)} "
              f"({100 * n_thermal / len(ok):.0f}%)  [Sandia SAND2015-9712C: 72% voltage, 26% thermal, 0% trafo]")

    if args.nsga2_spotcheck and ok:
        print(f"\n== NSGA-II spot-check ({args.nsga2_spotcheck} feeders spanning the size range) ==")
        spotcheck_nsga2(rows, args.nsga2_spotcheck)

    if args.save:
        OUT.mkdir(exist_ok=True)
        import csv
        with open(OUT / "m8_multi_feeder.csv", "w", newline="") as f:
            fieldnames = sorted({k for r in rows for k in r.keys()})
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(rows)
        print(f"\nsaved {OUT / 'm8_multi_feeder.csv'}")


if __name__ == "__main__":
    main()
