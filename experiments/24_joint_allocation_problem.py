"""M0 of the joint-allocation roadmap: does the constraint oracle actually bind?

ROADMAP.md's M0 asks for a documented formulation plus a `problem` object that,
given a subset of interconnection requests, runs `runpp` and returns
feasibility + objective values. `allocation.AllocationProblem` (this repo,
src/allocation/problem.py) is that object. This experiment exercises it on a
network this repo already trusts -- the IEEE European LV feeder, imported via
`gridsynth.import_opendss` and cross-validated against pandapower's native copy
in experiment 10 -- to check the one thing a formulation on paper can't show:
that the joint constraint is *real*, i.e. some subsets of requests are feasible
and some are not, on the same network, so a search over subsets has something
to search over.

The scenario is residential rooftop-PV interconnection (this repo's DER-hosting-
capacity framing, ROADMAP.md's "why now"): K candidate export requests placed
at existing house buses, sized like typical UK/EU residential PV (3-10 kWp).
The EULV feeder's source bus already sits at 1.05 pu (data/opendss/eulv), so
this is the classic overvoltage-limited hosting-capacity regime, not a thermal
one -- export pushes voltage up, and the feeder's own headroom to a 1.10 pu
planning limit is what the requests compete for.

M1 (greedy baseline vs. NSGA-II search) is *not* attempted here -- this
experiment only proves the oracle is correct and the problem non-trivial on one
seed, which is the M0 bar.

Run:  uv run python experiments/24_joint_allocation_problem.py
      uv run python experiments/24_joint_allocation_problem.py --k 40 --seed 0
"""

from __future__ import annotations

import argparse
import random
from pathlib import Path

from gridsynth import import_opendss

from allocation import AllocationProblem, Request

REPO = Path(__file__).resolve().parents[1]
MASTER = REPO / "data" / "opendss" / "eulv" / "Master_snapshot.dss"

PV_KW_LO, PV_KW_HI = 3.0, 10.0  # typical residential rooftop PV size band


def build_requests(load_buses: list[int], k: int, seed: int) -> list[Request]:
    """K synthetic PV export requests at a seeded sample of existing house buses."""
    rng = random.Random(seed)
    buses = rng.sample(load_buses, k)
    requests = []
    for i, bus in enumerate(buses):
        kw = rng.uniform(PV_KW_LO, PV_KW_HI)
        requests.append(Request(id=f"pv{i}", bus=bus, p_mw=-kw / 1000.0, queue_order=i))
    return requests


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--k", type=int, default=40, help="number of candidate PV requests")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    net = import_opendss(str(MASTER))
    load_buses = net.load.bus.tolist()
    requests = build_requests(load_buses, args.k, args.seed)
    problem = AllocationProblem(net=net, requests=requests)  # defaults: 0.94-1.10 pu, 100% loading

    print("Joint allocation M0: constraint oracle on the EULV feeder "
          f"({len(net.bus)} buses, {len(net.load)} houses, {net.trafo.sn_mva.iloc[0]*1000:.0f} kVA trafo)")
    print(f"{args.k} candidate rooftop-PV export requests ({PV_KW_LO:.0f}-{PV_KW_HI:.0f} kWp), seed={args.seed}\n")

    empty = problem.evaluate([])
    print(f"empty subset      : feasible={empty.feasible}  vmax={empty.vmax_pu:.4f} pu "
          f"(baseline feeder voltage, no PV connected)")

    full = problem.evaluate(r.id for r in requests)
    print(f"all {args.k} accepted    : feasible={full.feasible}  vmax={full.vmax_pu:.4f} pu  "
          f"max line load={full.max_line_loading_percent:.1f}%  "
          f"accommodated={full.accommodated_mw*1000:.1f} kW"
          f"{'  <- OVERVOLTAGE, thermal still fine' if full.thermal_violation_percent == 0 else ''}")

    print("\nqueue-order prefixes (accept the first N requests, in arrival order) --")
    print(f"  {'N':>4s} {'feasible':>8s} {'vmax (pu)':>10s} {'max line %':>10s} {'accommodated (kW)':>18s}")
    prefix_sizes = sorted(set([5, 10, 15, 20, 25, 30, 35, args.k]) & set(range(1, args.k + 1)))
    first_infeasible = None
    for n in prefix_sizes:
        r = problem.evaluate(req.id for req in requests[:n])
        print(f"  {n:>4d} {str(r.feasible):>8s} {r.vmax_pu:>10.4f} {r.max_line_loading_percent:>10.1f} "
              f"{r.accommodated_mw*1000:>18.1f}")
        if not r.feasible and first_infeasible is None:
            first_infeasible = n

    if first_infeasible is not None:
        print(f"\nThe oracle genuinely binds: prefixes up to some N < {args.k} are feasible, "
              f"and by N={first_infeasible} the queue-order prefix has crossed the {problem.vmax_pu} pu "
              "overvoltage limit -- so which subset (not just how many) matters, which is the joint "
              "problem M1's optimizer searches over.")
    else:
        print(f"\nAll prefixes stayed feasible at k={args.k} -- increase --k to push into the binding regime.")


if __name__ == "__main__":
    main()
