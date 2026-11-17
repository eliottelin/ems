"""Explicit plant application, native species browsing and depth agree with runs."""
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from importlib.util import find_spec
pytestmark = pytest.mark.skipif(
    any(find_spec(name) is None for name in ('data_blocks.traits', 'models.water_balance', 'models.vegetation', 'models.soil_carbon')),
    reason='These coupled plant-control regressions require all three selected models and TRY')
from streamlit.testing.v1 import AppTest


SCRIPT = '''
from pathlib import Path
import pandas as pd
import streamlit as st
from advanced import render_advanced
from engine import simulate
st.session_state['full_runs']=st.session_state.get('full_runs',0)+1
cfg={'models':['water_balance','vegetation','soil_carbon'],'layer_depths_cm':[0,10,30,100]}
params,options,depths,texture,overrides,selection,error=render_advanced(
    cfg,{'try_categorical':{'role':'traits'}},Path(st.session_state['fixture_root']))
effective={'params':params,'depths':depths,'selection':selection}
if effective!=st.session_state.get('effective'):
    st.session_state['simulation']=None
st.session_state['effective']=effective
if st.button('Run',key='test_run',disabled=error):
    dates=pd.date_range('2023-01-01',periods=31)
    forcing=pd.DataFrame({'date':dates.strftime('%Y-%m-%d'),'precip_mm':3.,'et0_mm':2.,'temp_c':20.})
    st.session_state['simulation']=simulate({**cfg,'latitude':30.,'longitude':-96.,
        'layer_depths_cm':depths,'soil_texture':texture,'trait_selection':selection},
        params,forcing=forcing)
'''


def species_frame():
    frame=pd.DataFrame({'AccSpeciesID':[1,2,3,4,5],
        'AccSpeciesName':['Alpha C3 grass','Beta C4 grass','Delta deciduous tree',
                          'Echo evergreen tree','Gamma unresolved'],
        'pft':['grass_c3','grass_c4','tree_deciduous','tree_evergreen','unresolved']})
    extra=pd.DataFrame({'AccSpeciesID':range(100,300),
        'AccSpeciesName':[f'Extra tree {number:03d}' for number in range(200)],
        'pft':'tree_deciduous'})
    return pd.concat([frame,extra],ignore_index=True)


@pytest.fixture
def app(tmp_path):
    from advanced import cached_species
    cached_species.clear()
    with patch('advanced.cached_species',return_value=(species_frame(),{'source_sha256':'fixture'})), \
         patch('data_blocks.traits.species_index_fingerprint',return_value=('fixture',)):
        instance=AppTest.from_string(SCRIPT,default_timeout=30)
        instance.session_state['fixture_root']=str(tmp_path)
        instance.run()
        assert not instance.exception,[error.value for error in instance.exception]
        yield instance


def select_species(app,identifier):
    app.text_input(key='species_query').set_value(str(identifier)).run()
    table=species_table(app)
    position=int(table.value.index[table.value.ID==identifier][0])
    app.session_state[table.key]={'selection':{'rows':[position],'columns':[],'cells':[]}}
    app.run()
    app.button(key='use_selected_plant').click().run()
    assert not app.exception,[error.value for error in app.exception]


def species_table(app):
    return next(item for item in app.dataframe if item.key and item.key.startswith('species_table_'))


def test_tree_then_crop_clears_species_and_agrees_with_engine_after_manual_edits(app):
    app.number_input(key='parameter_initial_leaf_carbon').set_value(321.).run()
    app.number_input(key='particle_density').set_value(2.7).run()
    select_species(app,3)
    assert app.selectbox(key='ecosystem').value=='forest'
    assert app.number_input(key='parameter_vegetation_max_npp').value==6.
    assert app.number_input(key='parameter_vegetation_max_npp').proto.set_value
    assert app.number_input(key='parameter_vegetation_lai_max').proto.set_value
    app.number_input(key='parameter_vegetation_max_npp').set_value(7.3).run()
    app.button(key='test_run').click().run()
    assert app.session_state['simulation'] is not None

    app.selectbox(key='ecosystem').set_value('crop').run()
    assert app.session_state['active_trait_species'] is None
    assert not [item for item in app.selectbox if item.key=='trait_species']
    assert app.number_input(key='parameter_vegetation_max_npp').value==8.
    assert app.number_input(key='parameter_vegetation_lai_max').value==5.
    assert app.number_input(key='parameter_vegetation_max_npp').proto.set_value
    assert app.number_input(key='parameter_vegetation_lai_max').proto.set_value
    assert app.number_input(key='parameter_initial_leaf_carbon').value==321.
    assert app.number_input(key='particle_density').value==2.7
    assert app.session_state['simulation'] is None
    assert any('Active plant preset: Crop' in caption.value for caption in app.caption)
    app.button(key='test_run').click().run()
    result=app.session_state['simulation']
    assert result.attrs['plant_preset']['max_npp']==8.
    assert result.attrs['plant_preset']['lai_max']==5.
    assert result.water_residual_mm.abs().max()<1e-8
    assert result.carbon_residual_gC_m2.abs().max()<1e-8


def test_species_application_changes_defaults_and_same_category_application_is_deliberate(app):
    select_species(app,2)
    assert app.selectbox(key='ecosystem').value=='grass_c4'
    assert app.number_input(key='parameter_vegetation_max_npp').value==6.
    assert app.session_state['effective']['selection']['pft']=='grass_c4'
    app.number_input(key='parameter_vegetation_max_npp').set_value(7.2).run()
    assert app.number_input(key='parameter_vegetation_max_npp').value==7.2
    select_species(app,1)
    assert app.selectbox(key='ecosystem').value=='grass_c3'
    assert app.number_input(key='parameter_vegetation_max_npp').value==5.
    select_species(app,3)
    app.number_input(key='parameter_vegetation_lai_max').set_value(7.1).run()
    select_species(app,4)
    assert app.selectbox(key='ecosystem').value=='forest'
    assert app.number_input(key='parameter_vegetation_lai_max').value==6.
    assert app.session_state['effective']['selection']['species_id']==4


def test_unresolved_selection_is_explicit_and_clear_preserves_the_ecosystem(app):
    app.selectbox(key='ecosystem').set_value('crop').run()
    select_species(app,5)
    assert app.selectbox(key='ecosystem').value=='crop'
    assert app.number_input(key='parameter_vegetation_max_npp').value==8.
    assert app.session_state['effective']['selection']['preset'] is None
    assert any('no supported unambiguous category' in item.value for item in app.info)
    app.button(key='clear_trait_species').click().run()
    assert app.session_state['effective']['selection'] is None
    assert app.selectbox(key='ecosystem').value=='crop'


def test_all_matching_rows_browse_without_changing_applied_species_or_manual_edits(app):
    assert len(species_table(app).value)==205
    assert list(species_table(app).value)==['Species','Group','Model preset','ID']
    assert any('205 plants' in caption.value for caption in app.caption)
    select_species(app,3)
    app.number_input(key='parameter_vegetation_max_npp').set_value(11.).run()
    app.text_input(key='species_query').set_value('Extra').run()
    assert len(species_table(app).value)==200
    assert app.session_state['effective']['selection']['species_id']==3
    assert app.number_input(key='parameter_vegetation_max_npp').value==11.
    app.selectbox(key='species_category').set_value('grass_c4').run()
    assert species_table(app).value.empty
    assert app.button(key='use_selected_plant').disabled
    app.text_input(key='species_query').set_value('').run()
    assert len(species_table(app).value)==1
    app.selectbox(key='species_category').set_value('all').run()
    app.selectbox(key='species_sort').set_value('name_desc').run()
    assert species_table(app).value.Species.iloc[0]=='Gamma unresolved'
    table=species_table(app)
    app.session_state[table.key]={'selection':{'rows':[1],'columns':[],'cells':[]}}
    app.run()
    assert not app.button(key='use_selected_plant').disabled
    assert app.session_state['effective']['selection']['species_id']==3
    assert app.number_input(key='parameter_vegetation_max_npp').value==11.


def test_saved_depth_and_total_boundary_controls_stay_synchronized(app):
    assert app.text_input(key='layer_depths').value=='0, 10, 30, 100'
    assert app.number_input(key='soil_depth_cm').value==100.
    app.button(key='test_run').click().run()
    assert app.session_state['simulation'] is not None
    app.toggle(key='edit_soil').set_value(True).run()
    app.number_input(key='soil_depth_cm').set_value(200.).run()
    assert app.text_input(key='layer_depths').value=='0, 20, 60, 200'
    assert not app.toggle(key='edit_soil').value
    assert app.session_state['simulation'] is None
    app.text_input(key='layer_depths').set_value('0, 25, 75, 150').run()
    assert app.number_input(key='soil_depth_cm').value==150.
    assert app.session_state['effective']['depths']==[0.,25.,75.,150.]
    app.text_input(key='layer_depths').set_value('0, 1001').run()
    assert app.error
    assert app.button(key='test_run').disabled


def test_group_sort_and_filters_use_all_rows_and_invalidate_pending_selection(app):
    app.selectbox(key='species_sort').set_value('name_desc').run()
    assert species_table(app).value.Species.iloc[0]=='Gamma unresolved'
    table=species_table(app)
    app.session_state[table.key]={'selection':{'rows':[0],'columns':[],'cells':[]}}
    app.run()
    assert not app.button(key='use_selected_plant').disabled
    app.selectbox(key='species_sort').set_value('group_name').run()
    assert species_table(app).key!=table.key
    assert app.button(key='use_selected_plant').disabled
    groups=species_table(app).value.Group.tolist()
    assert groups==sorted(groups)
    app.selectbox(key='species_category').set_value('tree_evergreen').run()
    assert len(species_table(app).value)==1
    assert species_table(app).value.Species.iloc[0]=='Echo evergreen tree'
    app.selectbox(key='species_category').set_value('forest').run()
    assert len(species_table(app).value)==202
    app.selectbox(key='species_category').set_value('crop').run()
    assert species_table(app).value.empty
    assert any('does not record crop use' in item.value for item in app.info)


def test_restoring_active_preset_explicitly_resets_the_browser_value(app):
    select_species(app,3)
    app.number_input(key='parameter_vegetation_max_npp').set_value(7.3).run()
    app.number_input(key='parameter_vegetation_lai_max').set_value(8.2).run()
    app.number_input(key='particle_density').set_value(2.7).run()
    app.button(key='reset_parameters').click().run()
    assert not app.exception,[error.value for error in app.exception]
    assert app.session_state['active_trait_species']['species_id']==3
    assert app.selectbox(key='ecosystem').value=='forest'
    assert app.number_input(key='parameter_vegetation_max_npp').value==6.
    assert app.number_input(key='parameter_vegetation_lai_max').value==6.
    assert app.number_input(key='parameter_vegetation_max_npp').proto.set_value
    assert app.number_input(key='parameter_vegetation_lai_max').proto.set_value
    assert app.number_input(key='particle_density').value==2.65
    assert app.number_input(key='particle_density').proto.set_value


def test_a_row_beyond_the_previous_fifty_result_limit_can_be_applied(app):
    app.text_input(key='species_query').set_value('Extra').run()
    table=species_table(app)
    assert len(table.value)==200
    position=int(table.value.index[table.value.ID==299][0])
    assert position>50
    app.session_state[table.key]={'selection':{'rows':[position],'columns':[],'cells':[]}}
    app.run()
    assert app.session_state['effective']['selection'] is None
    app.button(key='use_selected_plant').click().run()
    assert not app.exception,[error.value for error in app.exception]
    assert app.session_state['effective']['selection']['species_id']==299
    assert app.selectbox(key='ecosystem').value=='forest'
    assert app.number_input(key='parameter_vegetation_max_npp').value==6.


def test_changed_species_source_clears_stale_record_without_changing_manual_rates(app):
    select_species(app,3)
    app.number_input(key='parameter_vegetation_max_npp').set_value(11.).run()
    with patch('advanced.cached_species',return_value=(species_frame(),{'source_sha256':'replacement'})):
        app.text_input(key='species_query').set_value('Extra').run()
        assert not app.exception,[error.value for error in app.exception]
        assert app.session_state['effective']['selection'] is None
        assert app.selectbox(key='ecosystem').value=='forest'
        assert app.number_input(key='parameter_vegetation_max_npp').value==11.
        assert any('species source changed' in item.value for item in app.info)


@pytest.mark.parametrize('failure',[OSError('Species workbook unavailable'),
                                  RuntimeError('Corrupt species workbook')])
def test_unavailable_species_source_keeps_presets_usable_without_fake_choices(app,failure):
    select_species(app,3)
    assert app.session_state['active_trait_species']['species_id']==3
    with patch('advanced.cached_species',side_effect=failure):
        app.run()
        assert not app.exception,[error.value for error in app.exception]
        assert app.session_state['active_trait_species'] is None
        assert app.session_state['effective']['selection'] is None
        assert not [item for item in app.selectbox if item.key=='trait_species']
        assert not [item for item in app.dataframe if item.key and item.key.startswith('species_table_')]
        assert any('TRY species source is unavailable' in item.value for item in app.info)
        app.selectbox(key='ecosystem').set_value('crop').run()
        assert not app.exception,[error.value for error in app.exception]
        assert app.number_input(key='parameter_vegetation_max_npp').value==8.
        assert app.number_input(key='parameter_vegetation_lai_max').value==5.
        assert app.session_state['effective']['params']['vegetation']['preset']=='crop'
        assert not app.button(key='test_run').disabled
        app.button(key='test_run').click().run()
        result=app.session_state['simulation']
        assert result is not None
        assert result.attrs['plant_preset']['max_npp']==8.
        assert result.attrs['plant_preset']['lai_max']==5.
