"""Exported apps explain cold soil waits and reuse profiles for later runs."""
import io
import os
from pathlib import Path
import subprocess
import sys
from zipfile import ZipFile

from studio_builder.generator import make_project_zip


def test_soil_wait_feedback_and_cache_are_shared_by_data_and_model(tmp_path):
    with ZipFile(io.BytesIO(make_project_zip({
            'project_name': 'Soil loading feedback', 'models': ['water_balance'],
            'initial_run_days': 31}))) as archive:
        archive.extractall(tmp_path)
    project = next(tmp_path.iterdir())
    script = '''
from collections import Counter
from datetime import date
from unittest.mock import patch
import pandas as pd
import streamlit as st
from streamlit.testing.v1 import AppTest
from data_blocks import DataBlock
from source_cache import _cached_soil

_cached_soil.clear()
calls=Counter()
def fixture(key,request):
    calls[key]+=1
    if key=='soilgrids_wcs':
        report=request.options['_source_progress']
        assert callable(report)
        report(0.,'Loading SoilGrids profile · estimated 15–45s')
        report(1.,'Soil profile ready · all six depths checked')
        return DataBlock(key,pd.DataFrame([dict(top_cm=0,bottom_cm=200,sand_pct=40.,
            silt_pct=35.,clay_pct=25.,soc_g_kg=10.,bulk_density_g_cm3=1.3,
            coarse_fragments_pct=10.)]),'static depth profile',{}, {})
    if key=='open_meteo':
        days=pd.date_range(request.start,request.end)
        return DataBlock(key,pd.DataFrame({'date':days.strftime('%Y-%m-%d'),
            'precip_mm':3.,'et0_mm':2.,'temp_c':20.}),'daily',{}, {})
    if key=='terraclimate':
        return DataBlock(key,pd.DataFrame({'date':[str(request.start)],
            'period_end':[str(request.end)],'aet_mm':[30.]}),'monthly',{}, {})
    raise AssertionError('Unexpected source '+key)

with patch('data_blocks.load_block',fixture),patch('weather.available_historical_period',
        return_value=(date(1941,1,1),date(2023,1,31))),patch('streamlit.status',wraps=st.status) as panels:
    app=AppTest.from_file('app.py',default_timeout=30).run()
    app.number_input(key='latitude').set_value(30.)
    app.number_input(key='longitude').set_value(-96.).run()
    app.selectbox(key='block_choice').set_value('soilgrids_wcs').run()
    panels.reset_mock()
    app.button(key='load_data_block').click().run()
    assert panels.call_count==1,'A standalone load must have one loading panel'
    assert not app.exception,[e.value for e in app.exception]
    assert calls['soilgrids_wcs']==1
    assert not app.session_state['soil_load_timing']['cached']
    assert any('Soil profile ready in' in item.value for item in app.caption)
    assert panels.call_count==1

    app.date_input(key='period').set_value((date(2022,1,1),date(2022,1,31))).run()
    app.button(key='load_data_block').click().run()
    assert not app.exception,[e.value for e in app.exception]
    assert calls['soilgrids_wcs']==1,'Changing dates must reuse the static soil profile'
    assert app.session_state['soil_load_timing']['cached']
    assert any('Reused cached soil profile in' in item.value for item in app.caption)

    app.number_input(key='parameter_water_balance_soil_evaporation_coefficient').set_value(1.05).run()
    panels.reset_mock()
    app.button(key='run_model').click().run()
    assert panels.call_count==1,'Weather, soil and calculation must share one loading panel'
    assert not app.exception,[e.value for e in app.exception]
    assert app.session_state['simulation'] is not None
    assert calls['soilgrids_wcs']==1,'Model runs must share the profile loaded in Data'
    assert not calls['hwsd']
    assert app.session_state['simulation'].attrs['soil_mapping']['dataset']=='soilgrids_wcs'
    assert any('Reused cached soil profile in' in item.value for item in app.caption)
    assert calls['terraclimate']==0,'Optional downloads must not delay the simulation'
    before=app.session_state['simulation'].copy(deep=True)
    app.button(key='load_comparisons').click().run()
    assert not app.exception,[e.value for e in app.exception]
    assert calls['terraclimate']==1
    pd.testing.assert_frame_equal(before,app.session_state['simulation'])
    assert len(app.session_state['comparisons'])==1
    assert app.session_state['run_provenance']['stage_timings']
'''
    result = subprocess.run([sys.executable, '-c', script], cwd=project,
        env={**os.environ, 'PYTHONPATH': str(project)}, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
