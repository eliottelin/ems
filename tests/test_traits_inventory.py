"""Invalid optional TRY assets leave builder exports and presets available."""
import io
import json
from zipfile import ZipFile

import pytest

from studio_builder import generator, local_data


@pytest.mark.parametrize('source_state', ['missing', 'multiple', 'corrupt', 'unreadable'])
def test_invalid_optional_try_source_has_an_explicit_inventory_message(tmp_path, monkeypatch, source_state):
    if source_state != 'missing':
        (tmp_path / 'TRY_Categorical_Traits_Lookup_Table_one.xlsx').write_bytes(b'Invalid XLSX source')
    if source_state == 'multiple':
        (tmp_path / 'TRY_Categorical_Traits_Lookup_Table_two.xlsx').write_bytes(b'Another source')
    if source_state == 'unreadable':
        def unreadable(path):
            raise PermissionError('Source is not readable')
        monkeypatch.setattr(local_data, 'inspect_workbook', unreadable)
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    inventory = local_data.try_inventory(tmp_path)
    assert inventory['available'] is False
    expected = {'missing': 'Add your TRY workbook', 'multiple': 'More than one TRY workbook',
                'corrupt': 'BadZipFile', 'unreadable': 'Source is not readable'}[source_state]
    assert expected in inventory['message']
    assert before == {path.name: path.read_bytes() for path in tmp_path.iterdir()}

    # Exercise the generator's real unavailable-source branch, without rebinding
    # the inventory function's default argument to the repository's dataset.
    monkeypatch.setattr(generator, 'try_inventory', lambda: local_data.try_inventory(tmp_path))
    def unexpected_assets():
        raise AssertionError('Unavailable TRY data must not be bundled')
    monkeypatch.setattr(generator, 'try_assets', unexpected_assets)
    payload = generator.make_project_zip({'project_name': 'Preset-only plants', 'models': ['vegetation']})
    with ZipFile(io.BytesIO(payload)) as archive:
        config_path = next(name for name in archive.namelist() if name.endswith('/model_config.json'))
        config = json.loads(archive.read(config_path))
        assert config['local_try']['available'] is False
        assert config['models'] == ['vegetation']
        assert any(name.endswith('/parameters.py') for name in archive.namelist())
        assert not any(name.endswith('.xlsx') for name in archive.namelist())
