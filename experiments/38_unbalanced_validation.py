"""M14 of the joint-allocation roadmap: does the engine work on a genuinely
unbalanced network?

M13 split ROADMAP.md's "non-radial or unbalanced" open item into two
independent axes and closed the cheap one (non-radial). This milestone
closes the other, on the one sub-case it's actually achievable for:
unbalanced *loading* on a symmetric 3-phase topology -- every bus/line is a
full 3-phase node (e.g. this project's EULV/ENWL networks, or the native
network this experiment uses), but a single-phase-tap request (a real
residential PV/EV inverter, wired to one phase of a 3-phase trunk) loads
the three phases unevenly. `AllocationProblem.evaluate_sizes_3ph` (new in
M14) solves this via `pandapower.runpp_3ph` and folds all three phases'
voltage/loading into the same scalar `AllocationResult` shape `evaluate_
sizes` already returns.

What this milestone deliberately does *not* attempt: truly unsymmetrical
North-American topology (single-phase laterals, center-tapped split-phase
secondaries -- IEEE13/123, SMART-DS). Investigated directly, not assumed
away: pandapower's `runpp_3ph` is a symmetrical-component solver that
cannot correctly represent single-phase-connected elements at all
(pandapower issue #873, open, no workaround) -- a tooling gap, not a
missing-code gap, so building an oracle on top of it for that topology
would silently produce methodologically wrong results, not just a
harder-to-code one. That axis stays open; see docs/joint-allocation-
scope.md's M14 for the full trail (including two more "checked, not
assumed" catches along the way: `import_opendss`-imported networks are
missing `runpp_3ph`'s required ext_grid zero-sequence fields entirely, and
`runpp_3ph` itself does not raise on non-convergence the way `runpp`
does -- it silently returns an empty/all-NaN result with a stale
``converged=True`` flag, which `evaluate_sizes_3ph` guards against
explicitly).

The network: `pandapower.networks.ieee_european_lv_asymmetric()`, the same
907-bus/55-house European LV feeder topology this repo's own EULV network
mirrors, but pandapower's *native* asymmetric build -- already carrying
every zero-sequence field `runpp_3ph` needs, and already proven compatible
with it in Part I (experiments 01/03/04/05).

Two things are checked, not one: (1) the same total requested export power
is judged safe when spread across all three phases but *unsafe* when
concentrated on one -- the concrete reason this axis matters, not just a
mechanical checkbox. (2) `nsga2_search_3ph`/`greedy_baseline_3ph` replicate
M1's own "search beats baseline" bar on this new oracle.

Run:  uv run python experiments/38_unbalanced_validation.py
"""

from __future__ import annotations

import random
import time
import warnings
from pathlib import Path

import pandapower as pp
import pandapower.networks as pn

from allocation import (
    AllocationProblem,
    Request,
    compare_to_baseline,
    greedy_baseline_3ph,
    nsga2_search_3ph,
)

REPO = Path(__file__).resolve().parents[1]

PV_KW_LO, PV_KW_HI = 3.0, 10.0  # same band as experiments 24/25/33/37
K, POP_SIZE, N_GEN, SEED = 40, 40, 40, 0  # same budget as experiment 25/33's eulv run
PHASES = ("a", "b", "c")

# Part 1's demonstration: same total export (15 x 5 kWp = 75 kWp), spread
# across phases vs. concentrated on the network's tightest phase (phase C,
# already closest to the 1.10 pu ceiling in the base case -- checked below).
DEMO_N, DEMO_KW = 15, 5.0


def build_requests(load_buses: list[int], k: int, seed: int) -> list[Request]:
    rng = random.Random(seed)
    buses = rng.sample(load_buses, k)
    return [Request(id=f"pv{i}", bus=bus, p_mw=-rng.uniform(PV_KW_LO, PV_KW_HI) / 1000.0,
                     phase=rng.choice(PHASES), queue_order=i)
            for i, bus in enumerate(buses)]


def demonstrate_phase_concentration(net: pp.pandapowerNet, load_buses: list[int]) -> None:
    print("== (1) same total export, spread vs. concentrated on one phase ==")
    rng = random.Random(0)
    buses = rng.sample(load_buses, DEMO_N)

    spread = [Request(id=f"d{i}", bus=b, p_mw=-DEMO_KW / 1000.0, phase=None, queue_order=i)
              for i, b in enumerate(buses)]
    p_spread = AllocationProblem(net=net, requests=spread)
    r_spread = p_spread.evaluate_3ph([r.id for r in spread])
    print(f"  spread across all 3 phases:      vmax={r_spread.vmax_pu:.4f} pu  "
          f"feasible={r_spread.feasible}  ({DEMO_N * DEMO_KW:.0f} kWp total)")

    concentrated = [Request(id=f"d{i}", bus=b, p_mw=-DEMO_KW / 1000.0, phase="c", queue_order=i)
                     for i, b in enumerate(buses)]
    p_conc = AllocationProblem(net=net, requests=concentrated)
    r_conc_3ph = p_conc.evaluate_3ph([r.id for r in concentrated])
    print(f"  concentrated on phase C (3ph):    vmax={r_conc_3ph.vmax_pu:.4f} pu  "
          f"feasible={r_conc_3ph.feasible}  (same {DEMO_N * DEMO_KW:.0f} kWp total)")

    r_conc_balanced = p_conc.evaluate([r.id for r in concentrated])
    print(f"  same accept-set, positive-sequence evaluate() (phase ignored):\n"
          f"    vmax={r_conc_balanced.vmax_pu:.4f} pu  feasible={r_conc_balanced.feasible}")
    if r_conc_balanced.feasible and not r_conc_3ph.feasible:
        print("  -> the balanced oracle would accept this set; the unbalanced one correctly rejects it.")
    print()


def search_vs_baseline(net: pp.pandapowerNet, load_buses: list[int]) -> None:
    print(f"== (2) nsga2_search_3ph vs. greedy_baseline_3ph, {K} single-phase-tap requests ==")
    requests = build_requests(load_buses, K, SEED)
    problem = AllocationProblem(net=net, requests=requests)

    baseline = greedy_baseline_3ph(problem)
    print(f"  greedy baseline: {len(baseline.accepted_ids)}/{K} accepted, "
          f"{baseline.accommodated_mw*1000:.1f} kW, vmax={baseline.vmax_pu:.4f} pu")

    t0 = time.time()
    results = nsga2_search_3ph(problem, pop_size=POP_SIZE, n_gen=N_GEN, seed=SEED)
    dt = time.time() - t0
    feasible = [r for r in results if r.feasible]
    comparison = compare_to_baseline(results, baseline)
    best = comparison.best_feasible_search
    print(f"  nsga2_search_3ph ({dt:.0f}s): {len(feasible)}/{len(results)} feasible individuals")
    if best is not None:
        print(f"  best feasible: {len(best.accepted_ids)}/{K} accepted, "
              f"{best.accommodated_mw*1000:.1f} kW, vmax={best.vmax_pu:.4f} pu -- "
              f"{'beats' if comparison.dominates else 'does NOT beat'} the baseline "
              f"({baseline.accommodated_mw*1000:.1f} kW)")
    else:
        print("  no feasible individual found")


def main() -> None:
    warnings.filterwarnings("ignore")  # pandapower's 3ph solver is chatty on RuntimeWarning
    net = pn.ieee_european_lv_asymmetric()
    load_buses = net.asymmetric_load.bus.tolist()

    pp.runpp_3ph(net)
    vm = net.res_bus_3ph[["vm_a_pu", "vm_b_pu", "vm_c_pu"]]
    print("M14: unbalanced-network validation -- ieee_european_lv_asymmetric "
          f"({len(net.bus)} buses, {net.asymmetric_load.bus.nunique()} houses)\n")
    print(f"  base case (existing households only) already shows real per-phase imbalance:")
    print(f"    phase A: {vm.vm_a_pu.min():.4f}-{vm.vm_a_pu.max():.4f} pu "
          f"(mean {vm.vm_a_pu.mean():.4f})")
    print(f"    phase B: {vm.vm_b_pu.min():.4f}-{vm.vm_b_pu.max():.4f} pu "
          f"(mean {vm.vm_b_pu.mean():.4f})")
    print(f"    phase C: {vm.vm_c_pu.min():.4f}-{vm.vm_c_pu.max():.4f} pu "
          f"(mean {vm.vm_c_pu.mean():.4f})\n")

    demonstrate_phase_concentration(net, load_buses)
    search_vs_baseline(net, load_buses)


if __name__ == "__main__":
    main()
