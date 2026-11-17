"""User-facing builder behavior and independent exported-app execution."""
import io
import os
from pathlib import Path
import subprocess
import sys
from zipfile import ZipFile

from streamlit.testing.v1 import AppTest
from studio_builder.generator import make_project_zip


def test_builder_recovers_from_helpers_cached_before_upgrade():
    script = '''
from pathlib import Path
from streamlit.testing.v1 import AppTest
import studio_builder.registry as registry
import studio_builder.generator as generator
import studio_builder.onboarding as onboarding
import studio_builder.bootstrap as bootstrap
import template_generated_app.data_blocks.traits as template_traits
del template_traits.species_index
bootstrap.REFRESH_SCHEMA = 1

# Reproduce a live Streamlit process retaining the pre-upgrade module API.
del registry.resolve_data_blocks
del registry.optional_data_blocks
generator.MODELS = {'legacy_model': {}}
generator.DATASETS = {'legacy_dataset': {}}
onboarding.getting_started_markdown = lambda: '# Older setup guide'
generator.getting_started_markdown = onboarding.getting_started_markdown
del onboarding.AGENT_HELP
del onboarding.AGENT_PROMPT

app = AppTest.from_file(Path('builder_app.py').resolve(), default_timeout=30).run()
assert not app.exception, [item.message for item in app.exception]
assert callable(registry.resolve_data_blocks)
assert callable(template_traits.species_index)
assert generator.MODELS is registry.MODELS
assert generator.DATASETS is registry.DATASETS
assert generator.getting_started_markdown is onboarding.getting_started_markdown
assert onboarding.AGENT_HELP and onboarding.AGENT_PROMPT
assert any('Read AGENT_BRIEF.md' in item.value for item in app.code)
assert len(app.get('download_button')) == 1
app.get_by_key('models').set_value(['water_balance']).run()
assert not app.exception, [item.message for item in app.exception]
assert len(app.get('download_button')) == 1
'''
    completed = subprocess.run([sys.executable, '-c', script],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=60)
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_export_revision_tracks_helper_and_template_changes_but_ignores_caches(tmp_path):
    from studio_builder.bootstrap import source_revision
    package, template = tmp_path / 'builder', tmp_path / 'template'
    package.mkdir()
    template.mkdir()
    (package / 'onboarding.py').write_text('HELP = "first"\n')
    (template / 'app.py').write_text('print("first")\n')
    first = source_revision(package, template)
    (package / 'onboarding.py').write_text('HELP = "later"\n')
    second = source_revision(package, template)
    assert second != first
    (template / 'app.py').write_text('print("later")\n')
    third = source_revision(package, template)
    assert third != second
    (template / '__pycache__').mkdir()
    (template / '__pycache__/app.pyc').write_bytes(b'compiled cache')
    assert source_revision(package, template) == third
    (template / '.streamlit').mkdir()
    (template / '.streamlit/config.toml').write_text('[server]\nrunOnSave=true\n')
    fourth=source_revision(package, template)
    assert fourth!=third
    (template / '.streamlit/secrets.toml').write_text('test_only="ignored"\n')
    assert source_revision(package, template)==fourth


def test_builder_defaults_are_model_first_and_optional_data_is_contextual():
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / 'builder_app.py', default_timeout=30).run()
    assert not app.exception
    assert app.get_by_key('models').value == ['water_balance','vegetation','soil_carbon']
    assert not app.radio and not app.date_input
    assert not app.number_input
    assert [item.label for item in app.text_input] == ['Project name']
    assert [item.label for item in app.toggle] == ['Include Future climate']
    assert len(app.get('download_button')) == 1
    assert app.expander[-1].label == 'After downloading: setup and run'
    assert any('pip install -r requirements.txt' in item.value for item in app.code)
    assert any('python.exe -m streamlit run app.py' in item.value for item in app.code)
    assert any('Read AGENT_BRIEF.md' in item.value for item in app.code)
    assert not any('Model updates:' in item.value for item in app.caption)
    app.get_by_key('models').set_value(['water_balance']).run()
    assert not app.exception
    assert {item.key for item in app.toggle} == {'extra_try_categorical','extra_srdb','extra_open_meteo_climate'}
    app.toggle(key='extra_open_meteo_climate').set_value(True).run()
    assert not app.exception
    app.get_by_key('models').set_value([]).run()
    assert not app.exception
    assert not app.get('download_button')
    assert app.button[0].disabled


def test_exported_app_uses_runtime_site_and_dates_and_retains_results(tmp_path):
    config = {'project_name':'Standalone UI test','models':['water_balance','vegetation','soil_carbon'],
              'simulation_steps':30,'request':'Add configurable soil depths.'}
    with ZipFile(io.BytesIO(make_project_zip(config))) as archive:
        archive.extractall(tmp_path)
    project = next(tmp_path.iterdir())
    script = '''
from unittest.mock import patch
import pandas as pd
from streamlit.testing.v1 import AppTest
from data_blocks import DataBlock
from weather import WeatherDataError
from datetime import date,timedelta

place={'id':1,'name':'College Station','admin1':'Texas','country':'United States','latitude':30.,'longitude':-96.}

def fixture(key, request):
    if key != 'open_meteo':
        raise ValueError('No matching source records in test fixture')
    dates=pd.date_range(request.start,request.end)
    frame=pd.DataFrame({'date':dates.strftime('%Y-%m-%d'),'precip_mm':4.,'et0_mm':2.,'temp_c':20.})
    return DataBlock(key,frame,'daily',{}, {'fixture':True})

boundary_end=date.today()-timedelta(days=6)
with patch('data_blocks.load_block',fixture), patch('geocoding.search_places',return_value=[place]), patch('weather.available_historical_period',return_value=(date(1940,1,2),boundary_end)):
    app=AppTest.from_file('app.py',default_timeout=30).run()
    assert not app.exception
    assert app.button(key='run_model').disabled
    app.text_input(key='place_query').set_value('College Station, Texas').run()
    assert not app.exception, [str(item.value) for item in app.exception]
    assert app.selectbox(key='place_result').value is None
    app.selectbox(key='place_result').set_value(1).run()
    assert app.number_input(key='latitude').value==30.
    assert app.number_input(key='longitude').value==-96.
    assert app.date_input(key='period').value[1]==boundary_end
    assert not app.button(key='run_model').disabled
    assert not any(b.key=='check_weather' for b in app.button)
    assert not any(t.key=='full_history' for t in app.toggle)
    assert [t.label for t in app.tabs][:3] == ['Model','Data','Advanced']
    app.date_input(key='period').set_value((date(1900,1,1),date.today())).run()
    assert app.date_input(key='period').value==(date(1940,1,2),boundary_end), (app.date_input(key='period').value, [e.value for e in app.exception])
    app.date_input(key='period').set_value((boundary_end-timedelta(days=29),boundary_end)).run()
    app.button(key='run_model').click().run()
    assert app.session_state['simulation'] is None
    app.button(key='use_assumed_soil' if any(button.key=='use_assumed_soil' for button in app.button) else 'continue_run').click().run()
    assert not app.exception, [str(item.value) for item in app.exception]
    assert not app.error, [item.value for item in app.error]
    assert any('Simulated 30 days with daily updates' in item.value for item in app.success)
    assert not app.session_state.get('comparisons_requested')
    assert app.session_state['comparisons'].empty
    app.button(key='load_comparisons').click().run()
    assert not app.exception, [str(item.value) for item in app.exception]
    assert app.session_state['simulation'] is not None
    assert any('No matching complete observation windows' in item.value for item in app.info)
    assert any(item.label=='Diagnostics and data availability' for item in app.expander)
    assert app.session_state['data_errors']
    assert not app.warning
    assert any('Moisture by depth'==item.value for item in app.subheader)
    assert len(app.get('download_button')) >= 2
    app.run()
    assert any('Simulated 30 days with daily updates' in item.value for item in app.success)
    app.number_input(key='latitude').set_value(31.).run()
    assert not app.success, 'A site change must clear stale results'
    assert not app.exception
    failure=WeatherDataError('Missing et0_fao_evapotranspiration on 2023-01-03',{'missing_values':{'et0_fao_evapotranspiration':{'count':1}}})
    with patch('data_blocks.load_block',side_effect=failure):
        app.button(key='run_model').click().run()
    assert not app.exception
    assert any('et0_fao_evapotranspiration' in item.value for item in app.error)
    assert any(item.label=='Retrieval details' for item in app.expander)
'''
    completed = subprocess.run([sys.executable,'-c',script],cwd=project,
        env={**os.environ,'PYTHONPATH':str(project)},capture_output=True,text=True,timeout=60)
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_exported_app_maps_soil_and_species_and_shows_complete_month_comparison(tmp_path):
    with ZipFile(io.BytesIO(make_project_zip({'project_name':'Source mapping UI','models':['water_balance','vegetation','soil_carbon'],'initial_run_days':31}))) as archive:
        archive.extractall(tmp_path)
    project=next(tmp_path.iterdir())
    script='''
from unittest.mock import patch
from datetime import date
import pandas as pd
from streamlit.testing.v1 import AppTest
from data_blocks import DataBlock
from data_blocks.soil import UNITS
place={'id':9,'name':'Vegetated test plot','country':'Test','latitude':30.,'longitude':-96.}
def fixture(key,request):
    if key=='open_meteo':
        days=pd.date_range(request.start,request.end)
        return DataBlock(key,pd.DataFrame({'date':days.strftime('%Y-%m-%d'),'precip_mm':3.,'et0_mm':2.,'temp_c':25.}),'daily',{}, {'fixture':True})
    if key=='soilgrids_wcs':
        # A provider may create local cache assets during first retrieval.
        (request.data_dir/'.source-cache-fixture').write_text('downloaded cache')
        return DataBlock(key,pd.DataFrame([dict(top_cm=0,bottom_cm=200,sand_pct=40,silt_pct=35,clay_pct=25,soc_g_kg=10,bulk_density_g_cm3=1.3,coarse_fragments_pct=10)]),'static profile',UNITS,{'fixture':True})
    if key=='terraclimate':
        return DataBlock(key,pd.DataFrame({'date':['2001-01-01'],'period_end':['2001-01-31'],'aet_mm':[30.],'pet_mm':[60.]}),'monthly',{'aet_mm':'mm/month','pet_mm':'mm/month'},{'fixture':True})
    if key=='try_categorical':
        return DataBlock(key,pd.DataFrame({'AccSpeciesID':[1],'AccSpeciesName':['Test C4 grass'],'pft':['grass_c4']}),'species categories',{}, {'sha256':'fixture'})
    raise ValueError('No observations in fixture')
from advanced import cached_species
cached_species.clear()
with patch('advanced.cached_species',return_value=(pd.DataFrame({'AccSpeciesID':[1],'AccSpeciesName':['Test C4 grass'],'pft':['grass_c4']}),{'source_sha256':'fixture'})),patch('data_blocks.load_block',fixture),patch('geocoding.search_places',return_value=[place]),patch('weather.available_historical_period',return_value=(date(1940,1,2),date(2001,1,31))):
    app=AppTest.from_file('app.py',default_timeout=30).run()
    app.text_input(key='place_query').set_value('Test plot').run()
    app.selectbox(key='place_result').set_value(9).run()
    table=next(table for table in app.dataframe if table.key and table.key.startswith('species_table_'))
    app.session_state[table.key]={'selection':{'rows':[0],'columns':[],'cells':[]}}
    app.run()
    app.button(key='use_selected_plant').click().run()
    assert not app.exception, [e.value for e in app.exception]
    app.button(key='run_model').click().run()
    if app.session_state.get('run_recovery'):
        app.button(key='use_assumed_soil' if any(button.key=='use_assumed_soil' for button in app.button) else 'continue_run').click().run()
    assert not app.exception, [e.value for e in app.exception]
    assert not app.error, [e.value for e in app.error]
    result=app.session_state['simulation']
    assert result.attrs['soil_mapping']['dataset']=='soilgrids_wcs'
    assert result.attrs['trait_selection']['preset']=='grass_c4'
    assert result.attrs['plant_preset']['max_npp']==6.
    assert not app.session_state.get('comparisons_requested')
    assert app.session_state['comparisons'].empty
    app.button(key='load_comparisons').click().run()
    assert not app.exception, [e.value for e in app.exception]
    assert len(app.session_state['comparisons'])==1
    pd.testing.assert_frame_equal(result,app.session_state['simulation'])
    app.run()
    assert app.session_state['simulation'] is not None, 'First-use cache files must not erase a successful run'
    assert not any('No matching complete' in e.value for e in app.info)
    app.selectbox(key='block_choice').set_value('soilgrids_wcs').run()
    assert not app.exception, [e.value for e in app.exception]
    assert any(m.label=='Profile soil carbon' for m in app.metric)
    app.text_input(key='layer_depths').set_value('0, 20, 100, 200').run()
    assert app.session_state['simulation'] is None
    app.button(key='run_model').click().run()
    if app.session_state.get('run_recovery'):
        app.button(key='use_assumed_soil' if any(button.key=='use_assumed_soil' for button in app.button) else 'continue_run').click().run()
    assert not app.exception, [e.value for e in app.exception]
    assert len(app.session_state['simulation'].attrs['soil_profile'])==3
'''
    completed=subprocess.run([sys.executable,'-c',script],cwd=project,env={**os.environ,'PYTHONPATH':str(project)},capture_output=True,text=True,timeout=60)
    assert completed.returncode==0,completed.stdout+completed.stderr


import pytest

@pytest.mark.parametrize('models',[
 ['water_balance'],['vegetation'],['soil_carbon'],
 ['water_balance','vegetation'],['water_balance','soil_carbon'],
 ['vegetation','soil_carbon'],['water_balance','vegetation','soil_carbon']])
def test_all_seven_exported_app_selections_run_with_matching_controls_and_links(tmp_path,models):
    with ZipFile(io.BytesIO(make_project_zip({'project_name':'Selection check','models':models,'initial_run_days':15}))) as archive:
        archive.extractall(tmp_path)
    project=next(tmp_path.iterdir())
    script='''
from unittest.mock import patch
from datetime import date
import json
import pandas as pd
from streamlit.testing.v1 import AppTest
from data_blocks import DataBlock,NoMatchingData
selected=set(json.load(open('model_config.json'))['models'])
def fixture(key,request):
    if key=='open_meteo':
        days=pd.date_range(request.start,request.end)
        return DataBlock(key,pd.DataFrame({'date':days.strftime('%Y-%m-%d'),'precip_mm':4.,'et0_mm':2.,'temp_c':20.}),'daily',{}, {'fixture':True})
    if key=='soilgrids_wcs':
        frame=pd.DataFrame([dict(top_cm=0,bottom_cm=200,sand_pct=40,silt_pct=35,clay_pct=25,soc_g_kg=10,bulk_density_g_cm3=1.3,coarse_fragments_pct=10)])
        return DataBlock(key,frame,'static profile',{}, {'fixture':True})
    if key=='try_categorical':
        return DataBlock(key,pd.DataFrame({'pft':['grass_c3']}),'species categories',{}, {})
    raise NoMatchingData('Fixture has no complete comparison records')
with patch('data_blocks.load_block',fixture),patch('nearby_data.nearby_data_options',return_value=[]),patch('weather.available_historical_period',return_value=(date(1940,1,2),date(2023,1,15))):
    app=AppTest.from_file('app.py',default_timeout=30).run()
    assert not app.exception,[e.value for e in app.exception]
    for name,title in [('water_balance','Water'),('vegetation','Plants'),('soil_carbon','Soil carbon')]:
        assert any(e.label==title for e in app.expander)==(name in selected)
    assert all(item.value is not None for item in app.number_input if (item.key or '').startswith('parameter_'))
    if 'vegetation' in selected:
        table=next(table for table in app.dataframe if table.key and table.key.startswith('species_table_'))
        assert len(table.value)>50
        assert set(table.value.columns)=={'Species','Group','Model preset','ID'}
        assert not any(widget.key=='trait_species' for widget in app.selectbox)
        assert app.number_input(key='parameter_vegetation_max_npp').value==5.
        assert app.number_input(key='parameter_vegetation_root_turnover').value==.003
    app.number_input(key='latitude').set_value(30.).run()
    app.number_input(key='longitude').set_value(-96.).run()
    app.button(key='run_model').click().run()
    if app.session_state.get('run_recovery'):
        app.button(key='use_assumed_soil' if any(button.key=='use_assumed_soil' for button in app.button) else 'continue_run').click().run()
    assert not app.exception,[e.value for e in app.exception]
    assert not app.error,[e.value for e in app.error]
    result=app.session_state['simulation']
    assert len(result)==15
    for name,column in [('water_balance','aet_mm'),('vegetation','lai'),('soil_carbon','soil_carbon_gC_m2')]:
        assert (column in result)==(name in selected)
    links=result.attrs['coupling']
    expected=2*int({'water_balance','vegetation'}<=selected)+int({'water_balance','soil_carbon'}<=selected)+int({'vegetation','soil_carbon'}<=selected)
    assert len(links)==expected
    if 'water_balance' in selected:
        assert result.water_residual_mm.abs().max()<1e-9
    if 'soil_carbon' in selected:
        assert result.carbon_residual_gC_m2.abs().max()<1e-8
    assert any(t.label=='Comparisons' for t in app.tabs)==bool(selected&{'water_balance','soil_carbon'})
'''
    completed=subprocess.run([sys.executable,'-c',script],cwd=project,env={**os.environ,'PYTHONPATH':str(project)},capture_output=True,text=True,timeout=60)
    assert completed.returncode==0,completed.stdout+completed.stderr


def test_comparison_backup_for_partial_primary_and_later_independent_load_updates_results(tmp_path):
    with ZipFile(io.BytesIO(make_project_zip({'project_name':'Comparison update','models':['water_balance'],'initial_run_days':16}))) as archive:
        archive.extractall(tmp_path)
    project=next(tmp_path.iterdir())
    script='''
from unittest.mock import patch
from datetime import date
import pandas as pd
import streamlit as st
from streamlit.testing.v1 import AppTest
from data_blocks import DataBlock,NoMatchingData
state={'satellite_ready':False,'satellite_attempts':0,'primary_ready':True}
def fixture(key,request):
    if key=='open_meteo':
        days=pd.date_range(request.start,request.end)
        return DataBlock(key,pd.DataFrame({'date':days.strftime('%Y-%m-%d'),'precip_mm':4.,'et0_mm':2.,'temp_c':20.}),'daily',{}, {})
    if key=='terraclimate':
        if not state['primary_ready']:
            raise NoMatchingData('Primary comparison is currently unavailable')
        return DataBlock(key,pd.DataFrame({'date':['2023-01-01'],'period_end':['2023-01-31'],'aet_mm':[60.]}),'monthly',{}, {})
    if key=='mod16a2gf':
        state['satellite_attempts']+=1
        if not state['satellite_ready']:
            raise NoMatchingData('No acceptable satellite windows in first request')
        return DataBlock(key,pd.DataFrame({'date':['2023-01-01','2023-01-09'],'period_end':['2023-01-08','2023-01-16'],'aet_mm':[12.,13.]}),'8-day composite',{}, {'collection':'061','fixture':True})
    raise NoMatchingData('No matching fixture soil profile')
with patch('data_blocks.load_block',fixture),patch('nearby_data.nearby_data_options',return_value=[]),patch('weather.available_historical_period',return_value=(date(1940,1,2),date(2023,1,16))):
    app=AppTest.from_file('app.py',default_timeout=30).run()
    app.number_input(key='latitude').set_value(30.).run()
    app.number_input(key='longitude').set_value(-96.).run()
    app.button(key='run_model').click().run()
    if app.session_state.get('run_recovery'):
        app.button(key='use_assumed_soil' if any(button.key=='use_assumed_soil' for button in app.button) else 'continue_run').click().run()
    assert not app.exception,[e.value for e in app.exception]
    assert 'terraclimate' not in app.session_state['loaded_blocks']
    assert state['satellite_attempts']==0,'Comparison retrieval must wait for explicit loading'
    before=app.session_state['simulation'].copy(deep=True)
    app.button(key='load_comparisons').click().run()
    assert not app.exception,[e.value for e in app.exception]
    assert 'terraclimate' in app.session_state['loaded_blocks']
    assert state['satellite_attempts']==1,'No complete primary window must trigger the backup even when primary loaded'
    assert app.session_state['comparisons'].empty
    pd.testing.assert_frame_equal(before,app.session_state['simulation'])
    state['satellite_ready']=True
    app.selectbox(key='block_choice').set_value('mod16a2gf').run()
    app.button(key='load_data_block').click().run()
    assert not app.exception,[e.value for e in app.exception]
    assert not app.error,[e.value for e in app.error]
    assert len(app.session_state['comparisons'])==2
    assert set(app.session_state['comparisons'].block)=={'mod16a2gf'}
    assert len(app.session_state['run_provenance']['comparisons'])==2
    assert app.session_state['run_provenance']['sources']['mod16a2gf']['collection']=='061'
    pd.testing.assert_frame_equal(before,app.session_state['simulation'])
    assert any('Compared 2 complete windows' in item.value for item in app.success)

    # A new explicit comparison request must replace its entire source group,
    # including old successes and their provenance when providers now fail.
    state.update(primary_ready=False,satellite_ready=False)
    st.cache_data.clear()
    app.button(key='load_comparisons').click().run()
    assert not app.exception,[e.value for e in app.exception]
    for source in ('terraclimate','mod16a2gf'):
        assert source not in app.session_state['loaded_blocks']
        assert source not in app.session_state['run_provenance']['sources']
        assert source in app.session_state['data_errors']
        assert source in app.session_state['run_provenance']['unavailable']
    assert app.session_state['comparisons'].empty
    assert not app.session_state['run_provenance']['comparisons']
    pd.testing.assert_frame_equal(before,app.session_state['simulation'])

    # Successful later loads clear the previous failure state as well.
    state.update(primary_ready=True,satellite_ready=True)
    st.cache_data.clear()
    app.button(key='load_comparisons').click().run()
    assert not app.exception,[e.value for e in app.exception]
    for source in ('terraclimate','mod16a2gf'):
        assert source in app.session_state['loaded_blocks']
        assert source not in app.session_state['data_errors']
        assert source not in app.session_state['data_issues']
        assert source not in app.session_state['run_provenance']['unavailable']
    assert len(app.session_state['comparisons'])==2
    pd.testing.assert_frame_equal(before,app.session_state['simulation'])
'''
    result=subprocess.run([sys.executable,'-c',script],cwd=project,env={**os.environ,'PYTHONPATH':str(project)},capture_output=True,text=True,timeout=60)
    assert result.returncode==0,result.stdout+result.stderr


def test_exported_recovery_keeps_site_and_dates_with_explicit_proxies(tmp_path):
    config={'project_name':'Source recovery','models':['water_balance','soil_carbon']}
    with ZipFile(io.BytesIO(make_project_zip(config))) as archive:
        archive.extractall(tmp_path)
    project=next(tmp_path.iterdir())
    script='''
from unittest.mock import patch
from datetime import date
from streamlit.testing.v1 import AppTest
from data_blocks import NoMatchingData,DataBlock
import pandas as pd
study={'Record_number':303,'Study_number':1,'Study_midyear':2004.,'Latitude':34.98,'Longitude':-97.52,
       'Rh_annual':500.,'distance_km':253.,'period_start':'2003-07-01','period_end':'2004-06-30','period_inferred':True}
cell={'latitude':32.77917,'longitude':-96.82083,'distance_km':1.4,'mapping_unit':3866}
def fixture(key,request):
    if key=='open_meteo':
        assert request.latitude==32.78306 and request.longitude==-96.80667
        return DataBlock(key,pd.DataFrame({'date':pd.date_range(request.start,request.end).strftime('%Y-%m-%d'),
            'precip_mm':4.,'et0_mm':2.,'temp_c':20.}),'daily',{}, {})
    if key=='srdb':
        if request.latitude==study['Latitude']:
            return DataBlock(key,pd.DataFrame([{**study,'comparable_rh':True}]),'study annual',{}, {})
        raise NoMatchingData('No observations here',{'nearest_qualified_studies':[study]})
    if key=='hwsd' and request.latitude==cell['latitude']:
        return DataBlock(key,pd.DataFrame({'top_cm':[0.],'bottom_cm':[200.],'sand_pct':[40.],'silt_pct':[35.],'clay_pct':[25.],
            'soc_g_kg':[20.],'bulk_density_g_cm3':[1.3],'coarse_fragments_pct':[10.]}),'static depth profile',{}, {})
    raise NoMatchingData('No soil profile here')
cell.update(records=7,source_id='hwsd')
with patch('data_blocks.load_block',fixture),patch('nearby_data.nearby_data_options',return_value=[cell]),patch('weather.available_historical_period',return_value=(date(1941,1,1),date(2026,11,3))):
    app=AppTest.from_file('app.py',default_timeout=30).run()
    app.number_input(key='latitude').set_value(32.78306).run()
    app.number_input(key='longitude').set_value(-96.80667).run()
    original=app.date_input(key='period').value
    app.selectbox(key='block_choice').set_value('hwsd').run()
    app.button(key='load_data_block').click().run()
    assert not app.exception,[e.value for e in app.exception]
    app.button(key='find_nearby_data').click().run()
    assert not app.exception,[e.value for e in app.exception]
    assert app.number_input(key='latitude').value==32.78306
    assert app.date_input(key='period').value==original
    app.button(key='use_nearby_proxy').click().run()
    assert not app.exception,[e.value for e in app.exception]
    assert app.number_input(key='latitude').value==32.78306
    assert app.number_input(key='longitude').value==-96.80667
    assert app.date_input(key='period').value==original
    proxy=app.session_state['loaded_blocks']['hwsd'].provenance['spatial_proxy']
    assert proxy['simulation_site']['latitude']==32.78306
    assert proxy['source_site']['latitude']==cell['latitude']
    assert proxy['distance_km']>0
    app.button(key='run_model').click().run()
    if app.session_state.get('run_recovery'):
        app.button(key='use_assumed_soil' if any(button.key=='use_assumed_soil' for button in app.button) else 'continue_run').click().run()
    assert not app.exception,[e.value for e in app.exception]
    result=app.session_state['simulation']
    assert result is not None and result.water_residual_mm.abs().max()<1e-8
    assert app.session_state['run_provenance']['site']['latitude']==32.78306
    assert app.session_state['run_provenance']['sources']['hwsd']['spatial_proxy']['source_site']['latitude']==cell['latitude']
    assert app.session_state['run_provenance']['sources']['open_meteo'].get('spatial_proxy') is None
    app.selectbox(key='block_choice').set_value('srdb').run()
    app.button(key='load_data_block').click().run()
    assert not app.exception,[e.value for e in app.exception]
    app.button(key='use_study').click().run()
    assert not app.exception,[e.value for e in app.exception]
    assert app.number_input(key='latitude').value==32.78306
    assert app.date_input(key='period').value==original
    assert app.session_state['loaded_blocks']['srdb'].provenance['spatial_proxy']['source_site']['latitude']==study['Latitude']
    app.button(key='use_source_dates').click().run()
    assert not app.exception,[e.value for e in app.exception]
    assert app.number_input(key='latitude').value==32.78306
    assert app.date_input(key='period').value==(date(2003,7,1),date(2004,6,30))
    app.button(key='remove_proxy').click().run()
    assert not app.exception,[e.value for e in app.exception]
    assert 'srdb' not in app.session_state['source_proxies']
    assert app.number_input(key='latitude').value==32.78306
    assert 'hwsd' in app.session_state['source_proxies']
    app.number_input(key='latitude').set_value(32.8).run()
    assert not app.exception,[e.value for e in app.exception]
    assert not app.session_state['source_proxies']
'''
    result=subprocess.run([sys.executable,'-c',script],cwd=project,
        env={**os.environ,'PYTHONPATH':str(project)},capture_output=True,text=True,timeout=60)
    assert result.returncode==0,result.stdout+result.stderr
