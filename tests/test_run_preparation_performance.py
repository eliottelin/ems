"""Independent comparison retrieval overlaps soil without moving UI callbacks."""
from datetime import date
from threading import Event, get_ident

import pandas as pd
import requests
import pytest

from data_blocks import DataBlock
from run_preparation import prepare_sources


START, END = date(2023, 1, 1), date(2023, 1, 31)
BLOCKS = {
    'open_meteo': {'role': 'forcing'},
    'soilgrids_wcs': {'role': 'soil_properties'},
    'hwsd': {'role': 'fallback'},
    'terraclimate': {'role': 'validation'},
}


def soil_block(key):
    return DataBlock(key, pd.DataFrame({'top_cm': [0], 'bottom_cm': [200]}), 'static', {}, {})


def weather_block():
    return DataBlock('open_meteo', pd.DataFrame({'date': [str(START)]}), 'daily', {}, {})


def comparison_block():
    return DataBlock('terraclimate', pd.DataFrame({'date': [str(START)], 'period_end': [str(END)]}),
                     'monthly', {}, {})


def test_comparisons_overlap_soil_and_keep_progress_on_the_calling_thread():
    soil_started, comparison_started = Event(), Event()
    caller = get_ident()
    fetch_threads, progress_threads, checked_threads = {}, [], []

    def fetch(key):
        fetch_threads[key] = get_ident()
        if key == 'open_meteo':
            return weather_block()
        if key == 'terraclimate':
            comparison_started.set()
            assert soil_started.wait(2), 'Comparison must overlap soil retrieval'
            return comparison_block()
        if key == 'soilgrids_wcs':
            soil_started.set()
            assert comparison_started.wait(2), 'Soil must not finish before comparisons start'
            return soil_block(key)
        raise AssertionError('A successful primary profile must not load HWSD')

    result = prepare_sources(BLOCKS, ['water_balance'], START, END, fetch,
                             lambda frame: checked_threads.append(get_ident()),
                             lambda *args: progress_threads.append(get_ident()))

    assert not result['errors']
    assert result['soil_source'] == 'soilgrids_wcs'
    assert set(result['loaded']) == {'open_meteo', 'soilgrids_wcs', 'terraclimate'}
    assert fetch_threads['open_meteo'] == fetch_threads['soilgrids_wcs'] == caller
    assert fetch_threads['terraclimate'] != caller
    assert checked_threads == [caller]
    assert progress_threads and set(progress_threads) == {caller}


def test_weather_failure_does_not_start_soil_or_comparison_work():
    calls, progress_calls = [], []

    def fetch(key):
        calls.append(key)
        raise requests.Timeout('Weather unavailable')

    result = prepare_sources(BLOCKS, ['water_balance'], START, END, fetch,
                             lambda frame: None, lambda *args: progress_calls.append(args))

    assert calls == ['open_meteo']
    assert not progress_calls
    assert result['loaded'] == {}
    assert result['missing']['open_meteo']['status'] == 'Connection failed'


def test_ancillary_failure_does_not_interrupt_ordered_soil_fallback():
    comparison_started = Event()
    soil_calls = []
    caller = get_ident()

    def fetch(key):
        if key == 'open_meteo':
            return weather_block()
        if key == 'terraclimate':
            comparison_started.set()
            raise requests.Timeout('Comparison unavailable')
        soil_calls.append(key)
        assert get_ident() == caller
        assert comparison_started.wait(2), 'Comparison must already be running'
        if key == 'soilgrids_wcs':
            raise requests.Timeout('Primary soil unavailable')
        return soil_block(key)

    result = prepare_sources(BLOCKS, ['water_balance'], START, END, fetch,
                             lambda frame: None, lambda *args: None)

    assert soil_calls == ['soilgrids_wcs', 'hwsd']
    assert result['soil_source'] == 'hwsd'
    assert set(result['loaded']) == {'open_meteo', 'hwsd'}
    assert set(result['errors']) == {'soilgrids_wcs', 'terraclimate'}
    assert result['issues']['terraclimate']['status'] == 'Connection failed'
    assert 'soilgrids_wcs' not in result['missing'], 'A usable fallback must resolve the soil requirement'


def test_fast_run_never_starts_optional_comparisons():
    calls=[]

    def fetch(key):
        calls.append(key)
        if key=='open_meteo':
            return weather_block()
        if key=='soilgrids_wcs':
            return soil_block(key)
        raise AssertionError('Optional comparisons must not delay a fast run')

    result=prepare_sources(BLOCKS,['water_balance'],START,END,fetch,
        lambda frame:None,lambda *args:None,defer_comparisons=True)
    assert calls==['open_meteo','soilgrids_wcs']
    assert result['missing']=={}


def test_fast_run_fallback_finishes_without_searching_failed_primary():
    def fetch(key):
        if key=='open_meteo':
            return weather_block()
        if key=='soilgrids_wcs':
            raise requests.Timeout('REST exceeded foreground budget')
        return soil_block(key)

    result=prepare_sources(BLOCKS,['water_balance'],START,END,fetch,
        lambda frame:None,lambda *args:None,defer_comparisons=True)
    assert result['soil_source']=='hwsd'
    assert result['missing']=={}
    assert result['errors']['soilgrids_wcs'], 'Keep source failure in diagnostics'


@pytest.mark.parametrize('preference,expected',[
    ('soilgrids',['soilgrids_wcs','hwsd']),
    ('hwsd',['hwsd','soilgrids_wcs']),
])
def test_selected_source_priority_tries_both_real_sources_before_assumptions(preference,expected):
    calls=[]

    def fetch(key):
        if key=='open_meteo':
            return weather_block()
        calls.append(key)
        raise requests.Timeout('No usable source response')

    result=prepare_sources(BLOCKS,['water_balance'],START,END,fetch,lambda frame:None,
        lambda *args:None,defer_comparisons=True,soil_preference=preference)
    assert calls==expected
    assert result['soil'] is None
    assert [row['source_id'] for row in result['soil_attempts']]==expected
    assert [row['status'] for row in result['soil_attempts']]==['failed','failed']
    assert set(result['missing'])==set(expected)


def test_fast_hwsd_success_avoids_all_soilgrids_network_work():
    calls=[]

    def fetch(key):
        calls.append(key)
        return weather_block() if key=='open_meteo' else soil_block(key)

    result=prepare_sources(BLOCKS,['water_balance'],START,END,fetch,lambda frame:None,
        lambda *args:None,defer_comparisons=True,soil_preference='hwsd')
    assert calls==['open_meteo','hwsd']
    assert result['soil_source']=='hwsd'
    assert [row['status'] for row in result['soil_attempts']]==['selected','not_needed']


def test_unusable_model_depth_falls_through_to_the_other_real_profile():
    calls=[]

    def fetch(key):
        if key=='open_meteo':
            return weather_block()
        calls.append(key)
        block=soil_block(key)
        if key=='hwsd':
            block.frame['bottom_cm']=100
        return block

    def check(frame):
        if frame.bottom_cm.max()<200:
            raise ValueError('Source does not cover the modeled 200 cm depth')

    result=prepare_sources(BLOCKS,['water_balance'],START,END,fetch,check,
        lambda *args:None,defer_comparisons=True,soil_preference='hwsd')
    assert calls==['hwsd','soilgrids_wcs']
    assert result['soil_source']=='soilgrids_wcs'
    assert result['missing']=={}
    assert result['soil_attempts'][0]['status']=='failed'


def test_invalid_soil_preference_is_rejected_before_fetching():
    with pytest.raises(ValueError,match='preferred soil source'):
        prepare_sources(BLOCKS,[],START,END,lambda key:pytest.fail('No fetch expected'),
            lambda frame:None,lambda *args:None,soil_preference='unknown')


def test_preset_gate_requires_every_configured_soil_source_to_fail():
    from run_recovery import soil_recovery_state
    recovery={'soil_sources':['soilgrids_wcs','hwsd'],'loaded':{'open_meteo':weather_block()},
        'soil_attempts':[{'source_id':'soilgrids_wcs','status':'failed'},
                         {'source_id':'hwsd','status':'not_attempted'}]}
    assert soil_recovery_state(recovery)==(True,False)
    recovery['soil_attempts'][1]['status']='failed'
    assert soil_recovery_state(recovery)==(True,True)
    recovery['loaded']['hwsd']=soil_block('hwsd')
    assert soil_recovery_state(recovery)[0] is False


def test_weather_failure_does_not_claim_that_soil_sources_failed():
    result=prepare_sources(BLOCKS,['water_balance'],START,END,
        lambda key:(_ for _ in ()).throw(requests.Timeout('Weather unavailable')),
        lambda frame:None,lambda *args:None,defer_comparisons=True,soil_preference='hwsd')
    assert [row['status'] for row in result['soil_attempts']]==['not_attempted','not_attempted']
    assert set(result['missing'])=={'open_meteo'}
