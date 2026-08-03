"""Offline tests for locational/feeder hosting capacity (`allocation.locational`).

Same minimal radial-feeder convention as the other allocation tests, plus a
3-bus variant (a near bus and a farther, higher-impedance bus) to check that
`feeder_hosting_capacity` correctly picks out the worse of the two locations.
"""

import pandapower as pp
import pytest

from allocation import AllocationProblem, feeder_hosting_capacity, locational_hosting_capacity


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


@pytest.fixture
def two_hop_net(net):
    # b2 sits behind a second line -- more voltage drop for the same size
    # request, so its LHC should be strictly lower than b1's. max_i_ka is set
    # high on both lines so voltage (not the shared trunk's thermal rating)
    # is what actually binds -- otherwise both buses share the same thermal
    # ceiling on the b0-b1 line and the "farther is worse" premise doesn't hold.
    net.line.loc[0, "max_i_ka"] = 5.0
    b2 = pp.create_bus(net, vn_kv=0.4, name="far")
    pp.create_line_from_parameters(
        net, from_bus=1, to_bus=b2, length_km=0.5,
        r_ohm_per_km=0.5, x_ohm_per_km=0.1, c_nf_per_km=0.0, max_i_ka=5.0)
    return net


def test_locational_hosting_capacity_matches_thermal_limit(net):
    # max_i_ka=0.05 -> thermal ceiling sqrt(3)*0.4kV*0.05kA ~= 34.64 kVA.
    problem = AllocationProblem(net=net, requests=[])
    lhc = locational_hosting_capacity(problem, bus=1, hi_mw=0.01, max_hi_mw=1.0, tol_mw=0.0005)
    assert lhc * 1000 == pytest.approx(34.64, abs=0.5)


def test_locational_hosting_capacity_respects_direction(net):
    problem = AllocationProblem(net=net, requests=[])
    export_lhc = locational_hosting_capacity(problem, bus=1, direction=-1.0, hi_mw=0.01, tol_mw=0.0005)
    demand_lhc = locational_hosting_capacity(problem, bus=1, direction=1.0, hi_mw=0.01, tol_mw=0.0005)
    assert export_lhc > 0
    assert demand_lhc > 0


def test_feeder_hosting_capacity_picks_the_worse_of_two_locations(two_hop_net):
    problem = AllocationProblem(net=two_hop_net, requests=[], vmax_pu=1.02)
    hc, worst_bus, all_results = feeder_hosting_capacity(
        problem, buses=[1, 2], hi_mw=0.001, max_hi_mw=1.0, tol_mw=0.0002)
    assert worst_bus == 2  # farther bus, more voltage drop per kW -> binds first
    assert hc == all_results[2]
    assert all_results[2] < all_results[1]


def test_feeder_hosting_capacity_is_the_minimum_not_the_average(two_hop_net):
    problem = AllocationProblem(net=two_hop_net, requests=[], vmax_pu=1.02)
    hc, _, all_results = feeder_hosting_capacity(
        problem, buses=[1, 2], hi_mw=0.001, max_hi_mw=1.0, tol_mw=0.0002)
    assert hc == min(all_results.values())
