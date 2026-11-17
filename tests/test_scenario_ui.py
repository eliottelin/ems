"""Climate scenarios require mapped soil or an explicitly accepted assumption."""
import io
import os
from pathlib import Path
import subprocess
import sys
from zipfile import ZipFile

import pytest

from studio_builder.generator import make_project_zip


def extracted_check(tmp_path, script):
    config={'project_name':'Scenario soil UI','models':['water_balance','vegetation','soil_carbon'],
        'initial_run_days':31,'additional_datasets':['open_meteo_climate']}
    with ZipFile(io.BytesIO(make_project_zip(config))) as archive:
        archive.extractall(tmp_path)
    project=next(tmp_path.iterdir())
    result=subprocess.run([sys.executable,'-c',script],cwd=project,
        env={**os.environ,'PYTHONPATH':str(project)},capture_output=True,text=True,timeout=60)
    assert result.returncode==0,result.stdout+result.stderr


SETUP='''
from collections import Counter
from datetime import date
import json
from unittest.mock import patch
import pandas as pd
import streamlit as st
from streamlit.testing.v1 import AppTest
from data_blocks import DataBlock,NoMatchingData
calls=Counter()
def fixture(key,request):
    calls[key]+=1
    if key in ('open_meteo','open_meteo_climate'):
        days=pd.date_range(request.start,request.end)
        return DataBlock(key,pd.DataFrame({'date':days.strftime('%Y-%m-%d'),
            'precip_mm':3.,'et0_mm':2.,'temp_c':20.}),'daily',{},
            {'fixture':True,'source':key,'scenario':key=='open_meteo_climate'})
    if key in ('soilgrids_wcs','hwsd'):
        return DataBlock(key,pd.DataFrame([dict(top_cm=0,bottom_cm=200,sand_pct=40.,
            silt_pct=35.,clay_pct=25.,soc_g_kg=10.,bulk_density_g_cm3=1.3,
            coarse_fragments_pct=10.)]),'static profile',{}, {'fixture':True,'source':key})
    if key=='try_categorical':
        return DataBlock(key,pd.DataFrame({'AccSpeciesID':[1],'AccSpeciesName':['Fixture grass'],
            'pft':['grass_c3']}),'species categories',{}, {'fixture':True})
    raise NoMatchingData('No comparison observations in this fixture')
'''


@pytest.mark.parametrize('source',['soilgrids_wcs','hwsd'])
def test_scenario_blocks_implicit_soil_then_uses_independently_loaded_profile(tmp_path, source):
    extracted_check(tmp_path,SETUP+f'''
with patch('data_blocks.load_block',fixture),patch('weather.available_historical_period',
        return_value=(date(1941,1,1),date(2023,12,31))),patch('streamlit.download_button',wraps=st.download_button) as downloads:
    app=AppTest.from_file('app.py',default_timeout=30).run()
    app.number_input(key='latitude').set_value(30.).run()
    app.number_input(key='longitude').set_value(-96.).run()
    app.selectbox(key='block_choice').set_value('open_meteo_climate').run()
    app.button(key='load_data_block').click().run()
    assert not app.exception,[e.value for e in app.exception]
    assert app.button(key='run_scenario').disabled,'Climate forcing alone must not silently use a soil preset'
    assert app.session_state.get('scenario') is None
    assert not app.session_state.get('run_assumed_soil',False)

    app.selectbox(key='block_choice').set_value({source!r}).run()
    app.button(key='load_data_block').click().run()
    app.selectbox(key='block_choice').set_value('open_meteo_climate').run()
    assert not app.exception,[e.value for e in app.exception]
    assert not app.button(key='run_scenario').disabled
    app.button(key='run_scenario').click().run()
    assert not app.exception,[e.value for e in app.exception]
    assert not app.error,[e.value for e in app.error]
    scenario=app.session_state['scenario']
    assert scenario is not None and len(scenario)==365
    assert scenario.attrs['soil_mapping']['dataset']=={source!r}
    assert scenario.attrs['soil_provenance']['source']=={source!r}
    assert scenario.attrs['forcing_provenance']['source']=='open_meteo_climate'
    assert scenario.attrs['assumed_soil_explicitly_accepted'] is False
    assert scenario.water_residual_mm.abs().max()<1e-8
    assert any(item.proto.label=='Download scenario details' for item in app.get('download_button'))
    provenance_call=next(call for call in reversed(downloads.call_args_list) if call.kwargs.get('file_name')=='scenario_provenance.json')
    assert provenance_call.kwargs['mime']=='application/json'
    payload=json.loads(provenance_call.kwargs['data'] if 'data' in provenance_call.kwargs else provenance_call.args[1])
    assert payload['soil_mapping']['dataset']=={source!r}
    assert payload['soil_provenance']['source']=={source!r}
    assert payload['forcing_provenance']['source']=='open_meteo_climate'
    assert payload['assumed_soil_explicitly_accepted'] is False

    app.number_input(key='latitude').set_value(30.2).run()
    assert app.session_state.get('scenario') is None,'Changing site must discard the old scenario'
    app.button(key='load_data_block').click().run()
    assert app.button(key='run_scenario').disabled,'The previous site profile must not unlock the new site'
''')


def test_scenario_uses_assumed_soil_only_after_main_run_acceptance(tmp_path):
    extracted_check(tmp_path,SETUP+'''
def missing_soil(key,request):
    if key in ('soilgrids_wcs','hwsd'):
        raise NoMatchingData('No mapped soil in this fixture')
    return fixture(key,request)
with patch('data_blocks.load_block',missing_soil),patch('weather.available_historical_period',
        return_value=(date(1941,1,1),date(2023,12,31))):
    app=AppTest.from_file('app.py',default_timeout=30).run()
    app.number_input(key='latitude').set_value(30.).run()
    app.number_input(key='longitude').set_value(-96.).run()
    app.selectbox(key='block_choice').set_value('open_meteo_climate').run()
    app.button(key='load_data_block').click().run()
    assert app.button(key='run_scenario').disabled
    app.button(key='run_model').click().run()
    assert not app.exception,[e.value for e in app.exception]
    assert app.session_state['simulation'] is None
    assert not app.session_state.get('run_assumed_soil',False)
    assert not any(button.key=='run_scenario' and not button.disabled for button in app.button)

    app.button(key='use_assumed_soil').click().run()
    assert not app.exception,[e.value for e in app.exception]
    assert app.session_state['run_assumed_soil'] is True
    assert app.session_state['simulation'] is not None
    app.button(key='load_data_block').click().run()
    assert not app.button(key='run_scenario').disabled
    app.button(key='run_scenario').click().run()
    assert not app.exception,[e.value for e in app.exception]
    scenario=app.session_state['scenario']
    assert scenario is not None
    assert scenario.attrs['soil_mapping']['dataset']=='explicit texture preset fallback'
    assert scenario.attrs['assumed_soil_explicitly_accepted'] is True
    assert 'soil_provenance' not in scenario.attrs,'Assumed soil must not claim mapped provider provenance'

    app.selectbox(key='soil_texture').set_value('sand').run()
    assert app.session_state.get('scenario') is None
    assert app.session_state['run_assumed_soil'] is False,'Changed assumptions require a new explicit acceptance'
    app.button(key='load_data_block').click().run()
    assert app.button(key='run_scenario').disabled
''')
