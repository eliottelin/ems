"""Leaf/stem/root growth and canopy, plus legacy biomass API; g C/m²."""
from typing import Mapping, TypedDict
from . import require_finite


class VegetationStep(TypedDict):
    biomass: float  # End-of-day stock, g C/m2.
    litter: float  # Daily transfer from incoming biomass to soil, g C/m2/day.
    uptake: float  # Net external carbon input, g C/m2/day.


def step(biomass:float,temp_c:float,water_stress:float,params:Mapping[str, float]) -> VegetationStep:
    """Advance one day from biomass (g C/m2), temperature (C), stress (0-1).

    Uptake is schematic net growth, not GPP; no plant respiration is resolved.
    Turnover uses incoming biomass. All inputs must be finite.
    """
    max_growth=float(params.get("max_growth_gC_m2_day",3.0))
    capacity=float(params.get("carrying_capacity_gC_m2",1000.0))
    turnover_rate=float(params.get("turnover_fraction_day",0.002))
    require_finite(biomass=biomass, temp_c=temp_c, water_stress=water_stress,
                   max_growth_gC_m2_day=max_growth, carrying_capacity_gC_m2=capacity,
                   turnover_fraction_day=turnover_rate)
    if biomass<0 or capacity<=0 or max_growth<0 or not 0<=turnover_rate<=1 or not 0<=water_stress<=1:
        raise ValueError("Invalid vegetation parameter/state")
    # Demonstration response peaks at 25C and declines symmetrically.
    thermal=max(0.0,1.0-abs(temp_c-25.0)/25.0)
    uptake=max(0.0,max_growth*thermal*water_stress*(1.0-min(biomass/capacity,1.0)))
    litter=min(biomass,biomass*turnover_rate)
    return {"biomass":biomass+uptake-litter,"litter":litter,"uptake":uptake}
from parameters import PLANT_PRESETS as PRESETS, plant_parameters


def canopy(state, params):
    """LAI from leaf carbon; roots depend on root carbon, within preset limits."""
    import numpy as np
    lai = min(params['lai_max'], state[0]*params['sla_m2_gC'])
    depth = params['root_min_cm']+(params['root_max_cm']-params['root_min_cm'])*(1-np.exp(-state[2]/params.get('root_carbon_scale',200.)))
    return float(lai), float(depth)


def step_pools(state, temp_c, stress, day_of_year, latitude, params):
    """Leaf/stem/root C with net assimilation, phenology, allocation and litter.

    Net assimilation is NPP, not GPP. No artificial radiation or measured TRY
    numeric traits are implied. Presets are uncalibrated; see SCIENCE.md.
    """
    import numpy as np
    params=plant_parameters(overrides=params)
    s = np.asarray(state, dtype=float).copy()
    if s.shape != (3,) or not np.isfinite(s).all() or np.any(s < 0):
        raise ValueError('Plant pools must be three finite nonnegative stocks')
    if not np.isfinite([temp_c, stress, latitude]).all() or not 0 <= stress <= 1 or not -90 <= latitude <= 90:
        raise ValueError('Invalid plant environment')
    lai, _ = canopy(s, params)
    # Day length from solar geometry; no weather radiation is manufactured.
    declination = .409*np.sin(2*np.pi*day_of_year/365-1.39)  # FAO-56 equation 24
    angle = np.arccos(np.clip(-np.tan(np.radians(latitude))*np.tan(declination), -1, 1))
    daylight = 24*angle/np.pi
    thermal = np.clip((temp_c-params['t_base'])/(params['t_opt']-params['t_base']), 0, 1)
    thermal *= np.clip((params.get('t_max',params['t_opt']+20)-temp_c)/(params.get('t_max',params['t_opt']+20)-params['t_opt']), 0, 1)
    seasonal = np.clip(daylight/params.get('daylength_reference',12.), 0, 1)
    uptake = params['max_npp']*thermal*seasonal*stress*(1-np.exp(-params.get('light_extinction',.5)*lai))
    # Stress shifts allocation toward roots, without changing total NPP.
    root_fraction = params.get('root_allocation',.35)+params.get('drought_root_bonus',.20)*(1-stress)
    leaf_share=params.get('leaf_allocation',.45)
    allocation = np.array([leaf_share*(1-root_fraction), (1-leaf_share)*(1-root_fraction), root_fraction])
    senescence = 1+params.get('senescence_multiplier',3.)*(1-thermal*seasonal)
    rates = np.array([params['leaf_turnover']*senescence, params.get('stem_turnover',.001), params['root_turnover']])
    litter = s*(1-np.exp(-rates))
    s += uptake*allocation-litter
    return dict(state=s, uptake=float(uptake), above_litter=float(litter[:2].sum()),
                root_litter=float(litter[2]), residual=float(s.sum()-np.sum(state)-uptake+litter.sum()),
                daylength_hours=float(daylight))

