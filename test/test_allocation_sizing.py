"""Offline tests for continuous request sizing (M7): `AllocationProblem.evaluate_sizes`,
`continuous_greedy_baseline`, `nsga2_search_continuous`, and `lindistflow_search(continuous=True)`.

Same style as the other test_allocation_*.py files: small synthetic radial
feeders, no dependence on the EULV/ENWL data files (those are covered by
experiments/31_continuous_sizing.py, which validates at real-network scale).
"""

import pandapower as pp
import pytest

from allocation import AllocationProblem, Request
from allocation.baseline import continuous_greedy_baseline, greedy_baseline
from allocation.convex import lindistflow_search
from allocation.search import compare_to_baseline, nsga2_search, nsga2_search_continuous


@pytest.fixture
def net():
    """Same 2-bus feeder as test_allocation.py/test_allocation_convex.py."""
    net = pp.create_empty_network()
    b0 = pp.create_bus(net, vn_kv=0.4, name="source")
    b1 = pp.create_bus(net, vn_kv=0.4, name="load")
    pp.create_ext_grid(net, bus=b0, vm_pu=1.0)
    pp.create_line_from_parameters(
        net, from_bus=b0, to_bus=b1, length_km=0.1,
        r_ohm_per_km=0.5, x_ohm_per_km=0.1, c_nf_per_km=0.0, max_i_ka=0.05)
    return net


REQUESTS = [Request(id=f"r{i}", bus=1, p_mw=0.01, q_mvar=0.0, queue_order=i) for i in range(6)]


# -- AllocationProblem.evaluate_sizes ----------------------------------------

def test_evaluate_is_the_fraction_1_special_case_of_evaluate_sizes(net):
    problem = AllocationProblem(net=net, requests=REQUESTS)
    via_evaluate = problem.evaluate(["r0", "r1"])
    via_sizes = problem.evaluate_sizes({"r0": 1.0, "r1": 1.0})
    assert via_evaluate.accommodated_mw == pytest.approx(via_sizes.accommodated_mw)
    assert via_evaluate.feasible == via_sizes.feasible
    assert via_evaluate.accepted_ids == via_sizes.accepted_ids == {"r0", "r1"}
    assert dict(via_evaluate.sizes) == {"r0": 1.0, "r1": 1.0}


def test_a_fractional_size_scales_p_and_q_and_stays_in_sizes(net):
    req = [Request(id="r0", bus=1, p_mw=0.02, q_mvar=0.01)]
    problem = AllocationProblem(net=net, requests=req)
    full = problem.evaluate_sizes({"r0": 1.0})
    half = problem.evaluate_sizes({"r0": 0.5})
    assert half.accommodated_mw == pytest.approx(full.accommodated_mw / 2)
    assert half.sizes == {"r0": 0.5}
    assert half.accepted_ids == {"r0"}


def test_zero_fraction_is_omitted_from_accepted_ids_and_sizes(net):
    problem = AllocationProblem(net=net, requests=REQUESTS)
    result = problem.evaluate_sizes({"r0": 1.0, "r1": 0.0})
    assert result.accepted_ids == {"r0"}
    assert "r1" not in result.sizes


def test_out_of_range_fraction_is_rejected(net):
    problem = AllocationProblem(net=net, requests=REQUESTS)
    with pytest.raises(ValueError):
        problem.evaluate_sizes({"r0": 1.5})
    with pytest.raises(ValueError):
        problem.evaluate_sizes({"r0": -0.1})


def test_unknown_id_is_rejected(net):
    problem = AllocationProblem(net=net, requests=REQUESTS)
    with pytest.raises(ValueError):
        problem.evaluate_sizes({"not-a-real-id": 1.0})


# -- continuous_greedy_baseline: at least as good as the binary baseline ----

def test_continuous_baseline_beats_or_matches_binary_baseline(net):
    problem = AllocationProblem(net=net, requests=REQUESTS)
    binary = greedy_baseline(problem)
    continuous = continuous_greedy_baseline(problem)
    assert continuous.feasible
    assert continuous.accommodated_mw >= binary.accommodated_mw
    # the request right after the binary baseline's stopping point should
    # get a nonzero (but not full) partial award on this constructed case
    partials = {rid: frac for rid, frac in continuous.sizes.items() if 0 < frac < 1}
    assert len(partials) >= 1


def test_continuous_baseline_matches_binary_when_nothing_binds():
    net = pp.create_empty_network()
    b0 = pp.create_bus(net, vn_kv=0.4)
    b1 = pp.create_bus(net, vn_kv=0.4)
    pp.create_ext_grid(net, bus=b0, vm_pu=1.0)
    pp.create_line_from_parameters(
        net, from_bus=b0, to_bus=b1, length_km=0.01,
        r_ohm_per_km=0.1, x_ohm_per_km=0.05, c_nf_per_km=0.0, max_i_ka=1.0)
    requests = [Request(id=f"r{i}", bus=1, p_mw=0.0005, queue_order=i) for i in range(3)]
    problem = AllocationProblem(net=net, requests=requests)
    binary = greedy_baseline(problem)
    continuous = continuous_greedy_baseline(problem)
    assert continuous.accepted_ids == binary.accepted_ids
    assert all(frac == pytest.approx(1.0) for frac in continuous.sizes.values())


def test_min_fraction_drops_small_partial_awards(net):
    problem = AllocationProblem(net=net, requests=REQUESTS)
    generous = continuous_greedy_baseline(problem, min_fraction=0.0)
    strict = continuous_greedy_baseline(problem, min_fraction=0.9)
    assert strict.accommodated_mw <= generous.accommodated_mw
    assert all(frac >= 0.9 or frac == pytest.approx(1.0) for frac in strict.sizes.values())


# -- nsga2_search_continuous: never worse than binary nsga2_search ----------

def test_continuous_search_reaches_at_least_binary_search(net):
    problem = AllocationProblem(net=net, requests=REQUESTS)
    binary_results = nsga2_search(problem, pop_size=20, n_gen=15, seed=0)
    continuous_results = nsga2_search_continuous(problem, pop_size=20, n_gen=15, seed=0)

    binary_baseline = greedy_baseline(problem)
    continuous_baseline = continuous_greedy_baseline(problem)
    binary_best = compare_to_baseline(binary_results, binary_baseline).best_feasible_search
    continuous_best = compare_to_baseline(continuous_results, continuous_baseline).best_feasible_search

    assert continuous_best is not None
    assert continuous_best.feasible
    assert continuous_best.accommodated_mw >= binary_best.accommodated_mw - 1e-9


def test_continuous_search_parallel_matches_sequential(net):
    sequential = nsga2_search_continuous(AllocationProblem(net=net, requests=REQUESTS), pop_size=10, n_gen=5, seed=0, n_jobs=1)
    parallel = nsga2_search_continuous(AllocationProblem(net=net, requests=REQUESTS), pop_size=10, n_gen=5, seed=0, n_jobs=2)
    seq_sizes = [dict(r.sizes) for r in sequential]
    par_sizes = [dict(r.sizes) for r in parallel]
    assert seq_sizes == par_sizes


def test_continuous_search_results_are_ac_evaluated(net):
    problem = AllocationProblem(net=net, requests=REQUESTS)
    results = nsga2_search_continuous(problem, pop_size=10, n_gen=5, seed=0)
    assert len(results) > 0
    for r in results:
        reverified = problem.evaluate_sizes(r.sizes)
        assert r.feasible == reverified.feasible
        assert r.accommodated_mw == pytest.approx(reverified.accommodated_mw)


# -- callback: plumbing for the still-open convergence-vs-budget question ---

def test_callback_is_notified_once_per_generation_and_does_not_change_the_result(net):
    """`callback` (see search.py's module docstring) is pure instrumentation
    -- notified once per generation, and its presence must not perturb the
    search itself (same seed -> same result, with or without it)."""
    from pymoo.core.callback import Callback

    class _CountingCallback(Callback):
        def __init__(self):
            super().__init__()
            self.n_calls = 0

        def notify(self, algorithm):
            self.n_calls += 1

    n_gen = 5
    problem = AllocationProblem(net=net, requests=REQUESTS)
    cb = _CountingCallback()
    with_cb = nsga2_search_continuous(problem, pop_size=10, n_gen=n_gen, seed=0, callback=cb)
    assert cb.n_calls == n_gen

    without_cb = nsga2_search_continuous(AllocationProblem(net=net, requests=REQUESTS),
                                          pop_size=10, n_gen=n_gen, seed=0)
    assert [dict(r.sizes) for r in with_cb] == [dict(r.sizes) for r in without_cb]


# -- lindistflow_search(continuous=True): LP relaxation, still AC-verified --

def test_convex_continuous_reaches_at_least_convex_binary(net):
    problem = AllocationProblem(net=net, requests=REQUESTS)
    binary = lindistflow_search(problem)
    continuous = lindistflow_search(problem, continuous=True)
    # the *relaxed* LP optimum can never be below the MILP's (superset of the
    # feasible region); the AC-reverified result inherits that only when both
    # remain AC-feasible, which is why the safety margin test below exists.
    assert sum(continuous.relaxed.sizes.values()) >= sum(binary.relaxed.sizes.values()) - 1e-9


def test_convex_continuous_result_is_ac_verified(net):
    problem = AllocationProblem(net=net, requests=REQUESTS)
    result = lindistflow_search(problem, continuous=True)
    reverified = problem.evaluate_sizes(result.relaxed.sizes)
    assert result.result.feasible == reverified.feasible
    assert result.result.accommodated_mw == pytest.approx(reverified.accommodated_mw)


def test_convex_continuous_margin_recovers_feasibility_on_a_tight_case():
    """On a single tight edge, the unmargined continuous LP relaxation's own
    optimum can land close enough to the octagon/nominal-voltage/dropped-
    losses approximation boundary to come back AC-infeasible; a small
    thermal margin is expected to pull it back inside the true feasible
    region -- exactly the same tool `lindistflow_search_iterative` already
    uses, exposed here on the single-shot continuous search too."""
    net = pp.create_empty_network()
    b0 = pp.create_bus(net, vn_kv=0.4, name="source")
    b1 = pp.create_bus(net, vn_kv=0.4, name="load")
    pp.create_ext_grid(net, bus=b0, vm_pu=1.0)
    pp.create_line_from_parameters(
        net, from_bus=b0, to_bus=b1, length_km=0.1,
        r_ohm_per_km=0.5, x_ohm_per_km=0.1, c_nf_per_km=0.0, max_i_ka=0.05)
    requests = [Request(id=f"r{i}", bus=1, p_mw=0.01, queue_order=i) for i in range(6)]
    problem = AllocationProblem(net=net, requests=requests)

    margined = lindistflow_search(problem, continuous=True, thermal_margin_pct=2.0)
    assert margined.result.feasible
