"""Configurable domains conserve source inventories and expose resolved support."""
from copy import deepcopy

import numpy as np
import pandas as pd
import pytest

from comparison_view import collect_comparisons, RESPIRATION_DEPTH_NOTE
from data_blocks import DataBlock
from engine import simulate
from parameters import default_settings
from soil_profile import make_profile, resize_depths, root_weights, validate_depths


def source_profile():
    return pd.DataFrame([
        dict(top_cm=0, bottom_cm=10, sand_pct=40., silt_pct=35., clay_pct=25.,
             soc_g_kg=25., bulk_density_g_cm3=1.1, coarse_fragments_pct=5.),
        dict(top_cm=10, bottom_cm=35, sand_pct=40., silt_pct=35., clay_pct=25.,
             soc_g_kg=12., bulk_density_g_cm3=1.7, coarse_fragments_pct=65.),
        dict(top_cm=35, bottom_cm=100, sand_pct=40., silt_pct=35., clay_pct=25.,
             soc_g_kg=4., bulk_density_g_cm3=1.4, coarse_fragments_pct=20.),
    ])


@pytest.mark.parametrize('depths', [[], [0], [1, 5], [0, 5, 4], [0, 0],
    [0, float('inf')], [0, float('nan')], [0, 1001], list(range(32))])
def test_invalid_domains_fail_before_initialization(depths):
    with pytest.raises(ValueError):
        validate_depths(depths)


def test_valid_domain_limits_and_depth_resizing_preserve_saved_layer_fractions():
    assert validate_depths([0, 1000]) == [0., 1000.]
    assert len(validate_depths(np.linspace(0, 1000, 31))) == 31
    saved = [0, 10, 35, 100]
    assert resize_depths(saved, 250) == [0., 25., 87.5, 250.]
    assert saved == [0, 10, 35, 100]
    for invalid in (0, -1, 1001, float('nan')):
        with pytest.raises(ValueError):
            resize_depths(saved, invalid)


@pytest.mark.parametrize('depths', [[0, 100], [0, 5, 50, 100], [0, 10, 35, 100]])
def test_regridding_preserves_source_soc_and_fine_earth_pore_volume(depths):
    source = source_profile()
    original = source.copy(deep=True)
    thickness = source.bottom_cm-source.top_cm
    fine = 1-source.coarse_fragments_pct/100
    expected_carbon = (thickness*10*source.soc_g_kg*source.bulk_density_g_cm3*fine).sum()
    expected_pores = (thickness*10*(1-source.bulk_density_g_cm3/2.65)*fine).sum()
    profile = make_profile(depths, source)

    assert profile.initial_soc_gC_m2.sum() == pytest.approx(expected_carbon)
    assert profile.capacity_mm.sum() == pytest.approx(expected_pores)
    pd.testing.assert_frame_equal(source, original)
    assert profile.attrs['mapping']['layer_depths_cm'] == depths
    assert profile.attrs['mapping']['profile_depth_cm'] == 100.


def test_bulk_density_is_weighted_by_fine_earth_volume_when_layers_merge():
    source = source_profile()
    fine_volume = (source.bottom_cm-source.top_cm)*(1-source.coarse_fragments_pct/100)
    expected = np.dot(fine_volume, source.bulk_density_g_cm3)/fine_volume.sum()
    merged = make_profile([0, 100], source)

    assert merged.bulk_density_g_cm3.iloc[0] == pytest.approx(expected)
    assert merged.bulk_density_g_cm3.iloc[0] != pytest.approx(
        np.dot(source.bottom_cm-source.top_cm, source.bulk_density_g_cm3)/100)


def test_source_coverage_requires_the_entire_selected_domain_without_extrapolation():
    source = source_profile()
    with pytest.raises(ValueError, match='cover the complete'):
        make_profile([0, 150], source)
    source.loc[1, 'top_cm'] = 11
    with pytest.raises(ValueError, match='cover the complete'):
        make_profile([0, 100], source)


@pytest.mark.parametrize('depths', [[0, 20], [0, 20, 100], [0, 50, 500, 1000]])
def test_fallback_carbon_is_a_whole_profile_total_at_every_domain_depth(depths):
    profile = make_profile(depths, initial={'fallback_soc': 9000.})
    assert profile.initial_soc_gC_m2.sum() == pytest.approx(9000.)
    assert profile.attrs['mapping']['fallback_soc_gC_m2'] == 9000.
    assert 'whole-profile' in profile.attrs['mapping']['soc_fallback']


def test_engine_records_resolved_configuration_without_mutating_saved_config():
    config = {'models': ['vegetation', 'water_balance', 'soil_carbon'],
              'weather_source': 'demo_weather', 'days': 12, 'layer_depths_cm': [0, 15, 50],
              'soil_depth_cm': 999, 'latitude': 30.}
    original = deepcopy(config)
    params = default_settings()
    params['vegetation'] = {'preset': 'crop', 'max_npp': 9.}
    result = simulate(config, params)
    effective = result.attrs['effective_configuration']

    assert config == original
    assert result.attrs['layer_depths_cm'] == [0., 15., 50.]
    assert result.attrs['profile_depth_cm'] == 50.
    assert effective['soil_depth_cm'] == 50.
    assert effective['layer_depths_cm'] == [0., 15., 50.]
    assert effective['days'] == 12 and effective['end_date'] == '2023-01-12'
    assert effective['plant_parameters']['max_npp'] == 9.
    assert effective['parameters']['initial']['leaf_carbon'] == 100.
    assert 'Fresh domain' in result.attrs['domain_initialization']
    effective['layer_depths_cm'][1] = 1.
    assert config == original


def test_new_depth_starts_a_new_domain_with_capped_roots_and_conservative_budgets():
    config = {'models': ['water_balance', 'vegetation', 'soil_carbon'],
              'weather_source': 'demo_weather', 'days': 35, 'latitude': 30.}
    params = default_settings()
    params['vegetation'] = {'preset': 'forest'}
    results = [simulate({**config, 'layer_depths_cm': depths}, params)
               for depths in ([0, 20, 100], [0, 5, 15])]
    for result in results:
        assert result.root_depth_cm.max() <= result.attrs['profile_depth_cm']
        assert result.attrs['initial_plant_carbon_gC_m2'] == 400.
        assert result.attrs['initial_soil_carbon_gC_m2'] == pytest.approx(6200.)
        water_change = result.soil_water_mm.iloc[-1]-result.attrs['initial_water_mm']
        water_net = (result.precip_mm-result.aet_mm-result.runoff_mm-result.drainage_mm).sum()
        assert water_change == pytest.approx(water_net, abs=1e-9)
        carbon_change = (result.soil_carbon_gC_m2.iloc[-1]-result.attrs['initial_soil_carbon_gC_m2']
                         + result.plant_biomass_gC_m2.iloc[-1]-result.attrs['initial_plant_carbon_gC_m2'])
        carbon_net = (result.plant_uptake_gC_m2_day-result.soil_respiration_gC_m2_day).sum()
        assert carbon_change == pytest.approx(carbon_net, abs=1e-9)
    assert results[0].attrs['initial_water_mm'] > results[1].attrs['initial_water_mm']
    shallow = make_profile([0, 5, 15])
    assert root_weights(shallow, 1000) == pytest.approx(root_weights(shallow, 15))


def test_coupled_runs_preserve_initial_source_stocks_and_close_budgets_when_layers_merge():
    source = source_profile()
    config = {'models': ['water_balance', 'vegetation', 'soil_carbon'],
              'weather_source': 'demo_weather', 'days': 15, 'latitude': 30.}
    runs = [simulate({**config, 'layer_depths_cm': depths}, soil=source)
            for depths in ([0, 10, 35, 100], [0, 100])]
    assert runs[0].attrs['initial_water_mm'] == pytest.approx(runs[1].attrs['initial_water_mm'])
    assert runs[0].attrs['initial_soil_carbon_gC_m2'] == pytest.approx(
        runs[1].attrs['initial_soil_carbon_gC_m2'])
    for result in runs:
        water_delta = result.soil_water_mm.iloc[-1]-result.attrs['initial_water_mm']
        assert water_delta == pytest.approx(
            (result.precip_mm-result.aet_mm-result.runoff_mm-result.drainage_mm).sum(), abs=1e-9)
        carbon_delta = (result.soil_carbon_gC_m2.iloc[-1]-result.attrs['initial_soil_carbon_gC_m2']
                        + result.plant_biomass_gC_m2.iloc[-1]-result.attrs['initial_plant_carbon_gC_m2'])
        assert carbon_delta == pytest.approx(
            (result.plant_uptake_gC_m2_day-result.soil_respiration_gC_m2_day).sum(), abs=1e-9)


def test_annual_respiration_match_records_unverified_depth_support():
    simulation = pd.DataFrame({'date': pd.date_range('2023-01-01', '2023-12-31'),
                               'soil_respiration_gC_m2_day': 1.})
    simulation.attrs['profile_depth_cm'] = 40.
    source = DataBlock('srdb', pd.DataFrame([{'Record_number': 1,
        'Study_midyear': 2023.5, 'Rh_annual': 365., 'comparable_rh': True}]), 'annual', {}, {})
    comparisons, audit = collect_comparisons(simulation, {'srdb': source})

    assert len(comparisons) == 1
    assert comparisons.modeled_depth_cm.iloc[0] == 40.
    assert comparisons.depth_support.iloc[0] == RESPIRATION_DEPTH_NOTE
    assert audit.depth_support.iloc[0] == RESPIRATION_DEPTH_NOTE
    assert 'observed_depth_cm' not in comparisons
