"""A served version describes loaded modules, not merely changed source files."""
import importlib
import json
from pathlib import Path
import sys
import tomllib
from types import ModuleType

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from runtime_identity import get_application_identity


@pytest.fixture
def loaded_module(tmp_path, monkeypatch):
    name = '_ems_identity_fixture'
    path = tmp_path / (name + '.py')
    path.write_text("PLANT_CONTROLS_REVISION='old-v1'\nDEFAULTS={'max_npp':6}\ndef production():\n    return 6\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(sys, 'dont_write_bytecode', True)
    monkeypatch.delitem(sys.modules, name, raising=False)
    module = importlib.import_module(name)
    yield path, module
    sys.modules.pop(name, None)


def test_identity_uses_loaded_revision_and_absolute_module_location(loaded_module, tmp_path):
    path, module = loaded_module
    identity = get_application_identity(tmp_path, {'advanced': module})
    assert identity['application_revision'] == 'old-v1'
    assert identity['project_directory'] == str(tmp_path.resolve())
    assert identity['modules']['advanced']['module_path'] == str(path.resolve())
    assert len(identity['loaded_build']) == 64
    assert json.loads(json.dumps(identity)) == identity


def test_source_edit_does_not_claim_a_new_loaded_build_before_reload(loaded_module, tmp_path):
    path, module = loaded_module
    previous = get_application_identity(tmp_path, {'advanced': module})
    path.write_text("PLANT_CONTROLS_REVISION='new-v2'\nDEFAULTS={'max_npp':8}\ndef production():\n    return 8\n")
    assert module.production() == 6
    assert get_application_identity(tmp_path, {'advanced': module}) == previous

    importlib.invalidate_caches()
    reloaded = importlib.reload(module)
    updated = get_application_identity(tmp_path, {'advanced': reloaded})
    assert reloaded.production() == 8
    assert updated['application_revision'] == 'new-v2'
    assert updated['loaded_build'] != previous['loaded_build']
    assert updated['modules']['advanced']['loaded_fingerprint'] != previous['modules']['advanced']['loaded_fingerprint']


def test_loaded_defaults_changes_are_included_without_exposing_their_values(loaded_module, tmp_path):
    _, module = loaded_module
    previous = get_application_identity(tmp_path, {'parameters': module})
    module.DEFAULTS['max_npp'] = 11
    updated = get_application_identity(tmp_path, {'parameters': module})
    assert updated['loaded_build'] != previous['loaded_build']
    assert 'max_npp' not in json.dumps(updated)


def test_wrapped_functions_are_fingerprinted_by_their_loaded_original_code(tmp_path):
    module = ModuleType('identity_wrapped_fixture')
    exec('from functools import wraps\n'
         'def original():\n    return 6\n'
         '@wraps(original)\n'
         'def wrapped():\n    return original()\n', module.__dict__)
    previous = get_application_identity(tmp_path, {'controls': module})
    replacement = compile('def replacement():\n    return 8\n', '<loaded replacement>', 'exec')
    namespace = {}
    exec(replacement, namespace)
    module.wrapped.__wrapped__.__code__ = namespace['replacement'].__code__
    assert module.wrapped() == 8
    assert get_application_identity(tmp_path, {'controls': module})['loaded_build'] != previous['loaded_build']


def test_runtime_caches_and_warning_registry_do_not_change_loaded_code_identity(loaded_module, tmp_path):
    _, module = loaded_module
    previous = get_application_identity(tmp_path, {'controls': module})
    module.runtime_cache = {'last_result': 6}
    module.__warningregistry__ = {'version': 1}
    assert get_application_identity(tmp_path, {'controls': module}) == previous


def test_real_render_lookup_and_model_execution_keep_identity_stable(tmp_path):
    folder = tmp_path / 'data/try'
    folder.mkdir(parents=True)
    pd.DataFrame([dict(AccSpeciesID=1, AccSpeciesName='Poa pratensis',
        PlantGrowthForm='graminoid', LeafType='narrow', LeafPhenology='evergreen',
        PhotosyntheticPathway='C3', Woodiness='non-woody')]).to_excel(
            folder / 'TRY_Categorical_Traits_Lookup_Table_identity.xlsx',
            sheet_name='Categorical Trait Data', index=False)
    script = '''
from pathlib import Path
import streamlit as st
import advanced,engine,parameters,soil_profile
from data_blocks import traits,soil
from runtime_identity import get_application_identity
root=Path(st.session_state['project_root'])
modules={'advanced':advanced,'engine':engine,'parameters':parameters,
    'soil_profile':soil_profile,'traits':traits,'soil_sources':soil}
before=get_application_identity(root,modules)
params,options,depths,texture,overrides,selection,error=advanced.render_advanced(
    {'models':['water_balance','vegetation','soil_carbon'],'layer_depths_cm':[0,100]},
    {'try_categorical':{'role':'traits'}},root)
assert not error
after_render=get_application_identity(root,modules)
engine.simulate({'models':['water_balance','vegetation','soil_carbon'],
    'weather_source':'demo_weather','days':2,'layer_depths_cm':depths},params)
advanced.cached_species(str(root),traits.species_index_fingerprint(root/'data'))
after_execution=get_application_identity(root,modules)
assert before==after_render,{key:(before['modules'][key],after_render['modules'][key])
    for key in modules if before['modules'][key]!=after_render['modules'][key]}
assert before==after_execution,{key:(before['modules'][key],after_execution['modules'][key])
    for key in modules if before['modules'][key]!=after_execution['modules'][key]}
st.session_state['stable_build']=after_execution['loaded_build']
'''
    app = AppTest.from_string(script, default_timeout=30)
    app.session_state['project_root'] = str(tmp_path)
    app.run()
    assert not app.exception, [error.value for error in app.exception]
    build = app.session_state['stable_build']
    app.run()
    assert not app.exception, [error.value for error in app.exception]
    assert app.session_state['stable_build'] == build


def test_generated_reload_settings_do_not_enable_the_agent_api():
    path = Path(__file__).resolve().parents[1] / 'template_generated_app/.streamlit/config.toml'
    settings = tomllib.loads(path.read_text())
    assert settings['server']['fileWatcherType'] == 'poll'
    assert settings['server']['runOnSave'] is True
    assert 'enableAgentApi' not in settings['server']
