"""Explicit nearby-source requests preserve the simulation site and provenance."""
from datetime import date
import math
from data_blocks import DataBlock,DataRequest


def distance_km(latitude,longitude,other_latitude,other_longitude):
    a,b=map(math.radians,(latitude,other_latitude))
    dphi=b-a;dlambda=math.radians(other_longitude-longitude)
    h=math.sin(dphi/2)**2+math.cos(a)*math.cos(b)*math.sin(dlambda/2)**2
    return 6371.0088*2*math.asin(math.sqrt(min(1.,max(0.,h))))


def source_request(key,request):
    selection=request.options.get('source_proxies',{}).get(key)
    if not selection or selection.get('site')!=[request.latitude,request.longitude]:
        return request
    first=date.fromisoformat(selection['period_start']) if selection.get('period_start') else request.start
    last=date.fromisoformat(selection['period_end']) if selection.get('period_end') else request.end
    options={k:v for k,v in request.options.items() if k!='source_proxies'}
    if key=='hwsd':
        # The geographic location must determine this proxy, never an old override.
        options.pop('hwsd_unit_id',None)
    if key=='srdb' and selection.get('record') is not None:
        options['srdb_record']=selection['record']
    return DataRequest(selection['latitude'],selection['longitude'],first,last,request.data_dir,options)


def tag_source_proxy(key,block,original,effective):
    if effective is original:
        return block
    proxy={'selection':'Explicit user selection; original simulation site retained',
        'simulation_site':{'latitude':original.latitude,'longitude':original.longitude},
        'source_site':{'latitude':effective.latitude,'longitude':effective.longitude},
        'distance_km':distance_km(original.latitude,original.longitude,effective.latitude,effective.longitude),
        'source_period':[str(effective.start),str(effective.end)],
        'simulation_period':[str(original.start),str(original.end)],
        'meaning':'Nearby proxy data, not measurements or independent validation at the simulation site'}
    return DataBlock(block.id,block.frame,block.resolution,block.units,{**block.provenance,'spatial_proxy':proxy})


def proxy_selection(candidate,latitude,longitude):
    selection={'latitude':float(candidate['latitude']),'longitude':float(candidate['longitude']),
        'site':[latitude,longitude]}
    for field in ('period_start','period_end','record'):
        if candidate.get(field) is not None:
            selection[field]=candidate[field]
    # Validate coordinates and any study dates before writing session state.
    source_request('srdb',DataRequest(latitude,longitude,date(2000,1,1),date(2000,12,31),
        options={'source_proxies':{'srdb':selection}}))
    return selection
