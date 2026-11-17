"""Provider-shaped fixtures test units, matching, QA and source independence."""
from datetime import date
from io import BytesIO
import numpy as np
import pandas as pd
import pytest
from PIL import Image
from data_blocks import DataBlock, DataRequest, load_block
from data_blocks import soil, observations
from data_blocks.traits import load_try_categorical, classify_pft
from data_blocks.validation import compare
from studio_builder.registry import DATASETS, resolve_data_blocks
from weather import parse_daily


def request(tmp_path, start=date(2023, 1, 1), end=date(2023, 12, 31), **options):
    return DataRequest(30., -96., start, end, tmp_path, options)


def response(content=b'', text='', url='https://provider.example/data'):
    class Response:
        def raise_for_status(self):
            pass
    result = Response()
    result.content, result.text, result.url = content, text, url
    return result


@pytest.mark.parametrize('models', [
    ['water_balance'], ['vegetation'], ['soil_carbon'],
    ['water_balance','vegetation'], ['water_balance','soil_carbon'],
    ['vegetation','soil_carbon'], ['water_balance','vegetation','soil_carbon'],
])
def test_automatic_block_closure_and_optional_combinations(models):
    blocks = resolve_data_blocks(models)
    ids = {block['id'] for block in blocks}
    assert 'open_meteo' in ids
    assert ('try_categorical' in ids) == ('vegetation' in models)
    assert ('srdb' in ids) == ('soil_carbon' in models)
    assert ('terraclimate' in ids) == ('water_balance' in models)
    assert len(ids) == len(blocks)
    all_blocks = resolve_data_blocks(models, list(DATASETS))
    assert {block['id'] for block in all_blocks} == set(DATASETS)
    all_blocks[0]['units']['extra'] = 'test'
    assert 'extra' not in DATASETS['open_meteo']['units']


def test_open_meteo_rejects_gaps_bad_units_and_nonfinite_values():
    payload = {'daily': {'time':['2023-01-01','2023-01-02'], 'temperature_2m_mean':[20.,21.],
                        'precipitation_sum':[1.,0.], 'et0_fao_evapotranspiration':[2.,3.]}}
    assert len(parse_daily(payload,date(2023,1,1),date(2023,1,2),'test')) == 2
    payload['daily']['time'][1] = '2023-01-03'
    with pytest.raises(ValueError, match='consecutive'):
        parse_daily(payload,date(2023,1,1),date(2023,1,2),'test')
    payload['daily']['time'][1] = '2023-01-02'
    payload['daily_units'] = {'precipitation_sum':'inch'}
    with pytest.raises(ValueError, match='units'):
        parse_daily(payload,date(2023,1,1),date(2023,1,2),'test')
    payload.pop('daily_units')
    payload['daily']['precipitation_sum'][0] = float('nan')
    with pytest.raises(ValueError, match='finite'):
        parse_daily(payload,date(2023,1,1),date(2023,1,2),'test')


def test_climate_is_a_separate_daily_scenario_block(tmp_path, monkeypatch):
    def fake_get(url, params, timeout):
        assert '/v1/climate' in url
        assert 'et0_fao_evapotranspiration_sum' in params['daily']
        result = response()
        result.json = lambda: {'daily':{'time':['2030-01-01'], 'temperature_2m_mean':[25.],
                         'precipitation_sum':[0.], 'et0_fao_evapotranspiration_sum':[4.]}}
        return result
    monkeypatch.setattr('weather.requests.get', fake_get)
    block = load_block('open_meteo_climate', request(tmp_path,date(2030,1,1),date(2030,1,1)))
    assert block.frame.et0_mm.iloc[0] == 4
    assert 'not historical observations' in block.provenance['scenario']


def test_try_loads_local_workbook_and_preserves_ambiguous_traits(tmp_path):
    folder = tmp_path / 'try'
    folder.mkdir()
    rows = [dict(AccSpeciesID=1,AccSpeciesName='Test grass',PlantGrowthForm='graminoid',
                 LeafType='broadleaved',LeafPhenology='',PhotosyntheticPathway='C3',Woodiness='non-woody'),
            dict(AccSpeciesID=2,AccSpeciesName='Test mixed',PlantGrowthForm='shrub/tree',
                 LeafType='broadleaved',LeafPhenology='evergreen',PhotosyntheticPathway='C3',Woodiness='woody')]
    path = folder / 'TRY_Categorical_Traits_Lookup_Table_test.xlsx'
    pd.DataFrame([*rows, rows[0]]).to_excel(path,sheet_name='Categorical Trait Data',index=False)
    block = load_try_categorical(request(tmp_path))
    assert block.frame.pft.tolist() == ['grass_c3','unresolved']
    assert len(block.provenance['sha256']) == 64
    assert block.provenance['exact_duplicates_removed'] == 1
    rows[1]['AccSpeciesID'] = 1
    pd.DataFrame(rows).to_excel(path,sheet_name='Categorical Trait Data',index=False)
    with pytest.raises(ValueError,match='unique'):
        load_try_categorical(request(tmp_path))


def test_soilgrids_real_tiff_path_converts_six_depth_profiles(tmp_path, monkeypatch):
    raw = {'sand':400,'silt':350,'clay':250,'soc':120,'bdod':135,'cfvo':100}
    def unavailable_rest(*args, **kwargs):
        raise ValueError('REST deliberately unavailable in WCS recovery test')
    def fake_get(url, params, timeout):
        if params['SERVICE']!='WCS':
            raise ValueError('Point service deliberately unavailable in WCS recovery test')
        assert params['CRS']=='EPSG:4326' and params['WIDTH']==params['HEIGHT']==3
        prop = params['map'].split('/')[-1].split('.')[0]
        output = BytesIO()
        Image.fromarray(np.full((3,3),raw[prop],dtype=np.int16)).save(output,format='TIFF')
        return response(content=output.getvalue())
    monkeypatch.setattr(soil,'_rest_profile',unavailable_rest)
    monkeypatch.setattr(soil.requests,'get',fake_get)
    block = load_block('soilgrids_wcs',request(tmp_path,soilgrids_map_recovery=True))
    assert len(block.frame) == 6
    assert block.frame.bottom_cm.tolist() == [5,15,30,60,100,200]
    assert block.frame.bulk_density_g_cm3.iloc[0] == pytest.approx(1.35)
    assert block.frame.soc_g_kg.iloc[0] == 12
    assert block.frame.sand_pct.iloc[0] == 40
    raw['soc'] = -32768
    with pytest.raises(ValueError,match='valid'):
        load_block('soilgrids_wcs',request(tmp_path,soilgrids_map_recovery=True))


def test_hwsd_samples_mapping_raster_and_converts_carbon_percent(tmp_path):
    folder = tmp_path/'hwsd'
    folder.mkdir()
    table = pd.DataFrame([{'HWSD2_SMU_ID':42,'SEQUENCE':1,'TOPDEP':0,'BOTDEP':20,
        'SAND':40,'SILT':35,'CLAY':25,'ORG_CARBON':2,'BULK':1.3,'COARSE':10}])
    table.to_csv(folder/'HWSD2_LAYERS.csv',index=False)
    np.array([[42,43],[44,45]],dtype='<u2').tofile(folder/'HWSD2.bil')
    (folder/'HWSD2.hdr').write_text('NROWS 2\nNCOLS 2\nNBITS 16\nULXMAP -96\nULYMAP 30\nXDIM 1\nYDIM 1\nBYTEORDER I\n')
    block = load_block('hwsd', request(tmp_path))
    assert block.provenance['mapping_unit'] == 42
    assert block.frame.soc_g_kg.iloc[0] == 20
    assert block.frame.coarse_fragments_pct.iloc[0] == 10


def test_srdb_retains_total_rs_but_only_compares_qualified_rh(tmp_path):
    folder = tmp_path/'srdb'
    folder.mkdir()
    rows = [dict(Record_number=1,Study_number=1,Study_midyear=2023.5,Latitude=30,Longitude=-96,
             Rs_annual=900,Rh_annual=500,Quality_flag='Q0',Annual_coverage=1,YearsOfData=1,Manipulation='None'),
            dict(Record_number=2,Study_number=1,Study_midyear=2023.5,Latitude=30,Longitude=-96,
             Rs_annual=800,Rh_annual=np.nan,Quality_flag='Q0',Annual_coverage=1,YearsOfData=1,Manipulation='None')]
    pd.DataFrame(rows).to_csv(folder/'srdb-data.csv',index=False)
    block = load_block('srdb',request(tmp_path))
    assert block.frame.comparable_rh.tolist() == [True,False]
    sim = pd.DataFrame({'date':pd.date_range('2023-01-01','2023-12-31'), 'soil_respiration_gC_m2_day':2.})
    result = compare(sim,block)
    assert len(result) == 1 and result.modeled.iloc[0] == 730 and result.observed.iloc[0] == 500
    assert compare(sim.iloc[:-1],block).empty


def test_terraclimate_decoded_units_and_complete_month_comparison(tmp_path):
    folder=tmp_path/'terraclimate'
    folder.mkdir()
    for variable in ('aet','pet'):
        (folder/f'{variable}.csv').write_text(f'time,latitude,longitude,{variable}[unit="mm"]\n2023-02-01T00:00:00Z,30,-96,50\n')
    block=load_block('terraclimate',request(tmp_path))
    assert block.frame.aet_mm.iloc[0] == 50
    sim=pd.DataFrame({'date':pd.date_range('2023-02-01','2023-02-28'),'aet_mm':2.})
    result=compare(sim,block)
    assert result.modeled.iloc[0] == 56 and result.difference.iloc[0] == 6
    assert compare(sim.iloc[1:],block).empty
    with pytest.raises(ValueError,match='units'):
        observations.parse_terraclimate('date,aet\n2023-01-01,500','aet')


def modis_payload(stamp='A2023361', qc=0, version='061'):
    return {'ncols':1,'nrows':1,'subset':[
        {'band':band,'version':version,'modis_date':stamp,'data':[value]}
        for band,value in [('ET_500m',100),('PET_500m',200),('ET_QC_500m',qc)]]}


def test_mod16_scale_qa_collection_and_short_year_end_composite(tmp_path, monkeypatch):
    block_frame,rejected=observations.parse_modis(modis_payload())
    assert rejected == 0
    assert block_frame.aet_mm.iloc[0] == 10
    assert block_frame.period_end.iloc[0] == '2023-12-31'
    sim=pd.DataFrame({'date':pd.date_range('2023-12-27','2023-12-31'),'aet_mm':2.})
    block=DataBlock('mod16a2gf',block_frame,'8-day composite',{}, {})
    assert compare(sim,block).difference.iloc[0] == 0
    assert observations.parse_modis(modis_payload(qc=1))[0].empty
    with pytest.raises(ValueError,match='collection'):
        observations.parse_modis(modis_payload(version='006'))
    def fake_json(url,params):
        if url.endswith('/dates'):
            return {'dates':[{'calendar_date':'2023-12-27','modis_date':'A2023361'}]}
        return modis_payload()
    monkeypatch.setattr(observations,'get_json',fake_json)
    assert len(load_block('mod16a2gf',request(tmp_path)).frame) == 1


def test_forcing_injection_preserves_daily_contract_without_network():
    from engine import simulate
    frame=pd.DataFrame({'date':['2023-01-01','2023-01-02'],'precip_mm':[0.,4.], 'et0_mm':[2.,2.], 'temp_c':[20.,20.]})
    result=simulate({'models':['water_balance'],'weather_source':'open_meteo'},forcing=frame)
    assert len(result) == 2 and result.water_residual_mm.abs().max() < 1e-9
    frame.loc[1,'date']='2023-01-03'
    with pytest.raises(ValueError,match='daily'):
        simulate({'models':['water_balance'],'weather_source':'open_meteo'},forcing=frame)


def test_modis_recovers_collection_from_csv_when_live_shaped_json_omits_it(tmp_path, monkeypatch):
    payload=modis_payload(stamp='A2023001')
    for record in payload['subset']:
        record.pop('version')
    assert 'unreported' in observations.parse_modis(payload)[0].attrs['collection']
    text='\n'.join(f'MOD16A2GF.A2023001.h09v05.061.2026001000000.{band},MOD16A2GF,A2023001,Lat30Lon-96Samp1Line1,2026001000000,{value}'
                   for band,value in [('ET_500m',100),('PET_500m',200),('ET_QC_500m',0)])
    def fake_json(url,params):
        if url.endswith('/dates'):
            return {'dates':[{'calendar_date':'2023-01-01','modis_date':'A2023001'}]}
        return payload
    def fake_get(url,params,headers,timeout):
        assert headers['Accept']=='text/csv'
        return response(text=text)
    monkeypatch.setattr(observations,'get_json',fake_json)
    monkeypatch.setattr(observations.requests,'get',fake_get)
    block=load_block('mod16a2gf',request(tmp_path))
    assert block.provenance['collection']=='061'
    assert block.frame.aet_mm.iloc[0]==10
    with pytest.raises(ValueError,match='collection'):
        observations.parse_modis(observations.modis_csv_payload(text.replace('.061.','.006.')))
    with pytest.raises(ValueError,match='verifiable'):
        observations.modis_csv_payload('date,value\n2023-01-01,10\n')


def test_terra_cf_packing_offset_fill_time_and_coordinates(tmp_path):
    from scipy.io import netcdf_file
    path=tmp_path/'point.nc'
    with netcdf_file(path,'w') as dataset:
        dataset.createDimension('time',3)
        dataset.createDimension('station',1)
        time=dataset.createVariable('time','d',('time',))
        time[:]=[0,31,60]; time.units='days since 2024-01-01 00:00:00'; time.calendar='gregorian'
        for name,value in [('latitude',30.),('longitude',-96.)]:
            variable=dataset.createVariable(name,'d',('station',)); variable[:]=[value]
        value=dataset.createVariable('aet','h',('time','station'))
        value[:]=np.array([[646],[658],[-32768]],dtype='int16')
        value.units='mm'; value.scale_factor=np.float64(.1); value.add_offset=np.float64(0); value._FillValue=np.int16(-32768)
        dataset.version='V1.1'
    frame,metadata=observations.parse_terra_netcdf(path.read_bytes(),'aet',(30.,-96.))
    assert frame.aet_mm.tolist()==pytest.approx([64.6,65.8])
    assert frame.date.tolist()==['2024-01-01','2024-02-01']
    assert metadata['scale_factor']==.1 and metadata['rejected_records']==1
    assert metadata['version']=='V1.1'
    with pytest.raises(ValueError,match='coordinates'):
        observations.parse_terra_netcdf(path.read_bytes(),'aet',(31.,-96.))


def test_terra_discovers_current_aggregated_catalog(monkeypatch):
    catalog=b'''<catalog><service name="all" serviceType="Compound" base=""><service name="ncss" serviceType="NetcdfSubset" base="/thredds/ncss/grid/"/></service><dataset name="TerraClimate v1.1"><dataset name="AET" urlPath="agg_terraclimate_aet_1950_CurrentYear_GLOBE.nc"/></dataset></catalog>'''
    def fake_get(url,timeout):
        assert url.endswith('/terraclimate_aggregated.xml')
        return response(content=catalog)
    monkeypatch.setattr(observations.requests,'get',fake_get)
    assert observations.discover_ncss('aet')=='https://tds-proxy.nkn.uidaho.edu/thredds/ncss/grid/agg_terraclimate_aet_1950_CurrentYear_GLOBE.nc'


def test_hwsd_opt_in_map_download_samples_official_archive_shape(tmp_path,monkeypatch):
    from zipfile import ZipFile
    folder=tmp_path/'hwsd';folder.mkdir()
    pd.DataFrame([dict(HWSD2_SMU_ID=42,SEQUENCE=1,TOPDEP=0,BOTDEP=200,SAND=40,SILT=35,CLAY=25,ORG_CARBON=2,BULK=1.3,COARSE=10)]).to_csv(folder/'HWSD2_LAYERS.csv.gz',index=False)
    output=BytesIO()
    with ZipFile(output,'w') as archive:
        archive.writestr('HWSD2_RASTER/HWSD2.bil',np.array([[42]],dtype='<u2').tobytes())
        archive.writestr('HWSD2_RASTER/HWSD2.hdr','NROWS 1\nNCOLS 1\nNBITS 16\nULXMAP -96\nULYMAP 30\nXDIM 1\nYDIM 1\nBYTEORDER I\n')
    payload=output.getvalue()
    class Download:
        headers={'Content-Length':str(len(payload))}
        def raise_for_status(self): pass
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def iter_content(self,size): yield payload
    calls=[]
    monkeypatch.setattr(soil.requests,'get',lambda url,**kw:Download())
    block=load_block('hwsd',request(tmp_path,hwsd_reuse_existing=False,hwsd_download_map=True,_progress=lambda done,total:calls.append((done,total))))
    assert block.provenance['mapping_unit']==42
    assert block.frame.soc_g_kg.iloc[0]==20
    assert calls[-1]==(len(payload),len(payload))
    assert not list(folder.glob('*.zip'))


def test_supplied_hwsd_database_was_exported_with_source_hash():
    from pathlib import Path
    from hashlib import sha256
    import json
    source=Path(__file__).resolve().parents[1]/'data/private/HWSD2.mdb'
    table=source.parents[1]/'hwsd/HWSD2_LAYERS.csv.gz'
    if not source.is_file():
        pytest.skip('Supplied local database absent')
    meta=json.loads(table.with_name('source.json').read_text())
    assert meta['source_sha256']==sha256(source.read_bytes()).hexdigest()
    frame=pd.read_csv(table)
    assert len(frame)==meta['records']==408835
    block=load_block('hwsd',DataRequest(30.,-96.,date(2023,1,1),date(2023,1,1),source.parents[1],{'hwsd_unit_id':1666}))
    assert block.frame.top_cm.iloc[0]==0 and block.frame.bottom_cm.iloc[-1]==200
    from soil_profile import make_profile
    assert make_profile(source=block.frame).initial_soc_gC_m2.sum()>0


def test_try_category_selects_only_supported_broad_presets():
    from data_blocks.traits import preset_for_pft
    assert preset_for_pft('grass_c4')=='grass_c4'
    assert preset_for_pft('tree_deciduous')=='forest'
    assert preset_for_pft('unresolved') is None
    assert preset_for_pft('shrub') is None


def test_terra_limits_observation_request_to_actual_provider_time_coverage(monkeypatch):
    xml=b'<gridDataset><TimeSpan><begin>1950-01-01T00:00:00Z</begin><end>2025-12-01T00:00:00Z</end></TimeSpan></gridDataset>'
    monkeypatch.setattr(observations.requests,'get',lambda url,timeout:response(content=xml))
    first,last,metadata=observations.ncss_period('https://provider.example/aggregate.nc',date(2025,11,3),date(2026,11,3))
    assert (first,last)==(date(2025,11,3),date(2025,12,1))
    assert metadata['available_last_timestamp']=='2025-12-01'
    with pytest.raises(ValueError,match='no overlapping'):
        observations.ncss_period('https://provider.example/aggregate.nc',date(2026,1,1),date(2026,11,3))


def test_live_captured_soilgrids_profile_replays_through_complete_adapter(tmp_path,monkeypatch):
    import json,re
    from pathlib import Path
    capture=json.loads((Path(__file__).resolve().parents[1]/'docs/verification/soilgrids_live_profile_audit.json').read_text())
    pages={}
    for group in capture['captures']:
        for item in json.loads(group['content'][0]['text'])['results']:
            coverage=re.search(r'LAYERS=([^&]+)',item['url'])[1]
            pages[coverage]='<html><body>'+' '.join(part['text'] for part in item['text']['children'])+'</body></html>'
    def get(url,params,timeout):
        assert params['SERVICE']=='WMS'
        assert params['WIDTH']==params['HEIGHT']==101
        assert params['X']==params['Y']==50
        return response(text=pages[params['QUERY_LAYERS']])
    def unavailable_rest(*args,**kwargs):
        raise ValueError('REST deliberately unavailable to replay captured WMS profile')
    monkeypatch.setattr(soil,'_rest_profile',unavailable_rest)
    monkeypatch.setattr(soil.requests,'get',get)
    block=load_block('soilgrids_wcs',DataRequest(30.62798,-96.33441,date(2023,1,1),date(2023,12,31),tmp_path,{'soilgrids_map_recovery':True}))
    assert len(block.frame)==6 and len(block.provenance['sampled_cells'])==36
    top=block.frame.sort_values('top_cm').iloc[0]
    assert top.sand_pct==39.8 and top.soc_g_kg==35.7 and top.bulk_density_g_cm3==1.5
    from soil_profile import make_profile
    assert make_profile(source=block.frame).initial_soc_gC_m2.sum()==pytest.approx(18308.3731)


def test_soilgrids_point_response_rejects_wrong_layer_units_and_distant_cell():
    text='Latitude: -96.334988 Longitude: 30.626937 Value: 398 Query Layer: sand_0-5cm_mean Unit: g/kg'
    value=soil.parse_soil_point(text,'sand',0,5,30.62798,-96.33441)
    assert value[3]==39.8 and value[4]['distance_m']<200
    with pytest.raises(ValueError,match='units'):
        soil.parse_soil_point(text.replace('g/kg','kg/kg'),'sand',0,5,30.62798,-96.33441)
    with pytest.raises(ValueError,match='wrong layer'):
        soil.parse_soil_point(text,'sand',5,15,30.62798,-96.33441)
    with pytest.raises(ValueError,match='away'):
        soil.parse_soil_point(text,'sand',0,5,31.,-96.)
    with pytest.raises(ValueError,match='ambiguous multiple layer records'):
        soil.parse_soil_point(text+' '+text.replace('398','404'),'sand',0,5,30.62798,-96.33441)


def test_srdb_mixed_text_fields_load_without_dtype_warnings(tmp_path):
    import warnings
    folder=tmp_path/'srdb';folder.mkdir()
    fields=['Record_number','Study_number','Study_midyear','Latitude','Longitude','Rs_annual','Rh_annual','Quality_flag','Annual_coverage','YearsOfData','Manipulation','Rs_annual_err','C_soildepth']
    fields += ['Extra_'+str(i) for i in range(72)]
    path=folder/'srdb-data.csv'
    with path.open('w') as stream:
        stream.write(','.join(fields)+'\n')
        for i in range(20000):
            mixed='1' if i<10000 else 'range'
            stream.write(','.join([str(i), '1','2023','30','-96','900','500','Q0','1','1','None',mixed,mixed]+['']*72)+'\n')
    with warnings.catch_warnings():
        warnings.simplefilter('error',pd.errors.DtypeWarning)
        block=load_block('srdb',request(tmp_path))
    assert len(block.frame)==20000
    assert block.frame.C_soildepth.iloc[-1]=='range'


def test_srdb_no_match_retains_real_study_choices_instead_of_claiming_provider_failure(tmp_path):
    from data_blocks import NoMatchingData
    folder=tmp_path/'srdb';folder.mkdir()
    pd.DataFrame([dict(Record_number=1,Study_number=1,Study_midyear=2010,Latitude=31.,Longitude=-96.,Rs_annual=900,Rh_annual=500,Quality_flag='Q0',Annual_coverage=1,YearsOfData=1,Manipulation='None')]).to_csv(folder/'srdb-data.csv',index=False)
    with pytest.raises(NoMatchingData) as result:
        load_block('srdb',request(tmp_path))
    choices=result.value.details['nearest_qualified_studies']
    assert choices[0]['Study_midyear']==2010 and choices[0]['Latitude']==31.
    from data_status import source_issue,source_status
    issue=source_issue(result.value)
    assert issue['status']=='No matching records'
    assert source_status('hwsd',{'role':'fallback','fallback_for':'soilgrids_wcs'},{'soilgrids_wcs':object()},{},{})=='Backup not needed'


def test_comparison_reports_actual_day_counts_and_excluded_partial_windows():
    frame=pd.DataFrame({'date':['2024-02-01','2024-03-01'],'period_end':['2024-02-29','2024-03-31'],'aet_mm':[58.,62.]})
    block=DataBlock('terraclimate',frame,'monthly',{}, {})
    simulation=pd.DataFrame({'date':pd.date_range('2024-02-01','2024-03-20'),'aet_mm':2.})
    from data_blocks.validation import comparison_audit
    result=compare(simulation,block)
    assert result.days.tolist()==[29]
    assert result.modeled_daily_mean.tolist()==result.observed_daily_mean.tolist()==[2.]
    audit=comparison_audit(simulation,block)
    assert audit['Matched windows']==1 and audit['Incomplete simulation windows']==1
