"""Source-specific searches and explicit proxies preserve site/date meaning."""
from datetime import date
from io import BytesIO
from pathlib import Path
import math
import numpy as np
import pandas as pd
import pytest
import requests
from PIL import Image
from data_blocks import DataBlock,DataRequest,NoMatchingData
from data_blocks import soil
from data_blocks.validation import compare
import nearby_data
import data_blocks
import source_cache
from source_requests import source_request,tag_source_proxy,proxy_selection,distance_km


@pytest.fixture(autouse=True)
def isolated_soil_cache():
    source_cache._cached_soil.clear()
    yield
    source_cache._cached_soil.clear()


def soil_frame():
    return pd.DataFrame({'top_cm':[0.], 'bottom_cm':[200.], 'sand_pct':[40.],
        'silt_pct':[35.], 'clay_pct':[25.], 'soc_g_kg':[12.],
        'bulk_density_g_cm3':[1.35], 'coarse_fragments_pct':[10.]})


def request(tmp_path,**options):
    return DataRequest(30.,-96.,date(2023,1,1),date(2023,12,31),tmp_path,options)


def test_proxy_request_keeps_original_and_records_two_sites_and_periods(tmp_path):
    chosen=proxy_selection({'latitude':30.02,'longitude':-96.01},30.,-96.)
    original=request(tmp_path,source_proxies={'soilgrids_wcs':chosen})
    effective=source_request('soilgrids_wcs',original)
    assert (original.latitude,original.longitude)==(30.,-96.)
    assert (effective.latitude,effective.longitude)==(30.02,-96.01)
    assert (effective.start,effective.end)==(original.start,original.end)
    assert source_request('open_meteo',original) is original
    block=DataBlock('soilgrids_wcs',pd.DataFrame({'value':[1]}),'static',{}, {'native':True})
    proxy=tag_source_proxy('soilgrids_wcs',block,original,effective)
    assert 'spatial_proxy' not in block.provenance
    details=proxy.provenance['spatial_proxy']
    assert 2<details['distance_km']<3
    assert details['simulation_site']['latitude']==30.
    assert details['source_site']['latitude']==30.02


def test_stale_proxy_cannot_follow_user_to_another_site(tmp_path):
    chosen=proxy_selection({'latitude':30.02,'longitude':-96.},31.,-96.)
    original=request(tmp_path,source_proxies={'soilgrids_wcs':chosen})
    assert source_request('soilgrids_wcs',original) is original


def test_hwsd_proxy_uses_coordinates_instead_of_old_unit_override(tmp_path):
    chosen=proxy_selection({'latitude':30.02,'longitude':-96.},30.,-96.)
    effective=source_request('hwsd',request(tmp_path,source_proxies={'hwsd':chosen},hwsd_unit_id=7001))
    assert 'hwsd_unit_id' not in effective.options


def test_historical_study_does_not_change_run_dates_or_validate_wrong_year(tmp_path):
    chosen=proxy_selection({'latitude':31.,'longitude':-96.,'period_start':'2014-07-01',
        'period_end':'2015-06-30','record':303},30.,-96.)
    original=request(tmp_path,source_proxies={'srdb':chosen})
    effective=source_request('srdb',original)
    assert original.start==date(2023,1,1) and effective.start==date(2014,7,1)
    assert effective.options['srdb_record']==303
    block=DataBlock('srdb',pd.DataFrame({'Record_number':[303],'Study_midyear':[2015.],
        'comparable_rh':[True],'period_start':['2014-07-01'],'period_end':['2015-06-30'],'Rh_annual':[500.]}),'study annual',{}, {})
    proxy=tag_source_proxy('srdb',block,original,effective)
    sim=pd.DataFrame({'date':pd.date_range(original.start,original.end),'soil_respiration_gC_m2_day':2.})
    assert compare(sim,proxy).empty
    sim=pd.DataFrame({'date':pd.date_range(effective.start,effective.end),'soil_respiration_gC_m2_day':2.})
    comparison=compare(sim,proxy)
    assert comparison.modeled.iloc[0]==730.
    assert 'proxy' in comparison.evidence.iloc[0] and 'not observations' in comparison.evidence.iloc[0]
    assert comparison.source_distance_km.iloc[0]>100


@pytest.mark.parametrize('source',['soilgrids_wcs','hwsd','terraclimate','open_meteo','open_meteo_climate'])
def test_options_are_checked_by_the_selected_source_and_keep_requested_dates(source,tmp_path,monkeypatch):
    cells=[{'latitude':30.005,'longitude':-96.,'distance_km':.556},
           {'latitude':30.02,'longitude':-96.,'distance_km':2.224}]
    monkeypatch.setattr(nearby_data,'soilgrids_cells',lambda *args:cells)
    monkeypatch.setattr(nearby_data,'_sampled_cells',lambda *args:cells)
    monkeypatch.setattr('site_recovery.nearby_soil_sites',lambda *args:cells)
    checked=[]
    def loader(key,req):
        checked.append((key,req))
        if req.latitude==cells[0]['latitude']:
            raise NoMatchingData('This source has no complete data here')
        return DataBlock(key,soil_frame() if key in ('soilgrids_wcs','hwsd') else pd.DataFrame({'date':['2023-02-01'],'period_end':['2023-02-28'],'aet_mm':[20.]}),'monthly',{}, {})
    monkeypatch.setattr(nearby_data,'load_block',loader)
    monkeypatch.setattr(data_blocks,'load_block',loader)
    req=request(tmp_path)
    found=nearby_data.nearby_data_options(source,req)
    assert len(found)==1 and found[0]['latitude']==30.02
    assert len(checked)==2 and {k for k,r in checked}=={source}
    assert all((r.start,r.end)==(req.start,req.end) for k,r in checked)
    assert (req.latitude,req.longitude)==(30.,-96.)


def test_unavailable_provider_stops_search_instead_of_claiming_no_local_coverage(tmp_path,monkeypatch):
    def timeout(*args):
        raise requests.Timeout('Provider is down')
    monkeypatch.setattr(nearby_data,'load_block',timeout)
    with pytest.raises(requests.Timeout):
        nearby_data.nearby_data_options('terraclimate',request(tmp_path))


def test_missing_date_window_stops_spatial_search(tmp_path,monkeypatch):
    calls=[]
    def no_time(key,req):
        calls.append(req)
        raise NoMatchingData('No overlap',{'reason':'no_overlap_time','available_first':'1958-01-01','available_last':'2022-12-01'})
    monkeypatch.setattr(nearby_data,'load_block',no_time)
    with pytest.raises(NoMatchingData) as caught:
        nearby_data.nearby_data_options('terraclimate',request(tmp_path))
    assert len(calls)==1 and caught.value.details['reason']=='no_overlap_time'


def test_no_partial_months_are_offered_as_usable_comparisons(tmp_path,monkeypatch):
    monkeypatch.setattr(nearby_data,'_sampled_cells',lambda *args:[{'latitude':30.02,'longitude':-96.,'distance_km':2.2}])
    monkeypatch.setattr(nearby_data,'load_block',lambda key,r:DataBlock(key,pd.DataFrame({'date':['2023-01-01'],'period_end':['2023-01-31'],'aet_mm':[20.]}),'monthly',{}, {}))
    req=DataRequest(30.,-96.,date(2023,1,10),date(2023,1,20),tmp_path)
    with pytest.raises(NoMatchingData) as caught:
        nearby_data.nearby_data_options('terraclimate',req)
    assert caught.value.details['checked_locations']==1


def test_soil_overview_uses_real_nonmissing_cells_and_respects_radius(tmp_path,monkeypatch):
    array=np.full((201,201),-32768,dtype=np.int16)
    array[100,110]=450
    array[0,0]=500  # Square corner lies outside the circular search radius.
    output=BytesIO();Image.fromarray(array).save(output,format='TIFF')
    class Response:
        content=output.getvalue()
        def raise_for_status(self):pass
    monkeypatch.setattr(nearby_data.requests,'get',lambda *a,**kw:Response())
    cells=nearby_data.soilgrids_cells(request(tmp_path),25.)
    assert len(cells)==1 and 2<cells[0]['distance_km']<3
    assert cells[0]['longitude']>-96.


def test_soil_search_can_use_numeric_points_if_overview_route_is_unavailable(tmp_path,monkeypatch):
    def unavailable(*args):raise requests.Timeout('WCS route unavailable')
    monkeypatch.setattr(nearby_data,'soilgrids_cells',unavailable)
    monkeypatch.setattr(data_blocks,'load_block',lambda key,r:DataBlock(key,soil_frame(),'static',{}, {}))
    options=nearby_data.nearby_data_options('soilgrids_wcs',request(tmp_path),limit=1)
    assert len(options)==1 and options[0]['distance_km']==.5


def modis_region():
    radius=6371007.181;cell=463.312716528
    x=radius*math.radians(-96)*math.cos(math.radians(30));y=radius*math.radians(30)
    return {'nrows':3,'ncols':3,'xllcorner':x-1.5*cell,'yllcorner':y-1.5*cell,'cellsize':cell,
        'subset':[{'modis_date':'A2023001','band':band,'data':values} for band,values in [
            ('ET_500m',[32762]*5+[50]+[32762]*3),
            ('PET_500m',[32762]*5+[100]+[32762]*3),
            ('ET_QC_500m',[157]*5+[16]+[157]*3)]]}


def test_modis_search_rejects_urban_fill_and_inverts_actual_sinusoidal_grid(tmp_path,monkeypatch):
    def provider(url,params):
        return {'dates':[{'calendar_date':'2023-01-01','modis_date':'A2023001'}]} if url.endswith('/dates') else modis_region()
    monkeypatch.setattr(nearby_data,'get_json',provider)
    cells=nearby_data.modis_cells(request(tmp_path),25.)
    assert len(cells)==1 and cells[0]['longitude']>-96.
    assert cells[0]['latitude']==pytest.approx(30.)
    assert .46<cells[0]['distance_km']<.47
    assert nearby_data.modis_cells(request(tmp_path,modis_quality='strict'),25.)==[]


def test_modis_missing_dates_offer_temporal_remedy_without_requesting_neighborhood(tmp_path,monkeypatch):
    def provider(url,params):
        assert url.endswith('/dates')
        return {'dates':[{'calendar_date':'2020-01-01','modis_date':'A2020001'}]}
    monkeypatch.setattr(nearby_data,'get_json',provider)
    with pytest.raises(NoMatchingData) as caught:
        nearby_data.modis_cells(request(tmp_path),25.)
    assert caught.value.details['available_last']=='2020-01-01'
    assert caught.value.details['reason']=='no_complete_time_window'


def test_official_existing_map_is_reused_read_only_and_local_map_has_priority(tmp_path):
    old=tmp_path/'old'/'data'/'hwsd';old.mkdir(parents=True)
    bil=old/'HWSD2.bil';hdr=old/'HWSD2.hdr'
    with bil.open('wb') as stream:
        stream.truncate(21600*43200*2)  # Sparse fixture, no multi-GB allocation.
    hdr.write_text('NROWS 21600\nNCOLS 43200\nNBITS 16\nULXMAP -179.995833333333\nULYMAP 89.9958333333333\nXDIM .00833333333333333\nYDIM .00833333333333333\nBYTEORDER I\n')
    data=tmp_path/'new'/'data';data.mkdir(parents=True)
    before=(bil.stat().st_mtime_ns,bil.stat().st_size)
    assert soil.find_existing_hwsd_map(data,search_roots=[tmp_path])==(bil,hdr)
    assert before==(bil.stat().st_mtime_ns,bil.stat().st_size)
    assert not (data/'hwsd').exists()
    assert soil.find_existing_hwsd_map(data,reuse=False) is None
    local=data/'hwsd';local.mkdir()
    (local/'HWSD2.bil').write_bytes(b'local map');(local/'HWSD2.hdr').write_text('custom')
    assert soil.find_existing_hwsd_map(data,search_roots=[tmp_path])[0]==local/'HWSD2.bil'


def test_truncated_external_map_is_not_reused(tmp_path):
    old=tmp_path/'old'/'data'/'hwsd';old.mkdir(parents=True)
    (old/'HWSD2.bil').write_bytes(b'truncated')
    (old/'HWSD2.hdr').write_text('NROWS 21600\nNCOLS 43200\nNBITS 16\nULXMAP -179.995833333333\nULYMAP 89.9958333333333\nXDIM .00833333333333333\nYDIM .00833333333333333\n')
    assert soil.find_existing_hwsd_map(tmp_path/'new'/'data',search_roots=[tmp_path]) is None


def test_chosen_soil_candidate_reuses_profile_checked_during_search(tmp_path, monkeypatch):
    candidate = {'latitude':30.02, 'longitude':-96., 'distance_km':2.224}
    monkeypatch.setattr(nearby_data, 'soilgrids_cells', lambda *args: [candidate])
    checked = []
    def loader(key, effective):
        checked.append(effective)
        return DataBlock(key, soil_frame(), 'static depth profile', {}, {})
    monkeypatch.setattr(data_blocks, 'load_block', loader)
    candidates = nearby_data.nearby_data_options('soilgrids_wcs', request(tmp_path), limit=1)
    selected = source_request('soilgrids_wcs', request(tmp_path,
        source_proxies={'soilgrids_wcs':proxy_selection(candidates[0],30.,-96.)}))
    block = source_cache.load_cached_soil('soilgrids_wcs', selected)
    assert len(checked) == 1
    assert checked[0].options['soilgrids_map_recovery'] is True
    assert checked[0].options['soilgrids_use_rest'] is False
    assert block.provenance['cache']['storage'] == 'memory'
    assert block.provenance['request']['latitude'] == candidate['latitude']
