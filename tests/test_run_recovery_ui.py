"""Missing-data choices are part of Run, before the scientific calculation."""
import io,os,subprocess,sys
from zipfile import ZipFile
from studio_builder.generator import make_project_zip


def extracted_check(tmp_path,script,models=('water_balance',)):
    with ZipFile(io.BytesIO(make_project_zip({'project_name':'Run choices','models':list(models),'initial_run_days':31}))) as archive:
        archive.extractall(tmp_path)
    project=next(tmp_path.iterdir())
    result=subprocess.run([sys.executable,'-c',script],cwd=project,
        env={**os.environ,'PYTHONPATH':str(project)},capture_output=True,text=True,timeout=60)
    assert result.returncode==0,result.stdout+result.stderr


SETUP='''
from unittest.mock import patch
from collections import Counter
from datetime import date
import pandas as pd
from streamlit.testing.v1 import AppTest
from data_blocks import DataBlock,NoMatchingData
import engine
calls=Counter();calculations=[]
original_simulate=engine.simulate
def calculate(config,*args,**kwargs):
    calculations.append(config)
    assert config['latitude']==30. and config['longitude']==-96.
    return original_simulate(config,*args,**kwargs)
def soil_block(key):
    return DataBlock(key,pd.DataFrame([dict(top_cm=0,bottom_cm=200,sand_pct=40,silt_pct=35,
        clay_pct=25,soc_g_kg=10,bulk_density_g_cm3=1.3,coarse_fragments_pct=10)]),'static',{}, {})
def fixture(key,request):
    calls[(key,request.latitude)]+=1
    if key=='open_meteo':
        days=pd.date_range(request.start,request.end)
        return DataBlock(key,pd.DataFrame({'date':days.strftime('%Y-%m-%d'),'precip_mm':3.,'et0_mm':2.,'temp_c':20.}),'daily',{}, {})
    if key=='soilgrids_wcs' and request.latitude==30.01:
        return soil_block(key)
    if key=='terraclimate':
        return DataBlock(key,pd.DataFrame({'date':['2023-01-01'],'period_end':['2023-01-31'],'aet_mm':[30.]}),'monthly',{}, {})
    raise NoMatchingData('No data at this location')
candidate={'latitude':30.01,'longitude':-96.,'distance_km':1.112,'records':6}
def nearby(*args,**kwargs):
    calls['nearby']+=1
    return [candidate]
'''


def test_run_searches_only_on_request_then_offers_proxies_and_resumes(tmp_path):
    extracted_check(tmp_path,SETUP+'''
with patch('data_blocks.load_block',fixture),patch('nearby_data.nearby_data_options',nearby),patch('engine.simulate',calculate),patch('weather.available_historical_period',return_value=(date(1941,1,1),date(2023,1,31))):
    app=AppTest.from_file('app.py',default_timeout=30).run()
    app.number_input(key='latitude').set_value(30.).run()
    app.number_input(key='longitude').set_value(-96.).run()
    dates=app.date_input(key='period').value
    app.button(key='run_model').click().run()
    assert not app.exception,[e.value for e in app.exception]
    assert app.session_state['simulation'] is None and not calculations
    assert app.session_state['run_recovery']
    assert app.selectbox(key='run_missing_source').value=='soilgrids_wcs'
    assert calls['nearby']==0,'Opening recovery must not trigger an expensive nearby search'
    app.button(key='run_find_nearby').click().run()
    assert app.selectbox(key='run_proxy_choice_soilgrids_wcs').options
    assert calls['nearby']==1
    app.run()
    assert calls['nearby']==1 and not calculations
    app.button(key='run_use_proxy').click().run()
    assert not app.exception,[e.value for e in app.exception]
    assert len(calculations)==1 and app.session_state['simulation'] is not None
    assert not app.session_state.get('run_recovery')
    assert app.number_input(key='latitude').value==30.
    assert app.date_input(key='period').value==dates
    proxy=app.session_state['run_provenance']['sources']['soilgrids_wcs']['spatial_proxy']
    assert proxy['simulation_site']['latitude']==30. and proxy['source_site']['latitude']==30.01
    assert calls[('open_meteo',30.)]==1 and calls[('terraclimate',30.)]==0,'Resuming must reuse weather and defer optional comparisons'
    assert app.session_state['simulation'].water_residual_mm.abs().max()<1e-8
''')


def test_cancel_does_not_compute_or_choose_a_proxy(tmp_path):
    extracted_check(tmp_path,SETUP+'''
with patch('data_blocks.load_block',fixture),patch('nearby_data.nearby_data_options',nearby),patch('engine.simulate',calculate),patch('weather.available_historical_period',return_value=(date(1941,1,1),date(2023,1,31))):
    app=AppTest.from_file('app.py',default_timeout=30).run()
    app.number_input(key='latitude').set_value(30.).run()
    app.number_input(key='longitude').set_value(-96.).run()
    app.button(key='run_model').click().run()
    app.button(key='cancel_run').click().run()
    assert not app.exception,[e.value for e in app.exception]
    assert not calculations and app.session_state['simulation'] is None
    assert not app.session_state.get('run_recovery') and not app.session_state['source_proxies']
    assert any('Run cancelled' in e.value for e in app.info)
''')


def test_explicit_continue_uses_available_inputs_without_repeating_failed_requests(tmp_path):
    extracted_check(tmp_path,SETUP+'''
with patch('data_blocks.load_block',fixture),patch('nearby_data.nearby_data_options',nearby),patch('engine.simulate',calculate),patch('weather.available_historical_period',return_value=(date(1941,1,1),date(2023,1,31))):
    app=AppTest.from_file('app.py',default_timeout=30).run()
    app.number_input(key='latitude').set_value(30.).run()
    app.number_input(key='longitude').set_value(-96.).run()
    app.button(key='run_model').click().run()
    assert not calculations
    assert not any(button.key=='continue_run' for button in app.button),'Assumed soil must require its own explicit action'
    app.button(key='use_assumed_soil').click().run()
    assert not app.exception,[e.value for e in app.exception]
    assert len(calculations)==1 and app.session_state['simulation'] is not None
    assert app.session_state['run_provenance']['model']['soil_mapping']['dataset']=='explicit texture preset fallback'
    assert app.session_state['run_assumed_soil']
    assert app.session_state['run_provenance']['run_data_choices']['assumed_soil_explicitly_accepted'] is True
    assert set(app.session_state['run_provenance']['run_data_choices']['accepted_missing_sources'])=={'soilgrids_wcs','hwsd'}
    assert calls[('soilgrids_wcs',30.)]==1 and calls[('hwsd',30.)]==1
''')


def test_unavailable_weather_blocks_continue_and_retry_resumes_checks(tmp_path):
    extracted_check(tmp_path,SETUP+'''
def transient(key,request):
    if key=='open_meteo' and calls['weather_fail']==0:
        calls['weather_fail']+=1
        raise NoMatchingData('Weather unavailable')
    if key=='soilgrids_wcs':
        return soil_block(key)
    return fixture(key,request)
with patch('data_blocks.load_block',transient),patch('nearby_data.nearby_data_options',return_value=[]),patch('engine.simulate',calculate),patch('weather.available_historical_period',return_value=(date(1941,1,1),date(2023,1,31))):
    app=AppTest.from_file('app.py',default_timeout=30).run()
    app.number_input(key='latitude').set_value(30.).run()
    app.number_input(key='longitude').set_value(-96.).run()
    app.button(key='run_model').click().run()
    assert not app.exception,[e.value for e in app.exception]
    assert not any(button.key=='continue_run' for button in app.button) and not calculations
    app.button(key='retry_run_source').click().run()
    assert not app.exception,[e.value for e in app.exception]
    assert len(calculations)==1 and app.session_state['simulation'] is not None
''')


def test_proxy_that_becomes_unavailable_pauses_again_instead_of_silently_using_preset(tmp_path):
    extracted_check(tmp_path,SETUP+'''
def unavailable_proxy(key,request):
    if key=='soilgrids_wcs' and request.latitude==30.01:
        raise NoMatchingData('The previously checked proxy is now unavailable')
    return fixture(key,request)
with patch('data_blocks.load_block',unavailable_proxy),patch('nearby_data.nearby_data_options',nearby),patch('engine.simulate',calculate),patch('weather.available_historical_period',return_value=(date(1941,1,1),date(2023,1,31))):
    app=AppTest.from_file('app.py',default_timeout=30).run()
    app.number_input(key='latitude').set_value(30.).run()
    app.number_input(key='longitude').set_value(-96.).run()
    app.button(key='run_model').click().run()
    app.button(key='run_find_nearby').click().run()
    app.button(key='run_use_proxy').click().run()
    assert not app.exception,[e.value for e in app.exception]
    assert not calculations and app.session_state['simulation'] is None
    assert app.session_state['run_recovery'] and 'soilgrids_wcs' in app.session_state['run_recovery']['missing']
''')


def test_historical_comparisons_load_after_model_and_study_dates_remain_explicit(tmp_path):
    extracted_check(tmp_path,SETUP+'''
study={'Record_number':303,'Study_number':1,'Study_midyear':2020.5,'Latitude':31.,'Longitude':-96.,
    'Rh_annual':500.,'distance_km':111.,'period_start':'2020-01-01','period_end':'2020-12-31','comparable_rh':True}
def carbon_sources(key,request):
    if key=='soilgrids_wcs':
        return soil_block(key)
    if key=='srdb':
        if request.latitude==study['Latitude']:
            return DataBlock(key,pd.DataFrame([study]),'annual',{}, {})
        raise NoMatchingData('No study at the requested site and dates',{'nearest_qualified_studies':[study]})
    return fixture(key,request)
with patch('data_blocks.load_block',carbon_sources),patch('engine.simulate',calculate),patch('weather.available_historical_period',return_value=(date(1941,1,1),date(2023,1,31))):
    app=AppTest.from_file('app.py',default_timeout=30).run()
    app.number_input(key='latitude').set_value(30.).run()
    app.number_input(key='longitude').set_value(-96.).run()
    dates=app.date_input(key='period').value
    app.button(key='run_model').click().run()
    assert not app.exception,[e.value for e in app.exception]
    assert len(calculations)==1 and app.date_input(key='period').value==dates
    assert not calls[('srdb',30.)],'Historical comparisons must not block the core model'
    app.button(key='load_comparisons').click().run()
    assert not app.exception,[e.value for e in app.exception]
    assert len(calculations)==1 and app.session_state['simulation'] is not None
    assert app.date_input(key='period').value==dates
    app.selectbox(key='block_choice').set_value('srdb').run()
    app.button(key='use_study').click().run()
    app.button(key='use_source_dates').click().run()
    assert app.date_input(key='period').value==(date(2020,1,1),date(2020,12,31))
    app.button(key='run_model').click().run()
    app.button(key='load_comparisons').click().run()
    assert not app.exception,[e.value for e in app.exception]
    assert app.number_input(key='latitude').value==30.
    assert len(app.session_state['comparisons'])==1
    assert 'proxy' in app.session_state['comparisons'].evidence.iloc[0]
''',models=('soil_carbon',))


def test_source_preference_changes_order_but_reuses_valid_profiles_and_weather(tmp_path):
    extracted_check(tmp_path,SETUP+'''
def both_sources(key,request):
    if key in ('soilgrids_wcs','hwsd'):
        calls[(key,request.latitude)]+=1
        assert request.options.get('soilgrids_use_rest',True) is True
        return soil_block(key)
    return fixture(key,request)
with patch('data_blocks.load_block',both_sources),patch('engine.simulate',calculate),patch('weather.available_historical_period',return_value=(date(1941,1,1),date(2023,1,31))):
    app=AppTest.from_file('app.py',default_timeout=30).run()
    app.number_input(key='latitude').set_value(30.).run()
    app.number_input(key='longitude').set_value(-96.).run()
    app.segmented_control(key='soil_source_preference').set_value('hwsd').run()
    app.button(key='run_model').click().run()
    assert not app.exception,[e.value for e in app.exception]
    assert app.session_state['simulation'].attrs['soil_mapping']['dataset']=='hwsd'
    assert calls[('hwsd',30.)]==1 and calls[('soilgrids_wcs',30.)]==0
    assert app.session_state['run_provenance']['soil_attempts'][1]['status']=='not_needed'
    app.segmented_control(key='soil_source_preference').set_value('soilgrids').run()
    assert app.session_state['simulation'] is None,'Changing preferred source must invalidate previous results'
    app.button(key='run_model').click().run()
    assert not app.exception,[e.value for e in app.exception]
    assert app.session_state['simulation'].attrs['soil_mapping']['dataset']=='soilgrids_wcs'
    assert calls[('soilgrids_wcs',30.)]==1
    assert calls[('open_meteo',30.)]==1,'A source priority choice must not refetch unchanged weather'
    app.segmented_control(key='soil_source_preference').set_value('hwsd').run()
    app.button(key='run_model').click().run()
    assert not app.exception,[e.value for e in app.exception]
    assert calls[('hwsd',30.)]==1,'Previously checked profiles must remain reusable after changing preference'
    assert len(calculations)==3
''')


def test_missing_hwsd_map_has_explicit_install_action_before_assumed_soil(tmp_path):
    extracted_check(tmp_path,SETUP+'''
def installation(key,request):
    if key=='hwsd':
        calls[('hwsd',request.latitude)]+=1
        if request.options.get('hwsd_download_map'):
            return soil_block(key)
        raise FileNotFoundError('HWSD map not found locally or in existing downloaded apps')
    return fixture(key,request)
with patch('data_blocks.load_block',installation),patch('engine.simulate',calculate),patch('weather.available_historical_period',return_value=(date(1941,1,1),date(2023,1,31))):
    app=AppTest.from_file('app.py',default_timeout=30).run()
    app.number_input(key='latitude').set_value(30.).run()
    app.number_input(key='longitude').set_value(-96.).run()
    app.segmented_control(key='soil_source_preference').set_value('hwsd').run()
    app.button(key='run_model').click().run()
    assert not app.exception,[e.value for e in app.exception]
    assert not calculations
    assert app.button(key='use_assumed_soil').label=='Use assumed soil preset'
    assert app.selectbox(key='run_missing_source').value=='hwsd','Recovery should open the preferred failed source first'
    assert any('1.9 GB' in item.value for item in app.caption)
    app.button(key='run_download_hwsd').click().run()
    assert not app.exception,[e.value for e in app.exception]
    assert app.toggle(key='hwsd_download_map').value is True
    assert len(calculations)==1
    assert app.session_state['simulation'].attrs['soil_mapping']['dataset']=='hwsd'
    assert app.session_state['run_provenance']['run_data_choices']['assumed_soil_explicitly_accepted'] is False
''')


def test_recovery_keeps_diagnostics_and_assumptions_collapsed_with_one_primary_action(tmp_path):
    extracted_check(tmp_path,SETUP+'''
technical='Internal WMS GetFeatureInfo endpoint diagnostic'
def unavailable(key,request):
    if key=='soilgrids_wcs':
        raise NoMatchingData(technical,{'attempts':[{'method':'WMS','message':technical}]})
    return fixture(key,request)
with patch('data_blocks.load_block',unavailable),patch('nearby_data.nearby_data_options',nearby),patch('engine.simulate',calculate),patch('weather.available_historical_period',return_value=(date(1941,1,1),date(2023,1,31))):
    app=AppTest.from_file('app.py',default_timeout=30).run()
    app.number_input(key='latitude').set_value(30.).run()
    app.number_input(key='longitude').set_value(-96.).run()
    app.button(key='run_model').click().run()
    assert not app.exception,[e.value for e in app.exception]
    dialog=app.get('dialog')[0]
    assert [button.key for button in dialog.button if button.proto.type=='primary']==['run_find_nearby']
    assert not dialog.info,'The compact recovery flow should not stack informational alerts'
    assert not any(technical in item.value for item in dialog.caption)
    details=next(item for item in dialog.expander if item.label=='Source details')
    assumptions=next(item for item in dialog.expander if item.label=='Use assumed soil instead')
    assert not details.proto.expanded and not assumptions.proto.expanded
    assert assumptions.button(key='use_assumed_soil').proto.type=='secondary'
    assert assumptions.warning and 'not measurements' in assumptions.warning[0].value
    app.button(key='run_find_nearby').click().run()
    assert not app.exception,[e.value for e in app.exception]
    dialog=app.get('dialog')[0]
    assert [button.key for button in dialog.button if button.proto.type=='primary']==['run_use_proxy']
    assert not any(button.key=='run_find_nearby' for button in dialog.button),'Completed search should advance to candidate selection'
    assert not calculations
''')


def test_recovery_nearby_search_can_retry_after_a_failed_search(tmp_path):
    extracted_check(tmp_path,SETUP+'''
def retry_nearby(*args,**kwargs):
    calls['nearby']+=1
    if calls['nearby']==1:
        raise NoMatchingData('No usable nearby data were found')
    return [candidate]
with patch('data_blocks.load_block',fixture),patch('nearby_data.nearby_data_options',retry_nearby),patch('engine.simulate',calculate),patch('weather.available_historical_period',return_value=(date(1941,1,1),date(2023,1,31))):
    app=AppTest.from_file('app.py',default_timeout=30).run()
    app.number_input(key='latitude').set_value(30.).run()
    app.number_input(key='longitude').set_value(-96.).run()
    app.button(key='run_model').click().run()
    app.button(key='run_find_nearby').click().run()
    assert not app.exception,[e.value for e in app.exception]
    assert calls['nearby']==1 and not calculations
    assert app.button(key='run_find_nearby').label=='Find nearby data'
    app.button(key='run_find_nearby').click().run()
    assert not app.exception,[e.value for e in app.exception]
    assert calls['nearby']==2 and app.selectbox(key='run_proxy_choice_soilgrids_wcs').options
    app.button(key='run_use_proxy').click().run()
    assert not app.exception,[e.value for e in app.exception]
    assert len(calculations)==1
    assert app.session_state['simulation'].attrs['soil_mapping']['dataset']=='soilgrids_wcs'
''')


def test_temporal_weather_gap_does_not_offer_a_spatial_search_or_assumed_soil(tmp_path):
    extracted_check(tmp_path,SETUP+'''
def unavailable_dates(key,request):
    if key=='open_meteo':
        raise NoMatchingData('No complete weather window',{'reason':'no_complete_time_window'})
    return fixture(key,request)
with patch('data_blocks.load_block',unavailable_dates),patch('nearby_data.nearby_data_options',nearby),patch('engine.simulate',calculate),patch('weather.available_historical_period',return_value=(date(1941,1,1),date(2023,1,31))):
    app=AppTest.from_file('app.py',default_timeout=30).run()
    app.number_input(key='latitude').set_value(30.).run()
    app.number_input(key='longitude').set_value(-96.).run()
    app.button(key='run_model').click().run()
    assert not app.exception,[e.value for e in app.exception]
    dialog=app.get('dialog')[0]
    assert [button.key for button in dialog.button if button.proto.type=='primary']==['retry_run_source']
    assert not any(button.key in ('run_find_nearby','continue_run','use_assumed_soil') for button in dialog.button)
    assert not calculations and not calls['nearby']
''')


def test_optional_trait_failure_can_continue_with_real_soil(tmp_path):
    extracted_check(tmp_path,SETUP+'''
def optional_traits(key,request):
    if key=='soilgrids_wcs':
        return soil_block(key)
    if key=='try_categorical':
        raise ValueError('Trait archive unavailable in this fixture')
    return fixture(key,request)
with patch('data_blocks.load_block',optional_traits),patch('engine.simulate',calculate),patch('weather.available_historical_period',return_value=(date(1941,1,1),date(2023,1,31))):
    app=AppTest.from_file('app.py',default_timeout=30).run()
    app.number_input(key='latitude').set_value(30.).run()
    app.number_input(key='longitude').set_value(-96.).run()
    app.button(key='run_model').click().run()
    assert not app.exception,[e.value for e in app.exception]
    dialog=app.get('dialog')[0]
    assert [button.key for button in dialog.button if button.proto.type=='primary']==['continue_run']
    assert not any(button.key=='use_assumed_soil' for button in dialog.button)
    app.button(key='continue_run').click().run()
    assert not app.exception,[e.value for e in app.exception]
    assert len(calculations)==1
    assert app.session_state['simulation'].attrs['soil_mapping']['dataset']=='soilgrids_wcs'
    assert app.session_state['run_provenance']['run_data_choices']['assumed_soil_explicitly_accepted'] is False
''',models=('water_balance','vegetation'))


def test_rest_timeout_with_map_data_gap_still_offers_nearby_soil(tmp_path):
    extracted_check(tmp_path,SETUP+'''
import requests
from data_blocks import DataSourceError
state={'temporal_gap':False}
def mixed_failure(key,request):
    if key=='soilgrids_wcs' and request.latitude!=30.01:
        details={'attempts':[
            {'method':'SoilGrids REST API: mean predictions','exception':'Timeout','message':'REST timed out'},
            {'method':'WMS GetFeatureInfo: numeric values','exception':'NoMatchingData','message':'No mapped sand'},
            {'method':'WCS 1.0 geographic subset','exception':'NoMatchingData','message':'No mapped sand'}]}
        if state['temporal_gap']:
            details['reason']='no_complete_time_window'
        raise DataSourceError('SoilGrids did not return a usable profile',details) from requests.Timeout('REST timed out')
    return fixture(key,request)
with patch('data_blocks.load_block',mixed_failure),patch('nearby_data.nearby_data_options',nearby),patch('engine.simulate',calculate),patch('weather.available_historical_period',return_value=(date(1941,1,1),date(2023,1,31))):
    app=AppTest.from_file('app.py',default_timeout=30).run()
    app.number_input(key='latitude').set_value(30.).run()
    app.number_input(key='longitude').set_value(-96.).run()
    app.button(key='run_model').click().run()
    assert not app.exception,[e.value for e in app.exception]
    assert app.session_state['run_recovery']['missing']['soilgrids_wcs']['status']=='Connection failed'
    dialog=app.get('dialog')[0]
    assert any('SoilGrids: no usable profile at this site' in item.value for item in dialog.caption)
    assert [button.key for button in dialog.button if button.proto.type=='primary']==['run_find_nearby']
    app.button(key='run_find_nearby').click().run()
    app.button(key='run_use_proxy').click().run()
    assert not app.exception,[e.value for e in app.exception]
    assert len(calculations)==1
    assert app.number_input(key='latitude').value==30.
    assert app.session_state['run_provenance']['sources']['soilgrids_wcs']['spatial_proxy']['source_site']['latitude']==30.01

    state['temporal_gap']=True
    app.number_input(key='latitude').set_value(30.1).run()
    app.button(key='run_model').click().run()
    assert not app.exception,[e.value for e in app.exception]
    dialog=app.get('dialog')[0]
    assert not any(button.key=='run_find_nearby' for button in dialog.button),'Spatial recovery cannot repair an explicit time-window failure'
    assert any('no data for these dates' in item.value for item in dialog.caption)
    assert len(calculations)==1
''')


def test_all_soilgrids_route_timeouts_offer_retry_instead_of_nearby_search(tmp_path):
    extracted_check(tmp_path,SETUP+'''
import requests
from data_blocks import DataSourceError
def service_outage(key,request):
    if key=='soilgrids_wcs':
        details={'attempts':[
            {'method':'SoilGrids REST API: mean predictions','exception':'Timeout','message':'REST timed out'},
            {'method':'WMS GetFeatureInfo: numeric values','exception':'Timeout','message':'WMS timed out'},
            {'method':'WCS 1.0 geographic subset','exception':'ConnectionError','message':'WCS unavailable'}]}
        raise DataSourceError('SoilGrids services could not be reached',details) from requests.Timeout('REST timed out')
    return fixture(key,request)
with patch('data_blocks.load_block',service_outage),patch('nearby_data.nearby_data_options',nearby),patch('engine.simulate',calculate),patch('weather.available_historical_period',return_value=(date(1941,1,1),date(2023,1,31))):
    app=AppTest.from_file('app.py',default_timeout=30).run()
    app.number_input(key='latitude').set_value(30.).run()
    app.number_input(key='longitude').set_value(-96.).run()
    app.button(key='run_model').click().run()
    assert not app.exception,[e.value for e in app.exception]
    dialog=app.get('dialog')[0]
    assert [button.key for button in dialog.button if button.proto.type=='primary']==['retry_run_source']
    assert not any(button.key=='run_find_nearby' for button in dialog.button)
    assert any('SoilGrids: service unavailable' in item.value for item in dialog.caption)
    assert not calculations and not calls['nearby']
''')
