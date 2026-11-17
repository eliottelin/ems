"""Geocoding, real request contracts, and actionable missing-data diagnostics."""
from datetime import date
from datetime import timedelta
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse
import pytest
from geocoding import search_places, place_label
from weather import HISTORICAL_MODEL, WeatherDataError, fetch_open_meteo, parse_daily, available_historical_period


class Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


def daily_payload(values=None):
    return {'daily':{'time':['2023-01-01','2023-01-02','2023-01-03'],
        'temperature_2m_mean':[20.,21.,22.], 'precipitation_sum':[0.,1.,2.],
        'et0_fao_evapotranspiration':values if values is not None else [2.,3.,4.]},
        'daily_units':{'temperature_2m_mean':'°C','precipitation_sum':'mm','et0_fao_evapotranspiration':'mm'}}


def test_geocoding_preserves_location_choices_and_does_not_invent_matches(monkeypatch):
    place={'id':1,'name':'College Station','admin1':'Texas','country':'United States','latitude':30.628,'longitude':-96.3344}
    def get(url, params, timeout):
        assert 'geocoding-api.open-meteo.com' in url
        assert params['name'] == 'College Station, Texas'
        return Response({'results':[place]})
    monkeypatch.setattr('geocoding.requests.get',get)
    assert search_places(' College Station, Texas ')[0] == place
    assert 'Texas' in place_label(place)
    assert '-96.3344' in place_label(place)
    monkeypatch.setattr('geocoding.requests.get',lambda *a,**k:Response({}))
    assert search_places('Missing place') == []
    with pytest.raises(ValueError,match='at least two'):
        search_places('x')


def test_historical_request_keeps_fixed_era5_source_and_all_dates(monkeypatch):
    def get(url, params, timeout):
        assert params['models'] == 'era5'
        assert params['daily'] == 'temperature_2m_mean,precipitation_sum,et0_fao_evapotranspiration'
        assert params['start_date'] == '2023-01-01' and params['end_date'] == '2023-01-03'
        return Response(daily_payload())
    monkeypatch.setattr('weather.requests.get',get)
    frame=fetch_open_meteo(30.628,-96.3344,3,date(2023,1,1))
    assert len(frame)==3 and frame.et0_mm.tolist()==[2.,3.,4.]
    assert frame.attrs['provider_model'] == HISTORICAL_MODEL


def test_all_missing_et0_reports_variable_and_dates_without_suggesting_truncation(monkeypatch):
    monkeypatch.setattr('weather.requests.get',lambda *a,**k:Response(daily_payload([None,None,None])))
    with pytest.raises(WeatherDataError) as caught:
        fetch_open_meteo(30.,-96.,3,date(2023,1,1))
    error=caught.value
    assert 'et0_fao_evapotranspiration' in str(error) and '2023-01-01' in str(error)
    assert error.diagnostics['missing_values']['et0_fao_evapotranspiration']['count']==3
    assert error.diagnostics['model']=='era5'
    assert parse_qs(urlparse(error.diagnostics['request_url']).query)['models']==['era5']
    assert 'suggested_end_date' not in error.diagnostics


def test_archive_release_delay_suggests_only_a_complete_prefix():
    with pytest.raises(WeatherDataError) as caught:
        parse_daily(daily_payload([2.,3.,None]),date(2023,1,1),date(2023,1,3),'provider')
    assert caught.value.diagnostics['suggested_end_date']=='2023-01-02'
    assert 'not filled or dropped' in str(caught.value)
    with pytest.raises(WeatherDataError) as caught:
        parse_daily(daily_payload([2.,None,4.]),date(2023,1,1),date(2023,1,3),'provider')
    assert 'suggested_end_date' not in caught.value.diagnostics


def test_incomplete_response_lengths_are_reported():
    payload=daily_payload()
    payload['daily']['precipitation_sum'].pop()
    with pytest.raises(WeatherDataError) as caught:
        parse_daily(payload,date(2023,1,1),date(2023,1,3),'provider')
    assert caught.value.diagnostics['received_lengths']['precipitation_sum']==2
    assert caught.value.diagnostics['expected_days']==3


def test_available_dates_are_probed_instead_of_assuming_five_day_release_lag(monkeypatch):
    calls=[]
    def get(url,params,timeout):
        calls.append(params)
        start,end=date.fromisoformat(params['start_date']),date.fromisoformat(params['end_date'])
        dates=[start+timedelta(days=i) for i in range((end-start).days+1)]
        valid=[day!=date(1940,1,1) and day<=date(2026,11,3) for day in dates]
        data={'time':[str(day) for day in dates]}
        data.update({key:[1. if complete else None for complete in valid]
                     for key in ('temperature_2m_mean','precipitation_sum','et0_fao_evapotranspiration')})
        return Response({'daily':data})
    monkeypatch.setattr('weather.requests.get',get)
    first,last=available_historical_period(30.,-96.,date(2026,11,9))
    assert first==date(1940,1,2) and last==date(2026,11,3)
    assert len(calls)==2 and all(call['models']=='era5' for call in calls)


def test_captured_live_response_reproduces_recent_archive_nulls():
    path=Path(__file__).resolve().parents[1]/'docs/verification/open_meteo_live_2026-11-09.json'
    capture=json.loads(path.read_text())
    probe=next(item for item in capture['probes'] if 'start_date=2026-10-28' in item['url'] and parse_qs(urlparse(item['url']).query)['models']==['era5'])
    with pytest.raises(WeatherDataError) as caught:
        parse_daily(probe['response'],date(2026,10,28),date(2026,11,4),'Live ERA5 capture')
    diagnostics=caught.value.diagnostics
    assert diagnostics['suggested_end_date']=='2026-11-03'
    assert set(diagnostics['missing_values'])=={'temperature_2m_mean','precipitation_sum','et0_fao_evapotranspiration'}
