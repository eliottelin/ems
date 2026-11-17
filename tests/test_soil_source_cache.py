"""Static soil retrieval is reusable across periods, with local input invalidation."""
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

import data_blocks
import source_cache
from data_blocks import DataBlock, DataRequest


@pytest.fixture(autouse=True)
def isolated_soil_cache():
    source_cache._cached_soil.clear()
    yield
    source_cache._cached_soil.clear()


def request(tmp_path, start=date(2023, 1, 1), end=date(2023, 1, 7), **options):
    return DataRequest(30., -96., start, end, tmp_path, options)


def soil_frame():
    return pd.DataFrame({'top_cm': [0.], 'bottom_cm': [200.], 'sand_pct': [40.],
        'silt_pct': [35.], 'clay_pct': [25.], 'soc_g_kg': [12.],
        'bulk_density_g_cm3': [1.35], 'coarse_fragments_pct': [10.]})


def install_loader(monkeypatch):
    calls = []

    def load(key, requested):
        calls.append((key, requested))
        if requested.options.get('_source_progress'):
            requested.options['_source_progress'](1., 'Soil profile ready')
        return DataBlock(key, soil_frame(),
                         'static depth profile', {'sand_pct': '%'},
                         {'loaded_at_utc': 'original retrieval', 'nested': {'untouched': True}})

    monkeypatch.setattr(data_blocks, 'load_block', load)
    return calls


def test_same_site_reuses_profile_across_dates_and_unrelated_settings(tmp_path, monkeypatch):
    calls = install_loader(monkeypatch)
    first = request(tmp_path, modis_quality='recommended', source_proxies={})
    block = source_cache.load_cached_soil('soilgrids_wcs', first, (), 'rest-first')
    # A local comparison/trait file and different simulation dates do not change soil.
    later = request(tmp_path, date(2024, 1, 1), date(2024, 1, 31), modis_quality='strict')
    reused = source_cache.load_cached_soil('soilgrids_wcs', later,
        (('try/traits.xlsx', 123, 456),), 'rest-first')

    assert len(calls) == 1
    assert calls[0][1].options == {'_progress': None, '_source_progress': None}
    assert block.provenance['request']['start'] == '2023-01-01'
    assert reused.provenance['request']['start'] == '2024-01-01'
    assert reused.provenance['request']['end'] == '2024-01-31'
    assert reused.provenance['loaded_at_utc'] == 'original retrieval'


def test_cached_profiles_are_detached_from_returned_mutable_values(tmp_path, monkeypatch):
    calls = install_loader(monkeypatch)
    original = source_cache.load_cached_soil('soilgrids_wcs', request(tmp_path), (), 'rest-first')
    original.frame.loc[0, 'sand_pct'] = 0.
    original.units['sand_pct'] = 'changed'
    original.provenance['nested']['untouched'] = False
    original.provenance['request']['start'] = 'changed'

    reused = source_cache.load_cached_soil('soilgrids_wcs', request(tmp_path), (), 'rest-first')
    assert len(calls) == 1
    assert reused.frame.sand_pct.iloc[0] == 40.
    assert reused.units['sand_pct'] == '%'
    assert reused.provenance['nested']['untouched'] is True
    assert reused.provenance['request']['start'] == '2023-01-01'


def test_source_site_and_retrieval_policy_have_separate_cache_entries(tmp_path, monkeypatch):
    calls = install_loader(monkeypatch)
    original = request(tmp_path)
    moved = request(tmp_path)
    moved.latitude = 30.01
    for requested, policy in ((original, 'rest-first'), (moved, 'rest-first'),
                              (original, 'updated-policy')):
        source_cache.load_cached_soil('soilgrids_wcs', requested, (), policy)
    assert len(calls) == 3


def test_progress_callbacks_are_only_run_during_actual_retrieval(tmp_path, monkeypatch):
    calls = install_loader(monkeypatch)
    progress = []
    source_cache.load_cached_soil('soilgrids_wcs', request(tmp_path), (), 'rest-first',
                                  _source_progress=lambda *args: progress.append(args))
    source_cache.load_cached_soil('soilgrids_wcs', request(tmp_path), (), 'rest-first',
                                  _source_progress=lambda *args: progress.append(('cache hit',)))
    assert len(calls) == 1
    assert progress == [(1., 'Soil profile ready')]


def test_external_progress_elements_are_not_replayed_into_changed_layout(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    calls = []

    def load(key, requested):
        calls.append(key)
        requested.options['_progress'](50, 100)
        requested.options['_source_progress'](1., 'Soil profile ready')
        return DataBlock(key, soil_frame(), 'static profile', {}, {})

    monkeypatch.setattr(data_blocks, 'load_block', load)
    script = f'''
from datetime import date
from pathlib import Path
import streamlit as st
from data_blocks import DataRequest
from source_cache import load_cached_soil
if st.session_state.get('loaded'):
    st.caption('The layout changed before a cached load')
source_bar = st.progress(0., text='Waiting for soil')
download_bar = st.progress(0., text='Waiting for map')
load_cached_soil('hwsd', DataRequest(30., -96., date(2023,1,1), date(2023,1,7),
    Path({str(tmp_path)!r}), {{'hwsd_unit_id':42}}), (), 'progress-layout-test',
    _progress=lambda done,total: download_bar.progress(done/total, text='Map download'),
    _source_progress=lambda fraction,message: source_bar.progress(fraction, text=message))
st.session_state['loaded'] = True
'''
    app = AppTest.from_string(script).run()
    assert not app.exception
    assert [item.value for item in app.progress] == [100, 50]

    app.run()
    assert not app.exception
    assert calls == ['hwsd']
    assert [item.value for item in app.progress] == [0, 0]


def test_hwsd_invalidates_for_overrides_and_soil_files_only(tmp_path, monkeypatch):
    calls = install_loader(monkeypatch)
    base = request(tmp_path, hwsd_unit_id=42)
    table = (('hwsd/HWSD2_LAYERS.csv.gz', 100, 200),)
    source_cache.load_cached_soil('hwsd', base, table, 'rest-first')
    source_cache.load_cached_soil('hwsd', base,
        (*table, ('terraclimate/aet.csv', 777, 888)), 'rest-first')
    assert len(calls) == 1

    source_cache.load_cached_soil('hwsd', base,
        (('hwsd/HWSD2_LAYERS.csv.gz', 101, 200),), 'rest-first')
    source_cache.load_cached_soil('hwsd', request(tmp_path, hwsd_unit_id=43), table, 'rest-first')
    source_cache.load_cached_soil('hwsd', request(tmp_path, hwsd_unit_id=42, hwsd_sequence=2), table, 'rest-first')
    assert len(calls) == 4


def test_hwsd_external_table_updates_invalidate_the_profile(tmp_path, monkeypatch):
    calls = install_loader(monkeypatch)
    table = tmp_path / 'custom-soil.csv'
    table.write_text('first table')
    requested = request(tmp_path, hwsd_table=table, hwsd_unit_id=42)
    source_cache.load_cached_soil('hwsd', requested, (), 'rest-first')
    source_cache.load_cached_soil('hwsd', requested, (), 'rest-first')
    table.write_text('replacement soil table')
    source_cache.load_cached_soil('hwsd', requested, (), 'rest-first')
    assert len(calls) == 2


@pytest.mark.parametrize('path', ['data/hwsd/HWSD2_LAYERS.csv',
                                  'data/hwsd/HWSD2_LAYERS.csv.gz', 'data/hwsd/source.json'])
def test_hwsd_app_relative_soil_file_updates_invalidate_cache(tmp_path, monkeypatch, path):
    calls = install_loader(monkeypatch)
    requested = request(tmp_path, hwsd_unit_id=42)
    source_cache.load_cached_soil('hwsd', requested, ((path, 100, 200),), 'rest-first')
    source_cache.load_cached_soil('hwsd', requested, ((path, 100, 200),), 'rest-first')
    assert len(calls) == 1

    source_cache.load_cached_soil('hwsd', requested, ((path, 101, 200),), 'rest-first')
    assert len(calls) == 2


def test_hwsd_reused_external_map_metadata_is_part_of_cache_key(tmp_path, monkeypatch):
    calls = install_loader(monkeypatch)
    from data_blocks import soil
    raster, header = tmp_path / 'external.bil', tmp_path / 'external.hdr'
    raster.write_bytes(b'one')
    header.write_text('first header')
    monkeypatch.setattr(soil, 'find_existing_hwsd_map', lambda *args: (raster, header))
    source_cache.load_cached_soil('hwsd', request(tmp_path), (), 'rest-first')
    source_cache.load_cached_soil('hwsd', request(tmp_path), (), 'rest-first')
    header.write_text('replacement header')
    source_cache.load_cached_soil('hwsd', request(tmp_path), (), 'rest-first')
    assert len(calls) == 2


def test_non_soil_sources_cannot_use_date_independent_cache(tmp_path):
    with pytest.raises(ValueError, match='only supports'):
        source_cache.load_cached_soil('open_meteo', request(tmp_path), (), 'rest-first')


def cache_files(requested):
    return list((requested.data_dir.parent / '.ems-cache' / 'soil-profiles-v1').glob('*.json'))


def test_profile_survives_memory_cache_clear_and_updates_request_dates(tmp_path, monkeypatch):
    calls = install_loader(monkeypatch)
    first = request(tmp_path)
    fresh = source_cache.load_cached_soil('soilgrids_wcs', first)
    assert fresh.provenance['cache'] == {
        'hit': False, 'storage': 'source',
        'age_seconds': fresh.provenance['cache']['age_seconds'],
        'expires_after_seconds': 30 * 86400}
    source_cache._cached_soil.clear()  # A fresh server process has no memory entries.
    later = request(tmp_path, date(2024, 1, 1), date(2024, 1, 7))
    reused = source_cache.load_cached_soil('soilgrids_wcs', later)
    assert len(calls) == 1
    assert reused.provenance['cache']['storage'] == 'disk'
    assert reused.provenance['cache']['hit']
    assert reused.provenance['loaded_at_utc'] == 'original retrieval'
    assert reused.provenance['request']['start'] == '2024-01-01'
    pd.testing.assert_frame_equal(fresh.frame, reused.frame)
    assert source_cache.load_cached_soil('soilgrids_wcs', later).provenance['cache']['storage'] == 'memory'


def test_expired_disk_hit_does_not_extend_expiry_in_memory(tmp_path, monkeypatch):
    calls = install_loader(monkeypatch)
    clock = [10000000.]
    monkeypatch.setattr(source_cache, 'time', lambda: clock[0])
    requested = request(tmp_path)
    source_cache.load_cached_soil('soilgrids_wcs', requested)
    source_cache._cached_soil.clear()
    clock[0] += source_cache.CACHE_TTL_SECONDS - 1
    cached = source_cache.load_cached_soil('soilgrids_wcs', requested)
    assert cached.provenance['cache']['storage'] == 'disk'
    clock[0] += 2
    fresh = source_cache.load_cached_soil('soilgrids_wcs', requested)
    assert len(calls) == 2
    assert fresh.provenance['cache']['storage'] == 'source'


def test_corrupt_disk_content_requires_fresh_source_data(tmp_path, monkeypatch):
    import json
    calls = install_loader(monkeypatch)
    requested = request(tmp_path)
    source_cache.load_cached_soil('soilgrids_wcs', requested)
    source_cache._cached_soil.clear()
    # Change a numeric value without matching its checksum.
    path = next(path for path in cache_files(requested)
                if json.loads(path.read_text())['identity']['namespace'] == str(tmp_path))
    document = json.loads(path.read_text())
    document['block']['frame']['data'][0][2] = 90.
    path.write_text(json.dumps(document))
    block = source_cache.load_cached_soil('soilgrids_wcs', requested)
    assert len(calls) == 2
    assert block.frame.sand_pct.iloc[0] == 40.
    assert block.provenance['cache']['storage'] == 'source'


def test_invalid_source_profile_is_never_saved_or_cached(tmp_path, monkeypatch):
    calls = []
    def invalid(key, requested):
        calls.append(key)
        frame = soil_frame()
        frame.loc[0, 'bulk_density_g_cm3'] = -1.
        return DataBlock(key, frame, 'static depth profile', {}, {})
    monkeypatch.setattr(data_blocks, 'load_block', invalid)
    before = set(cache_files(request(tmp_path)))
    for _ in range(2):
        with pytest.raises(ValueError, match='negative'):
            source_cache.load_cached_soil('soilgrids_wcs', request(tmp_path))
    assert len(calls) == 2
    assert set(cache_files(request(tmp_path))) == before


def test_disk_write_failure_keeps_valid_retrieval_usable(tmp_path, monkeypatch):
    calls = install_loader(monkeypatch)
    def read_only(*args, **kwargs):
        raise PermissionError('read-only project')
    monkeypatch.setattr(source_cache, 'NamedTemporaryFile', read_only)
    block = source_cache.load_cached_soil('soilgrids_wcs', request(tmp_path))
    assert len(calls) == 1
    assert block.frame.sand_pct.iloc[0] == 40.


def test_recovery_permission_and_retry_reuse_success_but_reach_loader_on_miss(tmp_path, monkeypatch):
    calls = install_loader(monkeypatch)
    source_cache.load_cached_soil('soilgrids_wcs', request(tmp_path, soilgrids_retry=True))
    source_cache.load_cached_soil('soilgrids_wcs', request(tmp_path, soilgrids_retry=False))
    source_cache.load_cached_soil('soilgrids_wcs', request(tmp_path, soilgrids_map_recovery=True))
    source_cache.load_cached_soil('soilgrids_wcs', request(tmp_path, soilgrids_use_rest=True))
    source_cache.load_cached_soil('soilgrids_wcs', request(tmp_path, soilgrids_use_rest=False))
    assert len(calls) == 1
    source_cache.load_cached_soil('soilgrids_wcs', request(tmp_path, soilgrids_map_recovery=True,soilgrids_use_rest=True),
                                  source_policy='new-source-policy')
    assert len(calls) == 2
    assert calls[0][1].options['soilgrids_retry'] is True
    assert calls[1][1].options['soilgrids_map_recovery'] is True
    assert calls[1][1].options['soilgrids_use_rest'] is True


def test_app_and_nearby_local_signatures_reuse_same_hwsd_cache(tmp_path, monkeypatch):
    calls = install_loader(monkeypatch)
    folder = tmp_path / 'hwsd'
    folder.mkdir()
    table = folder / 'HWSD2_LAYERS.csv'
    table.write_text('soil source table signature')
    requested = request(tmp_path, hwsd_unit_id=42)
    nearby_signature = source_cache.soil_local_signature(tmp_path)
    app_signature = tuple(('data/' + path, modified, size)
                          for path, modified, size in nearby_signature)
    source_cache.load_cached_soil('hwsd', requested, nearby_signature)
    source_cache.load_cached_soil('hwsd', requested, app_signature)
    assert len(calls) == 1
