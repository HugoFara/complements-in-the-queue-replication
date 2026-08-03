"""Offline tests for M15's OpenDSS-direct oracle (`AllocationProblemDSS`)
and its baseline/search analogues (`greedy_baseline_dss`, `nsga2_search_dss`).

A minimal single-phase-lateral OpenDSS circuit, written to `tmp_path` --
the genuine North-American topology (a single-phase lateral tapped off a
3-phase trunk) `pandapower.runpp_3ph` cannot represent at all (issue
#873), which is the entire reason this oracle exists.
"""

import pytest

from allocation import AllocationProblemDSS, DSSRequest, greedy_baseline_dss, nsga2_search_dss

MASTER_DSS = """\
Clear
Set DefaultBaseFrequency=60
New Circuit.test basekv=4.16 bus1=sourcebus pu=1.0 phases=3 MVAsc3=20000 MVAsc1=21000
New Line.trunk phases=3 bus1=sourcebus bus2=b1 length=0.1 units=km r1=0.3 x1=0.6 r0=0.4 x0=1.2 c1=0 c0=0 normamps=400
New Line.lateral phases=1 bus1=b1.1 bus2=b2.1 length=0.1 units=km r1=0.5 x1=0.8 r0=0.5 x0=0.8 c1=0 c0=0 normamps=5
New Load.base bus1=b1.1.2.3 phases=3 kV=4.16 kW=10 kvar=3 conn=wye
Set VoltageBases=[4.16]
CalcVoltageBases
"""


@pytest.fixture
def master_path(tmp_path) -> str:
    path = tmp_path / "master.dss"
    path.write_text(MASTER_DSS)
    return str(path)


@pytest.fixture
def requests() -> list[DSSRequest]:
    return [
        DSSRequest(id="small", bus="b2", phase=1, p_kw=1.0, queue_order=0),
        DSSRequest(id="large", bus="b2", phase=1, p_kw=15.0, queue_order=1),
        DSSRequest(id="huge", bus="b2", phase=1, p_kw=50000.0, queue_order=2),
    ]


# -- AllocationProblemDSS.evaluate / evaluate_sizes --------------------------

def test_empty_subset_is_always_feasible(master_path, requests):
    problem = AllocationProblemDSS(master_path=master_path, requests=requests)
    result = problem.evaluate([])
    assert result.feasible
    assert result.converged
    assert result.accommodated_mw == 0.0


def test_small_request_on_the_single_phase_lateral_is_feasible(master_path, requests):
    problem = AllocationProblemDSS(master_path=master_path, requests=requests)
    result = problem.evaluate(["small"])
    assert result.feasible
    assert result.accommodated_mw == pytest.approx(0.001)


def test_large_request_overloads_the_lateral(master_path, requests):
    # the lateral's normamps=5 makes this a thermal, not voltage, violation
    problem = AllocationProblemDSS(master_path=master_path, requests=requests)
    result = problem.evaluate(["large"])
    assert not result.feasible
    assert result.thermal_violation_percent > 0


def test_severely_overloaded_request_is_reported_infeasible(master_path, requests):
    problem = AllocationProblemDSS(master_path=master_path, requests=requests)
    result = problem.evaluate(["huge"])
    assert not result.feasible


def test_unknown_request_id_rejected(master_path, requests):
    problem = AllocationProblemDSS(master_path=master_path, requests=requests)
    with pytest.raises(ValueError):
        problem.evaluate(["not-a-real-id"])


def test_size_fraction_out_of_range_rejected(master_path, requests):
    problem = AllocationProblemDSS(master_path=master_path, requests=requests)
    with pytest.raises(ValueError):
        problem.evaluate_sizes({"small": 1.5})


def test_duplicate_request_ids_rejected(master_path, requests):
    with pytest.raises(ValueError):
        AllocationProblemDSS(master_path=master_path, requests=[requests[0], requests[0]])


def test_repeated_accept_set_returns_the_cached_result(master_path, requests):
    problem = AllocationProblemDSS(master_path=master_path, requests=requests)
    r1 = problem.evaluate(["small"])
    r2 = problem.evaluate(["small"])
    assert r1 is r2


# -- greedy_baseline_dss -------------------------------------------------------

def test_greedy_baseline_dss_stops_at_the_first_violation(master_path):
    many = [DSSRequest(id=f"r{i}", bus="b2", phase=1, p_kw=4.0, queue_order=i) for i in range(10)]
    problem = AllocationProblemDSS(master_path=master_path, requests=many)
    result = greedy_baseline_dss(problem)
    assert result.feasible
    assert 0 < len(result.accepted_ids) < len(many)
    next_id = f"r{len(result.accepted_ids)}"
    with_next = problem.evaluate(set(result.accepted_ids) | {next_id})
    assert not with_next.feasible


def test_greedy_baseline_dss_accepts_everything_when_nothing_binds(master_path):
    few = [DSSRequest(id=f"r{i}", bus="b2", phase=1, p_kw=0.5, queue_order=i) for i in range(3)]
    problem = AllocationProblemDSS(master_path=master_path, requests=few)
    result = greedy_baseline_dss(problem)
    assert result.accepted_ids == {r.id for r in few}


# -- nsga2_search_dss -----------------------------------------------------------

def test_nsga2_search_dss_returns_some_feasible_results(master_path):
    many = [DSSRequest(id=f"r{i}", bus="b2", phase=1, p_kw=4.0, queue_order=i) for i in range(10)]
    problem = AllocationProblemDSS(master_path=master_path, requests=many)
    results = nsga2_search_dss(problem, pop_size=8, n_gen=5, seed=0)
    assert any(r.feasible for r in results)


def test_nsga2_search_dss_reproducible_with_same_seed(master_path):
    many = [DSSRequest(id=f"r{i}", bus="b2", phase=1, p_kw=4.0, queue_order=i) for i in range(10)]
    p1 = AllocationProblemDSS(master_path=master_path, requests=many)
    p2 = AllocationProblemDSS(master_path=master_path, requests=many)
    r1 = nsga2_search_dss(p1, pop_size=8, n_gen=5, seed=0)
    r2 = nsga2_search_dss(p2, pop_size=8, n_gen=5, seed=0)
    assert {r.accepted_ids for r in r1} == {r.accepted_ids for r in r2}
