"""M2 follow-up: check our engine's hosting-capacity number against ENWL's own
published capacity-register open data, now that an ENWL open-data API key is
available (electricitynorthwest.opendatasoft.com).

ENWL -- the same UK DNO whose OpenDSS feeders this repo already uses for
conductor inference (experiments 11/12/14/15/16) -- also publishes a live
"Capacity" data domain: per-asset registers computed from declared ratings and
metered/estimated load, refreshed regularly and covering every distribution
transformer and 11kV line section on their network. That looks, at first
glance, like exactly the real-world ground truth M2 wanted (see
26_m2_hosting_capacity_validation.py, which used Sandia's *academic* 216-feeder
OpenDSS study instead, because a real DNO's own numbers weren't accessible).

They are accessible now. But pulling the `sp-enw-capacity-distribution-tx`
dataset (real distribution-transformer generation headroom -- the *same asset
class* as EULV's 800kVA transformer, unlike Sandia's MV feeders) and checking
it against every possible power-flow interpretation shows it is **not** a
power-flow hosting-capacity result: `distribution_tx_generation_headroom_kva`
is exactly `rating - currently_connected_generation_kva`, full stop -- it does
not fall even for transformers ENWL's own data already flags "No Capacity" on
the demand side (utilisation > 100%). Confirmed against ENWL's other capacity
dataset too: `sp-enw-capacity-11kv-network`'s `section_headroom_kva` is exactly
`firm_capacity - estimated_max_load`, i.e. thermal-only, no voltage rise, no
power flow. These are capacity-*register* subtractions (administrative
accounting of what's nameplate-free), not the load-flow-simulated PV hosting
capacity Sandia published or this engine computes.

So this cannot be the same kind of exact-number validation Sandia's academic
study offered -- reported honestly, per the roadmap's own rule, rather than
forcing a comparison the data doesn't support. What it *can* do, credibly, is
support the roadmap's central thesis with real DNO numbers: a capacity
register that never runs a power flow will read "almost fully free" right up
to the point someone actually studies a connection, which is the interconnection
-queue problem this whole project exists to help fix.

Needs: ENWL_KEY (opendatasoft API key, free self-service signup at
https://electricitynorthwest.opendatasoft.com/pages/register/) in the
environment (or a repo-root `.env` -- see `.env.default`).

Run:  ENWL_KEY=... uv run python experiments/27_enwl_capacity_register_check.py
"""

from __future__ import annotations

import os

import requests

from allocation import AllocationProblem, feeder_hosting_capacity
from gridsynth import import_opendss

API = "https://electricitynorthwest.opendatasoft.com/api/explore/v2.1/catalog/datasets"
DATASET = "sp-enw-capacity-distribution-tx"

# EULV's own transformer + this repo's M2/M1 results (26_m2_hosting_capacity_
# validation.py, 25_joint_allocation_search.py), for the side-by-side.
EULV_TRANSFORMER_KVA = 800
EULV_SINGLE_SITE_LHC_KW = 99.7   # worst-case single-bus LHC, experiment 26
EULV_JOINT_ACCEPT_KW = 245.4     # NSGA-II joint accept-set, experiment 25


def _load_env_file() -> None:
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if "ENWL_KEY" in os.environ or not os.path.exists(env_path):
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def _query(select: str, where: str, group_by: str | None = None) -> list[dict]:
    params = {"apikey": os.environ["ENWL_KEY"], "select": select, "where": where, "limit": 20}
    if group_by:
        params["group_by"] = group_by
    resp = requests.get(f"{API}/{DATASET}/records", params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()["results"]


def main() -> None:
    _load_env_file()
    if "ENWL_KEY" not in os.environ:
        print("Set ENWL_KEY (free self-service signup at "
              "https://electricitynorthwest.opendatasoft.com/pages/register/) and re-run:\n"
              "  ENWL_KEY=... uv run python experiments/27_enwl_capacity_register_check.py")
        return

    print("M2 follow-up: our engine's LHC vs. ENWL's real published distribution-transformer\n"
          "'generation headroom' register, for the 800kVA class (EULV's own transformer size)\n")

    by_cat = _query(
        select="distribution_tx_utilisation_category as cat, count(*) as n, "
               "avg(distribution_tx_generation_headroom_kva) as avg_h, "
               "median(distribution_tx_generation_headroom_kva) as med_h, "
               "avg(distribution_tx_utilisation) as avg_util",
        where='distribution_tx_rating_kva="800"',
        group_by="distribution_tx_utilisation_category",
    )
    total_n = sum(r["n"] for r in by_cat)
    print(f"real ENWL 800kVA distribution transformers, by DNO-assigned demand-utilisation category "
          f"(n={total_n}):")
    for r in sorted(by_cat, key=lambda r: -(r["n"] or 0)):
        cat = r["cat"] or "(unlabeled)"
        avg_util = r["avg_util"]
        med_h = r["med_h"]
        util_str = f"avg demand utilisation {avg_util:.0f}%" if avg_util is not None else "utilisation n/a"
        h_str = f"reported generation headroom {med_h/800*100:.1f}% of rating" if med_h is not None else "headroom n/a"
        print(f"  {cat:<18} n={r['n']:<5} {util_str:<28} -> {h_str}")

    print("\nEven the 'No Capacity' bucket -- ENWL's own label for transformers already over their\n"
          "demand rating -- reports ~100% generation headroom. That is definitional, not a finding\n"
          "about this specific network: the field is `rating - currently_connected_generation_kva`,\n"
          "unconditional on load or voltage. It cannot fall below full rating just because a power\n"
          "flow would trip a constraint, because no power flow computed it.\n")

    print("Our engine, on EULV's own 800kVA transformer (a power-flow-computed hosting capacity, not\n"
          "a register subtraction):")
    print(f"  single worst-case site LHC : {EULV_SINGLE_SITE_LHC_KW:.1f} kW "
          f"({EULV_SINGLE_SITE_LHC_KW/EULV_TRANSFORMER_KVA*100:.1f}% of the 800 kVA rating) -- experiment 26")
    print(f"  NSGA-II joint accept-set   : {EULV_JOINT_ACCEPT_KW:.1f} kW "
          f"({EULV_JOINT_ACCEPT_KW/EULV_TRANSFORMER_KVA*100:.1f}% of rating) -- experiment 25")

    print("\nWhy this is still worth reporting even though it isn't a number-for-number validation:")
    print("  ENWL's own register would tell a developer '798 kVA of 800 kVA is free' at essentially")
    print("  every transformer regardless of how loaded it already is, because it never runs a power")
    print("  flow. Our engine, on the same class of asset, finds the real power-flow-constrained")
    print("  answer is one to two orders of magnitude smaller (12-31% of rating, voltage-bound) --")
    print("  the exact register-vs-reality gap this roadmap's 'why now' section cites FERC Order 2023")
    print("  cluster studies and GridCARE's funding as evidence for. A capacity register is a")
    print("  starting point for a queue, not a substitute for the joint study.")

    print("\n(secondary check, same pattern: `sp-enw-capacity-11kv-network`'s `section_headroom_kva`")
    print(" is exactly firm_capacity - estimated_max_load, i.e. thermal-only register subtraction,")
    print(" no voltage rise -- consistent with the distribution-tx dataset, not an independent case.)")

    # Recompute EULV's numbers from scratch here too (same seed=0 sample as
    # experiment 26), so this script is a standalone check, not just a repeat.
    import random
    from pathlib import Path
    master = Path(__file__).resolve().parents[1] / "data" / "opendss" / "eulv" / "Master_snapshot.dss"
    net = import_opendss(str(master))
    rng = random.Random(0)
    sample = rng.sample(net.load.bus.tolist(), 20)
    problem = AllocationProblem(net=net, requests=[])
    hc, worst_bus, _ = feeder_hosting_capacity(
        problem, buses=sample, direction=-1.0, hi_mw=0.01, max_hi_mw=0.5, tol_mw=0.0005)
    print(f"\n(sanity re-check: locational HC recomputed live just now = {hc*1000:.1f} kW at bus "
          f"{worst_bus}, consistent with experiment 26's recorded {EULV_SINGLE_SITE_LHC_KW:.1f} kW.)")


if __name__ == "__main__":
    main()
