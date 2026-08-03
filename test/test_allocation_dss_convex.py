"""Offline tests for M18's convex/MILP route for `AllocationProblemDSS`
(`allocation.dss_convex`).

Reuses test_allocation_dss.py's own minimal single-phase-lateral fixture
(a 3-phase trunk off a stiff source, one single-phase lateral with a
5A-rated line -- deliberately tiny so a moderate request already overloads
it) rather than the vendored IEEE13 data file, matching that file's own
"small synthetic circuit, no dependence on real network files" convention;
experiments/42_convex_dss_validation.py covers real-network-scale validation
against IEEE13. `sourcebus` doubles as this fixture's `root_bus` -- there's
no substation transformer or regulator between it and the rest of the
circuit, unlike IEEE13.
"""

import pytest

from allocation import AllocationProblemDSS, DSSRequest, greedy_baseline_dss
from allocation.dss_convex import build_lindistflow_model_dss, evaluate_relaxed_dss, lindistflow_search_dss

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

# Same trunk/lateral as MASTER_DSS, plus one extra 3-phase line closing a
# loop straight back to the source -- M19's minimal meshed-and-unbalanced
# case (the lateral off b1/b2 is still genuinely single-phase).
MESHED_MASTER_DSS = """\
Clear
Set DefaultBaseFrequency=60
New Circuit.test basekv=4.16 bus1=sourcebus pu=1.0 phases=3 MVAsc3=20000 MVAsc1=21000
New Line.trunk phases=3 bus1=sourcebus bus2=b1 length=0.1 units=km r1=0.3 x1=0.6 r0=0.4 x0=1.2 c1=0 c0=0 normamps=400
New Line.lateral phases=1 bus1=b1.1 bus2=b2.1 length=0.1 units=km r1=0.5 x1=0.8 r0=0.5 x0=0.8 c1=0 c0=0 normamps=5
New Line.tie phases=3 bus1=b1 bus2=sourcebus length=0.1 units=km r1=0.3 x1=0.6 r0=0.4 x0=1.2 c1=0 c0=0 normamps=400
New Load.base bus1=b1.1.2.3 phases=3 kV=4.16 kW=10 kvar=3 conn=wye
Set VoltageBases=[4.16]
CalcVoltageBases
"""

ROOT_BUS = "sourcebus"


@pytest.fixture
def master_path(tmp_path) -> str:
    path = tmp_path / "master.dss"
    path.write_text(MASTER_DSS)
    return str(path)


@pytest.fixture
def meshed_master_path(tmp_path) -> str:
    path = tmp_path / "meshed_master.dss"
    path.write_text(MESHED_MASTER_DSS)
    return str(path)


@pytest.fixture
def requests() -> list[DSSRequest]:
    return [
        DSSRequest(id="small", bus="b2", phase=1, p_kw=1.0, queue_order=0),
        DSSRequest(id="large", bus="b2", phase=1, p_kw=15.0, queue_order=1),
        DSSRequest(id="huge", bus="b2", phase=1, p_kw=50000.0, queue_order=2),
    ]


# -- build_lindistflow_model_dss / per-phase tree extraction ----------------

def test_model_builds_one_tree_per_phase(master_path, requests):
    problem = AllocationProblemDSS(master_path=master_path, requests=requests)
    model = build_lindistflow_model_dss(problem, root_bus=ROOT_BUS)
    assert set(model.trees) == {1, 2, 3}
    # phase 1 reaches both b1 (trunk) and b2 (lateral); phases 2/3 stop at b1
    assert set(model.trees[1].order) == {"b1", "b2"}
    assert set(model.trees[2].order) == {"b1"}
    assert set(model.trees[3].order) == {"b1"}


def test_nonzero_q_request_is_rejected(master_path):
    reactive = DSSRequest(id="r", bus="b2", phase=1, p_kw=1.0, q_kvar=0.5)
    problem = AllocationProblemDSS(master_path=master_path, requests=[reactive])
    with pytest.raises(NotImplementedError):
        build_lindistflow_model_dss(problem, root_bus=ROOT_BUS)


def test_request_on_a_phase_the_bus_does_not_carry_is_rejected(master_path):
    # b2 only exists on phase 1 (the lateral); phase 2 was never declared there
    bad = DSSRequest(id="r", bus="b2", phase=2, p_kw=1.0)
    problem = AllocationProblemDSS(master_path=master_path, requests=[bad])
    with pytest.raises(ValueError):
        build_lindistflow_model_dss(problem, root_bus=ROOT_BUS)


def test_request_at_the_root_bus_is_rejected(master_path):
    # the root's own voltage is fixed, not a decision variable -- mirrors
    # convex.py's own pandapower model, which can't host a request at ext_grid either
    bad = DSSRequest(id="r", bus=ROOT_BUS, phase=1, p_kw=1.0)
    problem = AllocationProblemDSS(master_path=master_path, requests=[bad])
    with pytest.raises(ValueError):
        build_lindistflow_model_dss(problem, root_bus=ROOT_BUS)


def test_meshed_network_is_rejected(meshed_master_path):
    """M19: a plain per-phase BFS never notices a cycle on its own -- it
    would silently pick an arbitrary spanning tree instead of raising,
    unlike convex.py's own radial-tree check. Caught on the real looped
    IEEE123 network (experiments/43_meshed_unbalanced_validation.py);
    reproduced here on a minimal 3-bus loop so it's covered offline too."""
    problem = AllocationProblemDSS(master_path=meshed_master_path, requests=[])
    with pytest.raises(ValueError):
        build_lindistflow_model_dss(problem, root_bus=ROOT_BUS)


def test_meshed_network_still_works_via_the_general_ac_route(meshed_master_path):
    """The mesh only breaks the radial-only MILP route -- AllocationProblemDSS
    itself (a general OpenDSS solve, no radial assumption) and greedy_
    baseline_dss (which just calls it request-by-request) should work
    exactly as on the non-meshed fixture."""
    requests = [
        DSSRequest(id="small", bus="b2", phase=1, p_kw=1.0, queue_order=0),
        DSSRequest(id="large", bus="b2", phase=1, p_kw=15.0, queue_order=1),
    ]
    problem = AllocationProblemDSS(master_path=meshed_master_path, requests=requests)
    base = problem.evaluate([])
    assert base.converged and base.feasible
    baseline = greedy_baseline_dss(problem)
    assert baseline.feasible
    assert "small" in baseline.accepted_ids


# -- evaluate_relaxed_dss: parity against the real OpenDSS oracle -----------

def test_relaxed_empty_subset_matches_the_real_baseline_exactly(master_path, requests):
    """The baseline calibration (see dss_convex._calibrate_baseline) is
    defined to close this exact gap at zero accepted requests -- checked
    directly, not just assumed to work from the derivation."""
    problem = AllocationProblemDSS(master_path=master_path, requests=requests)
    model = build_lindistflow_model_dss(problem, root_bus=ROOT_BUS)
    relaxed = evaluate_relaxed_dss(model, [])
    actual = problem.evaluate([])
    assert relaxed.vmin_pu == pytest.approx(actual.vmin_pu, abs=1e-9)
    assert relaxed.vmax_pu == pytest.approx(actual.vmax_pu, abs=1e-9)


def test_relaxed_can_report_infeasible_looking_predictions_without_erroring(master_path, requests):
    # "large" (15kW on a 5A-rated lateral), not "huge" (50000kW) -- a
    # moderate overload the unbounded LP forward-solve stays numerically
    # sane on, the same restraint convex.py's own equivalent test uses;
    # "huge" pushes the unbounded relaxation's own v^2 negative, a separate,
    # expected numerical-blowup case, not what this test is checking.
    problem = AllocationProblemDSS(master_path=master_path, requests=requests)
    model = build_lindistflow_model_dss(problem, root_bus=ROOT_BUS)
    relaxed = evaluate_relaxed_dss(model, ["large"])  # overloads the lateral
    actual = problem.evaluate(["large"])
    assert not actual.feasible
    assert relaxed.max_line_loading_percent > 90


# -- lindistflow_search_dss: MILP optimum, always AC re-verified ------------

def test_lindistflow_search_dss_result_is_ac_verified(master_path, requests):
    problem = AllocationProblemDSS(master_path=master_path, requests=requests)
    conv = lindistflow_search_dss(problem, root_bus=ROOT_BUS)
    reverified = problem.evaluate(conv.result.accepted_ids)
    assert conv.result.feasible == reverified.feasible
    assert conv.result.accommodated_mw == pytest.approx(reverified.accommodated_mw)
    assert conv.result.accepted_ids == conv.relaxed.accepted_ids


def test_lindistflow_search_dss_rejects_the_huge_request(master_path, requests):
    problem = AllocationProblemDSS(master_path=master_path, requests=requests)
    conv = lindistflow_search_dss(problem, root_bus=ROOT_BUS)
    assert conv.result.feasible
    assert "huge" not in conv.result.accepted_ids
    assert "small" in conv.result.accepted_ids


def test_lindistflow_search_dss_accepts_everything_when_nothing_binds(master_path):
    tiny = [DSSRequest(id=f"r{i}", bus="b2", phase=1, p_kw=0.01, queue_order=i) for i in range(3)]
    problem = AllocationProblemDSS(master_path=master_path, requests=tiny)
    conv = lindistflow_search_dss(problem, root_bus=ROOT_BUS)
    assert conv.result.feasible
    assert conv.result.accepted_ids == {r.id for r in tiny}


def test_lindistflow_search_dss_is_reproducible(master_path, requests):
    problem = AllocationProblemDSS(master_path=master_path, requests=requests)
    a = lindistflow_search_dss(problem, root_bus=ROOT_BUS)
    b = lindistflow_search_dss(problem, root_bus=ROOT_BUS)
    assert a.result.accepted_ids == b.result.accepted_ids
