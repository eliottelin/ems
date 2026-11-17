"""Regressions for the Dallas source report and documented provider conventions."""
from datetime import date
import numpy as np
import pandas as pd
import pytest
import requests

from data_blocks import DataRequest, NoMatchingData, load_block
from data_blocks import observations, soil
from data_blocks.validation import compare
from data_status import source_issue
from site_recovery import nearby_soil_sites


@pytest.fixture(autouse=True)
def isolated_cooldown(monkeypatch):
    monkeypatch.setattr(soil, '_REST_COOLDOWNS', {})


def srdb_row(midpoint=2015.5, **changes):
    return dict(Record_number=1, Study_number=1, Study_midyear=midpoint,
        Latitude=30., Longitude=-96., Rs_annual=900., Rh_annual=500.,
        Quality_flag='', Annual_coverage=1., YearsOfData=1., Manipulation='None') | changes


def write_srdb(folder, rows):
    target=folder/'srdb'
    target.mkdir()
    pd.DataFrame(rows).to_csv(target/'srdb-data.csv',index=False)


@pytest.mark.parametrize('midpoint,years,expected',[
    (2015.5,1,('2015-01-01','2015-12-31')),
    (2015.,1,('2014-07-01','2015-06-30')),
    (2015.5,2,None),(2015.25,1,None),(np.nan,1,None),
])
def test_srdb_documented_rounded_midpoints(midpoint,years,expected):
    result=observations.srdb_annual_window(midpoint,years)
    assert (tuple(map(str,result)) if result else None) == expected


def test_blank_flags_and_half_year_records_remain_eligible(tmp_path):
    write_srdb(tmp_path,[srdb_row()])
    req=DataRequest(30.,-96.,date(2015,1,1),date(2015,12,31),tmp_path)
    block=load_block('srdb',req)
    row=block.frame.iloc[0]
    assert row.comparable_rh and row.quality_flag_normalized=='Q0'
    assert (row.period_start,row.period_end)==('2015-01-01','2015-12-31')
    result=compare(pd.DataFrame({'date':pd.date_range(req.start,req.end),'soil_respiration_gC_m2_day':2.}),block)
    assert result.modeled.iloc[0]==730.
    assert 'inferred' in result.evidence.iloc[0]


def test_integer_midpoint_matches_previous_year_overlap_and_july_window(tmp_path):
    write_srdb(tmp_path,[srdb_row(2015.)])
    req=DataRequest(30.,-96.,date(2014,7,1),date(2014,12,31),tmp_path)
    block=load_block('srdb',req)
    assert block.frame.period_start.iloc[0]=='2014-07-01'
    assert compare(pd.DataFrame({'date':pd.date_range(req.start,req.end),'soil_respiration_gC_m2_day':2.}),block).empty
    sim=pd.DataFrame({'date':pd.date_range('2014-07-01','2015-06-30'),'soil_respiration_gC_m2_day':2.})
    assert compare(sim,block).modeled.iloc[0]==730.


def test_real_study_choices_include_inferred_window_and_exclude_flagged_multiyear(tmp_path):
    rows=[srdb_row(),{**srdb_row(),'Record_number':2,'Quality_flag':'Q1'},
          {**srdb_row(),'Record_number':3,'YearsOfData':2}]
    write_srdb(tmp_path,rows)
    with pytest.raises(NoMatchingData) as caught:
        load_block('srdb',DataRequest(32.78,-96.81,date(2025,1,1),date(2025,12,31),tmp_path))
    details=caught.value.details
    assert details['qualified_annual_rh_records']==1
    assert details['nearest_qualified_studies'][0]['period_start']=='2015-01-01'
    assert details['nearest_qualified_studies'][0]['period_inferred']
    assert source_issue(caught.value)['status']=='No matching records'


def test_explicit_study_proxy_loads_only_the_selected_record(tmp_path):
    write_srdb(tmp_path,[srdb_row(),{**srdb_row(),'Record_number':2,'Rh_annual':600.}])
    req=DataRequest(30.,-96.,date(2015,1,1),date(2015,12,31),tmp_path,{'srdb_record':2})
    block=load_block('srdb',req)
    assert block.frame.Record_number.tolist()==[2]
    assert block.frame.Rh_annual.tolist()==[600.]


def soil_assets(folder):
    target=folder/'hwsd';target.mkdir()
    rows=[]
    for unit in (7001,42,43):
        for top,bottom in ((0,20),(20,60),(60,200)):
            rows.append(dict(HWSD2_SMU_ID=unit,SEQUENCE=1,TOPDEP=top,BOTDEP=bottom,
                SAND=np.nan if unit==7001 else 40,SILT=np.nan if unit==7001 else 35,
                CLAY=np.nan if unit==7001 else 25,ORG_CARBON=np.nan if unit==7001 else 2,
                BULK=np.nan if unit==7001 else 1.3,COARSE=np.nan if unit==7001 else 10))
    frame=pd.DataFrame(rows)
    # The closest cell has an incomplete depth profile and must not be offered.
    frame.loc[(frame.HWSD2_SMU_ID==43)&frame.TOPDEP.eq(20),'TOPDEP']=25
    frame.to_csv(target/'HWSD2_LAYERS.csv',index=False)
    np.array([[7001,43,42],[7001,7001,7001]],dtype='<u2').tofile(target/'HWSD2.bil')
    (target/'HWSD2.hdr').write_text('NROWS 2\nNCOLS 3\nNBITS 16\nULXMAP -96\nULYMAP 30\nXDIM .02\nYDIM .02\nBYTEORDER I\n')


def test_urban_mapping_is_coverage_not_a_broken_download(tmp_path):
    soil_assets(tmp_path)
    with pytest.raises(NoMatchingData,match='urban/built-up') as caught:
        load_block('hwsd',DataRequest(30.,-96.,date(2023,1,1),date(2023,1,1),tmp_path))
    assert caught.value.details['map_lookup_succeeded']
    assert caught.value.details['mapping_unit']==7001
    assert source_issue(caught.value)['status']=='No matching records'


def test_nearby_candidates_use_actual_complete_cells_and_preserve_requested_site(tmp_path):
    soil_assets(tmp_path)
    req=DataRequest(30.,-96.,date(2023,1,1),date(2023,1,1),tmp_path)
    candidates=nearby_soil_sites(tmp_path,req.latitude,req.longitude)
    assert len(candidates)==1 and candidates[0]['mapping_unit']==42
    assert candidates[0]['longitude']==pytest.approx(-95.96)
    assert (req.latitude,req.longitude)==(30.,-96.)
    with pytest.raises(NoMatchingData):
        nearby_soil_sites(tmp_path,30.,-96.,max_km=1.)


def modis_payload(qc=16,et=67):
    return {'nrows':1,'ncols':1,'subset':[
        {'modis_date':'A2025337','band':band,'data':[value],'version':'061'}
        for band,value in [('ET_500m',et),('PET_500m',138),('ET_QC_500m',qc)]]}


@pytest.mark.parametrize('qc',[0,16,32,48])
def test_gap_filled_main_algorithm_quality_and_optional_strict_mode(qc):
    frame,rejected=observations.parse_modis(modis_payload(qc))
    assert not rejected and frame.aet_mm.iloc[0]==pytest.approx(6.7)
    strict,bad=observations.parse_modis(modis_payload(qc),'strict')
    assert bad==int(qc!=0) and strict.empty==(qc!=0)


@pytest.mark.parametrize('qc',[1,4,64,255])
def test_bad_modland_detectors_retrieval_and_fill_quality_stay_rejected(qc):
    frame,bad=observations.parse_modis(modis_payload(qc))
    assert frame.empty and bad==1


def test_urban_modis_has_specific_diagnostics_and_never_uses_fill_values(tmp_path,monkeypatch):
    payload=modis_payload(157,32762)
    for record in payload['subset']:
        record.pop('version')
    def fake_json(url,params):
        return {'dates':[{'calendar_date':'2025-12-03','modis_date':'A2025337'}]} if url.endswith('/dates') else payload
    def unexpected_csv(*args,**kwargs):
        raise AssertionError('Rejected observations need no extra collection metadata request')
    monkeypatch.setattr(observations,'get_json',fake_json)
    monkeypatch.setattr(observations.requests,'get',unexpected_csv)
    with pytest.raises(NoMatchingData,match='urban/built-up') as caught:
        load_block('mod16a2gf',DataRequest(32.78,-96.81,date(2025,12,1),date(2025,12,31),tmp_path))
    assert caught.value.details['fill_codes']=={'32762':1}
    assert caught.value.details['rejected_composites']==1


def test_soilgrids_preserves_connection_failure_when_fallback_has_no_data(tmp_path,monkeypatch):
    def timeout(*args,**kwargs):
        raise requests.Timeout('Numeric service timed out')
    def nodata(*args,**kwargs):
        raise NoMatchingData('No center-pixel soil value')
    monkeypatch.setattr(soil,'_rest_profile',timeout)
    monkeypatch.setattr(soil,'point_value',timeout)
    monkeypatch.setattr(soil,'wcs_value',nodata)
    with pytest.raises(ValueError) as caught:
        load_block('soilgrids_wcs',DataRequest(30.,-96.,date(2023,1,1),date(2023,1,1),tmp_path,{'soilgrids_use_rest':True,'soilgrids_map_recovery':True}))
    issue=source_issue(caught.value)
    assert issue['status']=='Connection failed'
    assert len(issue['details']['attempts'])==3


def test_all_soilgrids_routes_no_data_are_classified_as_coverage(tmp_path,monkeypatch):
    def nodata(*args,**kwargs):
        raise NoMatchingData('No soil value')
    monkeypatch.setattr(soil,'_rest_profile',nodata)
    monkeypatch.setattr(soil,'point_value',nodata)
    monkeypatch.setattr(soil,'wcs_value',nodata)
    with pytest.raises(NoMatchingData) as caught:
        load_block('soilgrids_wcs',DataRequest(30.,-96.,date(2023,1,1),date(2023,1,1),tmp_path,{'soilgrids_use_rest':True,'soilgrids_map_recovery':True}))
    assert source_issue(caught.value)['status']=='No matching records'
