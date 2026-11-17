"""Conservation and domain checks shared by EMS and selectively exported apps."""
import importlib
import importlib.util
from math import isfinite

import pytest


CASES = {
    "water_balance": ({"storage": 100., "precip_mm": 20., "et0_mm": 4.}, {
        "capacity_mm": 200., "field_capacity_mm": 150.,
        "infiltration_capacity_mm_day": 60., "crop_coefficient": .8,
    }),
    "vegetation": ({"biomass": 400., "temp_c": 25., "water_stress": .8}, {
        "max_growth_gC_m2_day": 3., "carrying_capacity_gC_m2": 1000.,
        "turnover_fraction_day": .002,
    }),
    "soil_carbon": ({"fast": 1200., "slow": 5000., "litter": 5.,
                     "temp_c": 25., "moisture": .8}, {
        "k_fast_day": .003, "k_slow_day": .00015, "q10": 2.,
        "fast_input_fraction": .7, "fast_to_slow_fraction": .2,
    }),
}


def component(name):
    # Only absent, unselected modules are skipped. Broken imports must fail.
    if importlib.util.find_spec("models." + name) is None:
        pytest.skip(f"{name} was not selected for this project")
    return importlib.import_module("models." + name)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
@pytest.mark.parametrize("name,kind,key", [
    (name, kind, key)
    for name, (inputs, params) in CASES.items()
    for kind, values in (("input", inputs), ("parameter", params))
    for key in values
])
def test_nonfinite_values_are_rejected(name, kind, key, bad):
    model = component(name)
    inputs, params = (values.copy() for values in CASES[name])
    (inputs if kind == "input" else params)[key] = bad
    with pytest.raises(ValueError, match=key):
        model.step(**inputs, params=params)


@pytest.mark.parametrize("name,kind,key,bad", [
    ("water_balance", "input", "storage", -1.),
    ("water_balance", "input", "storage", 201.),
    ("water_balance", "input", "precip_mm", -1.),
    ("water_balance", "input", "et0_mm", -1.),
    ("water_balance", "parameter", "capacity_mm", 0.),
    ("water_balance", "parameter", "field_capacity_mm", 201.),
    ("vegetation", "input", "biomass", -1.),
    ("vegetation", "input", "water_stress", 1.1),
    ("vegetation", "parameter", "carrying_capacity_gC_m2", 0.),
    ("vegetation", "parameter", "turnover_fraction_day", 1.1),
    ("soil_carbon", "input", "fast", -1.),
    ("soil_carbon", "input", "slow", -1.),
    ("soil_carbon", "input", "litter", -1.),
    ("soil_carbon", "input", "moisture", 1.1),
    ("soil_carbon", "parameter", "q10", 0.),
    ("soil_carbon", "parameter", "fast_input_fraction", 1.1),
    ("soil_carbon", "parameter", "fast_to_slow_fraction", 1.1),
])
def test_out_of_domain_values_are_rejected(name, kind, key, bad):
    model = component(name)
    inputs, params = (values.copy() for values in CASES[name])
    (inputs if kind == "input" else params)[key] = bad
    with pytest.raises(ValueError):
        model.step(**inputs, params=params)


@pytest.mark.parametrize("storage,rain,et0", [
    (0., 0., 0.), (0., 0., 100.), (200., 1000., 0.), (100., 25., 5.),
])
def test_water_conservation_in_dry_and_overflow_conditions(storage, rain, et0):
    result = component("water_balance").step(storage, rain, et0, {})
    net_input = rain - result["runoff"] - result["aet"] - result["drainage"]
    assert result["storage"] - storage == pytest.approx(net_input, abs=1e-10)
    assert abs(result["water_residual"]) < 1e-10
    assert 0 <= result["storage"] <= 200
    assert all(result[key] >= 0 for key in ("runoff", "aet", "drainage"))
    assert 0 <= result["water_stress"] <= 1


def test_drought_prevents_growth_but_preserves_turnover_balance():
    model = component("vegetation")
    dry = model.step(400., 25., 0., {})
    wet = model.step(400., 25., 1., {})
    assert dry["uptake"] == 0
    assert wet["uptake"] > dry["uptake"]
    for result in (dry, wet):
        assert result["biomass"] - 400 == pytest.approx(result["uptake"] - result["litter"])


def test_litter_is_added_after_decay_and_losses_cannot_exceed_stocks():
    model = component("soil_carbon")
    params = {"k_fast_day": 1e6, "k_slow_day": 1e6}
    empty = model.step(0., 0., 5., 25., 1., params)
    assert empty["respiration"] == 0
    assert empty["fast"] + empty["slow"] == pytest.approx(5.)
    result = model.step(1200., 5000., 5., 25., 1., params)
    assert result["fast"] >= 0 and result["slow"] >= 0
    assert result["fast"] + result["slow"] - 6200 == pytest.approx(5. - result["respiration"])
    assert abs(result["carbon_residual"]) < 1e-9


def test_q10_cap_does_not_overflow_and_matches_normal_temperature_response():
    model = component("soil_carbon")
    cool = model.step(1000., 0., 0., 20., 1., {})
    warm = model.step(1000., 0., 0., 30., 1., {})
    assert warm["respiration"] == pytest.approx(2 * cool["respiration"])
    capped = model.step(1000., 0., 0., 1e6, 1., {})
    assert capped["respiration"] == pytest.approx(10 * cool["respiration"])
    assert all(isfinite(value) for value in capped.values())


def test_coupled_water_and_ecosystem_carbon_conservation_over_a_year():
    water = component("water_balance")
    plants = component("vegetation")
    carbon = component("soil_carbon")
    from weather import demo_weather

    storage, biomass, fast, slow = 100., 400., 1200., 5000.
    initial_water = storage
    initial_carbon = biomass + fast + slow
    net_water = net_carbon = 0.
    for forcing in demo_weather(365).itertuples(index=False):
        w = water.step(storage, forcing.precip_mm, forcing.et0_mm, {})
        p = plants.step(biomass, forcing.temp_c, w["water_stress"], {})
        c = carbon.step(fast, slow, p["litter"], forcing.temp_c, w["water_stress"], {})
        next_water = w["storage"]
        next_carbon = p["biomass"] + c["fast"] + c["slow"]
        daily_water = forcing.precip_mm - w["runoff"] - w["aet"] - w["drainage"]
        daily_carbon = p["uptake"] - c["respiration"]
        assert next_water - storage == pytest.approx(daily_water, abs=1e-9)
        assert next_carbon - (biomass + fast + slow) == pytest.approx(daily_carbon, abs=1e-9)
        storage, biomass, fast, slow = next_water, p["biomass"], c["fast"], c["slow"]
        net_water += daily_water
        net_carbon += daily_carbon
        assert min(storage, biomass, fast, slow) >= 0
    assert storage - initial_water == pytest.approx(net_water, abs=1e-8)
    assert biomass + fast + slow - initial_carbon == pytest.approx(net_carbon, abs=1e-8)


def test_weather_demo_length_and_values():
    from weather import demo_weather
    weather = demo_weather(22)
    assert len(weather) == 22
    assert (weather.precip_mm >= 0).all()
    assert (weather.et0_mm >= 0).all()


def test_engine_runs_with_only_available_components():
    from engine import simulate

    selected = [name for name in CASES if importlib.util.find_spec("models." + name)]
    assert selected, "At least one selected model must be present"
    result = simulate({"models": selected, "weather_source": "demo_weather", "days": 30})
    assert len(result) == 30
    assert all(isfinite(value) for value in result.select_dtypes("number").to_numpy().flat)
    for column in ("water_residual_mm", "carbon_residual_gC_m2"):
        if column in result:
            assert result[column].abs().max() < 1e-8
