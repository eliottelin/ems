"""The private builder runs and exports using only deployable source assets."""
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from zipfile import ZipFile

from studio_builder.bootstrap import source_revision
from studio_builder.local_data import hwsd_assets, try_assets


ROOT = Path(__file__).resolve().parents[1]


def test_gitignore_updates_invalidate_export_cache_but_secrets_do_not(tmp_path):
    package, template = tmp_path/'builder', tmp_path/'template'
    package.mkdir(); template.mkdir()
    (package/'generator.py').write_text('EXPORT = True\n')
    (template/'app.py').write_text('print("example")\n')
    initial = source_revision(package, template)
    (template/'.gitignore').write_text('.env*\n')
    with_ignore = source_revision(package, template)
    assert with_ignore != initial
    (template/'.gitignore').write_text('.env*\n.ems-cache/\n')
    assert source_revision(package, template) != with_ignore
    final = source_revision(package, template)
    (template/'.streamlit').mkdir()
    (template/'.streamlit/secrets.toml').write_text('test_only = "excluded"\n')
    assert source_revision(package, template) == final


def test_builder_runs_and_exports_without_private_mdb_or_laptop_paths(tmp_path):
    repository = tmp_path/'ems'
    repository.mkdir()
    for filename in ('builder_app.py', 'requirements.txt'):
        shutil.copy2(ROOT/filename, repository/filename)
    for folder in ('studio_builder', 'template_generated_app'):
        shutil.copytree(ROOT/folder, repository/folder,
            ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.ems-cache', '.pytest_cache'))
    # These are the supplied deployable assets. The original Access database and
    # local Downloads maps are deliberately absent from this clean repository.
    for asset in hwsd_assets():
        target = repository/'data'/'hwsd'/asset.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(asset, target)
    for asset in try_assets():
        target = repository/asset.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(asset, target)
    script = '''
import io,json
from pathlib import Path
from zipfile import ZipFile
from streamlit.testing.v1 import AppTest
from studio_builder.local_data import try_inventory,hwsd_assets
from studio_builder.generator import make_project_zip
root=Path.cwd()
assert not (root/'data/private').exists()
assert not list((root/'data').rglob('*.mdb'))
app=AppTest.from_file(root/'builder_app.py',default_timeout=30).run()
assert not app.exception,[item.message for item in app.exception]
assert len(app.get('download_button'))==1
inventory=try_inventory()
assert inventory['available']
assert hwsd_assets()
payload=make_project_zip({'project_name':'Cloud model','models':['water_balance','vegetation','soil_carbon']})
with ZipFile(io.BytesIO(payload)) as archive:
    assert archive.testzip() is None
    names=archive.namelist()
    assert 'cloud-model/.gitignore' in names
    assert 'cloud-model/.streamlit/config.toml' in names
    assert 'cloud-model/data/hwsd/HWSD2_LAYERS.csv.gz' in names
    assert any(name.endswith('.xlsx') for name in names)
    assert any(name.endswith('Terms_of_service_of_the_TRY_File_Archive.pdf') for name in names)
    assert not any('/private/' in name or name.endswith(('.mdb','.bil','.hdr')) for name in names)
    assert not any('secrets.toml' in name or '/.ems-cache/' in name for name in names)
    config=json.loads(archive.read('cloud-model/model_config.json'))
    assert config['local_try']['available'] and config['local_hwsd']['records']>0
    assert '/Users/' not in json.dumps(config)
print('Clean builder generated a complete portable project')
'''
    result = subprocess.run([sys.executable, '-c', script], cwd=repository,
        env={**os.environ, 'PYTHONPATH': str(repository)}, capture_output=True,
        text=True, timeout=60)
    assert result.returncode == 0, result.stdout+result.stderr
