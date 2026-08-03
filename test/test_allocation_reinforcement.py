"""Offline tests for the real, catalogue-priced reinforcement-cost proxy
(`allocation.reinforcement`) and the baseline built on top of it.

Same style as test_allocation.py/test_allocation_convex.py: small synthetic
radial feeders, no dependence on the EULV/ENWL data files (those are covered
by experiments/30_reinforcement_cost.py, which validates at real-network
scale and is where the "does this catalogue actually help" question --
answered differently for EULV's voltage-limited case vs. ENWL's
thermal-limited one -- is checked).
"""

import pandapower as pp
import pytest

from allocation import AllocationProblem, Request
from allocation.baseline import greedy_baseline, reinforced_greedy_baseline
from allocation.reinforcement import DEFAULT_CATALOGUE, price_reinforcement


@pytest.fixture
def net():
    """A deliberately tiny thermal rating (0.05 kA) so a handful of small
    requests overload the line -- the catalogue's smallest tier (16 mm^2,
    ~0.103 kA) already clears it, giving upgrades a real gradient to size
    against."""
    net = pp.create_empty_network()
    b0 = pp.create_bus(net, vn_kv=0.4, name="source")
    b1 = pp.create_bus(net, vn_kv=0.4, name="load")
    pp.create_ext_grid(net, bus=b0, vm_pu=1.0)
    pp.create_line_from_parameters(
        net, from_bus=b0, to_bus=b1, length_km=0.1,
        r_ohm_per_km=0.5, x_ohm_per_km=0.1, c_nf_per_km=0.0, max_i_ka=0.05)
    return net


@pytest.fixture
def trafo_net():
    """A small transformer (0.05 MVA) feeding a load bus -- overloaded by
    demand requests, with no line thermally binding first."""
    net = pp.create_empty_network()
    hv = pp.create_bus(net, vn_kv=20.0, name="hv")
    lv = pp.create_bus(net, vn_kv=0.4, name="lv")
    pp.create_ext_grid(net, bus=hv, vm_pu=1.0)
    pp.create_transformer_from_parameters(
        net, hv_bus=hv, lv_bus=lv, sn_mva=0.05, vn_hv_kv=20.0, vn_lv_kv=0.4,
        vk_percent=4.0, vkr_percent=1.0, pfe_kw=0.1, i0_percent=0.1)
    return net


@pytest.fixture
def overvoltage_net():
    """A long, weak line whose binding constraint is over-voltage from
    export, not thermal -- the case this module's thermal-only lever cannot
    fix (mirrors ROADMAP.md M5/M6's EULV finding at unit-test scale)."""
    net = pp.create_empty_network()
    b0 = pp.create_bus(net, vn_kv=0.4, name="source")
    b1 = pp.create_bus(net, vn_kv=0.4, name="load")
    pp.create_ext_grid(net, bus=b0, vm_pu=1.0)
    pp.create_line_from_parameters(
        net, from_bus=b0, to_bus=b1, length_km=1.0,
        r_ohm_per_km=1.0, x_ohm_per_km=0.1, c_nf_per_km=0.0, max_i_ka=1.0)
    return net


REQUESTS = [Request(id=f"r{i}", bus=1, p_mw=0.005, queue_order=i) for i in range(15)]


# -- price_reinforcement: feasible-already costs nothing --------------------

def test_already_feasible_costs_nothing(net):
    problem = AllocationProblem(net=net, requests=REQUESTS)
    plan = price_reinforcement(problem, [])
    assert plan.feasible_after_reinforcement
    assert plan.total_cost_eur == 0.0
    assert plan.line_upgrades == ()
    assert plan.n_iterations == 0


# -- price_reinforcement: a thermally overloaded line gets a real, priced,
#    AC-reverified upgrade ----------------------------------------------------

def test_overloaded_line_is_upgraded_and_ac_reverified(net):
    problem = AllocationProblem(net=net, requests=REQUESTS)
    all_ids = [r.id for r in REQUESTS]
    unreinforced = problem.evaluate(all_ids)
    assert not unreinforced.feasible  # sanity: this set does overload the line

    plan = price_reinforcement(problem, all_ids)
    assert plan.feasible_after_reinforcement
    assert plan.total_cost_eur > 0.0
    assert len(plan.line_upgrades) == 1
    upgrade = plan.line_upgrades[0]
    assert upgrade.to_max_i_ka > upgrade.from_max_i_ka
    assert upgrade.cost_eur > 0.0

    # the result must be a real pandapower re-solve of the upgraded network,
    # not just the sizing arithmetic's own belief
    reverified = problem.evaluate(all_ids)  # problem.net itself is untouched
    assert reverified.feasible == unreinforced.feasible  # confirms price_reinforcement didn't mutate problem.net
    assert plan.result_after_reinforcement.feasible
    assert plan.result_after_reinforcement.accommodated_mw == pytest.approx(sum(r.p_mw for r in REQUESTS))


def test_picks_the_smallest_sufficient_tier(net):
    """A single small request only barely overloading the line should pick a
    small catalogue step, not jump straight to the largest conductor."""
    small_overload = [Request(id="r0", bus=1, p_mw=0.036, queue_order=0)]  # ~105% loading, just over the limit
    problem = AllocationProblem(net=net, requests=small_overload)
    plan = price_reinforcement(problem, ["r0"])
    assert plan.feasible_after_reinforcement
    assert len(plan.line_upgrades) == 1
    assert plan.line_upgrades[0].to_cross_section_mm2 == min(t.cross_section_mm2 for t in DEFAULT_CATALOGUE.line_tiers)


# -- price_reinforcement: an overloaded transformer gets upgraded -----------

def test_overloaded_transformer_is_upgraded(trafo_net):
    requests = [Request(id=f"r{i}", bus=1, p_mw=0.02, queue_order=i) for i in range(5)]  # 0.1 MW >> 0.05 MVA trafo
    problem = AllocationProblem(net=trafo_net, requests=requests)
    all_ids = [r.id for r in requests]
    assert not problem.evaluate(all_ids).feasible

    plan = price_reinforcement(problem, all_ids)
    assert plan.feasible_after_reinforcement
    assert len(plan.trafo_upgrades) == 1
    upgrade = plan.trafo_upgrades[0]
    assert upgrade.to_sn_mva > upgrade.from_sn_mva
    assert upgrade.cost_eur > 0.0
    assert plan.line_upgrades == ()


# -- price_reinforcement: a pure voltage violation has no lever here --------

def test_pure_overvoltage_is_reported_as_unresolved(overvoltage_net):
    export_requests = [Request(id=f"pv{i}", bus=1, p_mw=-0.05, queue_order=i) for i in range(3)]
    problem = AllocationProblem(net=overvoltage_net, requests=export_requests)
    all_ids = [r.id for r in export_requests]
    unreinforced = problem.evaluate(all_ids)
    assert not unreinforced.feasible
    assert unreinforced.voltage_violation_pu > 0.0
    assert unreinforced.thermal_violation_percent == 0.0  # thermal is not what's binding here

    plan = price_reinforcement(problem, all_ids)
    assert not plan.feasible_after_reinforcement
    assert plan.total_cost_eur == float("inf")
    assert plan.line_upgrades == ()  # nothing this catalogue can act on -- reported, not guessed


# -- reinforced_greedy_baseline: reinforcement as a lever, not just a wall --

def test_reinforced_baseline_accepts_at_least_as_much_as_plain_greedy(net):
    problem = AllocationProblem(net=net, requests=REQUESTS)
    base = greedy_baseline(problem)
    reinforced = reinforced_greedy_baseline(problem)
    assert reinforced.result.feasible
    assert reinforced.result.accommodated_mw >= base.accommodated_mw


def test_reinforced_baseline_spends_money_to_accept_more_on_a_binding_case(net):
    problem = AllocationProblem(net=net, requests=REQUESTS)
    base = greedy_baseline(problem)
    reinforced = reinforced_greedy_baseline(problem)
    assert reinforced.result.accommodated_mw > base.accommodated_mw
    assert reinforced.total_cost_eur > 0.0
    assert len(reinforced.line_upgrades) >= 1


def test_reinforced_baseline_respects_a_budget(net):
    problem = AllocationProblem(net=net, requests=REQUESTS)
    unlimited = reinforced_greedy_baseline(problem)
    assert unlimited.total_cost_eur > 0.0
    zero_budget = reinforced_greedy_baseline(problem, budget_eur=0.0)
    assert zero_budget.total_cost_eur == 0.0
    assert zero_budget.result.accommodated_mw == greedy_baseline(problem).accommodated_mw


def test_reinforced_baseline_matches_plain_greedy_when_nothing_binds():
    """No requests ever violate anything -> zero-cost, identical to greedy_baseline."""
    net = pp.create_empty_network()
    b0 = pp.create_bus(net, vn_kv=0.4)
    b1 = pp.create_bus(net, vn_kv=0.4)
    pp.create_ext_grid(net, bus=b0, vm_pu=1.0)
    pp.create_line_from_parameters(
        net, from_bus=b0, to_bus=b1, length_km=0.01,
        r_ohm_per_km=0.1, x_ohm_per_km=0.05, c_nf_per_km=0.0, max_i_ka=1.0)
    requests = [Request(id=f"r{i}", bus=1, p_mw=0.0005, queue_order=i) for i in range(3)]
    problem = AllocationProblem(net=net, requests=requests)
    base = greedy_baseline(problem)
    reinforced = reinforced_greedy_baseline(problem)
    assert reinforced.total_cost_eur == 0.0
    assert reinforced.result.accepted_ids == base.accepted_ids


# -- unknown request ids are rejected, matching AllocationProblem.evaluate --

def test_unknown_request_id_is_rejected(net):
    problem = AllocationProblem(net=net, requests=REQUESTS)
    with pytest.raises(ValueError):
        price_reinforcement(problem, ["not-a-real-id"])
