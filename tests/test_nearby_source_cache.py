"""Nearby results cache data without replaying progress into old page layouts."""
from datetime import date
from threading import get_ident

import pandas as pd
import pytest
import requests
from streamlit.testing.v1 import AppTest

import data_blocks
import nearby_cache
import nearby_data
import source_cache
from data_blocks import DataBlock, DataRequest


@pytest.fixture(autouse=True)
def isolated_caches():
    nearby_cache._cached_nearby.clear()
    source_cache._cached_soil.clear()
    yield
    nearby_cache._cached_nearby.clear()
    source_cache._cached_soil.clear()


def request(tmp_path, **options):
    return DataRequest(30., -96., date(2023, 1, 1), date(2023, 1, 31),
                       tmp_path / 'data', options)


def candidate():
    return {'latitude': 30.01, 'longitude': -96., 'distance_km': 1.112,
            'records': 6, 'checked_for': ['2023-01-01', '2023-01-31']}


def test_layout_change_reuses_candidates_without_replaying_external_progress(tmp_path, monkeypatch):
    calls = []

    def search(key, requested, progress=None):
        calls.append((key, requested))
        assert progress is not None
        progress(1, 2)
        return [candidate()]

    monkeypatch.setattr(nearby_data, 'nearby_data_options', search)
    script = f'''
from datetime import date
from pathlib import Path
from threading import get_ident
import streamlit as st
from data_blocks import DataRequest
from nearby_cache import load_cached_nearby
from nearby_data import nearby_data_options
if st.session_state.get('loaded'):
    st.caption('The page layout changed after the initial nearby search')
caller=get_ident()
with st.container(border=True):
    bar=st.progress(0.,text='Waiting for nearby search')
    def notify(done,total):
        assert get_ident()==caller
        bar.progress(done/total,text='Checked nearby candidate')
    found=load_cached_nearby('soilgrids_wcs',DataRequest(30.,-96.,date(2023,1,1),date(2023,1,31),
        Path({str(tmp_path / 'data')!r}),{{}}),_progress=notify,_search=nearby_data_options)
st.session_state['loaded']=True
st.write(found)
'''
    app = AppTest.from_string(script).run()
    assert not app.exception, [item.message for item in app.exception]
    assert [item.value for item in app.progress] == [50]

    app.run()
    assert not app.exception, [item.message for item in app.exception]
    assert len(calls) == 1, 'The second run should reuse the search result'
    assert [item.value for item in app.progress] == [0], 'Cached data must not replay an old progress element'
    assert any('page layout changed' in item.value for item in app.caption)


def test_progress_runs_on_caller_only_for_actual_searches(tmp_path):
    searches, notifications = [], []
    caller = get_ident()

    def search(key, requested, progress=None):
        searches.append((key, requested))
        assert get_ident() == caller
        assert progress is not None
        progress(1, 2)
        progress(2, 2)
        return [candidate()]

    def progress(done, total):
        assert get_ident() == caller
        notifications.append((done, total))

    def must_not_run(*args):
        raise AssertionError('A cache hit must not invoke a replacement callback')

    original = nearby_cache.load_cached_nearby('soilgrids_wcs', request(tmp_path),
        _progress=progress, _search=search)
    reused = nearby_cache.load_cached_nearby('soilgrids_wcs', request(tmp_path),
        _progress=must_not_run,
        _search=search)
    assert len(searches) == 1
    assert notifications == [(1, 2), (2, 2)]
    assert original == reused == [candidate()]


def test_failed_nearby_search_is_retried_and_only_success_is_cached(tmp_path):
    calls = []

    def search(key, requested, progress=None):
        calls.append(key)
        if len(calls) == 1:
            raise requests.Timeout('Temporary source outage')
        return [candidate()]

    with pytest.raises(requests.Timeout, match='Temporary source outage'):
        nearby_cache.load_cached_nearby('soilgrids_wcs', request(tmp_path), _search=search)
    found = nearby_cache.load_cached_nearby('soilgrids_wcs', request(tmp_path), _search=search)
    reused = nearby_cache.load_cached_nearby('soilgrids_wcs', request(tmp_path), _search=search)
    assert found == reused == [candidate()]
    assert calls == ['soilgrids_wcs', 'soilgrids_wcs']


def test_returned_candidates_cannot_mutate_cached_search_results(tmp_path):
    calls = []

    def search(key, requested, progress=None):
        calls.append(key)
        return [candidate()]

    first = nearby_cache.load_cached_nearby('soilgrids_wcs', request(tmp_path), _search=search)
    first[0]['latitude'] = 0.
    first[0]['checked_for'].append('changed')
    reused = nearby_cache.load_cached_nearby('soilgrids_wcs', request(tmp_path), _search=search)
    assert reused == [candidate()]
    assert calls == ['soilgrids_wcs']


def test_nested_soil_cache_progress_is_not_captured_by_nearby_cache(tmp_path, monkeypatch):
    searches, soil_loads = [], []

    def load(key, requested):
        soil_loads.append(key)
        requested.options['_progress'](50, 100)
        requested.options['_source_progress'](.75, 'Checked soil properties')
        frame = pd.DataFrame({'top_cm': [0.], 'bottom_cm': [200.], 'sand_pct': [40.],
            'silt_pct': [35.], 'clay_pct': [25.], 'soc_g_kg': [12.],
            'bulk_density_g_cm3': [1.35], 'coarse_fragments_pct': [10.]})
        return DataBlock(key, frame, 'static profile', {}, {})

    def search(key, requested, progress=None):
        searches.append(key)
        assert progress is not None
        source_cache.load_cached_soil('hwsd', requested,
            _progress=progress,
            _source_progress=lambda fraction, message: progress(round(fraction * 100), 100))
        return [candidate()]

    monkeypatch.setattr(data_blocks, 'load_block', load)
    monkeypatch.setattr(nearby_data, 'nearby_data_options', search)
    script = f'''
from datetime import date
from pathlib import Path
import streamlit as st
from data_blocks import DataRequest
from nearby_cache import load_cached_nearby
from nearby_data import nearby_data_options
if st.session_state.get('loaded'):
    with st.container(border=True):
        st.caption('A different layout now precedes the search')
with st.container(border=True):
    bar=st.progress(0.,text='Waiting for candidate profile')
    found=load_cached_nearby('hwsd',DataRequest(30.,-96.,date(2023,1,1),date(2023,1,31),
        Path({str(tmp_path / 'data')!r}),{{'hwsd_unit_id':42}}),
        _progress=lambda done,total:bar.progress(done/total,text='Candidate profile progress'),
        _search=nearby_data_options)
st.session_state['loaded']=True
st.write(found)
'''
    app = AppTest.from_string(script).run()
    assert not app.exception, [item.message for item in app.exception]
    assert [item.value for item in app.progress] == [75]

    app.run()
    assert not app.exception, [item.message for item in app.exception]
    assert searches == soil_loads == ['hwsd']
    assert [item.value for item in app.progress] == [0]
