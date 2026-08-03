"""Import an OpenDSS feeder into a balanced (positive-sequence) pandapower net.

Why balanced / positive-sequence (and not unsymmetrical 3-phase): pandapower's
`runpp_3ph` is a sequence-frame solver that cannot represent truly unsymmetrical
North-American topology (single-phase laterals, center-tapped split-phase) —
pandapower issue #873. Positive-sequence is also sufficient for the conductor-
inference / parameter-validation research this toolkit is built around, and
OpenDSS exposes the positive-sequence `R1/X1/C1` of every line directly, so the
phase->sequence reduction is free. The single-phase laterals are folded into the
balanced model as full three-phase equivalents; that is the main documented
fidelity loss, reported per-import in `net["opendss_import"]`.

Public API:
    import_opendss(master_path) -> pandapowerNet

The returned net carries an `opendss_import` dict (element counts, warnings, and
the OpenDSS-solved per-bus |V| in pu) so the round-trip validation harness in
experiments/08 can compare without re-solving OpenDSS.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandapower as pp

# OpenDSS LineUnits enum -> kilometres. The conversion factor cancels in the
# product r_ohm_per_km * length_km (= total ohms), so the *impedance* a pandapower
# line carries is exact even where the unit is ambiguous; only the reported
# length_km is nominal. "none" (0) is therefore safely treated as km.
_LINE_UNITS_TO_KM = {
    0: 1.0,          # none -> treat as km (impedance still exact, see above)
    1: 1.609344,     # miles
    2: 0.3048,       # kft (1000 ft)
    3: 1.0,          # km
    4: 1.0e-3,       # m
    5: 3.048e-4,     # ft
    6: 2.54e-5,      # in
    7: 1.0e-5,       # cm
}

_SQRT3 = math.sqrt(3.0)

# Base for expressing the Vsource's Thevenin impedance in per unit. Only the ratio
# matters -- it cancels against `sn_mva` on the pandapower impedance branch.
_SOURCE_SN_MVA = 100.0

# Below this the source is an idealisation, not a model: the common "stiff source"
# idioms (`r1=0 x1=0.001`, `R1=1e-05`) land 3-4 orders of magnitude under it, while
# a genuinely declared short-circuit impedance lands above.
_SOURCE_Z_NEGLIGIBLE_PU = 1e-3


def _kron_positive_sequence(rmat: list[float], xmat: list[float], n: int) -> tuple[float, float]:
    """Positive-sequence (R1, X1) for a matrix-defined (``rmatrix``/``xmatrix``)
    line of any conductor count, computed directly from the declared matrix.

    OpenDSS's own ``Lines.R1()``/``X1()``/``C1()`` are *symmetrical-component*
    fields: they hold whatever was last assigned via ``r1=``/``x1=``/``c1=``
    (which also makes OpenDSS internally regenerate an equivalent matrix), but
    switching a line/LineCode to matrix mode (``rmatrix=``/``xmatrix=``) does
    **not** clear or recompute them -- verified against OpenDSS's own source
    (``TLineObj``/`TLineCodeObj` construction hard-codes ``R1 := 0.0580``,
    ``X1 := 0.1206`` and the *text* property interface correctly reports
    those fields as ``'----'``/not-applicable once matrix mode is active, but
    the direct numeric getters used here do not). So for a matrix-defined
    line, ``R1()`` silently returns that stale default, not a value derived
    from the declared matrix -- confirmed both for a 4-wire (3 phase + explicit
    neutral) European LineCode and for a plain 3-conductor matrix LineCode.

    Always deriving from the raw matrix instead is safe for every case: for a
    symmetric-components-defined line OpenDSS auto-generates a consistent
    equivalent matrix (``CalcMatricesFromZ1Z0``), so re-deriving from it
    reproduces the declared R1/X1 exactly (verified). Bus tokens order phases
    before neutral by OpenDSS convention (``.1.2.3.4`` = A,B,C,N), so for
    n>3 conductors the trailing rows/columns (indices 3..n-1) are the ones to
    Kron-eliminate before averaging self/mutual over the remaining <=3 phase
    block; for n<=3 (the common case, and the only case OpenDSS's own R1/X1
    ever cover) there is nothing to eliminate.
    """
    z = np.asarray(rmat, dtype=float).reshape(n, n) + 1j * np.asarray(xmat, dtype=float).reshape(n, n)
    p = min(n, 3)
    zpp = z[:p, :p]
    if n > p:
        zpn, znp, znn = z[:p, p:], z[p:, :p], z[p:, p:]
        try:
            zpp = zpp - zpn @ np.linalg.solve(znn, znp)
        except np.linalg.LinAlgError:
            pass  # degenerate neutral block; fall back to the un-reduced phase block
    self_avg = np.trace(zpp) / p
    mutual_avg = (zpp.sum() - np.trace(zpp)) / (p * (p - 1)) if p > 1 else 0.0
    z1 = self_avg - mutual_avg
    return float(z1.real), float(z1.imag)


@dataclass
class ImportReport:
    """Diagnostics attached to the net as ``net["opendss_import"]``."""

    n_buses: int = 0
    n_lines: int = 0
    n_reactors: int = 0
    n_switches: int = 0
    n_transformers: int = 0
    n_split_phase_transformers: int = 0
    n_loads: int = 0
    n_shunts: int = 0
    single_phase_lines: int = 0
    two_phase_lines: int = 0
    warnings: list[str] = field(default_factory=list)
    vm_pu_opendss: dict[str, float] = field(default_factory=dict)
    bus_phases: dict[str, int] = field(default_factory=dict)
    source_bus: int | None = None        # the bus the OpenDSS Vsource sits on
    slack_bus: int | None = None         # where the ext_grid went (== source_bus unless modelled)
    source_r1_ohm: float = 0.0
    source_x1_ohm: float = 0.0
    source_z_pu: float = 0.0             # |Z1| per unit on 100 MVA and the Vsource basekV
    source_impedance_modelled: bool = False

    def as_dict(self) -> dict:
        return self.__dict__


def _busname(token: str) -> str:
    """Strip the node-connection suffix from an OpenDSS bus token.

    ``"b2.1.2.3" -> "b2"``; OpenDSS is case-insensitive and ``AllBusNames``
    returns lower-case, so we normalise here too.
    """
    return token.split(".", 1)[0].strip().lower()


def _connected_phases(token: str) -> int:
    """Count the phase nodes encoded in a bus token (``b2.1.2.3`` -> 3).

    A bare name (no suffix) means all phases are connected; the caller passes the
    element's declared phase count as the fallback in that case.
    """
    parts = token.split(".")[1:]
    phases = [p for p in parts if p not in ("", "0")]  # node 0 is neutral/ground
    return len(phases)


def import_opendss(master_path: str, solve: bool = True,
                   source_impedance: bool = False):
    """Build a balanced pandapower net from an OpenDSS master file.

    Parameters
    ----------
    master_path:
        Path to the OpenDSS master ``.dss`` (the file you would ``Redirect`` to).
    solve:
        If True (default), solve the circuit in OpenDSS first and capture the
        per-bus |V| (pu, phase-averaged) into the import report for validation.
    source_impedance:
        Model the Vsource's Thevenin impedance by giving the EMF its own bus and
        inserting a ``net.impedance`` branch, so the source bus sags under load as
        it does in OpenDSS. Off by default because it moves ``net.ext_grid.bus``
        off the feeder head -- see `_add_source`. When off, a non-negligible
        declared impedance is reported as a warning rather than silently dropped.

    Returns
    -------
    pandapowerNet with an ``opendss_import`` diagnostics dict.
    """
    import opendssdirect as dss

    dss.Command("Clear")
    dss.Command(f'Redirect "{master_path}"')
    if solve:
        dss.Solution.Solve()

    net = pp.create_empty_network(name=dss.Circuit.Name())
    report = ImportReport()

    bus_map = _add_buses(dss, net, report)
    if solve and dss.Solution.Converged():
        _capture_voltages(dss, report)

    _add_source(dss, net, bus_map, report, source_impedance)
    _add_lines(dss, net, bus_map, report)
    _add_reactors(dss, net, bus_map, report)
    _add_transformers(dss, net, bus_map, report)
    _add_loads(dss, net, bus_map, report)
    _add_capacitors(dss, net, bus_map, report)

    report.n_buses = len(net.bus)
    net["opendss_import"] = report.as_dict()
    return net


def _add_buses(dss, net, report: ImportReport) -> dict[str, int]:
    bus_map: dict[str, int] = {}
    for name in dss.Circuit.AllBusNames():
        dss.Circuit.SetActiveBus(name)
        kv_ln = dss.Bus.kVBase()  # OpenDSS bus base is line-to-neutral
        vn_kv = kv_ln * _SQRT3 if kv_ln > 0 else 0.0  # pandapower wants line-to-line
        if vn_kv == 0.0:
            report.warnings.append(f"bus {name!r} has no voltage base (kVBase=0)")
        report.bus_phases[name.lower()] = len([n for n in dss.Bus.Nodes() if n != 0])
        bus_map[name.lower()] = pp.create_bus(net, vn_kv=vn_kv, name=name)
    return bus_map


def _capture_voltages(dss, report: ImportReport) -> None:
    for name in dss.Circuit.AllBusNames():
        dss.Circuit.SetActiveBus(name)
        mags = dss.Bus.puVmagAngle()[::2]  # [mag, ang, mag, ang, ...]
        mags = [m for m in mags if m > 0]
        if mags:
            report.vm_pu_opendss[name.lower()] = sum(mags) / len(mags)


def _add_source(dss, net, bus_map, report: ImportReport,
                source_impedance: bool = False) -> None:
    """Add the ext_grid, optionally behind the Vsource's Thevenin impedance.

    An OpenDSS ``New Circuit.<name> pu=X r1=.. x1=..`` sets the EMF *behind* that
    impedance, so the source bus sags under load. pandapower's power flow treats an
    ext_grid as an ideal slack (source impedance is short-circuit data there), so
    pinning ``vm_pu`` on the Vsource bus overstates it by the internal drop --
    0.081 pu on EPRI Ckt7 at its 76.5 MW peak, against 0.002 on Ckt24 and 0.000 on
    SMART-DS.

    With ``source_impedance=True`` the EMF gets its own bus and the declared
    impedance is inserted as a ``net.impedance`` branch, which reproduces the sag.
    That moves ``net.ext_grid.bus`` OFF the feeder head, so callers that use it as
    the tree root want ``report["source_bus"]`` instead, and callers building a
    graph with ``create_nxgraph(..., include_impedances=False)`` must switch it on
    or the EMF bus lands in its own component. Hence the default is off: it changes
    net topology, not just numbers.
    """
    if not dss.Vsources.First():
        report.warnings.append("no Vsource found; net has no ext_grid")
        return
    name = dss.Vsources.Name()
    dss.Circuit.SetActiveElement("Vsource." + name)
    busname = _busname(dss.CktElement.BusNames()[0])
    bus = bus_map.get(busname)
    if bus is None:
        report.warnings.append("Vsource bus not found among circuit buses")
        return

    pu = dss.Vsources.PU()
    r1, x1 = _vsource_z1_ohm(dss)
    basekv = float(dss.Vsources.BasekV() or 0.0)
    z_base = basekv ** 2 / _SOURCE_SN_MVA if basekv > 0 else 0.0
    report.source_bus = bus
    report.source_r1_ohm, report.source_x1_ohm = r1, x1
    report.source_z_pu = math.hypot(r1, x1) / z_base if z_base > 0 else 0.0

    if source_impedance and report.source_z_pu > _SOURCE_Z_NEGLIGIBLE_PU:
        emf = pp.create_bus(net, vn_kv=float(net.bus.at[bus, "vn_kv"]),
                            name=f"{busname}_emf")
        pp.create_ext_grid(net, emf, vm_pu=pu, va_degree=0.0, name=name)
        pp.create_impedance(net, from_bus=emf, to_bus=bus,
                            rft_pu=r1 / z_base, xft_pu=x1 / z_base,
                            sn_mva=_SOURCE_SN_MVA, name=f"{name}_z1")
        report.slack_bus = emf
        report.source_impedance_modelled = True
        return

    pp.create_ext_grid(net, bus, vm_pu=pu, va_degree=0.0, name=name)
    report.slack_bus = bus
    if report.source_z_pu > _SOURCE_Z_NEGLIGIBLE_PU:
        report.warnings.append(
            f"Vsource impedance not modelled: r1={r1:g} x1={x1:g} ohm "
            f"(|Z1|={report.source_z_pu:.4g} pu on {_SOURCE_SN_MVA:g} MVA); the source bus "
            f"is pinned at vm_pu={pu:g} and will not sag under load. "
            f"Re-import with source_impedance=True to model it.")


def _vsource_z1_ohm(dss) -> tuple[float, float]:
    """Positive-sequence (R1, X1) of the active Vsource, in ohms.

    Read through the property interface rather than a typed getter: `Vsources` has
    no R1/X1 accessor, and OpenDSS keeps R1/X1 consistent with the MVAsc3/MVAsc1
    form, so this works whichever way the circuit declared its source.
    """
    try:
        return float(dss.Properties.Value("R1")), float(dss.Properties.Value("X1"))
    except (ValueError, TypeError):
        return 0.0, 0.0


def _add_lines(dss, net, bus_map, report: ImportReport) -> None:
    i = dss.Lines.First()
    while i:
        name = dss.Lines.Name()
        f = bus_map.get(_busname(dss.Lines.Bus1()))
        t = bus_map.get(_busname(dss.Lines.Bus2()))
        if f is None or t is None:
            report.warnings.append(f"line {name!r} references an unknown bus; skipped")
            i = dss.Lines.Next()
            continue

        phases = max(_connected_phases(dss.Lines.Bus1()), _connected_phases(dss.Lines.Bus2()))
        if phases == 0:
            phases = dss.Lines.Phases()
        if phases == 1:
            report.single_phase_lines += 1
        elif phases == 2:
            report.two_phase_lines += 1

        km = _LINE_UNITS_TO_KM.get(dss.Lines.Units(), 1.0)
        length_km = dss.Lines.Length() * km

        # A switch (or a zero-length jumper) becomes a pandapower bus-bus switch.
        if dss.Lines.IsSwitch() or length_km <= 0.0:
            closed = not dss.CktElement.IsOpen(1, 0)
            pp.create_switch(net, bus=f, element=t, et="b", closed=closed, name=name)
            report.n_switches += 1
            i = dss.Lines.Next()
            continue

        # Always derive R1/X1/C1 from the declared matrix rather than trusting
        # OpenDSS's own Lines.R1()/X1()/C1() -- those are stale symmetric-
        # component fields for any matrix-defined line, not just >3-conductor
        # ones (see `_kron_positive_sequence`). Safe for every case: it exactly
        # reproduces R1/X1 for a symmetric-components-defined line too.
        n_cond = dss.Lines.Phases()
        r1, x1 = _kron_positive_sequence(dss.Lines.RMatrix(), dss.Lines.XMatrix(), n_cond)
        c1, _ = _kron_positive_sequence(dss.Lines.CMatrix(), [0.0] * (n_cond * n_cond), n_cond)

        # The OpenDSS LineCode names the physical conductor; carry it through as
        # pandapower's std_type so the conductor identity survives the import
        # (used as the ground-truth label by the conductor-inference experiments).
        pp.create_line_from_parameters(
            net, from_bus=f, to_bus=t, length_km=length_km,
            r_ohm_per_km=r1 / km,
            x_ohm_per_km=x1 / km,
            c_nf_per_km=c1 / km,
            max_i_ka=dss.Lines.NormAmps() / 1000.0,
            std_type=dss.Lines.LineCode() or None,
            name=name,
        )
        report.n_lines += 1
        i = dss.Lines.Next()


def _add_reactors(dss, net, bus_map, report: ImportReport) -> None:
    """Series (bus-to-bus) Reactor elements as a fixed-impedance pandapower line.

    EPRI's Ckt5/Ckt7 test circuits model the substation's Thevenin-equivalent
    source impedance as a ``Reactor`` between ``SourceBus`` and the feeder head
    bus, not a ``Transformer`` -- without this, that link is silently dropped
    and the whole feeder downstream of it comes back electrically unsupplied
    (confirmed: 2997/2998 Ckt5 buses). A shunt reactor (single bus, to ground)
    is a different physical element (not a branch) and is out of scope here.
    """
    i = dss.Reactors.First()
    while i:
        name = dss.Reactors.Name()
        dss.Circuit.SetActiveElement("Reactor." + name)
        buses = dss.CktElement.BusNames()
        if len(buses) < 2 or _busname(buses[0]) == _busname(buses[1]):
            report.warnings.append(f"reactor {name!r} is a shunt (single bus); skipped")
            i = dss.Reactors.Next()
            continue

        f = bus_map.get(_busname(buses[0]))
        t = bus_map.get(_busname(buses[1]))
        if f is None or t is None:
            report.warnings.append(f"reactor {name!r} references an unknown bus; skipped")
            i = dss.Reactors.Next()
            continue

        max_i_ka = dss.CktElement.NormalAmps() / 1000.0
        pp.create_line_from_parameters(
            net, from_bus=f, to_bus=t, length_km=1.0,
            r_ohm_per_km=dss.Reactors.R(), x_ohm_per_km=dss.Reactors.X(), c_nf_per_km=0.0,
            max_i_ka=max_i_ka if max_i_ka > 0 else 10.0,
            name=name,
        )
        report.n_reactors += 1
        i = dss.Reactors.Next()


def _add_transformers(dss, net, bus_map, report: ImportReport) -> None:
    i = dss.Transformers.First()
    while i:
        _add_one_transformer(dss, net, bus_map, report)
        i = dss.Transformers.Next()


def _add_one_transformer(dss, net, bus_map, report: ImportReport) -> None:
    name = dss.Transformers.Name()
    dss.Circuit.SetActiveElement("Transformer." + name)
    wbus = [_busname(b) for b in dss.CktElement.BusNames()]
    nwdg = dss.Transformers.NumWindings()

    kva, pct_r, tap = [], [], []
    for w in range(1, nwdg + 1):
        dss.Transformers.Wdg(w)
        kva.append(dss.Transformers.kVA())
        pct_r.append(dss.Transformers.R())    # %R of this winding
        tap.append(dss.Transformers.Tap())    # solved tap ratio (captures RegControl)
    xhl = dss.Transformers.Xhl()              # HV-LV leakage reactance, %

    # Pick the HV winding and the LV winding.
    split_phase = False
    if nwdg == 2:
        hv_w, lv_w = 0, 1
    elif nwdg == 3 and len(set(wbus)) == 2:
        # Center-tapped split-phase service transformer (the dominant SMART-DS
        # element): one HV winding + two LV windings on the SAME secondary bus.
        # Collapse to a balanced 2-winding equivalent. This is a documented
        # approximation -- positive-sequence cannot represent 120/240 V split
        # phase (pandapower #873) -- so LV voltages here carry the larger error.
        split_phase = True
        unique = [b for b in dict.fromkeys(wbus)]
        lv_name = next(b for b in unique if wbus.count(b) == 2)
        hv_name = next(b for b in unique if wbus.count(b) == 1)
        hv_w, lv_w = wbus.index(hv_name), wbus.index(lv_name)
    else:
        report.warnings.append(
            f"transformer {name!r} has {nwdg} windings across "
            f"{len(set(wbus))} buses; unsupported topology, skipped")
        return

    bus_hv = bus_map.get(wbus[hv_w])
    bus_lv = bus_map.get(wbus[lv_w])
    if bus_hv is None or bus_lv is None:
        report.warnings.append(f"transformer {name!r} references an unknown bus; skipped")
        return

    # Order by the buses' own voltage base so hv_bus really is the higher side.
    if net.bus.at[bus_hv, "vn_kv"] < net.bus.at[bus_lv, "vn_kv"]:
        bus_hv, bus_lv, hv_w, lv_w = bus_lv, bus_hv, lv_w, hv_w

    # Use the connected buses' vn_kv (already kVBase*sqrt(3)) as the winding
    # ratings: OpenDSS propagates voltage bases through transformers, so the bus
    # ratio already equals the turns ratio -- this sidesteps line-to-line vs
    # line-to-neutral / sqrt(3) ambiguity. The solved tap multiplies on top.
    vn_hv = net.bus.at[bus_hv, "vn_kv"] * tap[hv_w]
    vn_lv = net.bus.at[bus_lv, "vn_kv"] * tap[lv_w]
    if vn_hv <= 0 or vn_lv <= 0:
        report.warnings.append(f"transformer {name!r} has a zero-base winding; skipped")
        return
    if any(abs(t - 1.0) > 1e-6 for t in tap):
        report.warnings.append(
            f"transformer {name!r} imported at solved tap {tuple(round(t, 4) for t in tap)} "
            "(RegControl baked in as a fixed tap)")

    vkr = pct_r[hv_w] + pct_r[lv_w]           # copper/short-circuit R, % (= %loadloss)
    vk = math.hypot(vkr, xhl)                  # vk = sqrt(vkr^2 + vx^2)
    pp.create_transformer_from_parameters(
        net, hv_bus=bus_hv, lv_bus=bus_lv,
        sn_mva=max(kva) / 1000.0,
        vn_hv_kv=vn_hv, vn_lv_kv=vn_lv,
        vk_percent=vk, vkr_percent=vkr,
        pfe_kw=0.0, i0_percent=0.0,            # core losses dropped in v1
        shift_degree=0.0,                      # vector-group shift: no effect on balanced |V|
        name=name,
    )
    report.n_transformers += 1
    if split_phase:
        report.n_split_phase_transformers += 1


def _add_loads(dss, net, bus_map, report: ImportReport) -> None:
    i = dss.Loads.First()
    while i:
        name = dss.Loads.Name()
        dss.Circuit.SetActiveElement("Load." + name)
        bus = bus_map.get(_busname(dss.CktElement.BusNames()[0]))
        if bus is None:
            report.warnings.append(f"load {name!r} references an unknown bus; skipped")
            i = dss.Loads.Next()
            continue
        pp.create_load(net, bus, p_mw=dss.Loads.kW() / 1000.0,
                       q_mvar=dss.Loads.kvar() / 1000.0, name=name)
        report.n_loads += 1
        i = dss.Loads.Next()


def _add_capacitors(dss, net, bus_map, report: ImportReport) -> None:
    i = dss.Capacitors.First()
    while i:
        name = dss.Capacitors.Name()
        dss.Circuit.SetActiveElement("Capacitor." + name)
        bus = bus_map.get(_busname(dss.CktElement.BusNames()[0]))
        if bus is None:
            report.warnings.append(f"capacitor {name!r} references an unknown bus; skipped")
            i = dss.Capacitors.Next()
            continue
        # A shunt capacitor injects reactive power -> negative q_mvar in pandapower's
        # consumer sign convention (positive q_mvar = inductive absorption).
        pp.create_shunt(net, bus, q_mvar=-dss.Capacitors.kvar() / 1000.0, p_mw=0.0,
                        name=name)
        report.n_shunts += 1
        i = dss.Capacitors.Next()
