"""Daily coupling of selectable water, plant and depth-resolved soil modules."""
from importlib import import_module
from copy import deepcopy
from datetime import date
import numpy as np
import pandas as pd
from soil_profile import make_profile, root_weights
from weather import demo_weather, fetch_open_meteo

# Legacy one-step APIs remain available for comparison and existing projects.
DEFAULTS={
 "water_balance":{"capacity_mm":200,"field_capacity_mm":150,"infiltration_capacity_mm_day":60,"crop_coefficient":0.8},
 "vegetation":{"max_growth_gC_m2_day":3.0,"carrying_capacity_gC_m2":1000,"turnover_fraction_day":0.002},
 "soil_carbon":{"k_fast_day":0.003,"k_slow_day":0.00015,"q10":2.0,"fast_input_fraction":0.7,"fast_to_slow_fraction":0.2},
}

from parameters import default_settings, plant_parameters
SCIENCE_DEFAULTS = default_settings()


def coupling_contract(selected):
    selected=set(selected)
    links=[]
    if {'water_balance','vegetation'}<=selected:
        links += [('Plants','Water','LAI, canopy extinction, rooting depth and root fractions'),('Water','Plants','Actual/potential transpiration ratio limits NPP')]
    if {'vegetation','soil_carbon'}<=selected:
        links += [('Plants','Soil carbon','Aboveground litter to surface; root litter distributed by root fractions')]
    if {'water_balance','soil_carbon'}<=selected:
        links += [('Water','Soil carbon','Layer pore saturation controls microbial rate limitation')]
    return [{'From':a,'To':b,'Exchange':c} for a,b,c in links]


def simulate(config, params=None, forcing=None, soil=None, progress=None):
    selected = set(config['models'])
    if not selected or not selected <= {'water_balance','vegetation','soil_carbon'}:
        raise ValueError('Choose supported model components')
    settings = {key: dict(value) for key, value in SCIENCE_DEFAULTS.items()}
    for key, value in (params or {}).items():
        settings[key].update(value)
    start = date.fromisoformat(config.get('start_date', '2023-01-01'))
    if forcing is not None:
        df = forcing.copy()
    elif config['weather_source'] == 'open_meteo':
        df = fetch_open_meteo(config['latitude'], config['longitude'], config['days'], start)
    elif config['weather_source'] == 'demo_weather':
        df = demo_weather(config['days'], start)
    else:
        raise ValueError('Unknown forcing source')
    required = {'date', 'precip_mm', 'et0_mm', 'temp_c'}
    if not required.issubset(df.columns) or df.empty:
        raise ValueError('Forcing must contain daily date, precipitation, ET0 and temperature')
    values = df[['precip_mm','et0_mm','temp_c']].apply(pd.to_numeric, errors='raise')
    dates = pd.to_datetime(df.date, errors='raise')
    if not np.isfinite(values.to_numpy()).all() or (values[['precip_mm','et0_mm']] < 0).any().any():
        raise ValueError('Forcing must be finite with nonnegative precipitation and ET0')
    if not dates.reset_index(drop=True).equals(pd.Series(pd.date_range(dates.iloc[0],periods=len(df),freq='D'))):
        raise ValueError('The selected models require consecutive daily forcing')
    df[values.columns] = values
    initial=settings['initial']
    profile = make_profile(config.get('layer_depths_cm'),soil,config.get('soil_texture','loam'),initial,config.get('soil_overrides'))
    resolved_depths = [float(profile.top_cm.iloc[0]), *profile.bottom_cm.astype(float).tolist()]
    profile_depth = resolved_depths[-1]
    profile.attrs['mapping']['dataset'] = config.get('soil_source','supplied soil profile' if soil is not None else 'explicit texture preset fallback')
    water = import_module('models.water_balance') if 'water_balance' in selected else None
    plants = import_module('models.vegetation') if 'vegetation' in selected else None
    carbon = import_module('models.soil_carbon') if 'soil_carbon' in selected else None
    if not np.isfinite(list(initial.values())).all() or min(initial.values())<0 or not 0<=initial['water_fraction_fc']<=1:
        raise ValueError('Initial states must be finite/nonnegative; water fraction must be within 0–1')
    fractions=np.array([initial[name+'_fraction'] for name in ('particulate','dissolved','microbial','mineral','aggregate')])
    if not np.isclose(fractions.sum(),1.,atol=1e-8,rtol=0):
        raise ValueError('Initial carbon pool fractions must sum to one')
    storage=(profile.capacity_mm*profile.field_capacity*initial['water_fraction_fc']).to_numpy(float)
    plant_state=np.array([initial['leaf_carbon'],initial['stem_carbon'],initial['root_carbon']])
    carbon_state=profile.initial_soc_gC_m2.to_numpy()[:,None]*fractions
    latitude = float(config.get('latitude', 30.62798))
    if not np.isfinite(latitude) or not -90 <= latitude <= 90:
        raise ValueError('Latitude must be between -90 and 90')
    plant_params = {}
    if plants:
        preset = settings['vegetation'].get('preset','grass_c3')
        if preset not in plants.PRESETS:
            raise ValueError('Unknown ecosystem preset')
        plant_params=plant_parameters(preset,{k:v for k,v in settings['vegetation'].items() if k!='preset'})
    # These inputs are fixed throughout a run. Prepare them outside the daily
    # and water-substep loops without changing fluxes or numerical resolution.
    hydraulics = water.prepare_profile(profile) if water else None
    capacities = profile.capacity_mm.to_numpy(float)
    field_capacity = profile.field_capacity.to_numpy(float)
    layer_labels = [f'{top:g}_{bottom:g}cm' for top,bottom in zip(profile.top_cm,profile.bottom_cm)]
    water_settings = {**settings['water_balance']}
    if plants:
        water_settings['light_extinction'] = plant_params['light_extinction']
    result = []
    initial_water = float(storage.sum())
    initial_soil = float(carbon_state.sum()) if carbon else 0.
    initial_plant = float(plant_state.sum()) if plants else 0.
    daily_forcing = df[['date','precip_mm','et0_mm','temp_c']].itertuples(index=False,name=None)
    for index, (day, rain, et0, temperature) in enumerate(daily_forcing):
        stamp = pd.Timestamp(day)
        row = dict(date=stamp.strftime('%Y-%m-%d'), precip_mm=float(rain),
                   et0_mm=float(et0), temp_c=float(temperature))
        lai, depth = plants.canopy(plant_state, plant_params) if plants else (0., min(40.,profile_depth))
        depth = min(depth, profile_depth)
        roots = root_weights(profile,depth,plant_params.get('root_density_beta',3.))
        stress = 1.
        if water:
            w = water.step_profile(storage, row['precip_mm'], row['et0_mm'], hydraulics, roots, lai,
                                   settings['water_balance']['substeps'],water_settings)
            storage, stress = w['storage'], w['water_stress']
            row.update(soil_water_mm=float(storage.sum()), aet_mm=w['aet'], runoff_mm=w['runoff'],
                       drainage_mm=w['drainage'], water_residual_mm=w['residual'],
                       evaporation_mm=w['evaporation'], transpiration_mm=w['transpiration'],
                       interception_mm=w['interception'], water_stress=stress)
        litter = np.zeros(len(profile)); uptake = plant_residual = 0.
        if plants:
            p = plants.step_pools(plant_state,row['temp_c'],stress,stamp.day_of_year,latitude,plant_params)
            plant_state = p['state']; uptake = p['uptake']; plant_residual = p['residual']
            litter += p['root_litter']*roots; litter[0] += p['above_litter']
            lai, depth = plants.canopy(plant_state,plant_params)
            row.update(plant_biomass_gC_m2=float(plant_state.sum()), plant_uptake_gC_m2_day=uptake,
                       leaf_carbon_gC_m2=float(plant_state[0]), stem_carbon_gC_m2=float(plant_state[1]),
                       root_carbon_gC_m2=float(plant_state[2]), lai=lai, root_depth_cm=min(depth,profile_depth),
                       litter_gC_m2_day=float(litter.sum()), plant_residual_gC_m2=plant_residual,
                       daylength_hours=p['daylength_hours'])
        if carbon:
            saturation = storage/capacities if water else field_capacity
            c = carbon.step_layers(carbon_state,litter,row['temp_c'],saturation,settings['soil_carbon'])
            carbon_state = c['state']
            row.update(soil_carbon_gC_m2=float(carbon_state.sum()), soil_respiration_gC_m2_day=float(c['respiration'].sum()),
                       carbon_residual_gC_m2=float(c['residual'].sum()),
                       ecosystem_carbon_residual_gC_m2=float(c['residual'].sum()+plant_residual))
            for pool, name in enumerate(carbon.POOL_NAMES):
                row[f'{name}_carbon_gC_m2'] = float(carbon_state[:,pool].sum())
        for layer,label in enumerate(layer_labels):
            if water:
                row[f'water_{label}_mm'] = float(storage[layer])
                row[f'saturation_{label}'] = float(storage[layer]/capacities[layer])
                row[f'water_residual_{label}_mm'] = float(w['layer_residual'][layer])
            if carbon:
                row[f'soc_{label}_gC_m2'] = float(carbon_state[layer].sum())
                row[f'carbon_residual_{label}_gC_m2'] = float(c['residual'][layer])
        result.append(row)
        if progress and (index == 0 or (index+1) % max(1,len(df)//100) == 0 or index+1 == len(df)):
            progress(index+1,len(df))
    output = pd.DataFrame(result)
    effective_configuration = deepcopy(config)
    effective_configuration.update(models=sorted(selected), layer_depths_cm=resolved_depths,
        soil_depth_cm=profile_depth, start_date=dates.iloc[0].strftime('%Y-%m-%d'),
        end_date=dates.iloc[-1].strftime('%Y-%m-%d'), days=len(df), step_days=1,
        soil_texture=config.get('soil_texture','loam'),
        soil_source=profile.attrs['mapping']['dataset'],
        trait_selection=deepcopy(config.get('trait_selection')),
        parameters=deepcopy(settings), plant_parameters=deepcopy(plant_params))
    output.attrs.update(scientific_version='calabrese-depth-v2',coupling=coupling_contract(selected), soil_mapping=profile.attrs['mapping'],
                        trait_selection=config.get('trait_selection'),
                        layer_depths_cm=resolved_depths, profile_depth_cm=profile_depth,
                        effective_configuration=effective_configuration,
                        domain_initialization='Fresh domain initialized from the configured source profile and initial states for this run',
                        soil_profile=profile.to_dict('records'), parameters=settings,
                        plant_preset=plant_params, initial_water_mm=initial_water,
                        initial_soil_carbon_gC_m2=initial_soil, initial_plant_carbon_gC_m2=initial_plant,
                        assumptions=['Daily forcing distributed uniformly across numerical substeps',
                          'Air temperature drives all soil layers; no soil thermal solver',
                          *(['Absent water module: field-capacity moisture and unstressed plants'] if not water else []),
                          *(['Absent plant module: bare soil and no fresh litter inputs'] if not plants else []),
                          *(['Absent soil-carbon module: litter is exported from the modeled domain'] if plants and not carbon else []),
                          'Soil carbon does not dynamically change porosity or water hydraulic parameters',
                          'No snow, groundwater, nutrients, management or lateral carbon transport',
                          'Uncalibrated model; conservation does not demonstrate observational accuracy'])
    return output
