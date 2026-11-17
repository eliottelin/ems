"""Long soil lookups report useful progress and stop unnecessary map work."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from itertools import count
from threading import Event, get_ident

import pandas as pd
import pytest
import requests

from data_blocks import DataRequest, NoMatchingData, load_block
from data_blocks import soil


@pytest.fixture(autouse=True)
def isolated_cooldown(monkeypatch):
    monkeypatch.setattr(soil, '_REST_COOLDOWNS', {})


def request(tmp_path, callback):
    return DataRequest(30., -96., date(2023, 1, 1), date(2023, 1, 7), tmp_path,
                       {'_source_progress': callback, 'soilgrids_use_rest': True, 'soilgrids_map_recovery': True})


def value(_request, prop, top, bottom):
    physical = {'sand': 40., 'silt': 35., 'clay': 25., 'soc': 12., 'bdod': 1.35, 'cfvo': 10.}
    return top, bottom, soil.PROPERTIES[prop][0], physical[prop]


def values(req):
    return [value(req, prop, top, bottom) for top, bottom in soil.DEPTHS for prop in soil.PROPERTIES]


def unavailable(*args, **kwargs):
    raise requests.Timeout('REST temporarily unavailable')


def test_rest_reports_first_lookup_estimate_and_validated_completion(tmp_path, monkeypatch):
    updates = []
    caller = get_ident()

    def progress(fraction, message):
        assert get_ident() == caller
        updates.append((fraction, message))

    req = request(tmp_path, progress)
    monkeypatch.setattr(soil, '_rest_profile', lambda request: values(request))
    block = load_block('soilgrids_wcs', req)

    assert len(block.frame) == 6
    assert updates[0][0] == 0.
    assert 'up to 20s' in updates[0][1]
    assert updates[-1][0] == 1.
    assert 'six depths checked' in updates[-1][1]


def test_map_backup_reports_values_and_remaining_time_on_caller_thread(tmp_path, monkeypatch):
    updates = []
    caller = get_ident()
    clock = count(100.)

    def progress(fraction, message):
        assert get_ident() == caller, 'UI callbacks must never run in map workers'
        updates.append((fraction, message))

    monkeypatch.setattr(soil, '_rest_profile', unavailable)
    monkeypatch.setattr(soil, 'point_value', value)
    monkeypatch.setattr(soil, 'monotonic', lambda: next(clock))
    block = load_block('soilgrids_wcs', request(tmp_path, progress))

    assert 'WMS' in block.provenance['method']
    assert any('WMS numeric map values' in message for _, message in updates)
    value_updates = [(fraction, message) for fraction, message in updates if 'of 36 values' in message]
    assert len(value_updates) == 36
    assert '1 of 36 values' in value_updates[0][1]
    assert '36 of 36 values' in value_updates[-1][1]
    assert any('about ' in message and 'remaining' in message for _, message in value_updates)
    assert [fraction for fraction, _ in updates] == sorted(fraction for fraction, _ in updates)
    assert updates[-1][0] == 1.


def test_invalid_rest_profile_is_not_reported_ready_before_map_validation(tmp_path, monkeypatch):
    updates = []
    req = request(tmp_path, lambda fraction, message: updates.append((fraction, message)))
    invalid = [(top, bottom, key, 20. if key == 'sand_pct' else amount)
               for top, bottom, key, amount in values(req)]
    monkeypatch.setattr(soil, '_rest_profile', lambda request: invalid)
    monkeypatch.setattr(soil, 'point_value', value)

    block = load_block('soilgrids_wcs', req)

    assert 'WMS' in block.provenance['method']
    assert sum(fraction == 1. for fraction, _ in updates) == 1
    assert updates[-1][0] == 1.


def test_failed_routes_keep_progress_below_ready_and_explain_fallbacks(tmp_path, monkeypatch):
    updates = []

    def no_data(*args, **kwargs):
        raise NoMatchingData('No mapped soil at this cell')

    monkeypatch.setattr(soil, '_rest_profile', no_data)
    monkeypatch.setattr(soil, 'point_value', no_data)
    monkeypatch.setattr(soil, 'wcs_value', no_data)
    with pytest.raises(NoMatchingData) as caught:
        load_block('soilgrids_wcs', request(tmp_path, lambda fraction, message: updates.append((fraction, message))))

    assert len(caught.value.details['attempts']) == 3
    assert all(fraction < 1. for fraction, _ in updates)
    assert any('WCS map backup' in message for _, message in updates)
    assert 'all enabled routes checked' in updates[-1][1]


def test_map_failure_cancels_queued_requests_instead_of_fetching_other_depths(tmp_path, monkeypatch):
    release = Event()
    cancelled = []
    calls = []

    class ObservedExecutor(ThreadPoolExecutor):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.futures = []

        def submit(self, *args, **kwargs):
            future = super().submit(*args, **kwargs)
            self.futures.append(future)
            return future

        def shutdown(self, wait=True, *, cancel_futures=False):
            cancelled.extend(future.cancelled() for future in self.futures)
            release.set()
            super().shutdown(wait=wait, cancel_futures=cancel_futures)

    def sample(req, prop, top, bottom):
        calls.append((prop, top, bottom))
        if (prop, top, bottom) == ('sand', 0, 5):
            return value(req, prop, top, bottom)
        if (prop, top, bottom) == ('clay', 0, 5):
            raise NoMatchingData('One invalid property makes this route unusable')
        assert release.wait(3.), 'Queued requests were not cancelled promptly'
        return value(req, prop, top, bottom)

    monkeypatch.setattr(soil, 'ThreadPoolExecutor', ObservedExecutor)
    with pytest.raises(NoMatchingData):
        soil._map_profile(request(tmp_path, None), sample)

    assert any(cancelled)
    assert len(calls) < 36


def test_local_hwsd_reports_loading_and_checked_completion(tmp_path):
    folder = tmp_path / 'hwsd'
    folder.mkdir()
    pd.DataFrame([{'HWSD2_SMU_ID': 42, 'SEQUENCE': 1, 'TOPDEP': 0, 'BOTDEP': 200,
                   'SAND': 40, 'SILT': 35, 'CLAY': 25, 'ORG_CARBON': 1.2,
                   'BULK': 1.35, 'COARSE': 10}]).to_csv(folder / 'HWSD2_LAYERS.csv', index=False)
    updates = []
    req = request(tmp_path, lambda fraction, message: updates.append((fraction, message)))
    req.options['hwsd_unit_id'] = 42

    block = load_block('hwsd', req)

    assert len(block.frame) == 1
    assert updates[0][0] == 0.
    assert 'local HWSD' in updates[0][1]
    assert updates[-1][0] == 1.
    assert 'checks passed' in updates[-1][1]


def test_waiting_rest_heartbeat_runs_on_caller_and_uses_existing_callback(tmp_path, monkeypatch):
    release = Event()
    caller = get_ident()
    updates = []
    def retrieve(req):
        assert release.wait(3.), 'REST heartbeat was not emitted'
        return values(req)
    def report(fraction, message):
        assert get_ident() == caller
        updates.append((fraction, message))
        if 'Waiting for SoilGrids REST' in message:
            release.set()
    monkeypatch.setattr(soil, '_rest_profile', retrieve)
    req = request(tmp_path, report)
    req.options['soilgrids_map_recovery'] = False
    try:
        block = load_block('soilgrids_wcs', req)
        assert len(block.frame) == 6
        assert any('Waiting for SoilGrids REST' in message for _, message in updates)
        assert updates[-1][0] == 1.
    finally:
        release.set()
