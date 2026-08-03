"""Offline tests for `gridsynth.parameters`."""

import pytest

from gridsynth.parameters import DEFAULT_LV, resistance_from_area, generic_lv_catalogue


def test_generic_lv_catalogue_anchors_to_default_lv():
    cat = generic_lv_catalogue()
    anchor = cat.loc[f"generic_lv_al_{int(DEFAULT_LV.cross_section_mm2)}"]
    assert anchor["max_i_ka"] == pytest.approx(DEFAULT_LV.max_i_ka)
    assert anchor["r_ohm_per_km"] == pytest.approx(
        resistance_from_area(DEFAULT_LV.cross_section_mm2), rel=0.1)


def test_generic_lv_catalogue_is_monotonic_in_cross_section():
    cat = generic_lv_catalogue()
    # bigger cross-section -> lower resistance, higher ampacity, always
    assert (cat["r_ohm_per_km"].diff().dropna() < 0).all()
    assert (cat["max_i_ka"].diff().dropna() > 0).all()
