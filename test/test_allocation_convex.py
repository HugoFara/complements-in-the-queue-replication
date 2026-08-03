"""Offline tests for the LinDistFlow MILP alternative to NSGA-II (`allocation.convex`).

Same style as test_allocation.py/test_allocation_search.py: small synthetic
radial feeders, no dependence on the EULV/ENWL data files (those are covered
by experiments/29_convex_relaxation.py, which validates at real-network scale).
"""

import pandapower as pp
import pytest

from allocation import AllocationProblem, Request, queue_fairness_violations
from allocation.convex import (
    _solve,
    _summarize,
    build_lindistflow_model,
    evaluate_relaxed,
    lindistflow_search,
    lindistflow_search_fair,
    lindistflow_search_iterative,
)


@pytest.fixture
def net():
    """Same 2-bus feeder as test_allocation.py."""
    net = pp.create_empty_network()
    b0 = pp.create_bus(net, vn_kv=0.4, name="source")
    b1 = pp.create_bus(net, vn_kv=0.4, name="load")
    pp.create_ext_grid(net, bus=b0, vm_pu=1.0)
    pp.create_line_from_parameters(
        net, from_bus=b0, to_bus=b1, length_km=0.1,
        r_ohm_per_km=0.5, x_ohm_per_km=0.1, c_nf_per_km=0.0, max_i_ka=0.05)
    return net


@pytest.fixture
def branching_net():
    """Root -> bus1 -> {bus2, bus3}: a 2-level tree with a branch, to exercise
    the recursive subtree power/Q aggregation beyond a single edge."""
    net = pp.create_empty_network()
    b0 = pp.create_bus(net, vn_kv=0.4, name="source")
    b1 = pp.create_bus(net, vn_kv=0.4, name="mid")
    b2 = pp.create_bus(net, vn_kv=0.4, name="leaf-a")
    b3 = pp.create_bus(net, vn_kv=0.4, name="leaf-b")
    pp.create_ext_grid(net, bus=b0, vm_pu=1.0)
    pp.create_line_from_parameters(
        net, from_bus=b0, to_bus=b1, length_km=0.1,
        r_ohm_per_km=0.5, x_ohm_per_km=0.1, c_nf_per_km=0.0, max_i_ka=0.05)
    pp.create_line_from_parameters(
        net, from_bus=b1, to_bus=b2, length_km=0.1,
        r_ohm_per_km=0.5, x_ohm_per_km=0.1, c_nf_per_km=0.0, max_i_ka=0.05)
    pp.create_line_from_parameters(
        net, from_bus=b1, to_bus=b3, length_km=0.1,
        r_ohm_per_km=0.5, x_ohm_per_km=0.1, c_nf_per_km=0.0, max_i_ka=0.05)
    pp.create_load(net, bus=b2, p_mw=0.001, q_mvar=0.0003, name="existing")
    return net


REQUESTS = [
    Request(id="small", bus=1, p_mw=0.002, queue_order=0),
    Request(id="large", bus=1, p_mw=0.05, queue_order=1),
    Request(id="export", bus=1, p_mw=-0.05, queue_order=2),
]


# -- build_lindistflow_model / tree extraction -------------------------------

def test_tree_extraction_finds_the_one_edge(net):
    problem = AllocationProblem(net=net, requests=REQUESTS)
    model = build_lindistflow_model(problem)
    assert model.root == 0
    assert model.nonroot_buses == [1]
    assert model.kind[1] == "line"
    assert model.r_pu[1] > 0
    assert model.x_pu[1] > 0


def test_non_radial_network_is_rejected():
    net = pp.create_empty_network()
    b0 = pp.create_bus(net, vn_kv=0.4)
    b1 = pp.create_bus(net, vn_kv=0.4)
    pp.create_ext_grid(net, bus=b0, vm_pu=1.0)
    # two parallel lines between the same bus pair -> a loop, not a tree
    for _ in range(2):
        pp.create_line_from_parameters(
            net, from_bus=b0, to_bus=b1, length_km=0.1,
            r_ohm_per_km=0.5, x_ohm_per_km=0.1, c_nf_per_km=0.0, max_i_ka=0.05)
    problem = AllocationProblem(net=net, requests=[])
    with pytest.raises(ValueError):
        build_lindistflow_model(problem)


def test_nonzero_q_request_is_rejected(net):
    reactive_request = Request(id="r", bus=1, p_mw=0.01, q_mvar=0.005)
    problem = AllocationProblem(net=net, requests=[reactive_request])
    with pytest.raises(NotImplementedError):
        build_lindistflow_model(problem)


# -- evaluate_relaxed: parity against the real pandapower oracle ------------

def test_relaxed_empty_subset_matches_pandapower_closely(net):
    problem = AllocationProblem(net=net, requests=REQUESTS)
    model = build_lindistflow_model(problem)
    relaxed = evaluate_relaxed(model, [])
    actual = problem.evaluate([])
    assert relaxed.vmax_pu == pytest.approx(actual.vmax_pu, abs=1e-6)
    assert relaxed.vmin_pu == pytest.approx(actual.vmin_pu, abs=1e-3)


def test_relaxed_matches_pandapower_on_a_branching_tree(branching_net):
    """Voltage at each leaf should reflect the accumulated drop along its own
    path -- catches a bug where subtree aggregation double counts or misses
    a branch."""
    requests = [Request(id="pv_a", bus=2, p_mw=-0.01), Request(id="pv_b", bus=3, p_mw=-0.01)]
    problem = AllocationProblem(net=branching_net, requests=requests)
    model = build_lindistflow_model(problem)

    for accepted in ([], ["pv_a"], ["pv_a", "pv_b"]):
        relaxed = evaluate_relaxed(model, accepted)
        actual = problem.evaluate(accepted)
        assert relaxed.vmax_pu == pytest.approx(actual.vmax_pu, abs=1e-3)
        assert relaxed.vmin_pu == pytest.approx(actual.vmin_pu, abs=1e-3)


def test_relaxed_can_report_infeasible_looking_predictions_without_erroring(net):
    """A forward parity check must not itself blow up just because the
    accept-set it's asked to evaluate happens to be near/over the feasibility
    boundary -- that's exactly the case parity-checking needs to see."""
    problem = AllocationProblem(net=net, requests=REQUESTS)
    model = build_lindistflow_model(problem)
    relaxed = evaluate_relaxed(model, ["large"])  # overloads the line
    actual = problem.evaluate(["large"])
    assert not actual.feasible
    assert relaxed.max_line_loading_percent > 90  # should also look stressed, even if not bounded


# -- lindistflow_search: MILP optimum, always AC re-verified ----------------

def test_lindistflow_search_result_is_ac_verified(net):
    requests = [Request(id=f"r{i}", bus=1, p_mw=0.001, queue_order=i) for i in range(5)]
    problem = AllocationProblem(net=net, requests=requests)
    conv = lindistflow_search(problem)
    # .result must be exactly what the real oracle says about the chosen set
    reverified = problem.evaluate(conv.result.accepted_ids)
    assert conv.result.feasible == reverified.feasible
    assert conv.result.accommodated_mw == pytest.approx(reverified.accommodated_mw)
    assert conv.result.accepted_ids == conv.relaxed.accepted_ids


def test_summarize_clamps_solver_overshoot_past_one(net):
    """M8 (multi-feeder validation) caught this on a real ENWL feeder
    (network_7/Feeder_3): HiGHS returned 1.000000000000004 for a should-be-1.0
    binary accept variable -- `evaluate_sizes`'s strict [0, 1] range check
    then raised. `_summarize` must clamp, not pass the raw solver value
    through, since `_solve`'s own docstring already says MILP binaries are
    only "very close to" 0/1, not exact."""
    model = build_lindistflow_model(AllocationProblem(net=net, requests=REQUESTS))
    accept_vals = {"small": 1.000000000000004, "large": 0.0, "export": 0.0}
    P_vals = {j: 0.0 for j in model.nonroot_buses}
    V2_vals = {j: 1.0 for j in model.nonroot_buses}
    relaxed = _summarize(model, accept_vals, P_vals, V2_vals)
    assert relaxed.sizes["small"] <= 1.0
    AllocationProblem(net=net, requests=REQUESTS).evaluate_sizes(relaxed.sizes)  # must not raise


def test_lindistflow_search_accepts_everything_when_nothing_binds(net):
    requests = [Request(id=f"r{i}", bus=1, p_mw=0.0005, queue_order=i) for i in range(3)]
    problem = AllocationProblem(net=net, requests=requests)
    conv = lindistflow_search(problem)
    assert conv.result.feasible
    assert conv.result.accepted_ids == {r.id for r in requests}


def test_lindistflow_search_picks_a_feasible_subset_when_not_everything_fits(net):
    many = [Request(id=f"r{i}", bus=1, p_mw=0.005, queue_order=i) for i in range(15)]
    problem = AllocationProblem(net=net, requests=many)
    conv = lindistflow_search(problem)
    assert conv.result.feasible
    assert 0 < len(conv.result.accepted_ids) < len(many)


# -- lindistflow_search_iterative: loss-corrected, still always AC-verified --

def test_iterative_search_result_is_ac_verified_and_feasible(net):
    many = [Request(id=f"r{i}", bus=1, p_mw=0.005, queue_order=i) for i in range(15)]
    problem = AllocationProblem(net=net, requests=many)
    conv = lindistflow_search_iterative(problem)
    assert conv.result.feasible
    reverified = problem.evaluate(conv.result.accepted_ids)
    assert conv.result.accommodated_mw == pytest.approx(reverified.accommodated_mw)


def test_iterative_search_trace_matches_iteration_count(net):
    many = [Request(id=f"r{i}", bus=1, p_mw=0.005, queue_order=i) for i in range(15)]
    problem = AllocationProblem(net=net, requests=many)
    conv = lindistflow_search_iterative(problem, max_iters=4)
    assert 1 <= conv.n_iterations <= 4
    assert len(conv.accommodated_mw_trace) == conv.n_iterations
    if conv.n_iterations < 4:
        assert conv.converged


def test_iterative_search_never_exceeds_max_iters(net):
    many = [Request(id=f"r{i}", bus=1, p_mw=0.005, queue_order=i) for i in range(15)]
    problem = AllocationProblem(net=net, requests=many)
    conv = lindistflow_search_iterative(problem, max_iters=2)
    assert conv.n_iterations <= 2


def test_iterative_search_is_reproducible(net):
    many = [Request(id=f"r{i}", bus=1, p_mw=0.005, queue_order=i) for i in range(15)]
    problem = AllocationProblem(net=net, requests=many)
    a = lindistflow_search_iterative(problem)
    b = lindistflow_search_iterative(problem)
    assert a.result.accepted_ids == b.result.accepted_ids


def test_iterative_search_stays_feasible_on_a_branching_tree(branching_net):
    requests = [Request(id="pv_a", bus=2, p_mw=-0.01), Request(id="pv_b", bus=3, p_mw=-0.01)]
    problem = AllocationProblem(net=branching_net, requests=requests)
    conv = lindistflow_search_iterative(problem)
    assert conv.result.feasible


# -- lindistflow_search_fair: M12's epsilon-constraint fairness cap ---------

def test_lindistflow_search_fair_respects_the_cap(net):
    many = [Request(id=f"r{i}", bus=1, p_mw=0.005, queue_order=i) for i in range(15)]
    problem = AllocationProblem(net=net, requests=many)
    for cap in (0, 1, 3, 10):
        conv = lindistflow_search_fair(problem, max_violations=cap)
        assert queue_fairness_violations(many, conv.result.accepted_ids) <= cap


def test_lindistflow_search_fair_zero_cap_has_no_violations(net):
    many = [Request(id=f"r{i}", bus=1, p_mw=0.005, queue_order=i) for i in range(15)]
    problem = AllocationProblem(net=net, requests=many)
    conv = lindistflow_search_fair(problem, max_violations=0)
    assert queue_fairness_violations(many, conv.result.accepted_ids) == 0


def test_lindistflow_search_fair_result_is_ac_verified(net):
    many = [Request(id=f"r{i}", bus=1, p_mw=0.005, queue_order=i) for i in range(15)]
    problem = AllocationProblem(net=net, requests=many)
    conv = lindistflow_search_fair(problem, max_violations=2)
    reverified = problem.evaluate(conv.result.accepted_ids)
    assert conv.result.feasible == reverified.feasible
    assert conv.result.accommodated_mw == pytest.approx(reverified.accommodated_mw)


def test_lindistflow_search_fair_a_large_cap_matches_unconstrained_capacity(net):
    many = [Request(id=f"r{i}", bus=1, p_mw=0.005, queue_order=i) for i in range(15)]
    problem = AllocationProblem(net=net, requests=many)
    plain = lindistflow_search(problem)
    unconstrained_cap = len(many) * (len(many) - 1) // 2  # every pair could be a violation
    fair = lindistflow_search_fair(problem, max_violations=unconstrained_cap)
    assert fair.result.accommodated_mw == pytest.approx(plain.result.accommodated_mw)


def test_solve_rejects_continuous_combined_with_max_violations(net):
    many = [Request(id=f"r{i}", bus=1, p_mw=0.005, queue_order=i) for i in range(5)]
    model = build_lindistflow_model(AllocationProblem(net=net, requests=many))
    with pytest.raises(NotImplementedError):
        _solve(model, fixed_accept=None, time_limit_s=30.0, mip_gap=0.001,
               continuous=True, max_violations=1)
