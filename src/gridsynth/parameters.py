"""Estimate line electrical parameters (R, X, C, thermal rating) for OSM lines.

The honest baseline -- and what every incumbent OSM->grid pipeline does -- is a
*voltage-class lookup*: assume one default conductor per voltage level and copy
its textbook parameters onto every line. This module implements that baseline so
its error can be measured against a ground-truth feeder (experiments/03), and
provides a physics path for the case where a cross-section is actually known.

Parameters returned are per-km and include zero-sequence values, which the
unbalanced three-phase power flow requires.
"""

from __future__ import annotations

from dataclasses import dataclass

# Resistivity at 20 C, ohm * mm^2 / m.
RESISTIVITY = {"Al": 0.0282, "Cu": 0.0172}


@dataclass(frozen=True)
class Conductor:
    """A conductor's full per-km parameter set (positive + zero sequence)."""

    name: str
    material: str
    cross_section_mm2: float
    r_ohm_per_km: float
    x_ohm_per_km: float
    c_nf_per_km: float
    r0_ohm_per_km: float
    x0_ohm_per_km: float
    c0_nf_per_km: float
    max_i_ka: float

    def as_params(self) -> dict[str, float]:
        return {
            "r_ohm_per_km": self.r_ohm_per_km,
            "x_ohm_per_km": self.x_ohm_per_km,
            "c_nf_per_km": self.c_nf_per_km,
            "r0_ohm_per_km": self.r0_ohm_per_km,
            "x0_ohm_per_km": self.x0_ohm_per_km,
            "c0_nf_per_km": self.c0_nf_per_km,
            "max_i_ka": self.max_i_ka,
        }


# Default conductor assumed when OSM gives only the voltage class.
# LV: a common 4x95 mm^2 aluminium cable. Capacitance is neglected at LV, which
# matches the convention used by the IEEE European LV reference feeder.
DEFAULT_LV = Conductor(
    name="default_lv_al_95",
    material="Al",
    cross_section_mm2=95.0,
    r_ohm_per_km=0.320,
    x_ohm_per_km=0.075,
    c_nf_per_km=0.0,
    r0_ohm_per_km=0.960,  # ~3x R for a 4-wire cable
    x0_ohm_per_km=0.090,
    c0_nf_per_km=0.0,
    max_i_ka=0.300,
)

# MV: a typical 150 mm^2 aluminium overhead/cable conductor at ~10-20 kV.
DEFAULT_MV = Conductor(
    name="default_mv_al_150",
    material="Al",
    cross_section_mm2=150.0,
    r_ohm_per_km=0.206,
    x_ohm_per_km=0.110,
    c_nf_per_km=250.0,
    r0_ohm_per_km=0.620,
    x0_ohm_per_km=0.400,
    c0_nf_per_km=150.0,
    max_i_ka=0.360,
)

LV_THRESHOLD_KV = 1.0


def default_conductor(vn_kv: float) -> Conductor:
    """Pick the default conductor for a voltage level."""
    return DEFAULT_LV if vn_kv < LV_THRESHOLD_KV else DEFAULT_MV


def resistance_from_area(cross_section_mm2: float, material: str = "Al") -> float:
    """DC resistance estimate (ohm/km) from a cross-section, R = rho / A."""
    return RESISTIVITY[material] / cross_section_mm2 * 1000.0


# Standard IEC 60228 aluminium LV cross-sections (mm^2).
STANDARD_LV_CROSS_SECTIONS_MM2 = (16.0, 25.0, 35.0, 50.0, 70.0, 95.0, 120.0, 150.0, 185.0, 240.0)


def generic_lv_catalogue():
    """A generic, no-utility-specific catalogue of standard LV aluminium
    conductors, for physics-based (planner-style) sizing where no local
    conductor inventory is available. R comes from
    ``resistance_from_area`` (physics, not fitted); reactance is held at
    ``DEFAULT_LV``'s value, matching the standard engineering approximation
    that LV cable reactance is dominated by geometry, not cross-section, and
    varies little across standard sizes. Ampacity has no such simple
    physical formula, so it is interpolated from a single calibration point
    (``DEFAULT_LV``: 95 mm^2 -> 300 A) via the textbook rule of thumb that LV
    cable ampacity scales roughly with cross-section^0.6 -- an approximation,
    clearly weaker than the R estimate, but still a real physical scaling
    rather than an invented number.

    Returns a ``pandas.DataFrame`` indexed by conductor name, with the
    columns a planner estimator expects (``r_ohm_per_km``, ``x_ohm_per_km``,
    ``c_nf_per_km``, ``max_i_ka``), so it drops directly into a pylovo-style
    estimator alongside a real per-region catalogue.
    """
    import pandas as pd

    anchor_mm2, anchor_i_ka = DEFAULT_LV.cross_section_mm2, DEFAULT_LV.max_i_ka
    rows = {}
    for mm2 in STANDARD_LV_CROSS_SECTIONS_MM2:
        rows[f"generic_lv_al_{int(mm2)}"] = {
            "r_ohm_per_km": resistance_from_area(mm2, "Al"),
            "x_ohm_per_km": DEFAULT_LV.x_ohm_per_km,
            "c_nf_per_km": DEFAULT_LV.c_nf_per_km,
            "max_i_ka": anchor_i_ka * (mm2 / anchor_mm2) ** 0.6,
        }
    return pd.DataFrame.from_dict(rows, orient="index")


def estimate_line_params(
    vn_kv: float,
    cross_section_mm2: float | None = None,
    material: str = "Al",
) -> dict[str, float]:
    """Estimate per-km line parameters from what OSM can supply.

    With only a voltage class (the realistic OSM case) this returns the default
    conductor for that level. If a cross-section is known, R (and R0) are
    replaced with a physics-based estimate while X/C/rating stay at the default.
    """
    base = default_conductor(vn_kv)
    params = base.as_params()
    if cross_section_mm2 is not None:
        r = resistance_from_area(cross_section_mm2, material)
        params["r_ohm_per_km"] = r
        params["r0_ohm_per_km"] = 3.0 * r
    return params
