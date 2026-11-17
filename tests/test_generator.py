import io
import json
import os
import subprocess
import sys
from zipfile import ZipFile
import pytest
from studio_builder.generator import make_project_zip,validate

BASE={'project_name':'Soil experiment #1','models':['water_balance','vegetation','soil_carbon'],
 'weather_source':'open_meteo','additional_datasets':['terraclimate','try_categorical','mod16a2gf'],
 'latitude':30.6,'longitude':-96.3,'days':30,'request':'Add optional fire disturbance.'}

def test_zip_structure_and_agent_neutral_brief():
    payload=make_project_zip(BASE)
    assert payload.startswith(b'PK')
    with ZipFile(io.BytesIO(payload)) as z:
        names=z.namelist()
        assert any(n.endswith('app.py') for n in names)
        assert any(n.endswith('models/water_balance.py') for n in names)
        assert any(n.endswith('models/vegetation.py') for n in names)
        assert any(n.endswith('models/soil_carbon.py') for n in names)
        brief=z.read(next(n for n in names if n.endswith('AGENT_BRIEF.md'))).decode()
        assert 'Add optional fire disturbance.' in brief
        assert 'any coding assistant' in brief
        assert 'Independent data blocks' in brief
        cfg=json.loads(z.read(next(n for n in names if n.endswith('model_config.json'))))
        assert cfg['initial_run_days']==30
        assert cfg['step_days']==1
        assert cfg['time_selection']=='runtime'
        assert 'latitude' not in cfg and 'start_date' not in cfg
        assert {block['id'] for block in cfg['data_blocks']} >= {'open_meteo', 'try_categorical', 'soilgrids_wcs', 'srdb', 'terraclimate'}
        assert cfg['weather_source']=='open_meteo'

def test_only_selected_module_exported():
    cfg={**BASE,'models':['water_balance']}
    with ZipFile(io.BytesIO(make_project_zip(cfg))) as z:
        names=z.namelist()
        assert any(n.endswith('models/water_balance.py') for n in names)
        assert not any(n.endswith('models/vegetation.py') for n in names)
        assert not any(n.endswith('models/soil_carbon.py') for n in names)

def test_bad_config_is_rejected():
    with pytest.raises(ValueError):
        validate({**BASE,'models':['fake_model']})
    with pytest.raises(ValueError):
        validate({**BASE,'latitude':999})
    with pytest.raises(ValueError):
        validate({**BASE,'days':0})


@pytest.mark.parametrize('depths', [[0, 10, 75], [0, 200], [0, 25, 250, 1000]])
def test_saved_depth_boundaries_are_preserved_in_future_exports(depths):
    with ZipFile(io.BytesIO(make_project_zip({**BASE, 'layer_depths_cm': depths}))) as archive:
        cfg=json.loads(archive.read(next(name for name in archive.namelist() if name.endswith('/model_config.json'))))
        assert cfg['layer_depths_cm']==depths
        assert cfg['soil_depth_cm']==depths[-1]
        card=archive.read(next(name for name in archive.namelist() if name.endswith('/MODEL_CARD.md'))).decode()
        assert f'{len(depths)-1} initial soil layers to {depths[-1]:g} cm' in card


def test_total_depth_can_resize_default_export_layers_without_overriding_saved_boundaries():
    from studio_builder.generator import configured_depths
    assert configured_depths({'soil_depth_cm': 100})==[0, 2.5, 7.5, 15, 30, 50, 100]
    assert configured_depths({'soil_depth_cm': 999, 'layer_depths_cm': [0, 10, 50]})==[0, 10, 50]


@pytest.mark.parametrize('depths', [[1, 10], [0, 0, 10], [0, 1001], [0, float('nan')], list(range(32))])
def test_invalid_export_depths_are_rejected(depths):
    with pytest.raises(ValueError):
        validate({**BASE, 'layer_depths_cm': depths})


def test_runtime_context_and_optional_block_export_are_portable():
    config = {'project_name':'Plant sandbox','models':['vegetation'],
              'simulation_steps':20000,'additional_datasets':['open_meteo_climate'],
              'request':'Add depth-dependent roots.'}
    with ZipFile(io.BytesIO(make_project_zip(config))) as archive:
        names=archive.namelist()
        assert any(name.endswith('data_blocks/forcing.py') for name in names)
        assert any(name.endswith('data_blocks/traits.py') for name in names)
        assert not any(name.endswith('data_blocks/soil.py') for name in names)
        assert not any(name.endswith('data_blocks/observations.py') for name in names)
        config_name=next(name for name in names if name.endswith('model_config.json'))
        exported=json.loads(archive.read(config_name))
        assert exported['initial_run_days']==20000
        assert exported['step_days']==1
        assert 'latitude' not in exported and 'start_date' not in exported
        assert {block['id'] for block in exported['data_blocks']} == {'open_meteo','try_categorical','open_meteo_climate'}
        guide=archive.read(next(name for name in names if name.endswith('GETTING_STARTED.md'))).decode()
        assert 'python.org' in guide and 'requirements.txt' in guide
        assert 'Windows PowerShell' in guide and 'Homebrew is optional' in guide
        assert 'Streamlit Community Cloud' in guide and 'Create app' in guide
        if exported['local_try']['available']:
            assert any('/data/try/' in name and name.endswith('.xlsx') for name in names)
            assert any(name.endswith('Terms_of_service_of_the_TRY_File_Archive.pdf') for name in names)


@pytest.mark.parametrize("models", [
    ["water_balance"], ["vegetation"], ["soil_carbon"],
    ["water_balance", "vegetation", "soil_carbon"],
])
def test_exported_science_tests_run_independently(tmp_path, models):
    with ZipFile(io.BytesIO(make_project_zip({**BASE, "models": models}))) as archive:
        archive.extractall(tmp_path)
    project = next(tmp_path.iterdir())
    assert (project / "MODEL_INTERFACES.md").is_file()
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"], cwd=project,
        env={**os.environ, "PYTHONPATH": str(project)},
        capture_output=True, text=True, timeout=60,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_export_bundles_only_processed_hwsd_and_scientific_specification():
    with ZipFile(io.BytesIO(make_project_zip(BASE))) as archive:
        names=archive.namelist()
        assert any(name.endswith('/data/hwsd/HWSD2_LAYERS.csv.gz') for name in names)
        assert any(name.endswith('/data/hwsd/source.json') for name in names)
        assert any(name.endswith('/SCIENCE.md') for name in names)
        assert not any(name.endswith('.mdb') or name.endswith('.bil') or '/private/' in name for name in names)
        cfg=json.loads(archive.read(next(name for name in names if name.endswith('model_config.json'))))
        assert cfg['scientific_version']=='calabrese-depth-v2'
        assert cfg['layer_depths_cm']==[0,5,15,30,60,100,200]
        assert any(name.endswith('/.streamlit/config.toml') for name in names)
        assert any(name.endswith('/runtime_identity.py') for name in names)


def test_export_packages_only_the_allowed_hidden_streamlit_config(tmp_path,monkeypatch):
    import studio_builder.generator as generator
    settings=tmp_path/'.streamlit'
    settings.mkdir()
    (settings/'config.toml').write_text('[server]\nfileWatcherType="poll"\nrunOnSave=true\n')
    (settings/'secrets.toml').write_text('test_only="do not bundle"\n')
    (tmp_path/'.gitignore').write_text('.env*\n.streamlit/secrets.toml\n.ems-cache/\n')
    (tmp_path/'.env').write_text('test_only=do not bundle\n')
    (tmp_path/'app.py').write_text('print("test export")\n')
    monkeypatch.setattr(generator,'TEMPLATE',tmp_path)
    monkeypatch.setattr(generator,'hwsd_assets',lambda:[])
    with ZipFile(io.BytesIO(make_project_zip({'models':['water_balance']}))) as archive:
        names=archive.namelist()
        assert any(name.endswith('/.streamlit/config.toml') for name in names)
        assert any(name.endswith('/.gitignore') for name in names)
        assert not any(name.endswith('/secrets.toml') or name.endswith('/.env') for name in names)
