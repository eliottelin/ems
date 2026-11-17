"""Find actual nearby mapped soil cells; never silently relocate a simulation."""
from pathlib import Path
import math
import numpy as np
import pandas as pd
from data_blocks import NoMatchingData,require_columns


def nearby_soil_sites(data_dir,latitude,longitude,max_km=25.,limit=3,options=None):
    from data_blocks.soil import ensure_hwsd_map,load_hwsd_table
    if not np.isfinite([latitude,longitude,max_km]).all() or not -90<=latitude<=90 or not -180<=longitude<=180 or not 0<max_km<=100 or not isinstance(limit,int) or not 1<=limit<=10:
        raise ValueError('Invalid site/radius for mapped-soil lookup')
    folder=Path(data_dir)/'hwsd'
    bil,hdr=ensure_hwsd_map(data_dir,options)
    tables=[folder/'HWSD2_LAYERS.csv',folder/'HWSD2_LAYERS.csv.gz']
    table=next((p for p in tables if p.is_file()),None)
    if table is None:
        databases=sorted(Path(data_dir).rglob('HWSD2.mdb'))
        if not databases:
            raise FileNotFoundError('HWSD soil table is needed to verify candidate cells')
        table=databases[0]
    frame,_=load_hwsd_table(table)
    keys=['HWSD2_SMU_ID','SEQUENCE','TOPDEP','BOTDEP','SAND','SILT','CLAY','ORG_CARBON','BULK','COARSE']
    frame.columns=frame.columns.str.upper()
    require_columns(frame,keys)
    frame=frame[keys].apply(pd.to_numeric,errors='coerce')
    frame=frame[frame.SEQUENCE.eq(1)].copy()
    frame['valid']=np.isfinite(frame[keys]).all(axis=1)&frame.BULK.gt(0)&frame.TOPDEP.ge(0)&frame.BOTDEP.gt(frame.TOPDEP)
    frame['valid']&=frame[['SAND','SILT','CLAY','COARSE']].ge(0).all(axis=1)&frame[['SAND','SILT','CLAY','COARSE']].le(100).all(axis=1)&frame.ORG_CARBON.between(0,100)
    frame['valid']&=(frame.SAND+frame.SILT+frame.CLAY-100).abs().le(5)
    frame=frame.sort_values(['HWSD2_SMU_ID','TOPDEP'])
    previous_bottom=frame.groupby('HWSD2_SMU_ID').BOTDEP.shift()
    frame['valid']&=previous_bottom.isna()|frame.TOPDEP.eq(previous_bottom)
    groups=frame.groupby('HWSD2_SMU_ID').agg(valid=('valid','all'),top=('TOPDEP','min'),bottom=('BOTDEP','max'))
    units=groups.index[groups.valid&groups.top.eq(0)&groups.bottom.ge(200)].to_numpy()
    header={parts[0].upper():parts[1] for line in hdr.read_text().splitlines() if len(parts:=line.split())>=2}
    rows,cols=int(header['NROWS']),int(header['NCOLS'])
    dx,dy=float(header['XDIM']),float(header['YDIM'])
    x0,y0=float(header['ULXMAP']),float(header['ULYMAP'])
    cr=math.floor((longitude-x0)/dx+.5);rr=math.floor((y0-latitude)/dy+.5)
    if not 0<=rr<rows or not 0<=cr<cols:
        raise NoMatchingData('Selected point lies outside the HWSD geographic map')
    reach_y=math.ceil(max_km/111./dy)+1
    reach_x=math.ceil(max_km/(111.*max(.01,math.cos(math.radians(latitude))))/dx)+1
    r0,r1=max(0,rr-reach_y),min(rows,rr+reach_y+1)
    c0,c1=max(0,cr-reach_x),min(cols,cr+reach_x+1)
    bits=int(header['NBITS'])
    if bits not in (16,32) or header.get('LAYOUT','BIL').upper()!='BIL' or int(header.get('NBANDS','1'))!=1:
        raise ValueError('HWSD candidate lookup requires a single-band 16/32-bit BIL grid')
    order='<' if header.get('BYTEORDER','I').upper() in ('I','LSBFIRST') else '>'
    kind='i' if header.get('PIXELTYPE','UNSIGNEDINT').upper() in ('SIGNED','SIGNEDINT') else 'u'
    dtype=np.dtype(f'{order}{kind}{bits//8}')
    stride=int(header.get('TOTALROWBYTES',cols*dtype.itemsize));offset=int(header.get('SKIPBYTES',0))
    data=np.memmap(bil,dtype='u1',mode='r')
    required=offset+(rows-1)*stride+cols*dtype.itemsize
    if len(data)<required:
        raise ValueError('HWSD map is truncated')
    raster=np.ndarray((rows,cols),dtype=dtype,buffer=data,offset=offset,strides=(stride,dtype.itemsize))
    window=np.asarray(raster[r0:r1,c0:c1])
    latitudes=y0-np.arange(r0,r1)[:,None]*dy
    longitudes=x0+np.arange(c0,c1)[None,:]*dx
    phi=np.radians(latitudes);dphi=phi-math.radians(latitude);dlambda=np.radians(longitudes-longitude)
    hav=np.sin(dphi/2)**2+np.cos(phi)*math.cos(math.radians(latitude))*np.sin(dlambda/2)**2
    distance=6371.0088*2*np.arcsin(np.sqrt(np.clip(hav,0,1)))
    eligible=np.isin(window,units)&(distance<=max_km)&(distance>.01)
    positions=np.argwhere(eligible)
    if not len(positions):
        raise NoMatchingData(f'No complete HWSD soil profile found within {max_km:g} km; choose a field site or an explicit soil preset.')
    selected=[]
    for index in np.argsort(distance[eligible]):
        r,c=positions[index]
        lat,lon=float(latitudes[r,0]),float(longitudes[0,c])
        # Offer geographically distinct candidates instead of three adjacent cells.
        if any(111*math.hypot(lat-float(p['latitude']),(lon-float(p['longitude']))*math.cos(math.radians(lat)))<2 for p in selected):
            continue
        selected.append({'latitude':lat,'longitude':lon,'distance_km':float(distance[r,c]),'mapping_unit':int(window[r,c]),
            'source':'HWSD geographic soil cell; not a verified field site or guaranteed satellite observation'})
        if len(selected)>=limit:
            break
    return selected
