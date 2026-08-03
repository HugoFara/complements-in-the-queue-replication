"""M16 of the joint-allocation roadmap: run the M15 OpenDSS-direct oracle on
SMART-DS, the richest open US feeder corpus this repo already vendors
elsewhere -- the low-risk follow-up Section 7 of `docs/paper.md` named right
after M15 shipped ("IEEE13/123 were enough to validate the approach, a
SMART-DS pass is a natural, low-risk follow-up now that it exists").

**Screening, not assuming, which feeder to use.** SMART-DS's own
"no_loadshapes" snapshot is a single fixed operating point, not a scaled
planning case -- experiment 09 already found the one cached rural feeder
(`rhs0_1247--rdt1527`, GSO/rural) has vmin=0.877 pu at its own declared load,
*before any new request*, the same baseline-infeasible trap M2/M3 hit with
EPRI's Ckt5. That finding turned out to generalize, not be a fluke of one
feeder: screened 3 more GSO/rural feeders (same hub and two neighbors) and 4
GSO/urban-suburban feeders (a different hub) directly through OpenDSS
(`ctx.Solution.Solve()` + a raw `puVmagAngle()` scan, no pandapower import
needed -- cheap enough to screen 8 feeders in well under a minute):

```
rhs0_1247--rdt1527 (rural, cached)   3294 buses  vmin=0.8777  INFEASIBLE
rhs0_1247--rdt1534 (rural)           2150 buses  vmin=0.8304  INFEASIBLE
rhs0_1247--rdt1948 (rural)           2735 buses  vmin=0.8772  INFEASIBLE
uhs0_1247--udt12274 (urban-suburban) 3851 buses  vmin=0.9252  INFEASIBLE
uhs0_1247--udt16115 (urban-suburban)  722 buses  vmin=0.9344  INFEASIBLE
uhs1_1247--udt20176 (urban-suburban) 5014 buses  vmin=0.9332  INFEASIBLE
uhs1_1247--udt15926 (urban-suburban) 2758 buses  vmin=0.9620  feasible
uhs0_1247--udt14717 (urban-suburban) 4591 buses  vmin=0.9439  feasible (chosen)
```

Every rural feeder tried is baseline-infeasible (long, sparsely regulated
laterals -- vmin 0.83-0.88 pu); urban-suburban feeders, shorter and denser,
are markedly closer to the 0.94 pu floor and two of four clear it. Chose
`uhs0_1247--udt14717`: the largest baseline-feasible feeder found (4,591
buses -- close to, though slightly under, the M3 ENWL feeder's 4,769-bus
scale, and well above IEEE123's 123), 81% single-/two-phase lines (the same
order as rdt1527's ~80% figure from experiment 09 -- genuine North-American
topology, not a fluke of the one feeder already tried), 542 service
transformers, 3,563 loads.

**A second, different baseline-infeasibility trap, on the thermal axis this
time -- found, not assumed away.** Unlike IEEE13 (M15: no line ampacities
declared at all, thermal loading meaningless as published), SMART-DS
declares real per-conductor `normamps` ratings. But applying them as-is
still fails the empty-subset baseline: 127 of 4,047 rated lines already
exceed 100% loading (up to 197.8%) serving the feeder's own *existing* load,
before any new request. Investigated, not dismissed: this is localized (median
line loading is 20%, only ~3% of lines are over 100%), and the same
transformer apparent-power-vs-rated-kVA fix M15 already made (raw current
comparison is invalid across a transformer's differently-rated windings) was
re-verified correct here rather than assumed to still hold. Scoped this
validation to the voltage axis, the same choice M0 made for EULV and M15
made for IEEE13 -- but for a third, distinct reason each time (EULV: thermal
genuinely isn't the binding constraint; IEEE13: ratings aren't declared;
here: the published snapshot's *existing* load already exceeds a real
minority of its own real ratings, a genuine reinforcement-backlog finding
consistent with this project's M2-follow-up finding on ENWL's own capacity
register, not a data-quality gap to fix).

**Result: the largest search-beats-baseline margin measured anywhere in this
project.** The chosen feeder clears the voltage floor by only 0.0039 pu
(0.39%) at baseline -- the thinnest margin of any network in this repo's
corpus. On 40 single-phase-tap EV/demand requests (6-17 kW, the band needed
to actually stress a margin this thin without every request already being
individually infeasible), `greedy_baseline_dss` accepts only 2/40 (28.4 kW):
request `ev2` (12.9 kW at bus `udm307`) is individually infeasible alone
(drops vmin to 0.9392, just under the floor) and, being third in queue
order, permanently blocks every request behind it -- even though most of the
other 37 are individually fine, confirmed directly. `nsga2_search_dss`
simply excludes `ev2` (and 3 others) and accepts the remaining 36/40 (417.3
kW) -- **+1367.0%**, dwarfing every prior margin in this project (M1: +3.6%,
M13: +7.7%, M14: +87%, M15/IEEE13: +192.4%) because this feeder's
near-zero baseline margin makes the serial "stop at first violation"
process maximally fragile to exactly one badly-placed early request, the
same failure mode M15 already named, now at its most extreme form yet.

Run:  uv run python experiments/40_smartds_dss_validation.py
"""

from __future__ import annotations

import random
import subprocess
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

REPO = Path(__file__).resolve().parents[1]
S3_PREFIX = ("s3://oedi-data-lake/SMART-DS/v1.0/2018/GSO/urban-suburban/scenarios/"
             "base_timeseries/opendss_no_loadshapes/uhs0_1247/uhs0_1247--udt14717")
FEEDER_DIR = REPO / "cache" / "smartds" / "uhs0_1247--udt14717"
MASTER = FEEDER_DIR / "Master.dss"

EXCLUDE_BUSES = {"udt14717-uhs0_1247x"}  # the circuit's own source bus
K = 40
KW_LO, KW_HI = 6.0, 17.0
SEED = 0
POP_SIZE, N_GEN = 20, 20  # sequential (n_jobs=1): ~80s on this 4,591-bus feeder --
# kept intentionally modest and single-process, not parallelized, since holding
# several full-feeder OpenDSS contexts alive at once on a network this size is a
# real, measured memory cost (n_jobs=4 briefly touched ~20 GB RSS in testing),
# not worth trading for speed on a feeder that already answers in ~80s.


def ensure_feeder() -> bool:
    """Download the feeder from OEDI (anonymous S3, ~2 MB) if not already cached."""
    if MASTER.exists():
        return True
    print(f"feeder not cached; downloading from {S3_PREFIX} ...")
    FEEDER_DIR.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            ["aws", "s3", "cp", "--no-sign-request", "--recursive",
             S3_PREFIX + "/", str(FEEDER_DIR) + "/",
             "--exclude", "*", "--include", "*.dss", "--include", "*.DSS"],
            check=True, capture_output=True, text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as e:
        print("  download failed:", getattr(e, "stderr", e))
        print(f"  Fetch manually with:\n    aws s3 cp --no-sign-request --recursive \\\n"
              f"      {S3_PREFIX}/ {FEEDER_DIR}/ --exclude '*' --include '*.dss' --include '*.DSS'")
        return False
    return MASTER.exists()


def candidate_attach_points(master_path: Path, exclude_buses: set[str]) -> list[tuple[str, int]]:
    """Every (bus, phase) pair actually present on the solved circuit -- same
    approach as experiment 39, OpenDSS's own `Bus.Nodes()` naturally handling a
    network where different buses have different phase counts."""
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


def build_requests(points: list[tuple[str, int]], k: int, kw_lo: float, kw_hi: float,
                    seed: int) -> list[DSSRequest]:
    rng = random.Random(seed)
    sample = rng.sample(points, k)
    return [DSSRequest(id=f"ev{i}", bus=bus, phase=phase, p_kw=rng.uniform(kw_lo, kw_hi), queue_order=i)
            for i, (bus, phase) in enumerate(sample)]


def main() -> None:
    if not ensure_feeder():
        return

    print("M16: SMART-DS via the M15 OpenDSS-direct oracle -- uhs0_1247--udt14717\n")

    base_problem = AllocationProblemDSS(master_path=str(MASTER), requests=[], max_loading_percent=1e6)
    base = base_problem.evaluate([])
    print(f"base case (no candidate requests): converged={base.converged}  feasible={base.feasible}  "
          f"vmin={base.vmin_pu:.4f}  vmax={base.vmax_pu:.4f}  pu")
    print("  (thermal loading excluded -- unlike IEEE13/123, this feeder DOES declare real line\n"
          "   ampacities, but ~3% are already over 100% at its own existing load; see this file's\n"
          "   own docstring. Voltage is this feeder's meaningful, baseline-feasible axis: margin\n"
          "   is only 0.0039 pu above the 0.94 floor.)\n")
    if not base.feasible:
        print("WARNING: empty subset is already infeasible -- the screening in this file's own\n"
              "         docstring was meant to rule this out; re-check before trusting results below.\n")

    points = candidate_attach_points(MASTER, EXCLUDE_BUSES)
    requests = build_requests(points, K, KW_LO, KW_HI, SEED)
    problem = AllocationProblemDSS(master_path=str(MASTER), requests=requests, max_loading_percent=1e6)

    print(f"== nsga2_search_dss vs. greedy_baseline_dss ({K} single-phase-tap EV/demand requests) ==")
    baseline = greedy_baseline_dss(problem)
    print(f"  greedy baseline: {len(baseline.accepted_ids)}/{K} accepted, "
          f"{baseline.accommodated_mw*1000:.1f} kW, vmin={baseline.vmin_pu:.4f} pu")

    t0 = time.time()
    results = nsga2_search_dss(problem, pop_size=POP_SIZE, n_gen=N_GEN, seed=SEED)
    dt = time.time() - t0
    feasible = [r for r in results if r.feasible]
    comparison = compare_to_baseline(results, baseline)
    best = comparison.best_feasible_search
    print(f"  nsga2_search_dss ({dt:.0f}s): {len(feasible)}/{len(results)} feasible individuals")
    if best is not None:
        gain_pct = (best.accommodated_mw / baseline.accommodated_mw - 1) * 100 if baseline.accommodated_mw else float("nan")
        print(f"  best feasible: {len(best.accepted_ids)}/{K} accepted, "
              f"{best.accommodated_mw*1000:.1f} kW, vmin={best.vmin_pu:.4f} pu -- "
              f"{'beats' if comparison.dominates else 'does NOT beat'} the baseline "
              f"({gain_pct:+.1f}%)")
    else:
        print("  no feasible individual found")


if __name__ == "__main__":
    main()
