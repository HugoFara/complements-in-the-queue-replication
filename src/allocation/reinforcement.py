"""A real, catalogue-priced reinforcement-cost proxy.

``AllocationProblem.evaluate``'s ``reinforcement_cost_proxy`` (problem.py) is a
placeholder: monotonic in how far voltage/thermal margins are violated, enough
to give NSGA-II a smooth gradient to climb out of infeasibility with (a
*continuous* penalty a live search needs), but explicitly not a currency
figure -- see ROADMAP.md's "What 'cost' means" and docs/paper.md Section 5.
This module is the real thing it was scoped to become: given a subset that
overloads a line or transformer, what would it actually cost -- in a real
currency, from a real (if illustrative) catalogue -- to fix it?

The lever modeled is deliberately narrow and thermal-only: replace an
overloaded line with a larger standard conductor, or an overloaded
transformer with the next standard kVA size, priced from a small catalogue.
Two things are reused rather than invented: the line catalogue is exactly
``gridsynth.parameters.generic_lv_catalogue`` -- the same standard IEC 60228
LV aluminium cross-sections and R/ampacity physics Part I already validated
for conductor *inference*, reused here for conductor *upgrade sizing* -- and
every upgraded network is re-solved through the same ``pandapower.runpp``
oracle the rest of this package trusts, never just trusted from the sizing
arithmetic (the standing rule since M5).

What this does **not** model, on purpose (keep it simple, don't over-model):
voltage-specific devices (regulators, OLTC, capacitor banks) -- a pure
voltage violation with no thermal overload is reported as unresolved by this
catalogue, honestly, not silently priced at zero or guessed away. Pricing is
material/equipment cost only (conductor mass x an illustrative EUR/kg,
transformer kVA x an illustrative EUR/kVA) -- labor, trenching, permitting,
and regional variation, which typically dominate a real installed cost, are
explicitly excluded. The constants below are order-of-magnitude and labelled
as such; swap in a real utility price sheet via the ``catalogue`` argument to
get an authoritative number.
"""

from __future__ import annotations

import copy
from collections.abc import Iterable
from dataclasses import dataclass, field

import pandapower as pp

from allocation.problem import AllocationProblem, AllocationResult
from gridsynth.parameters import STANDARD_LV_CROSS_SECTIONS_MM2, generic_lv_catalogue

ALU_DENSITY_KG_PER_M3 = 2700.0  # a real physical constant, not a price assumption

# Illustrative, order-of-magnitude catalogue prices -- material/equipment cost
# only, no labor/trenching/permitting. Not sourced from a specific vendor
# quote; the point is a cost that scales like a real cost driver (conductor
# cross-section, transformer kVA), not a specific number to trust blindly.
EUR_PER_KG_AL_CONDUCTOR = 8.0
EUR_PER_KVA_TRAFO = 20.0

# Standard IEC-class distribution transformer ratings (kVA) -- ENWL's own
# feeders in this corpus use an 800 kVA unit, one of these tiers.
TRAFO_KVA_TIERS: tuple[float, ...] = (50.0, 100.0, 160.0, 250.0, 400.0, 630.0,
                                       800.0, 1000.0, 1250.0, 1600.0, 2000.0)


@dataclass(frozen=True)
class LineTier:
    """One catalogue conductor size and its electrical parameters."""

    cross_section_mm2: float
    r_ohm_per_km: float
    x_ohm_per_km: float
    max_i_ka: float


def _default_line_tiers() -> tuple[LineTier, ...]:
    """Line upgrade tiers straight from ``gridsynth.parameters.generic_lv_catalogue``
    -- the same catalogue, not a re-derived one, so a change to Part I's
    validated conductor physics automatically flows through to this pricing."""
    cat = generic_lv_catalogue()
    tiers = [
        LineTier(cross_section_mm2=mm2, r_ohm_per_km=float(row.r_ohm_per_km),
                  x_ohm_per_km=float(row.x_ohm_per_km), max_i_ka=float(row.max_i_ka))
        for mm2, (_, row) in zip(STANDARD_LV_CROSS_SECTIONS_MM2, cat.iterrows())
    ]
    return tuple(sorted(tiers, key=lambda t: t.cross_section_mm2))


@dataclass(frozen=True)
class ReinforcementCatalogue:
    """Prices + upgrade tiers ``price_reinforcement`` sizes against."""

    line_tiers: tuple[LineTier, ...] = field(default_factory=_default_line_tiers)
    trafo_kva_tiers: tuple[float, ...] = TRAFO_KVA_TIERS
    eur_per_kg_al: float = EUR_PER_KG_AL_CONDUCTOR
    eur_per_kva_trafo: float = EUR_PER_KVA_TRAFO


DEFAULT_CATALOGUE = ReinforcementCatalogue()


@dataclass(frozen=True)
class LineUpgrade:
    """One line reconductored to a larger catalogue tier.

    ``from_max_i_ka``/``to_max_i_ka`` are the real, unambiguous quantity an
    upgrade is chosen on (``to_max_i_ka`` is always strictly greater). A
    reverse cross-section label for "from" is deliberately not reported: on a
    real imported feeder the existing conductor is often a different class
    (e.g. a 3-core distribution trunk cable) than this catalogue's generic
    LV service-drop physics, so nearest-R matching can output a *smaller*
    mm2 label for the (real, correct) upgrade than the current conductor's
    nearest match -- true but confusing, so better left unreported than
    misread as a downgrade.
    """

    line_idx: int
    from_max_i_ka: float
    to_cross_section_mm2: float
    to_max_i_ka: float
    length_km: float
    cost_eur: float


@dataclass(frozen=True)
class TrafoUpgrade:
    """One transformer replaced with the next standard kVA size."""

    trafo_idx: int
    from_sn_mva: float
    to_sn_mva: float
    cost_eur: float


@dataclass(frozen=True)
class ReinforcementPlan:
    """What it costs to make ``accepted_ids`` AC-feasible on ``problem.net``.

    ``line_upgrades``/``trafo_upgrades``/``total_cost_eur`` describe only the
    upgrades this call found necessary (always computed fresh against
    ``problem.net``'s original ratings, never against a network some earlier
    call already mutated -- so ``total_cost_eur`` is the *total* cost to
    serve ``accepted_ids`` from an unreinforced feeder, not a marginal delta).
    ``result_after_reinforcement`` is AC-reverified through ``pandapower.runpp``
    on the actually-upgraded network, matching every other search result in
    this package.
    """

    accepted_ids: frozenset[str]
    baseline: AllocationResult  # as-built, no upgrades -- problem.evaluate(accepted_ids)
    line_upgrades: tuple[LineUpgrade, ...]
    trafo_upgrades: tuple[TrafoUpgrade, ...]
    total_cost_eur: float
    feasible_after_reinforcement: bool
    result_after_reinforcement: AllocationResult
    n_iterations: int


def _line_upgrade_cost_eur(cross_section_mm2: float, length_km: float, catalogue: ReinforcementCatalogue) -> float:
    area_m2 = cross_section_mm2 * 1e-6
    mass_kg = area_m2 * length_km * 1000.0 * ALU_DENSITY_KG_PER_M3
    return catalogue.eur_per_kg_al * mass_kg


def _upgrade_line(net: pp.pandapowerNet, line_idx: int, catalogue: ReinforcementCatalogue,
                   target_loading_pct: float, safety_margin_pct: float) -> LineUpgrade | None:
    row = net.line.loc[line_idx]
    loading_pct = float(net.res_line.at[line_idx, "loading_percent"])
    required_i_ka = row.max_i_ka * (loading_pct / target_loading_pct) * (1.0 + safety_margin_pct / 100.0)
    tier = next((t for t in catalogue.line_tiers if t.max_i_ka >= required_i_ka), None)
    if tier is None or tier.max_i_ka <= row.max_i_ka:
        return None  # already at/beyond the largest catalogue tier -- out of scope for this catalogue
    from_i_ka = float(row.max_i_ka)
    cost = _line_upgrade_cost_eur(tier.cross_section_mm2, float(row.length_km), catalogue) * float(row.parallel)
    net.line.loc[line_idx, ["r_ohm_per_km", "x_ohm_per_km", "max_i_ka"]] = (
        tier.r_ohm_per_km, tier.x_ohm_per_km, tier.max_i_ka)
    return LineUpgrade(line_idx=int(line_idx), from_max_i_ka=from_i_ka,
                        to_cross_section_mm2=tier.cross_section_mm2, to_max_i_ka=tier.max_i_ka,
                        length_km=float(row.length_km), cost_eur=cost)


def _upgrade_trafo(net: pp.pandapowerNet, trafo_idx: int, catalogue: ReinforcementCatalogue,
                    target_loading_pct: float, safety_margin_pct: float) -> TrafoUpgrade | None:
    row = net.trafo.loc[trafo_idx]
    loading_pct = float(net.res_trafo.at[trafo_idx, "loading_percent"])
    current_kva = row.sn_mva * 1000.0
    required_kva = current_kva * (loading_pct / target_loading_pct) * (1.0 + safety_margin_pct / 100.0)
    tier_kva = next((k for k in catalogue.trafo_kva_tiers if k >= required_kva), None)
    if tier_kva is None or tier_kva <= current_kva:
        return None  # already at/beyond the largest catalogue tier -- out of scope for this catalogue
    cost = catalogue.eur_per_kva_trafo * tier_kva * float(row.parallel)
    net.trafo.loc[trafo_idx, "sn_mva"] = tier_kva / 1000.0
    return TrafoUpgrade(trafo_idx=int(trafo_idx), from_sn_mva=float(row.sn_mva),
                         to_sn_mva=tier_kva / 1000.0, cost_eur=cost)


def _solve(net: pp.pandapowerNet, problem: AllocationProblem) -> dict | None:
    try:
        pp.runpp(net, **problem.runpp_kwargs)
    except pp.LoadflowNotConverged:
        return None
    vmin = float(net.res_bus.vm_pu.min())
    vmax = float(net.res_bus.vm_pu.max())
    max_line_pct = float(net.res_line.loading_percent.max()) if len(net.line) else 0.0
    max_trafo_pct = float(net.res_trafo.loading_percent.max()) if len(net.trafo) else 0.0
    voltage_violation = max(0.0, problem.vmin_pu - vmin) + max(0.0, vmax - problem.vmax_pu)
    thermal_violation = (max(0.0, max_line_pct - problem.max_loading_percent)
                          + max(0.0, max_trafo_pct - problem.max_loading_percent))
    return dict(vmin=vmin, vmax=vmax, max_line_pct=max_line_pct, max_trafo_pct=max_trafo_pct,
                voltage_violation=voltage_violation, thermal_violation=thermal_violation,
                feasible=(voltage_violation == 0.0 and thermal_violation == 0.0))


def _result_from_summary(accepted: frozenset[str], by_id: dict, summary: dict) -> AllocationResult:
    accommodated_mw = sum(abs(by_id[rid].p_mw) for rid in accepted)
    return AllocationResult(
        accepted_ids=accepted, converged=True, feasible=summary["feasible"],
        accommodated_mw=accommodated_mw, vmin_pu=summary["vmin"], vmax_pu=summary["vmax"],
        max_line_loading_percent=summary["max_line_pct"], max_trafo_loading_percent=summary["max_trafo_pct"],
        voltage_violation_pu=summary["voltage_violation"], thermal_violation_percent=summary["thermal_violation"],
        reinforcement_cost_proxy=summary["thermal_violation"] + summary["voltage_violation"] * 100)


def price_reinforcement(
    problem: AllocationProblem,
    accepted_ids: Iterable[str],
    catalogue: ReinforcementCatalogue = DEFAULT_CATALOGUE,
    safety_margin_pct: float = 2.0,
    max_iters: int = 5,
) -> ReinforcementPlan:
    """What would it cost to make ``accepted_ids`` AC-feasible on ``problem.net``?

    Solves as-built first (0 cost if already feasible). If a line or
    transformer is thermally overloaded, sizes the smallest catalogue tier
    that clears the limit (with ``safety_margin_pct`` headroom, the same
    "don't cut it exactly at the boundary" discipline M5's iterative solver
    uses), applies it, and re-solves -- repeating up to ``max_iters`` rounds,
    since upgrading one element can occasionally shift enough current that a
    previously-fine neighbor now binds. Every round is a real
    ``pandapower.runpp`` solve on the actually-upgraded network, never just
    the sizing arithmetic's own belief.

    A pure voltage violation (or a thermal violation past the largest
    catalogue tier) has no lever in this module and is reported as such:
    ``feasible_after_reinforcement=False``, ``total_cost_eur=inf`` -- not
    silently priced at zero or guessed away.
    """
    by_id = {r.id: r for r in problem.requests}
    accepted = frozenset(accepted_ids)
    unknown = accepted - by_id.keys()
    if unknown:
        raise ValueError(f"unknown request id(s): {sorted(unknown)}")

    baseline_result = problem.evaluate(accepted)

    net = copy.deepcopy(problem.net)
    for rid in accepted:
        r = by_id[rid]
        pp.create_load(net, bus=r.bus, p_mw=r.p_mw, q_mvar=r.q_mvar, name=r.id)

    summary = _solve(net, problem)
    if summary is None:
        # Didn't even converge before any upgrade -- this catalogue can't
        # confidently identify what to fix, so it doesn't guess.
        return ReinforcementPlan(accepted_ids=accepted, baseline=baseline_result, line_upgrades=(),
                                  trafo_upgrades=(), total_cost_eur=float("inf"),
                                  feasible_after_reinforcement=False,
                                  result_after_reinforcement=baseline_result, n_iterations=0)

    target = problem.max_loading_percent
    line_upgrades: list[LineUpgrade] = []
    trafo_upgrades: list[TrafoUpgrade] = []
    unresolved_lines: set[int] = set()
    unresolved_trafos: set[int] = set()
    n_iterations = 0

    for _ in range(max_iters):
        if summary["feasible"]:
            break
        over_lines = ([i for i in net.res_line.index[net.res_line.loading_percent > target]
                        if i not in unresolved_lines] if len(net.line) else [])
        over_trafos = ([i for i in net.res_trafo.index[net.res_trafo.loading_percent > target]
                         if i not in unresolved_trafos] if len(net.trafo) else [])
        if not over_lines and not over_trafos:
            break  # nothing left this catalogue can act on (e.g. a pure voltage violation)
        n_iterations += 1
        applied = False
        for li in over_lines:
            upgrade = _upgrade_line(net, li, catalogue, target, safety_margin_pct)
            if upgrade is not None:
                line_upgrades.append(upgrade)
                applied = True
            else:
                unresolved_lines.add(li)
        for ti in over_trafos:
            upgrade = _upgrade_trafo(net, ti, catalogue, target, safety_margin_pct)
            if upgrade is not None:
                trafo_upgrades.append(upgrade)
                applied = True
            else:
                unresolved_trafos.add(ti)
        if not applied:
            break
        summary = _solve(net, problem)
        if summary is None:
            break

    total_cost = sum(u.cost_eur for u in line_upgrades) + sum(u.cost_eur for u in trafo_upgrades)
    feasible_after = summary is not None and summary["feasible"]
    result_after = _result_from_summary(accepted, by_id, summary) if summary is not None else baseline_result

    return ReinforcementPlan(
        accepted_ids=accepted, baseline=baseline_result,
        line_upgrades=tuple(line_upgrades), trafo_upgrades=tuple(trafo_upgrades),
        total_cost_eur=(total_cost if feasible_after else float("inf")),
        feasible_after_reinforcement=feasible_after,
        result_after_reinforcement=result_after, n_iterations=n_iterations)
