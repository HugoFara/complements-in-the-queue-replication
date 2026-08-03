"""Offline tests for M1's greedy baseline and NSGA-II search (`allocation.baseline`,
`allocation.search`).

Same minimal two-bus radial feeder as test_allocation.py, kept fast (small
population/generation counts) since NSGA-II costs one `runpp` per individual.
"""

import multiprocessing.pool as mp_pool

import pandapower as pp
import pytest

from allocation import AllocationProblem, AllocationResult, Request, compare_to_baseline, greedy_baseline, nsga2_search
from allocation.search import _pool


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


def _result(accepted_ids=(), accommodated_mw=0.0, feasible=True, cost=0.0) -> AllocationResult:
    return AllocationResult(
        accepted_ids=frozenset(accepted_ids), converged=True, feasible=feasible,
        accommodated_mw=accommodated_mw, vmin_pu=1.0, vmax_pu=1.0,
        max_line_loading_percent=10.0, max_trafo_loading_percent=5.0,
        voltage_violation_pu=0.0, thermal_violation_percent=0.0,
        reinforcement_cost_proxy=cost)


# -- greedy_baseline --------------------------------------------------------

def test_greedy_baseline_stops_at_the_first_violation(net):
    many = [Request(id=f"r{i}", bus=1, p_mw=0.005, queue_order=i) for i in range(15)]
    problem = AllocationProblem(net=net, requests=many)
    result = greedy_baseline(problem)
    assert result.feasible
    assert 0 < len(result.accepted_ids) < len(many)
    next_id = f"r{len(result.accepted_ids)}"  # queue order is r0, r1, ... so this is next-in-line
    with_next = problem.evaluate(set(result.accepted_ids) | {next_id})
    assert not with_next.feasible


def test_greedy_baseline_accepts_everything_when_nothing_binds(net):
    few = [Request(id=f"r{i}", bus=1, p_mw=0.001, queue_order=i) for i in range(3)]
    problem = AllocationProblem(net=net, requests=few)
    result = greedy_baseline(problem)
    assert result.accepted_ids == {r.id for r in few}


# -- nsga2_search -------------------------------------------------------------

def test_nsga2_search_returns_some_feasible_results(net):
    requests = [Request(id=f"r{i}", bus=1, p_mw=0.002, queue_order=i) for i in range(8)]
    problem = AllocationProblem(net=net, requests=requests)
    results = nsga2_search(problem, pop_size=8, n_gen=3, seed=0)
    assert len(results) > 0
    assert any(r.feasible for r in results)


def test_nsga2_search_reproducible_with_same_seed(net):
    requests = [Request(id=f"r{i}", bus=1, p_mw=0.002, queue_order=i) for i in range(8)]
    problem = AllocationProblem(net=net, requests=requests)
    r1 = nsga2_search(problem, pop_size=8, n_gen=3, seed=0)
    r2 = nsga2_search(problem, pop_size=8, n_gen=3, seed=0)
    assert {r.accepted_ids for r in r1} == {r.accepted_ids for r in r2}


# -- M10: n_jobs parallelizes evaluation without changing the result --------

def test_pool_n_jobs_one_is_a_noop():
    with _pool(1) as pool:
        assert pool is None


def test_pool_n_jobs_multiple_creates_a_process_pool():
    with _pool(2) as pool:
        assert isinstance(pool, mp_pool.Pool)


def test_nsga2_search_parallel_matches_sequential(net):
    requests = [Request(id=f"r{i}", bus=1, p_mw=0.002, queue_order=i) for i in range(8)]
    sequential = nsga2_search(AllocationProblem(net=net, requests=requests), pop_size=8, n_gen=3, seed=0, n_jobs=1)
    parallel = nsga2_search(AllocationProblem(net=net, requests=requests), pop_size=8, n_gen=3, seed=0, n_jobs=2)
    assert {r.accepted_ids for r in sequential} == {r.accepted_ids for r in parallel}


# -- compare_to_baseline ------------------------------------------------------

def test_compare_to_baseline_detects_a_dominating_result():
    baseline = _result({"a"}, accommodated_mw=0.01)
    better = _result({"a", "b"}, accommodated_mw=0.02)
    cmp = compare_to_baseline([baseline, better], baseline)
    assert cmp.dominates
    assert cmp.best_feasible_search is better


def test_compare_to_baseline_no_feasible_search_results():
    baseline = _result({"a"}, accommodated_mw=0.01)
    infeasible = _result({"a", "b"}, accommodated_mw=0.03, feasible=False, cost=5.0)
    cmp = compare_to_baseline([infeasible], baseline)
    assert not cmp.dominates
    assert cmp.best_feasible_search is None


def test_compare_to_baseline_rejects_a_worse_result():
    baseline = _result({"a", "b"}, accommodated_mw=0.02)
    worse = _result({"a"}, accommodated_mw=0.01)
    cmp = compare_to_baseline([worse], baseline)
    assert not cmp.dominates
