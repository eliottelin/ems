"""Conservation, depth mapping and response tests for the actual coupled engine."""
from importlib.util import find_spec
from importlib import import_module
import numpy as np
import pandas as pd
import pytest
from engine import simulate
from soil_profile import make_profile, root_weights
from weather import demo_weather


def component(name):
    if find_spec('models.'+name) is None:
        pytest.skip(name+' not selected')
    return import_module('models.'+name)


@pytest.mark.parametrize('depths',[[0,5,15,30,60,100,200],[0,20,70,200],[0,200]])
@pytest.mark.parametrize('rain,et0',[(0.,0.),(0.,100.),(1000.,0.),(25.,5.)])
def test_profile_water_balance_each_layer_and_whole(depths,rain,et0):
    water=component('water_balance')
    profile=make_profile(depths)
    incoming=(profile.capacity_mm*profile.field_capacity).to_numpy()
    result=water.step_profile(incoming,rain,et0,profile,root_weights(profile,40),lai=3)
    assert abs(result['residual'])<1e-9
    assert np.abs(result['layer_residual']).max()<1e-9
    assert result['storage'].sum()-incoming.sum()==pytest.approx(rain-result['aet']-result['runoff']-result['drainage'],abs=1e-9)
    assert (result['storage']>=0).all()
    assert (result['storage']<=profile.capacity_mm+1e-9).all()
    assert incoming==pytest.approx(profile.capacity_mm*profile.field_capacity), 'Inputs must not mutate'


def test_root_depth_changes_uptake_distribution_without_creating_water():
    water=component('water_balance')
    profile=make_profile()
    state=(profile.capacity_mm*profile.field_capacity).to_numpy()
    shallow=water.step_profile(state,0,5,profile,root_weights(profile,15),lai=3)
    deep=water.step_profile(state,0,5,profile,root_weights(profile,150),lai=3)
    assert shallow['uptake_layers'][-2:] == pytest.approx([0.,0.])
    assert (deep['uptake_layers'][-2:]>0).any()
    assert abs(deep['residual'])<1e-9
    assert root_weights(profile,15).sum()==pytest.approx(1)


def test_profile_drought_limits_transpiration():
    water=component('water_balance'); profile=make_profile()
    dry=water.step_profile(np.zeros(len(profile)),0,5,profile,root_weights(profile,40),lai=3)
    wet=water.step_profile((profile.capacity_mm*profile.field_capacity).to_numpy(),0,5,profile,root_weights(profile,40),lai=3)
    assert dry['transpiration']==0 and dry['water_stress']==0
    assert wet['transpiration']>0 and wet['water_stress']>0


def test_plant_allocation_phenology_and_internal_litter_balance():
    plants=component('vegetation'); params=plants.PRESETS['grass_c3']
    state=np.array([100.,100.,200.])
    warm=plants.step_pools(state,20.,1.,180,30.,params)
    dry=plants.step_pools(state,20.,0.,180,30.,params)
    cold=plants.step_pools(state,-5.,1.,10,30.,params)
    assert warm['uptake']>dry['uptake']==cold['uptake']==0
    for result in (warm,dry,cold):
        assert result['state'].sum()-state.sum()==pytest.approx(result['uptake']-result['above_litter']-result['root_litter'])
        assert abs(result['residual'])<1e-10
    low=plants.canopy(np.array([50.,100.,50.]),params)
    high=plants.canopy(np.array([500.,100.,500.]),params)
    assert high[0]>low[0] and high[1]>low[1]
    assert high[0]<=params['lai_max'] and high[1]<=params['root_max_cm']


@pytest.mark.parametrize('rate',[0.,.003,1e6])
def test_five_soil_pools_have_nonnegative_conservative_fluxes(rate):
    carbon=component('soil_carbon')
    state=np.array([[100.,10.,20.,500.,50.],[100.,1.,5.,800.,100.]])
    litter=np.array([5.,2.])
    params={key:rate for key in ['k_particulate','k_uptake','k_microbial','k_desorption','k_aggregate','k_sorption','k_aggregation']}
    result=carbon.step_layers(state,litter,25.,np.array([.6,.6]),params)
    assert (result['state']>=-1e-12).all()
    assert (result['respiration']>=0).all()
    assert result['state'].sum(axis=1)-state.sum(axis=1)==pytest.approx(litter-result['respiration'],abs=1e-10)
    assert np.abs(result['residual']).max()<1e-10


def test_data_to_parameter_mapping_conserves_source_carbon_on_regridding():
    if find_spec('data_blocks.soil') is None:
        pytest.skip('Soil source block omitted')
    raw=pd.DataFrame([dict(top_cm=0,bottom_cm=20,sand_pct=40,silt_pct=35,clay_pct=25,soc_g_kg=20,bulk_density_g_cm3=1.3,coarse_fragments_pct=10),
                      dict(top_cm=20,bottom_cm=200,sand_pct=40,silt_pct=35,clay_pct=25,soc_g_kg=5,bulk_density_g_cm3=1.5,coarse_fragments_pct=20)])
    profile=make_profile([0,5,15,30,60,100,200],raw)
    expected=20*1.3*20*10*.9 + 5*1.5*180*10*.8
    assert profile.initial_soc_gC_m2.sum()==pytest.approx(expected)
    assert profile.porosity.iloc[0]==pytest.approx(1-1.3/2.65)
    assert (profile.ksat_mm_day==250).all()
    assert make_profile([0,200],raw).initial_soc_gC_m2.sum()==pytest.approx(expected)
    with pytest.raises(ValueError,match='cover'):
        make_profile([0,250],raw)


@pytest.mark.parametrize('depths',[[0,20,70,200],[0,5,15,30,60,100,200]])
def test_new_engine_full_year_external_budgets_and_progress(depths):
    selected=[key for key in ['water_balance','vegetation','soil_carbon'] if find_spec('models.'+key)]
    calls=[]
    result=simulate({'models':selected,'weather_source':'demo_weather','days':365,'layer_depths_cm':depths},progress=lambda done,total:calls.append((done,total)))
    assert calls[-1]==(365,365)
    assert all(a[0]<b[0] for a,b in zip(calls,calls[1:]))
    assert np.isfinite(result.select_dtypes('number').to_numpy()).all()
    if 'water_balance' in selected:
        change=result.soil_water_mm.iloc[-1]-result.attrs['initial_water_mm']
        net=(result.precip_mm-result.aet_mm-result.runoff_mm-result.drainage_mm).sum()
        assert change==pytest.approx(net,abs=1e-8)
    if 'soil_carbon' in selected:
        change=result.soil_carbon_gC_m2.iloc[-1]-result.attrs['initial_soil_carbon_gC_m2']
        net=-result.soil_respiration_gC_m2_day.sum()
        if 'vegetation' in selected:
            change+=result.plant_biomass_gC_m2.iloc[-1]-result.attrs['initial_plant_carbon_gC_m2']
            net+=result.plant_uptake_gC_m2_day.sum()
        assert change==pytest.approx(net,abs=1e-8)


@pytest.mark.parametrize('depths',[[1,20],[0,20,10],[0,float('nan')],[0,0],[0,2000]])
def test_invalid_depths_fail_explicitly(depths):
    with pytest.raises(ValueError):
        make_profile(depths)


@pytest.mark.parametrize('selected',[
    ['water_balance'],['vegetation'],['soil_carbon'],
    ['water_balance','vegetation'],['water_balance','soil_carbon'],
    ['vegetation','soil_carbon'],['water_balance','vegetation','soil_carbon']])
def test_every_available_component_combination_has_only_its_own_outputs(selected):
    if any(find_spec('models.'+name) is None for name in selected):
        pytest.skip('Unselected model deliberately absent from export')
    result=simulate({'models':selected,'weather_source':'demo_weather','days':10})
    for name,column in [('water_balance','soil_water_mm'),('vegetation','lai'),('soil_carbon','soil_carbon_gC_m2')]:
        assert (column in result)==(name in selected)
    assert len(result)==10


def test_darcy_interface_zero_flux_for_hydrostatic_head_and_correct_gravity_sign():
    water=component('water_balance')
    profile=make_profile([0,20,40])
    profile['b']=1.; profile['air_entry_mm']=100.
    # Centers differ by 200 mm: psi_lower = psi_upper + 200 balances gravity.
    saturation=np.array([.2,1/3])  # psi=-500,-300 mm
    interface,_=water.darcy_flux(saturation,profile)
    assert interface[0]==pytest.approx(0.,abs=1e-12)
    downward,_=water.darcy_flux(np.array([.5,.5]),profile)
    upward,_=water.darcy_flux(np.array([.1,.8]),profile)
    assert downward[0]>0 and upward[0]<0


def test_prepared_hydraulics_are_read_only_and_detached_from_source():
    water=component('water_balance')
    profile=make_profile([0,20,40])
    prepared=water.prepare_profile(profile)
    roots=root_weights(profile,40)
    storage=(profile.capacity_mm*profile.field_capacity).to_numpy()
    expected=water.step_profile(storage,25.,5.,profile,roots,lai=3.)
    profile.loc[:,'ksat_mm_day']=0.
    profile.loc[:,'capacity_mm']=1.
    actual=water.step_profile(storage,25.,5.,prepared,roots,lai=3.)
    for key in expected:
        np.testing.assert_array_equal(actual[key],expected[key])
    for values in vars(prepared).values():
        assert not values.flags.writeable
        with pytest.raises(ValueError,match='read-only'):
            values.flat[0]=0.


def test_engine_prepares_hydraulics_once_per_simulation(monkeypatch):
    water=component('water_balance')
    prepare=water.prepare_profile
    snapshots=[]

    def counted(profile):
        if not isinstance(profile,water.HydraulicProfile):
            snapshots.append(profile)
        return prepare(profile)

    monkeypatch.setattr(water,'prepare_profile',counted)
    result=simulate({'models':['water_balance'],'weather_source':'demo_weather','days':30})
    assert len(snapshots)==1
    assert len(result)==30
    assert result.water_residual_mm.abs().max()<1e-9


def test_direct_darcy_api_accepts_minimal_dataframe_without_air_entry():
    water=component('water_balance')
    profile=make_profile([0,20,40])
    saturation=np.array([.4,.7])
    expected=water.darcy_flux(saturation,profile)
    minimal=profile[['top_cm','bottom_cm','ksat_mm_day','b']].copy()
    actual=water.darcy_flux(saturation,minimal)
    np.testing.assert_array_equal(actual[0],expected[0])
    assert actual[1]==expected[1]


def test_shared_canopy_coefficient_and_water_stress_link_selected_models():
    for name in ('water_balance','vegetation'):
        component(name)
    from parameters import default_settings
    forcing=demo_weather(30)
    low=default_settings();low['vegetation']['light_extinction']=.2
    high=default_settings();high['vegetation']['light_extinction']=1.
    config={'models':['water_balance','vegetation'],'weather_source':'demo_weather'}
    a=simulate(config,low,forcing=forcing)
    b=simulate(config,high,forcing=forcing)
    assert b.transpiration_mm.sum()>a.transpiration_mm.sum()
    assert b.plant_uptake_gC_m2_day.sum()!=pytest.approx(a.plant_uptake_gC_m2_day.sum())
    assert abs(b.water_residual_mm).max()<1e-9


def test_fao56_day_length_and_editable_plant_parameters():
    plants=component('vegetation')
    from parameters import plant_parameters
    p=plant_parameters()
    result=plants.step_pools(np.array([100.,100.,200.]),20.,1.,80,0.,p)
    assert result['daylength_hours']==pytest.approx(12.)
    warm=plants.step_pools(np.array([100.,100.,200.]),20.,1.,180,40.,p)
    assert warm['daylength_hours']>14
    shifted=plant_parameters(overrides={'t_base':-5.,'t_opt':15.,'t_max':35.})
    assert shifted['t_base']==-5
    with pytest.raises(ValueError,match='base < optimum'):
        plant_parameters(overrides={'t_base':25.,'t_opt':20.})


def test_initial_and_layer_override_parameters_reach_actual_engine():
    selected=[n for n in ('water_balance','vegetation','soil_carbon') if find_spec('models.'+n)]
    from parameters import default_settings
    params=default_settings();params['initial'].update(leaf_carbon=50.,stem_carbon=70.,root_carbon=80.,fallback_soc=9000.)
    profile=make_profile();overrides=profile[['top_cm','bottom_cm','porosity','field_capacity','wilting_point','ksat_mm_day','b','air_entry_mm']].copy()
    overrides['ksat_mm_day']=10.
    result=simulate({'models':selected,'weather_source':'demo_weather','days':2,'soil_overrides':overrides.to_dict('records')},params)
    if 'vegetation' in selected:
        assert result.attrs['initial_plant_carbon_gC_m2']==200.
    if 'soil_carbon' in selected:
        assert result.attrs['initial_soil_carbon_gC_m2']==pytest.approx(9000.)
    assert result.attrs['soil_mapping']['manual_hydraulic_overrides']
    assert all(row['ksat_mm_day']==10 for row in result.attrs['soil_profile'])
