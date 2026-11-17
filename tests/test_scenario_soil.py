"""Climate scenarios cannot bypass mapped-soil coverage or assumption consent."""
from datetime import date
import io
import os
from pathlib import Path
import subprocess
import sys
from zipfile import ZipFile

import pandas as pd
import pytest

from data_blocks import DataBlock
from scenario_soil import resolve_scenario_soil
from studio_builder.generator import make_project_zip


DEPTHS = [0, 5, 15, 30, 60, 100, 200]


def block(key='soilgrids_wcs', bottom=200):
    return DataBlock(key, pd.DataFrame([dict(top_cm=0, bottom_cm=bottom,
        sand_pct=40., silt_pct=35., clay_pct=25., soc_g_kg=10.,
        bulk_density_g_cm3=1.3, coarse_fragments_pct=10.)]),
        'static depth profile', {}, {'request': {'start': '2023-01-01', 'end': '2023-12-31'}})


def resolve(loaded, **kwargs):
    return resolve_scenario_soil(loaded, DEPTHS, 'loam', {}, **kwargs)


def test_scenario_reuses_static_mapped_soil_without_changing_profile_or_dates():
    source = block()
    before = source.frame.copy(deep=True)
    chosen = resolve({'soilgrids_wcs': source})
    assert chosen['ready']
    assert chosen['soil_source'] == 'soilgrids_wcs'
    pd.testing.assert_frame_equal(chosen['soil'], before)
    assert source.provenance['request']['start'] == str(date(2023, 1, 1))
    chosen['soil'].loc[0, 'sand_pct'] = 0.
    pd.testing.assert_frame_equal(source.frame, before)


def test_selected_priority_and_valid_fallback_apply_to_scenarios():
    sources = {'soilgrids_wcs': block(), 'hwsd': block('hwsd')}
    assert resolve(sources)['soil_source'] == 'soilgrids_wcs'
    assert resolve(sources, preference='hwsd')['soil_source'] == 'hwsd'
    sources['soilgrids_wcs'] = block(bottom=100)
    assert resolve(sources)['soil_source'] == 'hwsd'


def test_partial_depth_cannot_silently_become_assumed_soil():
    chosen = resolve({'soilgrids_wcs': block(bottom=100)})
    assert not chosen['ready']
    assert chosen['soil'] is None and chosen['soil_source'] is None
    assert 'complete 100–200 cm layer' in chosen['message']


def test_missing_soil_blocks_scenario_until_explicit_assumption_acceptance():
    assert not resolve({})['ready']
    chosen = resolve({}, assumed_accepted=True)
    assert chosen['ready'] and chosen['soil'] is None
    assert chosen['soil_source'] == 'explicit texture preset fallback'
    # A later valid measurement-derived profile takes precedence over assumptions.
    assert resolve({'hwsd': block('hwsd')}, assumed_accepted=True)['soil_source'] == 'hwsd'


def test_invalid_initial_assumptions_still_block_after_acceptance():
    chosen = resolve_scenario_soil({}, DEPTHS, 'loam', {'fallback_soc': -1.},
                                  assumed_accepted=True)
    assert not chosen['ready']
    assert 'invalid' in chosen['message']


def test_unknown_source_preference_fails_clearly():
    with pytest.raises(ValueError, match='preferred soil source'):
        resolve({}, preference='unknown')


def test_plant_only_scenario_does_not_require_soil_data_or_preset_acceptance():
    chosen = resolve({}, requires_soil=False)
    assert chosen['ready'] and chosen['soil'] is None
    assert chosen['soil_source'] == 'not required by selected models'
    assert 'rooting domain' in chosen['message']
    assert 'not a site soil measurement' in chosen['message']
    assert resolve({'hwsd': block('hwsd')}, requires_soil=False)['soil_source'] == 'hwsd'


def test_plant_only_domain_settings_still_require_valid_layer_boundaries():
    chosen = resolve_scenario_soil({}, [0, 100, 50], 'loam', {}, requires_soil=False)
    assert not chosen['ready']
    assert 'rooting domain is invalid' in chosen['message']


def test_exported_plant_only_climate_scenario_runs_without_mapped_soil(tmp_path):
    payload = make_project_zip({'project_name': 'Plant climate', 'models': ['vegetation'],
                               'additional_datasets': ['open_meteo_climate']})
    with ZipFile(io.BytesIO(payload)) as archive:
        archive.extractall(tmp_path)
    project = tmp_path/'plant-climate'
    assert not (project/'data_blocks'/'soil.py').exists()
    script = '''
from datetime import date
from unittest.mock import patch
import pandas as pd
from streamlit.testing.v1 import AppTest
from data_blocks import DataBlock
def load(key,request):
    assert key=='open_meteo_climate',key
    dates=pd.date_range(request.start,request.end)
    return DataBlock(key,pd.DataFrame({'date':dates.strftime('%Y-%m-%d'),
        'precip_mm':3.,'et0_mm':2.,'temp_c':20.}),'daily',{}, {'fixture':True})
with patch('data_blocks.load_block',load),patch('weather.available_historical_period',
        return_value=(date(1941,1,1),date(2023,12,31))):
    app=AppTest.from_file('app.py',default_timeout=30).run()
    app.number_input(key='latitude').set_value(30.)
    app.number_input(key='longitude').set_value(-96.).run()
    app.selectbox(key='block_choice').set_value('open_meteo_climate').run()
    app.date_input(key='climate_period').set_value((date(2030,1,1),date(2030,1,31))).run()
    app.button(key='load_data_block').click().run()
    assert not app.exception,[item.message for item in app.exception]
    assert not app.button(key='run_scenario').disabled
    assert not any(button.key=='use_assumed_soil' for button in app.button)
    app.button(key='run_scenario').click().run()
    assert not app.exception,[item.message for item in app.exception]
    assert not app.error,[item.value for item in app.error]
    result=app.session_state['scenario']
    assert len(result)==31
    assert result.attrs['soil_mapping']['dataset']=='not required by selected models'
    assert not result.attrs['assumed_soil_explicitly_accepted']
    assert not app.session_state.get('run_recovery')
'''
    result = subprocess.run([sys.executable, '-c', script], cwd=project,
        env={**os.environ, 'PYTHONPATH': str(project)}, capture_output=True,
        text=True, timeout=60)
    assert result.returncode == 0, result.stdout+result.stderr
