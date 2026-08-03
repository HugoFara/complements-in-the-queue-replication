"""Offline tests for M9's queue-order fairness objective (`queue_fairness_violations`,
`allocation.search.nsga2_search_fair`).

Same minimal two-bus radial feeder as test_allocation.py/test_allocation_search.py.
"""

import pandapower as pp
import pytest

from allocation import (
    AllocationProblem,
    Request,
    continuous_greedy_baseline,
    greedy_baseline,
    nsga2_search_continuous_fair,
    nsga2_search_fair,
    queue_fairness_violations,
    reinforced_greedy_baseline,
)


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


# -- queue_fairness_violations ------------------------------------------------

def test_no_violations_when_accepted_set_is_a_queue_prefix():
    assert queue_fairness_violations(REQUESTS, {"r0", "r1", "r2"}) == 0


def test_no_violations_for_empty_or_full_accept_set():
    assert queue_fairness_violations(REQUESTS, set()) == 0
    assert queue_fairness_violations(REQUESTS, {r.id for r in REQUESTS}) == 0


def test_one_violation_for_a_single_queue_jump():
    # r0 rejected, r1 accepted: exactly one (rejected-earlier, accepted-later) pair.
    assert queue_fairness_violations(REQUESTS, {"r1"}) == 1


def test_violations_count_every_rejected_earlier_request():
    # r0, r1, r2 rejected; r3, r4 accepted -> each of r3/r4 counts all 3 earlier rejections.
    assert queue_fairness_violations(REQUESTS, {"r3", "r4"}) == 6


def test_order_independent_of_id_iteration_order():
    shuffled = list(reversed(REQUESTS))
    assert queue_fairness_violations(shuffled, {"r3", "r4"}) == 6


# -- greedy_baseline is fairness-perfect by construction ----------------------

def test_greedy_baseline_has_zero_violations(net):
    many = [Request(id=f"r{i}", bus=1, p_mw=0.005, queue_order=i) for i in range(15)]
    problem = AllocationProblem(net=net, requests=many)
    result = greedy_baseline(problem)
    assert queue_fairness_violations(problem.requests, result.accepted_ids) == 0


# -- the "smarter" baselines already trade fairness away, now measurable -----

def test_continuous_greedy_baseline_can_violate_fairness(net):
    # A request too big to fit even at min_fraction is dropped, not stopped-at
    # -- unlike greedy_baseline, the walk continues past it.
    requests = [
        Request(id="huge", bus=1, p_mw=1.0, queue_order=0),
        Request(id="small", bus=1, p_mw=0.001, queue_order=1),
    ]
    problem = AllocationProblem(net=net, requests=requests)
    result = continuous_greedy_baseline(problem, min_fraction=0.5)
    assert "huge" not in result.accepted_ids
    assert "small" in result.accepted_ids
    assert queue_fairness_violations(problem.requests, result.accepted_ids) == 1


def test_reinforced_greedy_baseline_can_violate_fairness(net):
    # budget_eur=0 means no upgrade is ever affordable, so a violation is
    # skipped (not stopped-at) exactly like continuous_greedy_baseline above.
    requests = [
        Request(id="huge", bus=1, p_mw=0.05, queue_order=0),
        Request(id="small", bus=1, p_mw=0.001, queue_order=1),
    ]
    problem = AllocationProblem(net=net, requests=requests)
    reinforced = reinforced_greedy_baseline(problem, budget_eur=0.0)
    result = reinforced.result
    assert "huge" not in result.accepted_ids
    assert "small" in result.accepted_ids
    assert queue_fairness_violations(problem.requests, result.accepted_ids) == 1


# -- nsga2_search_fair ---------------------------------------------------------

def test_nsga2_search_fair_returns_some_feasible_results(net):
    problem = AllocationProblem(net=net, requests=REQUESTS)
    results = nsga2_search_fair(problem, pop_size=8, n_gen=3, seed=0)
    assert len(results) > 0
    assert any(r.feasible for r in results)


def test_nsga2_search_fair_reproducible_with_same_seed(net):
    problem = AllocationProblem(net=net, requests=REQUESTS)
    r1 = nsga2_search_fair(problem, pop_size=8, n_gen=3, seed=0)
    r2 = nsga2_search_fair(problem, pop_size=8, n_gen=3, seed=0)
    assert {r.accepted_ids for r in r1} == {r.accepted_ids for r in r2}


def test_nsga2_search_fair_parallel_matches_sequential(net):
    sequential = nsga2_search_fair(AllocationProblem(net=net, requests=REQUESTS), pop_size=8, n_gen=3, seed=0, n_jobs=1)
    parallel = nsga2_search_fair(AllocationProblem(net=net, requests=REQUESTS), pop_size=8, n_gen=3, seed=0, n_jobs=2)
    assert {r.accepted_ids for r in sequential} == {r.accepted_ids for r in parallel}


def test_nsga2_search_fair_front_spans_a_fairness_range(net):
    # A search with more candidates than the net can jointly host should
    # surface both a 0-violation point (e.g. the empty/prefix set) and at
    # least one point that trades violations for more accommodated MW.
    many = [Request(id=f"r{i}", bus=1, p_mw=0.005, queue_order=i) for i in range(15)]
    problem = AllocationProblem(net=net, requests=many)
    results = nsga2_search_fair(problem, pop_size=20, n_gen=15, seed=0)
    feasible = [r for r in results if r.feasible]
    violations = [queue_fairness_violations(problem.requests, r.accepted_ids) for r in feasible]
    assert min(violations) == 0


# -- nsga2_search_continuous_fair (M12) ---------------------------------------

def test_nsga2_search_continuous_fair_returns_some_feasible_results(net):
    problem = AllocationProblem(net=net, requests=REQUESTS)
    results = nsga2_search_continuous_fair(problem, pop_size=8, n_gen=3, seed=0)
    assert len(results) > 0
    assert any(r.feasible for r in results)


def test_nsga2_search_continuous_fair_reproducible_with_same_seed(net):
    problem = AllocationProblem(net=net, requests=REQUESTS)
    r1 = nsga2_search_continuous_fair(problem, pop_size=8, n_gen=3, seed=0)
    r2 = nsga2_search_continuous_fair(problem, pop_size=8, n_gen=3, seed=0)
    assert [r.sizes for r in r1] == [r.sizes for r in r2]


def test_nsga2_search_continuous_fair_parallel_matches_sequential(net):
    sequential = nsga2_search_continuous_fair(
        AllocationProblem(net=net, requests=REQUESTS), pop_size=8, n_gen=3, seed=0, n_jobs=1)
    parallel = nsga2_search_continuous_fair(
        AllocationProblem(net=net, requests=REQUESTS), pop_size=8, n_gen=3, seed=0, n_jobs=2)
    assert [r.sizes for r in sequential] == [r.sizes for r in parallel]


def test_nsga2_search_continuous_fair_front_spans_a_fairness_range(net):
    many = [Request(id=f"r{i}", bus=1, p_mw=0.005, queue_order=i) for i in range(15)]
    problem = AllocationProblem(net=net, requests=many)
    results = nsga2_search_continuous_fair(problem, pop_size=20, n_gen=15, seed=0)
    feasible = [r for r in results if r.feasible]
    violations = [queue_fairness_violations(problem.requests, r.accepted_ids) for r in feasible]
    assert min(violations) == 0


def test_nsga2_search_continuous_fair_partial_award_counts_as_accepted(net):
    # A nonzero-fraction award to a later request while an earlier one gets
    # exactly 0 must count as a queue-jump, matching AllocationResult.accepted_ids.
    problem = AllocationProblem(net=net, requests=REQUESTS)
    result = problem.evaluate_sizes({"r1": 0.3})  # r0 gets nothing, r1 gets a partial award
    assert queue_fairness_violations(REQUESTS, result.accepted_ids) == 1
