"""Offline tests for the honest-baseline revision (M22-M26): the binary
skip-and-continue FCFS baseline (`skip_continue_baseline`) and the second,
max-displacement (L-infinity) fairness metric (`max_queue_displacement`).

Same minimal two-bus radial feeder as test_allocation.py / test_allocation_fairness.py.
"""

import pandapower as pp
import pytest

from allocation import (
    AllocationProblem,
    Request,
    greedy_baseline,
    max_queue_displacement,
    queue_fairness_violations,
    skip_continue_baseline,
)
from allocation.baseline import reinforced_greedy_baseline


@pytest.fixture
def net():
    net = pp.create_empty_network()
    b0 = pp.create_bus(net, vn_kv=0.4, name="source")
    b1 = pp.create_bus(net, vn_kv=0.4, name="load")
    pp.create_ext_grid(net, bus=b0, vm_pu=1.0)
    pp.create_line_from_parameters(
        net, from_bus=b0, to_bus=b1, length_km=0.1,
        r_ohm_per_km=0.5, x_ohm_per_km=0.1, c_nf_per_km=0.0, max_i_ka=0.05)
    return net


REQUESTS = [Request(id=f"r{i}", bus=1, p_mw=0.001, queue_order=i) for i in range(5)]


# -- max_queue_displacement ---------------------------------------------------

def test_displacement_zero_for_prefix_empty_and_full():
    assert max_queue_displacement(REQUESTS, {"r0", "r1", "r2"}) == 0
    assert max_queue_displacement(REQUESTS, set()) == 0
    assert max_queue_displacement(REQUESTS, {r.id for r in REQUESTS}) == 0


def test_displacement_of_a_single_jump():
    # r0 rejected, r1 accepted: r1 cut ahead of exactly 1 earlier request.
    assert max_queue_displacement(REQUESTS, {"r1"}) == 1


def test_displacement_is_the_max_not_the_sum():
    # r0,r1,r2 rejected; r3,r4 accepted: each cut ahead of 3 earlier rejected.
    # inversions (L1) sums to 6; max-displacement (Linf) is 3.
    assert queue_fairness_violations(REQUESTS, {"r3", "r4"}) == 6
    assert max_queue_displacement(REQUESTS, {"r3", "r4"}) == 3


def test_displacement_distinguishes_many_small_jumps_from_one_big_one():
    reqs = [Request(id=f"r{i}", bus=1, p_mw=0.001, queue_order=i) for i in range(6)]
    # accept every other request: r1,r3,r5 -> each jumps 1,2,3 earlier rejects.
    many_small = {"r1", "r3", "r5"}
    # reject r0..r4, accept r5 only -> one request, jumped 5 (but few inversions).
    one_big = {"r5"}
    assert max_queue_displacement(reqs, many_small) == 3
    assert max_queue_displacement(reqs, one_big) == 5
    # the L1 count orders them the other way -- the two metrics genuinely differ.
    assert queue_fairness_violations(reqs, many_small) == 6
    assert queue_fairness_violations(reqs, one_big) == 5


def test_displacement_never_exceeds_inversions():
    for acc in ({"r1"}, {"r3", "r4"}, {"r2", "r4"}, {"r0", "r4"}):
        assert max_queue_displacement(REQUESTS, acc) <= queue_fairness_violations(REQUESTS, acc)


def test_displacement_order_independent_of_iteration():
    assert max_queue_displacement(list(reversed(REQUESTS)), {"r3", "r4"}) == 3


# -- skip_continue_baseline ---------------------------------------------------

def test_skip_continue_equals_reinforced_zero_budget(net):
    many = [Request(id=f"r{i}", bus=1, p_mw=0.005, queue_order=i) for i in range(15)]
    problem = AllocationProblem(net=net, requests=many)
    sc = skip_continue_baseline(problem)
    r0 = reinforced_greedy_baseline(problem, budget_eur=0.0).result
    assert sc.accepted_ids == r0.accepted_ids
    assert sc.accommodated_mw == pytest.approx(r0.accommodated_mw)


def test_skip_continue_walks_past_an_infeasible_request(net):
    # A huge early request is individually infeasible; a small later one fits.
    requests = [
        Request(id="huge", bus=1, p_mw=1.0, queue_order=0),
        Request(id="small", bus=1, p_mw=0.001, queue_order=1),
    ]
    problem = AllocationProblem(net=net, requests=requests)
    stop_dead = greedy_baseline(problem)
    skip = skip_continue_baseline(problem)
    # stop-dead freezes at the first violation and seats nothing.
    assert "small" not in stop_dead.accepted_ids
    # skip-continue sets the huge one aside and still seats the small one.
    assert "huge" not in skip.accepted_ids
    assert "small" in skip.accepted_ids
    assert skip.accommodated_mw > stop_dead.accommodated_mw


def test_skip_continue_accepts_everything_when_unbound(net):
    # A handful of tiny requests all fit: skip-continue seats all of them, and
    # (like a prefix) incurs no queue displacement.
    tiny = [Request(id=f"r{i}", bus=1, p_mw=0.0005, queue_order=i) for i in range(4)]
    problem = AllocationProblem(net=net, requests=tiny)
    skip = skip_continue_baseline(problem)
    assert skip.accepted_ids == {r.id for r in tiny}
    assert max_queue_displacement(problem.requests, skip.accepted_ids) == 0


def test_skip_continue_can_incur_a_queue_jump(net):
    requests = [
        Request(id="huge", bus=1, p_mw=1.0, queue_order=0),
        Request(id="small", bus=1, p_mw=0.001, queue_order=1),
    ]
    problem = AllocationProblem(net=net, requests=requests)
    skip = skip_continue_baseline(problem)
    # unlike greedy_baseline's prefix guarantee, skip-continue can jump the queue.
    assert queue_fairness_violations(problem.requests, skip.accepted_ids) == 1
    assert max_queue_displacement(problem.requests, skip.accepted_ids) == 1


# -- skip_continue_baseline_dss (the OpenDSS-direct honest FCFS comparator) ----

_DSS_MASTER = """\
Clear
Set DefaultBaseFrequency=60
New Circuit.test basekv=4.16 bus1=sourcebus pu=1.0 phases=3 MVAsc3=20000 MVAsc1=21000
New Line.trunk phases=3 bus1=sourcebus bus2=b1 length=0.1 units=km r1=0.3 x1=0.6 r0=0.4 x0=1.2 c1=0 c0=0 normamps=400
New Line.lateral phases=1 bus1=b1.1 bus2=b2.1 length=0.1 units=km r1=0.5 x1=0.8 r0=0.5 x0=0.8 c1=0 c0=0 normamps=5
New Load.base bus1=b1.1.2.3 phases=3 kV=4.16 kW=10 kvar=3 conn=wye
Set VoltageBases=[4.16]
CalcVoltageBases
"""


def test_skip_continue_dss_walks_past_an_infeasible_request(tmp_path):
    from allocation import AllocationProblemDSS, DSSRequest, greedy_baseline_dss, skip_continue_baseline_dss

    master = tmp_path / "master.dss"
    master.write_text(_DSS_MASTER)
    # "large" (15 kW on a normamps=5 lateral) is individually infeasible and sits
    # first in the queue; "small" fits and sits behind it.
    requests = [
        DSSRequest(id="large", bus="b2", phase=1, p_kw=15.0, queue_order=0),
        DSSRequest(id="small", bus="b2", phase=1, p_kw=1.0, queue_order=1),
    ]
    problem = AllocationProblemDSS(master_path=str(master), requests=requests)
    stop_dead = greedy_baseline_dss(problem)
    skip = skip_continue_baseline_dss(problem)
    # stop-dead freezes at "large" and seats nothing; skip-continue skips it and
    # still seats "small".
    assert "small" not in stop_dead.accepted_ids
    assert "large" not in skip.accepted_ids
    assert "small" in skip.accepted_ids
    assert skip.accommodated_mw > stop_dead.accommodated_mw


# -- nsga2_search_dss_seeded (M28: does warm-starting matter?) ----------------

def _seeded_test_problem(tmp_path):
    from allocation import AllocationProblemDSS, DSSRequest

    master = tmp_path / "master.dss"
    master.write_text(_DSS_MASTER)
    requests = [DSSRequest(id=f"r{i}", bus="b2", phase=1, p_kw=1.0, queue_order=i) for i in range(6)] + [
        DSSRequest(id="huge", bus="b2", phase=1, p_kw=15.0, queue_order=6)]
    return AllocationProblemDSS(master_path=str(master), requests=requests)


def test_nsga2_search_dss_seeded_never_worse_than_the_seed(tmp_path):
    # Elitism guarantee: however tiny the budget, seeding generation 0 with a
    # known-feasible accept-set means the final population can never do worse
    # than that accept-set's own accommodated_mw -- it's always in the pool
    # NSGA-II's survival selection compares against.
    from allocation import nsga2_search_dss_seeded, skip_continue_baseline_dss

    problem = _seeded_test_problem(tmp_path)
    skip = skip_continue_baseline_dss(problem)
    for trial_seed in range(5):
        results = nsga2_search_dss_seeded(
            problem, seed_accept_sets=[skip.accepted_ids], pop_size=4, n_gen=1, seed=trial_seed)
        feasible = [r for r in results if r.feasible]
        assert feasible
        best = max(r.accommodated_mw for r in feasible)
        assert best >= skip.accommodated_mw - 1e-9


def test_nsga2_search_dss_seeded_recovers_what_blind_search_can_miss(tmp_path):
    # At the same tiny budget, unseeded random search can land short of the
    # baseline it's supposed to beat (nothing guarantees generation 0 resembles
    # a good accept-set); seeding removes that risk. This is exactly the
    # confound experiment 57 flagged on SMART-DS and could not separate from a
    # genuine "no room to improve" result.
    from allocation import nsga2_search_dss, nsga2_search_dss_seeded, skip_continue_baseline_dss

    problem = _seeded_test_problem(tmp_path)
    skip = skip_continue_baseline_dss(problem)

    unseeded = nsga2_search_dss(problem, pop_size=4, n_gen=1, seed=0)
    unseeded_feasible = [r for r in unseeded if r.feasible]
    unseeded_best = max((r.accommodated_mw for r in unseeded_feasible), default=0.0)
    assert unseeded_best < skip.accommodated_mw  # blind search falls short here

    seeded = nsga2_search_dss_seeded(
        problem, seed_accept_sets=[skip.accepted_ids], pop_size=4, n_gen=1, seed=0)
    seeded_feasible = [r for r in seeded if r.feasible]
    seeded_best = max(r.accommodated_mw for r in seeded_feasible)
    assert seeded_best >= skip.accommodated_mw - 1e-9
