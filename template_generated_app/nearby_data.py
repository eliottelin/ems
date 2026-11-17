"""Find checked nearby source data without moving the simulation site."""
from datetime import date,timedelta
from io import BytesIO
import math
from time import monotonic
import numpy as np
import requests
from PIL import Image
from data_blocks import DataRequest,NoMatchingData,load_block,get_json
from source_requests import distance_km

SPATIAL_SOURCES={'soilgrids_wcs','hwsd','mod16a2gf','terraclimate','open_meteo','open_meteo_climate'}


def _sampled_cells(latitude,longitude,max_km):
    cells=[]
    for radius in sorted(set(min(max_km,x) for x in (.5,2.,5.,10.,25.))):
        for angle in (0,90,180,270):
            theta=math.radians(angle);delta=radius/6371.0088
            phi=math.radians(latitude);lam=math.radians(longitude)
            p=math.asin(math.sin(phi)*math.cos(delta)+math.cos(phi)*math.sin(delta)*math.cos(theta))
            l=lam+math.atan2(math.sin(theta)*math.sin(delta)*math.cos(phi),math.cos(delta)-math.sin(phi)*math.sin(p))
            cells.append({'latitude':math.degrees(p),'longitude':(math.degrees(l)+180)%360-180,'distance_km':radius})
    return cells


def _rank_cells(latitudes,longitudes,valid,latitude,longitude,max_km,limit=12,spacing_km=.5):
    candidates=[]
    ys,xs=np.broadcast_arrays(latitudes,longitudes)
    a,b=np.radians(ys),math.radians(latitude)
    h=np.sin((a-b)/2)**2+np.cos(a)*math.cos(b)*np.sin(np.radians(xs-longitude)/2)**2
    distances=6371.0088*2*np.arcsin(np.sqrt(np.clip(h,0,1)))
    mask=valid&np.isfinite(distances)&(distances<=max_km)&(distances>.02)&(ys>=-90)&(ys<=90)
    positions=np.argwhere(mask)
    for index in np.argsort(distances[mask]):
        r,c=positions[index];lat,lon=float(ys[r,c]),float(xs[r,c])
        lon=(lon+180)%360-180
        if any(distance_km(lat,lon,v['latitude'],v['longitude'])<spacing_km for v in candidates):
            continue
        candidates.append({'latitude':lat,'longitude':lon,'distance_km':float(distances[r,c])})
        if len(candidates)>=limit:
            break
    return candidates


def soilgrids_cells(request,max_km):
    """Use a bounded sand overview to avoid blind full-profile requests."""
    dy=max_km/111.195;dx=min(180.,dy/max(.01,math.cos(math.radians(request.latitude))))
    west,east=max(-180.,request.longitude-dx),min(180.,request.longitude+dx)
    south,north=max(-90.,request.latitude-dy),min(90.,request.latitude+dy)
    width=height=201
    response=requests.get('https://maps.isric.org/mapserv',params={
        'map':'/map/sand.map','SERVICE':'WCS','VERSION':'1.0.0','REQUEST':'GetCoverage',
        'COVERAGE':'sand_0-5cm_mean','CRS':'EPSG:4326','RESPONSE_CRS':'EPSG:4326',
        'BBOX':f'{west},{south},{east},{north}','WIDTH':width,'HEIGHT':height,'FORMAT':'GEOTIFF_INT16'},timeout=(10,30))
    response.raise_for_status()
    with Image.open(BytesIO(response.content)) as image:
        if image.size!=(width,height):
            raise ValueError('SoilGrids overview has unexpected dimensions')
        values=np.asarray(image,dtype=float)
        nodata=float(str(getattr(image,'tag_v2',{}).get(42113,'-32768')).strip('\x00'))
    if values.shape!=(height,width):
        raise ValueError('SoilGrids overview must contain one band')
    valid=np.isfinite(values)&(values!=nodata)&(values>=0)&(values<=1000)
    latitudes=north-(np.arange(height)[:,None]+.5)*(north-south)/height
    longitudes=west+(np.arange(width)[None,:]+.5)*(east-west)/width
    return _rank_cells(latitudes,longitudes,valid,request.latitude,request.longitude,max_km)


def modis_cells(request,max_km):
    """Search the provider's actual sinusoidal grid, then verify point records."""
    from data_blocks.observations import MODIS_URL
    available=get_json(MODIS_URL+'/dates',params={'latitude':request.latitude,'longitude':request.longitude})['dates']
    dates=[]
    for item in available:
        first=date.fromisoformat(item['calendar_date']);last=min(first+timedelta(days=7),date(first.year,12,31))
        if request.start<=first<=last<=request.end:
            dates.append(item)
    if not dates:
        raise NoMatchingData('No complete published satellite composite fits these run dates. Nearby pixels cannot supply a missing date window.',
            {'reason':'no_complete_time_window','available_first':min((d['calendar_date'] for d in available),default=None),
             'available_last':max((d['calendar_date'] for d in available),default=None)})
    stamp=max(dates,key=lambda d:d['calendar_date'])['modis_date']
    extent=math.ceil(max_km)
    payload=get_json(MODIS_URL+'/subset',params={'latitude':request.latitude,'longitude':request.longitude,
        'startDate':stamp,'endDate':stamp,'kmAboveBelow':extent,'kmLeftRight':extent})
    rows,cols=int(payload['nrows']),int(payload['ncols'])
    if not 1<=rows<=501 or not 1<=cols<=501:
        raise ValueError('Unexpected MODIS search-grid dimensions')
    bands={r['band']:np.asarray(r['data'],dtype=float).reshape(rows,cols) for r in payload['subset'] if r['modis_date']==stamp}
    et,pet,qc=(bands[k] for k in ('ET_500m','PET_500m','ET_QC_500m'))
    valid=np.isfinite(et)&np.isfinite(pet)&np.isfinite(qc)&(et>=-32767)&(et<=32700)&(pet>=-32767)&(pet<=32700)&(qc>=0)&(qc<255)&(qc==np.floor(qc))
    code=np.where(valid,qc,255).astype(np.uint8);scf=(code>>5)&7
    valid&=((code&1)==0)&((code&4)==0)&(scf<=1)
    if request.options.get('modis_quality','recommended')=='strict':
        valid&=(scf==0)&(((code>>3)&3)==0)
    cell=float(payload['cellsize']);x0=float(payload['xllcorner']);y0=float(payload['yllcorner'])
    if not np.isfinite([cell,x0,y0]).all() or not 400<=cell<=600:
        raise ValueError('Unexpected MOD16 sinusoidal grid metadata')
    # MODIS sphere radius from the product's sinusoidal grid definition.
    radius=6371007.181
    y=y0+(rows-np.arange(rows)[:,None]-.5)*cell
    x=x0+(np.arange(cols)[None,:]+.5)*cell
    latitudes=np.degrees(y/radius)
    longitudes=np.degrees(x/(radius*np.cos(y/radius)))
    return _rank_cells(latitudes,longitudes,valid,request.latitude,request.longitude,max_km)


def nearby_data_options(key,request,max_km=25.,limit=3,progress=None):
    """Candidates have usable data from this source, not just another soil map.

    Bounded grid/point sampling is not a guarantee of the globally nearest cell.
    A source outage or invalid response is propagated instead of searching random
    locations indefinitely. Every offered candidate passes the full source loader.
    """
    if key not in SPATIAL_SOURCES or not 0<max_km<=25 or not isinstance(limit,int) or not 1<=limit<=3:
        raise ValueError('Unsupported source or nearby search limits')
    options={k:v for k,v in request.options.items() if k!='source_proxies'}
    if key=='soilgrids_wcs':
        options['soilgrids_map_recovery']=True
        options['soilgrids_use_rest']=False
        # One search budget is shared by candidate checks. A missing cell must
        # not restart the full remote recovery budget at every new coordinate.
        options['_soil_deadline']=min(float(options.get('_soil_deadline',float('inf'))),monotonic()+120.)
    if key=='hwsd':
        from site_recovery import nearby_soil_sites
        options.pop('hwsd_unit_id',None)
        candidates=nearby_soil_sites(request.data_dir,request.latitude,request.longitude,max_km,limit,options)
    elif key=='soilgrids_wcs':
        try:
            candidates=soilgrids_cells(request,max_km)
        except (requests.RequestException,ValueError,OSError):
            candidates=[]
        if not candidates:
            candidates=_sampled_cells(request.latitude,request.longitude,max_km)
    elif key=='mod16a2gf':
        candidates=modis_cells(request,max_km)
    else:
        candidates=_sampled_cells(request.latitude,request.longitude,max_km)
    found=[];checked=0
    for candidate in candidates:
        checked+=1
        if progress:
            progress(checked,len(candidates))
        effective=DataRequest(candidate['latitude'],candidate['longitude'],request.start,request.end,request.data_dir,options)
        try:
            if key in ('soilgrids_wcs','hwsd'):
                from source_cache import load_cached_soil, soil_local_signature
                block=load_cached_soil(key,effective,soil_local_signature(effective.data_dir))
            else:
                block=load_block(key,effective)
        except NoMatchingData as exc:
            if exc.details.get('reason') in ('no_overlap_time','no_complete_time_window'):
                raise
            continue
        if key in ('terraclimate','mod16a2gf'):
            complete=block.frame.date.ge(str(request.start))&block.frame.period_end.le(str(request.end))
            if not complete.any():
                continue
        found.append({**candidate,'source_id':key,'records':len(block.frame),
            'checked_for':[str(request.start),str(request.end)],'support':'Explicit nearby proxy; original site stays fixed'})
        if len(found)>=limit:
            break
    if not found:
        raise NoMatchingData(f'No usable nearby data found in the checked {key} cells within {max_km:g} km.',
            {'search_radius_km':max_km,'checked_locations':checked,'meaning':'Finite grid/point search; not proof that every pixel is missing. A different period or source may be needed.'})
    return found
