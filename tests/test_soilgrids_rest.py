"""Automatic SoilGrids REST recovery preserves source/profile checks."""
from copy import deepcopy
from collections import deque
from datetime import date
from threading import Event
from time import monotonic as actual_monotonic

import pandas as pd
import pytest
import requests

from data_blocks import DataBlock, DataRequest, NoMatchingData, load_block
from data_blocks import soil
from data_status import source_issue
from run_preparation import prepare_sources


@pytest.fixture(autouse=True)
def isolated_rest_budget(monkeypatch):
    # Each case has its own budget, including when the suite runs in one process.
    monkeypatch.setattr(soil, '_REST_CALLS', deque())
    monkeypatch.setattr(soil, '_REST_COOLDOWNS', {})


def request(tmp_path, **options):
    return DataRequest(30., -96., date(2023, 1, 1), date(2023, 1, 1), tmp_path,
                       {'soilgrids_use_rest': True, 'soilgrids_map_recovery': True, **options})


def rest_payload():
    raw = {'sand': 400, 'silt': 350, 'clay': 250, 'soc': 120, 'bdod': 135, 'cfvo': 100}
    units = {'sand': 'g/kg', 'silt': 'g/kg', 'clay': 'g/kg', 'soc': 'dg/kg',
             'bdod': 'cg/cm³', 'cfvo': 'cm³/dm³'}
    layers = []
    for prop, (_, factor) in soil.PROPERTIES.items():
        depths = []
        for index, (top, bottom) in enumerate(soil.DEPTHS):
            depths.append({'label': f'{top}-{bottom}cm',
                           'range': {'top_depth': top, 'bottom_depth': bottom, 'unit_depth': 'cm'},
                           'values': {'mean': raw[prop] - index * 10 if prop == 'soc' else raw[prop]}})
        layers.append({'name': prop,
                       'unit_measure': {'mapped_units': units[prop], 'target_units': '', 'd_factor': factor},
                       'depths': list(reversed(depths))})
    return {'type': 'Feature', 'geometry': {'type': 'Point', 'coordinates': [-96., 30.]},
            'properties': {'layers': list(reversed(layers))}}


def install_rest_response(monkeypatch, payload):
    calls = []

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return deepcopy(payload)

    def get(url, params, timeout):
        calls.append((url, params, timeout))
        return Response()

    monkeypatch.setattr(soil.requests, 'get', get)
    return calls


def mapped_value(_request, prop, top, bottom, **kwargs):
    raw = {'sand': 400, 'silt': 350, 'clay': 250, 'soc': 120, 'bdod': 135, 'cfvo': 100}
    name, factor = soil.PROPERTIES[prop]
    return top, bottom, name, raw[prop] / factor


def unexpected_request(message):
    raise AssertionError(message)


def test_rest_rate_budget_limits_requests_and_expires_after_one_minute(monkeypatch):
    clock = [100.]
    monkeypatch.setattr(soil, 'monotonic', lambda: clock[0])

    for _ in range(5):
        soil._reserve_rest_call()
    with pytest.raises(requests.RequestException, match='5 calls/minute'):
        soil._reserve_rest_call()

    clock[0] = 159.99
    with pytest.raises(requests.RequestException):
        soil._reserve_rest_call()
    clock[0] = 160.
    soil._reserve_rest_call()
    assert list(soil._REST_CALLS) == [160.]


def test_exhausted_rest_budget_uses_maps_without_sending_another_rest_request(tmp_path, monkeypatch):
    monkeypatch.setattr(soil, 'monotonic', lambda: 100.)
    for _ in range(5):
        soil._reserve_rest_call()
    monkeypatch.setattr(soil.requests, 'get', lambda *args, **kwargs: unexpected_request('REST budget exhausted'))
    monkeypatch.setattr(soil, 'point_value', mapped_value)
    monkeypatch.setattr(soil, 'wcs_value', lambda *args, **kwargs: unexpected_request('WCS is unnecessary'))

    block = load_block('soilgrids_wcs', request(tmp_path))

    assert 'WMS' in block.provenance['method']
    assert block.provenance['attempts'][0]['exception'] == 'RequestException'
    assert '5 calls/minute' in block.provenance['attempts'][0]['message']


def test_rest_is_primary_and_batches_complete_profile_with_unit_conversions(tmp_path, monkeypatch):
    calls = install_rest_response(monkeypatch, rest_payload())

    def unexpected(*args, **kwargs):
        unexpected_request('A complete REST profile should not query map-service backups')

    monkeypatch.setattr(soil, 'point_value', unexpected)
    monkeypatch.setattr(soil, 'wcs_value', unexpected)
    block = load_block('soilgrids_wcs', request(tmp_path))

    assert len(calls) == 1
    url, params, timeout = calls[0]
    assert url == 'https://rest.isric.org/soilgrids/v2.0/properties/query'
    assert params['lat'] == 30. and params['lon'] == -96.
    assert set(params['property']) == set(soil.PROPERTIES)
    assert set(params['depth']) == {f'{top}-{bottom}cm' for top, bottom in soil.DEPTHS}
    assert params['value'] in ('mean', ['mean'])
    assert timeout
    assert block.id == 'soilgrids_wcs'
    assert block.frame.top_cm.tolist() == [0, 5, 15, 30, 60, 100]
    assert block.frame.bottom_cm.tolist() == [5, 15, 30, 60, 100, 200]
    assert block.frame.sand_pct.tolist() == [40.] * 6
    assert block.frame.silt_pct.tolist() == [35.] * 6
    assert block.frame.clay_pct.tolist() == [25.] * 6
    assert block.frame.soc_g_kg.tolist() == [12., 11., 10., 9., 8., 7.]
    assert block.frame.bulk_density_g_cm3.tolist() == [1.35] * 6
    assert block.frame.coarse_fragments_pct.tolist() == [10.] * 6
    assert block.units == soil.UNITS
    assert block.provenance['endpoint'] == url
    assert 'REST' in block.provenance['method']


@pytest.mark.parametrize('missing', ['property', 'depth', 'mean', 'null'])
def test_rest_rejects_incomplete_profiles_instead_of_filling_missing_values(tmp_path, monkeypatch, missing):
    payload = rest_payload()
    layers = payload['properties']['layers']
    if missing == 'property':
        layers.pop()
    elif missing == 'depth':
        layers[0]['depths'].pop()
    elif missing == 'mean':
        layers[0]['depths'][0]['values'].pop('mean')
    else:
        layers[0]['depths'][0]['values']['mean'] = None
    install_rest_response(monkeypatch, payload)

    with pytest.raises(NoMatchingData):
        soil._rest_profile(request(tmp_path))


@pytest.mark.parametrize('invalid', ['schema', 'units', 'factor', 'coordinates', 'depth_range', 'property_name'])
def test_rest_rejects_malformed_or_mismatched_source_metadata(tmp_path, monkeypatch, invalid):
    payload = rest_payload()
    layer = payload['properties']['layers'][0]
    if invalid == 'schema':
        payload = ['unexpected JSON root']
    elif invalid == 'units':
        layer['unit_measure']['mapped_units'] = 'kg/kg'
    elif invalid == 'factor':
        layer['unit_measure']['d_factor'] = 1
    elif invalid == 'coordinates':
        payload['geometry']['coordinates'] = [-100., 35.]
    elif invalid == 'property_name':
        layer['name'] = []
    else:
        layer['depths'][0]['range']['bottom_depth'] = 201
    install_rest_response(monkeypatch, payload)

    with pytest.raises(ValueError):
        soil._rest_profile(request(tmp_path))


def test_rest_invalid_json_recovers_through_numeric_point_queries(tmp_path, monkeypatch):
    calls = []

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            calls.append('REST')
            raise ValueError('Service returned HTML instead of JSON')

    monkeypatch.setattr(soil.requests, 'get', lambda *args, **kwargs: Response())

    def point(*args, **kwargs):
        calls.append('WMS')
        return mapped_value(*args, **kwargs)

    monkeypatch.setattr(soil, 'point_value', point)
    monkeypatch.setattr(soil, 'wcs_value', lambda *args, **kwargs: unexpected_request('WCS is unnecessary'))
    block = load_block('soilgrids_wcs', request(tmp_path))

    assert calls == ['REST'] + ['WMS'] * 36
    assert len(block.frame) == 6
    assert 'WMS' in block.provenance['method']


def test_complete_but_physically_invalid_rest_profile_recovers_through_wms(tmp_path, monkeypatch):
    payload = rest_payload()
    sand = next(layer for layer in payload['properties']['layers'] if layer['name'] == 'sand')
    for depth in sand['depths']:
        depth['values']['mean'] = 200  # Texture totals 80%, outside the accepted tolerance.
    calls = install_rest_response(monkeypatch, payload)
    point_calls = []

    def point(*args, **kwargs):
        point_calls.append(args[1:])
        return mapped_value(*args, **kwargs)

    monkeypatch.setattr(soil, 'point_value', point)
    monkeypatch.setattr(soil, 'wcs_value', lambda *args, **kwargs: unexpected_request('WCS is unnecessary'))
    block = load_block('soilgrids_wcs', request(tmp_path))

    assert len(calls) == 1 and len(point_calls) == 36
    assert block.frame.sand_pct.tolist() == [40.] * 6
    assert 'WMS' in block.provenance['method']


def test_rest_and_numeric_point_failures_recover_through_wcs(tmp_path, monkeypatch):
    calls = []

    def rest(*args, **kwargs):
        calls.append('REST')
        raise requests.Timeout('REST unavailable')

    def point(*args, **kwargs):
        calls.append('WMS')
        raise NoMatchingData('Point query returned no mapped soil')

    def wcs(*args, **kwargs):
        calls.append('WCS')
        return mapped_value(*args, **kwargs)

    monkeypatch.setattr(soil, '_rest_profile', rest)
    monkeypatch.setattr(soil, 'point_value', point)
    monkeypatch.setattr(soil, 'wcs_value', wcs)
    block = load_block('soilgrids_wcs', request(tmp_path))

    assert calls == ['REST', 'WMS'] + ['WCS'] * 36
    assert len(block.frame) == 6
    assert 'WCS' in block.provenance['method']


@pytest.mark.parametrize('connection_route', [None, 'REST', 'WMS', 'WCS'])
def test_all_route_failures_keep_coverage_distinct_from_connection_errors(tmp_path, monkeypatch, connection_route):
    def fail(route):
        def retrieve(*args, **kwargs):
            if route == connection_route:
                raise requests.Timeout(f'{route} timed out')
            raise NoMatchingData(f'{route} has no mapped soil value')
        return retrieve

    monkeypatch.setattr(soil, '_rest_profile', fail('REST'))
    monkeypatch.setattr(soil, 'point_value', fail('WMS'))
    monkeypatch.setattr(soil, 'wcs_value', fail('WCS'))

    with pytest.raises(ValueError) as caught:
        load_block('soilgrids_wcs', request(tmp_path))

    issue = source_issue(caught.value)
    assert issue['status'] == ('Connection failed' if connection_route else 'No matching records')
    assert len(issue['details']['attempts']) == 3
    assert issue['details']['latitude'] == 30.
    assert issue['details']['longitude'] == -96.


@pytest.mark.parametrize('soilgrids_available', [True, False])
def test_run_preparation_keeps_hwsd_below_soilgrids(tmp_path, monkeypatch, soilgrids_available):
    install_rest_response(monkeypatch, rest_payload())
    profile = load_block('soilgrids_wcs', request(tmp_path))
    calls = []

    def fetch(key):
        calls.append(key)
        if key == 'open_meteo':
            return DataBlock(key, pd.DataFrame({'date': ['2023-01-01']}), 'daily', {}, {})
        if key == 'soilgrids_wcs':
            if not soilgrids_available:
                raise requests.Timeout('All SoilGrids routes failed')
            return profile
        return DataBlock('hwsd', profile.frame, profile.resolution, profile.units, {})

    blocks = {'open_meteo': {'role': 'forcing'}, 'soilgrids_wcs': {'role': 'soil'},
              'hwsd': {'role': 'fallback'}}
    result = prepare_sources(blocks, ['soil_carbon'], date(2023, 1, 1), date(2023, 1, 1),
                             fetch, soil.validate_profile, lambda *args: None)

    assert calls == ['open_meteo', 'soilgrids_wcs'] + ([] if soilgrids_available else ['hwsd'])
    assert result['soil_source'] == ('soilgrids_wcs' if soilgrids_available else 'hwsd')
    assert result['soil'] is profile.frame


def test_explicit_rest_only_does_not_enter_expensive_map_recovery(tmp_path, monkeypatch):
    def timeout(_request):
        raise requests.Timeout('REST stalled')
    monkeypatch.setattr(soil, '_rest_profile', timeout)
    monkeypatch.setattr(soil, 'point_value', lambda *args: unexpected_request('Map recovery must be explicit'))
    monkeypatch.setattr(soil, 'wcs_value', lambda *args: unexpected_request('Map recovery must be explicit'))
    req = request(tmp_path, soilgrids_use_rest=True, soilgrids_map_recovery=False)
    with pytest.raises(ValueError) as caught:
        load_block('soilgrids_wcs', req)
    details = caught.value.details
    assert len(details['attempts']) == 1
    assert details['map_recovery_available'] is True
    assert details['attempts'][0]['seconds'] >= 0
    assert details['foreground_budget_seconds'] == 20.


def test_explicit_maps_only_preserves_checked_profile(tmp_path, monkeypatch):
    calls=[]
    monkeypatch.setattr(soil,'_rest_profile',lambda *args:unexpected_request('Paused REST must be opt-in'))
    def point(*args):
        calls.append('WMS')
        return mapped_value(*args)
    monkeypatch.setattr(soil,'point_value',point)
    monkeypatch.setattr(soil,'wcs_value',lambda *args:unexpected_request('Valid WMS profile needs no WCS'))
    req=DataRequest(30.,-96.,date(2023,1,1),date(2023,1,1),tmp_path,{'soilgrids_use_rest':False})
    block=load_block('soilgrids_wcs',req)
    assert calls==['WMS']*36
    assert len(block.frame)==6
    assert block.provenance['rest_requested'] is False
    assert block.provenance['map_recovery_requested'] is True
    assert block.provenance['attempts']==[]


def test_maps_only_numeric_wms_failure_recovers_through_wcs(tmp_path, monkeypatch):
    calls=[]
    monkeypatch.setattr(soil,'_rest_profile',lambda *args:unexpected_request('Paused REST must be opt-in'))
    def missing_point(*args):
        calls.append('WMS')
        raise NoMatchingData('No complete WMS value')
    def wcs(*args):
        calls.append('WCS')
        return mapped_value(*args)
    monkeypatch.setattr(soil,'point_value',missing_point)
    monkeypatch.setattr(soil,'wcs_value',wcs)
    req=DataRequest(30.,-96.,date(2023,1,1),date(2023,1,1),tmp_path,{'soilgrids_use_rest':False})
    block=load_block('soilgrids_wcs',req)
    assert calls==['WMS']+['WCS']*36
    assert 'WCS' in block.provenance['method']
    assert len(block.provenance['attempts'])==1
    assert block.provenance['attempts'][0]['exception']=='NoMatchingData'


def test_all_disabled_routes_fail_clearly_without_requests(tmp_path, monkeypatch):
    monkeypatch.setattr(soil,'_rest_profile',lambda *args:unexpected_request('Disabled REST'))
    monkeypatch.setattr(soil,'point_value',lambda *args:unexpected_request('Disabled WMS'))
    monkeypatch.setattr(soil,'wcs_value',lambda *args:unexpected_request('Disabled WCS'))
    with pytest.raises(ValueError,match='No SoilGrids retrieval route is enabled'):
        load_block('soilgrids_wcs',request(tmp_path,soilgrids_use_rest=False,soilgrids_map_recovery=False))


def test_foreground_deadline_does_not_wait_for_inflight_http(tmp_path, monkeypatch):
    release = Event()
    started = Event()
    def stalled(_request):
        started.set()
        assert release.wait(3.)
        return [mapped_value(_request, prop, top, bottom)
                for top, bottom in soil.DEPTHS for prop in soil.PROPERTIES]
    monkeypatch.setattr(soil, '_rest_profile', stalled)
    monkeypatch.setattr(soil, 'REST_BUDGET_SECONDS', .05)
    began = actual_monotonic()
    try:
        with pytest.raises(ValueError) as caught:
            load_block('soilgrids_wcs', request(tmp_path, soilgrids_map_recovery=False))
        assert started.is_set()
        assert actual_monotonic()-began < .5
        assert 'foreground budget' in caught.value.details['attempts'][0]['message']
    finally:
        release.set()


def test_transient_outage_cooldown_is_shared_and_retry_is_explicit(tmp_path, monkeypatch):
    calls = []
    def retrieve(req):
        calls.append(req.latitude)
        if len(calls) == 1:
            raise requests.Timeout('Temporary provider outage')
        return [mapped_value(req, prop, top, bottom)
                for top, bottom in soil.DEPTHS for prop in soil.PROPERTIES]
    monkeypatch.setattr(soil, '_rest_profile', retrieve)
    for latitude in (30., 31.):
        req = request(tmp_path, soilgrids_map_recovery=False)
        req.latitude = latitude
        with pytest.raises(ValueError):
            load_block('soilgrids_wcs', req)
    assert calls == [30.]
    recovered = load_block('soilgrids_wcs', request(tmp_path, soilgrids_map_recovery=False, soilgrids_retry=True))
    assert len(recovered.frame) == 6
    assert calls == [30., 30.]


def test_missing_cell_cooldown_does_not_hide_other_sites(tmp_path, monkeypatch):
    calls = []
    def retrieve(req):
        calls.append(req.latitude)
        if req.latitude == 30.:
            raise NoMatchingData('Unmapped cell')
        return [mapped_value(req, prop, top, bottom)
                for top, bottom in soil.DEPTHS for prop in soil.PROPERTIES]
    monkeypatch.setattr(soil, '_rest_profile', retrieve)
    for _ in range(2):
        with pytest.raises(NoMatchingData):
            load_block('soilgrids_wcs', request(tmp_path, soilgrids_map_recovery=False))
    req = request(tmp_path, soilgrids_map_recovery=False)
    req.latitude = 31.
    assert len(load_block('soilgrids_wcs', req).frame) == 6
    assert calls == [30., 31.]


def test_default_automatic_mode_selects_complete_rest_profile_without_map_calls(tmp_path, monkeypatch):
    calls=install_rest_response(monkeypatch,rest_payload())
    monkeypatch.setattr(soil,'point_value',lambda *args:unexpected_request('Valid REST needs no map requests'))
    req=DataRequest(30.,-96.,date(2023,1,1),date(2023,1,1),tmp_path)

    block=load_block('soilgrids_wcs',req)

    assert len(calls)==1
    assert block.provenance['rest_requested'] is True
    assert block.provenance['rest_status']=='selected'
    assert block.provenance['attempts']==[]


def test_service_outage_automatically_recovers_after_shared_cooldown(tmp_path, monkeypatch):
    clock=[100.]
    calls=[]
    monkeypatch.setattr(soil,'monotonic',lambda:clock[0])

    def retrieve(req):
        calls.append(req.latitude)
        if len(calls)==1:
            raise requests.Timeout('The REST service is temporarily unavailable')
        return [mapped_value(req,prop,top,bottom)
                for top,bottom in soil.DEPTHS for prop in soil.PROPERTIES]

    monkeypatch.setattr(soil,'_rest_profile',retrieve)
    monkeypatch.setattr(soil,'point_value',mapped_value)
    initial=DataRequest(30.,-96.,date(2023,1,1),date(2023,1,1),tmp_path)
    during_outage=DataRequest(31.,-96.,date(2023,1,1),date(2023,1,1),tmp_path)
    recovered_site=DataRequest(32.,-96.,date(2023,1,1),date(2023,1,1),tmp_path)

    fallback=load_block('soilgrids_wcs',initial)
    skipped=load_block('soilgrids_wcs',during_outage)
    assert calls==[30.], 'Other sites must not repeat a known REST outage wait'
    assert 'WMS' in fallback.provenance['method']
    assert fallback.provenance['rest_status']=='unavailable'
    assert fallback.provenance['attempts'][0]['attempted'] is True
    assert skipped.provenance['rest_status']=='cooldown'
    assert skipped.provenance['attempts'][0]['attempted'] is False
    assert skipped.provenance['attempts'][0]['cooldown_scope']=='service'
    assert skipped.provenance['attempts'][0]['retry_after_seconds']==300

    clock[0]+=soil.FAILURE_COOLDOWN_SECONDS
    restored=load_block('soilgrids_wcs',recovered_site)
    assert calls==[30.,32.], 'A later ordinary lookup must automatically try REST again'
    assert 'REST' in restored.provenance['method']
    assert restored.provenance['rest_status']=='selected'
    assert restored.provenance['attempts']==[]
    assert 'service' not in soil._REST_COOLDOWNS


def test_partial_rest_cell_does_not_open_global_service_circuit(tmp_path, monkeypatch):
    payload=rest_payload()
    density=next(layer for layer in payload['properties']['layers'] if layer['name']=='bdod')
    next(depth for depth in density['depths'] if depth['label']=='0-5cm')['values']['mean']=None
    install_rest_response(monkeypatch,payload)
    monkeypatch.setattr(soil,'point_value',mapped_value)
    first=load_block('soilgrids_wcs',request(tmp_path))
    assert first.provenance['rest_status']=='unavailable'
    assert 'service' not in soil._REST_COOLDOWNS
    assert (30.,-96.) in soil._REST_COOLDOWNS

    other=request(tmp_path)
    other.latitude=31.
    # The GeoJSON feature must match the second requested site exactly.
    complete=rest_payload()
    complete['geometry']['coordinates']=[-96.,31.]
    calls=install_rest_response(monkeypatch,complete)
    second=load_block('soilgrids_wcs',other)
    assert len(calls)==1
    assert 'REST' in second.provenance['method']
    assert second.provenance['rest_status']=='selected'


def test_rate_limit_skip_does_not_mark_rest_service_as_down(tmp_path, monkeypatch):
    monkeypatch.setattr(soil,'monotonic',lambda:100.)
    for _ in range(5):
        soil._reserve_rest_call()
    monkeypatch.setattr(soil,'point_value',mapped_value)
    monkeypatch.setattr(soil.requests,'get',lambda *args,**kwargs:unexpected_request('REST request budget exhausted'))
    block=load_block('soilgrids_wcs',request(tmp_path))
    attempt=block.provenance['attempts'][0]
    assert attempt['attempted'] is False
    assert attempt['cooldown_scope']=='rate_limit'
    assert attempt['retry_after_seconds']==60
    assert 'service' not in soil._REST_COOLDOWNS


def test_hwsd_table_decode_and_hash_are_reused_until_file_changes(tmp_path, monkeypatch):
    table = tmp_path/'table.csv'
    table.write_text('HWSD2_SMU_ID,SEQUENCE\n42,1\n')
    original_read = pd.read_csv
    calls = []
    def read(*args, **kwargs):
        calls.append(args[0])
        return original_read(*args, **kwargs)
    monkeypatch.setattr(pd, 'read_csv', read)
    frame, first_hash = soil.load_hwsd_table(table)
    frame.loc[0, 'HWSD2_SMU_ID'] = 999
    again, second_hash = soil.load_hwsd_table(table)
    assert again.HWSD2_SMU_ID.tolist() == [42]
    assert first_hash == second_hash
    assert len(calls) == 1
    table.write_text('HWSD2_SMU_ID,SEQUENCE\n123,1\n')
    changed, changed_hash = soil.load_hwsd_table(table)
    assert changed.HWSD2_SMU_ID.tolist() == [123]
    assert changed_hash != first_hash
    assert len(calls) == 2


def test_missing_hwsd_map_does_not_start_global_download_without_opt_in(tmp_path, monkeypatch):
    monkeypatch.setattr(soil, 'find_existing_hwsd_map', lambda *args: None)
    monkeypatch.setattr(soil, 'download_hwsd_map', lambda *args: unexpected_request('Global map download needs explicit opt-in'))
    with pytest.raises(FileNotFoundError, match='automatic map download'):
        soil.ensure_hwsd_map(tmp_path)
