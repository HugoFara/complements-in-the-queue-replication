"""Offline tests for the joint-allocation constraint oracle (`allocation.problem`).

A minimal two-bus radial LV feeder, deliberately small enough to overload, so
the feasibility transitions (thermal, voltage) can be exercised without
touching the EULV data file experiments/24 uses.
"""

import pandapower as pp
import pytest

from allocation import AllocationProblem, Request

REQUESTS = [
    Request(id="small", bus=1, p_mw=0.002, queue_order=0),
    Request(id="large", bus=1, p_mw=0.05, queue_order=1),
    Request(id="export", bus=1, p_mw=-0.05, queue_order=2),
]


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


def test_empty_subset_is_always_feasible(net):
    problem = AllocationProblem(net=net, requests=REQUESTS)
    result = problem.evaluate([])
    assert result.feasible
    assert result.accommodated_mw == 0.0
    assert result.voltage_violation_pu == 0.0
    assert result.thermal_violation_percent == 0.0


def test_small_request_alone_is_feasible(net):
    problem = AllocationProblem(net=net, requests=REQUESTS)
    result = problem.evaluate(["small"])
    assert result.feasible
    assert result.accommodated_mw == pytest.approx(0.002)


def test_large_demand_overloads_the_line(net):
    problem = AllocationProblem(net=net, requests=REQUESTS)
    result = problem.evaluate(["large"])
    assert not result.feasible
    assert result.thermal_violation_percent > 0


def test_export_request_raises_voltage_relative_to_demand_only(net):
    problem = AllocationProblem(net=net, requests=REQUESTS)
    small_only = problem.evaluate(["small"])
    with_export = problem.evaluate(["small", "export"])
    assert with_export.vmax_pu > small_only.vmax_pu


def test_accommodated_mw_only_counts_accepted(net):
    problem = AllocationProblem(net=net, requests=REQUESTS)
    result = problem.evaluate(["small", "large"])
    assert result.accommodated_mw == pytest.approx(0.002 + 0.05)


def test_unknown_request_id_rejected(net):
    problem = AllocationProblem(net=net, requests=REQUESTS)
    with pytest.raises(ValueError):
        problem.evaluate(["not-a-real-id"])


def test_duplicate_request_ids_rejected(net):
    with pytest.raises(ValueError):
        AllocationProblem(net=net, requests=[REQUESTS[0], REQUESTS[0]])


# -- M10: evaluate_sizes caches repeated accept-sets -------------------------

def test_repeated_accept_set_returns_the_cached_result(net):
    problem = AllocationProblem(net=net, requests=REQUESTS)
    r1 = problem.evaluate(["small"])
    r2 = problem.evaluate(["small"])
    assert r1 is r2


def test_cache_distinguishes_different_accept_sets(net):
    problem = AllocationProblem(net=net, requests=REQUESTS)
    small = problem.evaluate(["small"])
    large = problem.evaluate(["large"])
    assert small is not large
    assert small.feasible != large.feasible


def test_cache_distinguishes_different_fractions_of_the_same_request(net):
    problem = AllocationProblem(net=net, requests=REQUESTS)
    half = problem.evaluate_sizes({"large": 0.5})
    full = problem.evaluate_sizes({"large": 1.0})
    assert half is not full
    assert half.accommodated_mw != full.accommodated_mw


def test_cache_is_not_shared_across_problem_instances(net):
    # each AllocationProblem gets its own cache -- a fresh instance on the
    # same net/requests must not spuriously reuse another instance's result.
    p1 = AllocationProblem(net=net, requests=REQUESTS)
    p2 = AllocationProblem(net=net, requests=REQUESTS)
    r1 = p1.evaluate(["small"])
    r2 = p2.evaluate(["small"])
    assert r1 is not r2
    assert r1 == r2
