"""Offline tests for M14's unbalanced oracle (`AllocationProblem.evaluate_3ph`/
`evaluate_sizes_3ph`) and its baseline/search analogues (`greedy_baseline_3ph`,
`nsga2_search_3ph`).

A minimal two-bus 3ph-solvable fixture: unlike test_allocation.py's fixture,
`pandapower.runpp_3ph` additionally needs the ext_grid's zero/negative-
sequence fields and every line's r0/x0/c0 -- absent from `import_opendss`'s
positive-sequence-only import (the real gap M14 found and documented), so
they're set explicitly here.
"""

import pandapower as pp
import pytest

from allocation import AllocationProblem, Request, greedy_baseline_3ph, nsga2_search_3ph

REQUESTS = [
    Request(id="small_a", bus=1, p_mw=0.002, phase="a", queue_order=0),
    Request(id="large_a", bus=1, p_mw=0.05, phase="a", queue_order=1),
    Request(id="balanced", bus=1, p_mw=0.002, phase=None, queue_order=2),
]


@pytest.fixture
def net():
    net = pp.create_empty_network()
    b0 = pp.create_bus(net, vn_kv=0.4, name="source")
    b1 = pp.create_bus(net, vn_kv=0.4, name="load")
    pp.create_ext_grid(net, bus=b0, vm_pu=1.0, s_sc_max_mva=100.0, s_sc_min_mva=80.0,
                        rx_max=0.1, rx_min=0.1, r0x0_max=0.1, x0x_max=1.0,
                        r0x0_min=0.1, x0x_min=1.0)
    pp.create_line_from_parameters(
        net, from_bus=b0, to_bus=b1, length_km=0.1,
        r_ohm_per_km=0.5, x_ohm_per_km=0.1, c_nf_per_km=0.0, max_i_ka=0.05,
        r0_ohm_per_km=0.5, x0_ohm_per_km=0.1, c0_nf_per_km=0.0)
    return net


# -- AllocationProblem.evaluate_3ph / evaluate_sizes_3ph ---------------------

def test_empty_subset_is_always_feasible(net):
    problem = AllocationProblem(net=net, requests=REQUESTS)
    result = problem.evaluate_3ph([])
    assert result.feasible
    assert result.accommodated_mw == 0.0


def test_single_phase_tap_request_is_feasible_alone(net):
    problem = AllocationProblem(net=net, requests=REQUESTS)
    result = problem.evaluate_3ph(["small_a"])
    assert result.feasible
    assert result.accommodated_mw == pytest.approx(0.002)


def test_large_single_phase_request_overloads_the_line(net):
    problem = AllocationProblem(net=net, requests=REQUESTS)
    result = problem.evaluate_3ph(["large_a"])
    assert not result.feasible
    assert result.thermal_violation_percent > 0


def test_severely_overloaded_request_is_reported_infeasible_not_nan_feasible(net):
    # pandapower.runpp_3ph does not raise on non-convergence -- it leaves
    # net['converged'] True and returns an empty/all-NaN res_bus_3ph. This
    # locks in evaluate_sizes_3ph's explicit guard against that: without it,
    # max(0.0, nan) == 0.0 in Python would silently report a diverged solve
    # as feasible.
    huge = [Request(id="huge", bus=1, p_mw=5.0, phase="a", queue_order=0)]
    problem = AllocationProblem(net=net, requests=huge)
    result = problem.evaluate_3ph(["huge"])
    assert not result.converged
    assert not result.feasible


def test_unknown_request_id_rejected(net):
    problem = AllocationProblem(net=net, requests=REQUESTS)
    with pytest.raises(ValueError):
        problem.evaluate_3ph(["not-a-real-id"])


def test_size_fraction_out_of_range_rejected(net):
    problem = AllocationProblem(net=net, requests=REQUESTS)
    with pytest.raises(ValueError):
        problem.evaluate_sizes_3ph({"small_a": 1.5})


# -- phase attach semantics ---------------------------------------------------

def test_single_phase_tap_and_balanced_attach_give_different_results(net):
    # Same magnitude, different phase attachment -- phase=None spreads the
    # load evenly across all three phases (pandapower's own runpp_3ph
    # behavior for a plain pp.create_load); phase="a" concentrates it on one.
    problem = AllocationProblem(net=net, requests=REQUESTS)
    tapped = problem.evaluate_3ph(["small_a"])
    balanced = problem.evaluate_3ph(["balanced"])
    assert tapped.vmin_pu != pytest.approx(balanced.vmin_pu)


def test_evaluate_3ph_cache_is_independent_of_the_balanced_cache(net):
    # _cache (evaluate/evaluate_sizes) and _cache_3ph (evaluate_3ph/
    # evaluate_sizes_3ph) must not collide on the same (id, fraction)
    # genotype -- the same accept-set means different physics in each.
    problem = AllocationProblem(net=net, requests=REQUESTS)
    balanced_result = problem.evaluate(["small_a"])
    unbalanced_result = problem.evaluate_3ph(["small_a"])
    assert problem._cache and problem._cache_3ph
    assert set(problem._cache) != set(problem._cache_3ph) or balanced_result != unbalanced_result


# -- greedy_baseline_3ph -------------------------------------------------------

def test_greedy_baseline_3ph_stops_at_the_first_violation(net):
    many = [Request(id=f"r{i}", bus=1, p_mw=0.005, phase="a", queue_order=i) for i in range(15)]
    problem = AllocationProblem(net=net, requests=many)
    result = greedy_baseline_3ph(problem)
    assert result.feasible
    assert 0 < len(result.accepted_ids) < len(many)
    next_id = f"r{len(result.accepted_ids)}"
    with_next = problem.evaluate_3ph(set(result.accepted_ids) | {next_id})
    assert not with_next.feasible


def test_greedy_baseline_3ph_accepts_everything_when_nothing_binds(net):
    few = [Request(id=f"r{i}", bus=1, p_mw=0.001, phase="a", queue_order=i) for i in range(3)]
    problem = AllocationProblem(net=net, requests=few)
    result = greedy_baseline_3ph(problem)
    assert result.accepted_ids == {r.id for r in few}


# -- nsga2_search_3ph -----------------------------------------------------------

def test_nsga2_search_3ph_returns_some_feasible_results(net):
    many = [Request(id=f"r{i}", bus=1, p_mw=0.005, phase="a", queue_order=i) for i in range(10)]
    problem = AllocationProblem(net=net, requests=many)
    results = nsga2_search_3ph(problem, pop_size=8, n_gen=5, seed=0)
    assert any(r.feasible for r in results)


def test_nsga2_search_3ph_reproducible_with_same_seed(net):
    many = [Request(id=f"r{i}", bus=1, p_mw=0.005, phase="a", queue_order=i) for i in range(10)]
    p1 = AllocationProblem(net=net, requests=many)
    p2 = AllocationProblem(net=net, requests=many)
    r1 = nsga2_search_3ph(p1, pop_size=8, n_gen=5, seed=0)
    r2 = nsga2_search_3ph(p2, pop_size=8, n_gen=5, seed=0)
    assert {r.accepted_ids for r in r1} == {r.accepted_ids for r in r2}
