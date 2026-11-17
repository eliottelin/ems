"""Field respiration and monthly/composite water comparison products."""
from datetime import date, timedelta
from hashlib import sha256
from io import StringIO, BytesIO
from urllib.parse import urljoin
import re
import xml.etree.ElementTree as ET
import csv
from collections import Counter
import numpy as np
import pandas as pd
import requests
from . import DataBlock, DataRequest, NoMatchingData, get_json, require_columns

SRDB_URL = "https://raw.githubusercontent.com/bpbond/srdb/master/srdb-data.csv"
MODIS_URL = "https://modis.ornl.gov/rst/api/v1/MOD16A2GF"
TERRA_CATALOG = "https://tds-proxy.nkn.uidaho.edu/thredds/terraclimate_aggregated.xml"


def csv_text(path, url=None, params=None):
    if path:
        return open(path, encoding="utf-8-sig").read(), str(path)
    response = requests.get(url, params=params, timeout=60)
    response.raise_for_status()
    return response.text, response.url


def srdb_annual_window(midyear,years=1):
    """Documented half-year midpoint convention, not int(midyear) calendar dates.

    Source dates are rounded, so these remain inferred comparison windows.
    Multi-year averages are not silently treated as a single-year measurement.
    """
    try:
        midyear,years=float(midyear),float(years)
    except (TypeError,ValueError):
        return None
    if not np.isfinite([midyear,years]).all() or years!=1:
        return None
    year=int(np.floor(midyear)); fraction=midyear-year
    if not 1<year<9999:
        return None
    if np.isclose(fraction,.5,rtol=0,atol=1e-8):
        return date(year,1,1),date(year,12,31)
    if np.isclose(fraction,0,rtol=0,atol=1e-8):
        return date(year-1,7,1),date(year,6,30)
    return None


def load_srdb(request: DataRequest) -> DataBlock:
    local = request.options.get("srdb_path")
    if not local and (request.data_dir / "srdb" / "srdb-data.csv").is_file():
        local = request.data_dir / "srdb" / "srdb-data.csv"
    text, source = csv_text(local, SRDB_URL)
    # Read the whole schema consistently. Mixed free-text error/depth fields
    # are retained; comparison columns are explicitly converted below.
    frame = pd.read_csv(StringIO(text), na_values=[-9999, -999, "NA"],low_memory=False)
    require_columns(frame, ["Record_number", "Study_number", "Study_midyear", "Latitude", "Longitude", "Rs_annual", "Rh_annual"])
    for key in ["Study_midyear", "Latitude", "Longitude", "Rs_annual", "Rh_annual"]:
        frame[key] = pd.to_numeric(frame[key], errors="coerce")
    frame = frame.dropna(subset=["Latitude", "Longitude", "Study_midyear"]).copy()
    frame = frame[frame.Latitude.between(-90, 90) & frame.Longitude.between(-180, 180)].copy()
    if frame.empty:
        raise NoMatchingData('SRDB contains no records with valid coordinates and study years.',
            {'catalog_records':0,'nearest_qualified_studies':[],'source':source})
    lat, lon = np.radians(frame.Latitude.to_numpy()), np.radians(frame.Longitude.to_numpy())
    dlat, dlon = lat-np.radians(request.latitude), lon-np.radians(request.longitude)
    a = np.sin(dlat/2)**2 + np.cos(lat)*np.cos(np.radians(request.latitude))*np.sin(dlon/2)**2
    frame["distance_km"] = 6371.0088*2*np.arcsin(np.sqrt(np.clip(a, 0, 1)))
    radius = float(request.options.get("srdb_radius_km", 1.))
    if not np.isfinite(radius) or radius <= 0:
        raise ValueError("SRDB matching radius must be positive")
    # In the source archive a blank flag means no flag; requiring the literal
    # string Q0 incorrectly eliminated every otherwise usable baseline record.
    frame['quality_flag_normalized']=frame.Quality_flag.astype('string').fillna('').str.strip().replace({'':'Q0'}) if 'Quality_flag' in frame else 'unknown'
    eligible=pd.Series(False,index=frame.index)
    if {'Annual_coverage','YearsOfData','Manipulation'}.issubset(frame):
        eligible=(frame.quality_flag_normalized.eq('Q0')
            & pd.to_numeric(frame.Annual_coverage,errors='coerce').ge(.99)
            & pd.to_numeric(frame.YearsOfData,errors='coerce').eq(1)
            & frame.Manipulation.astype('string').fillna('').str.strip().str.lower().isin(['','none','control']))
    windows=[srdb_annual_window(mid,years) for mid,years in zip(frame.Study_midyear,frame.get('YearsOfData',pd.Series(np.nan,index=frame.index)))]
    frame['period_start']=[str(w[0]) if w else None for w in windows]
    frame['period_end']=[str(w[1]) if w else None for w in windows]
    frame['period_inferred']=True
    frame['comparable_rh']=(eligible & np.isfinite(frame.Rh_annual) & frame.Rh_annual.ge(0) & frame.period_start.notna())
    coarse_year=frame.Study_midyear.astype(int).astype(str)
    first=frame.period_start.fillna(coarse_year+'-01-01')
    last=frame.period_end.fillna(coarse_year+'-12-31')
    selected=frame[(frame.distance_km<=radius)&first.le(str(request.end))&last.ge(str(request.start))].copy()
    if request.options.get('srdb_record') is not None:
        selected=selected[selected.Record_number.eq(float(request.options['srdb_record']))]
    choices=frame[frame.comparable_rh & first.ge('1941-01-01') & last.lt(str(date.today()))].sort_values('distance_km').head(5)
    fields=['Record_number','Study_number','Study_midyear','Latitude','Longitude','Rh_annual','distance_km','period_start','period_end','period_inferred']
    if 'Site_name' in frame:
        fields.append('Site_name')
    catalog={'catalog_records':len(frame),'radius_km':radius,'nearest_qualified_studies':choices[fields].to_dict('records'),
             'qualified_annual_rh_records':int(frame.comparable_rh.sum()),
             'available_study_years':[int(frame.Study_midyear.min()),int(frame.Study_midyear.max())]}
    if selected.empty:
        raise NoMatchingData(f'No SRDB study within {radius:g} km matches {request.start} to {request.end}.',
            {**catalog,
             'source':source,'source_sha256':sha256(text.encode()).hexdigest(),
             'meaning':'These are distinct study sites/windows, not substitutes for the current run. Windows are inferred from the documented midpoint convention and need study-level chronology review.'})
    frame=selected
    return DataBlock("srdb", frame, "study annual", {"Rh_annual": "g C/m2/year", "Rs_annual": "g C/m2/year"},
                     {"source": source, "sha256": sha256(text.encode()).hexdigest(), **catalog,
                      "matching": "Geographic/year candidates; ecological/site equivalence still needs review",
                      "respiration": "Only Rh_annual is comparable to modeled heterotrophic respiration",
                      'quality':'Blank flags mean no flags and normalize to Q0; all other flags remain excluded',
                      'time_support':'One-year windows inferred from source midpoint: X.5 => calendar X; X.0 => July X-1 through June X. Confirm chronology in the study.',
                      'time_reference':'https://github.com/bpbond/srdb/blob/master/expanded_field_notes.txt'})


def discover_ncss(variable):
    response = requests.get(TERRA_CATALOG, timeout=60)
    response.raise_for_status()
    root = ET.fromstring(response.content)
    services = [node.get("base") for node in root.iter() if node.get("serviceType", "").lower() == "netcdfsubset"]
    datasets = [node.get("urlPath") for node in root.iter()
                if node.get("urlPath") and re.search(rf"(?:_|/){variable}(?:_|/|\.)", node.get("urlPath", ""))]
    if not services or not datasets:
        raise ValueError("TerraClimate catalog has changed. Set terraclimate_url to its current NCSS point endpoint, or provide NCSS CSV files in data/terraclimate/.")
    return urljoin(TERRA_CATALOG, services[0]) + datasets[0]


def ncss_period(url,start,end):
    """Request only available monthly timestamps, keeping original model dates."""
    response = requests.get(url.rstrip('/')+'/dataset.xml',timeout=60)
    response.raise_for_status()
    root = ET.fromstring(response.content)
    span = next((node for node in root.iter() if node.tag.rsplit('}',1)[-1]=='TimeSpan'),None)
    if span is None:
        raise ValueError('TerraClimate time coverage metadata missing')
    bounds = {node.tag.rsplit('}',1)[-1]:node.text for node in span}
    first,last = pd.Timestamp(bounds['begin']).date(),pd.Timestamp(bounds['end']).date()
    clipped_start,clipped_end = max(start,first),min(end,last)
    if clipped_start > clipped_end:
        raise NoMatchingData(f'TerraClimate has no overlapping monthly timestamps; available {first} to {last}',
            {'reason':'no_overlap_time','available_first':str(first),'available_last':str(last)})
    return clipped_start,clipped_end,{'available_first_timestamp':str(first),'available_last_timestamp':str(last),
                                    'requested_start':str(clipped_start),'requested_end':str(clipped_end)}


def parse_terraclimate(text, variable, site=None):
    frame = pd.read_csv(StringIO(text), comment="#")
    columns = {key: re.split(r"\[|\(", key)[0].strip().lower() for key in frame.columns}
    source_column = next((key for key, name in columns.items() if name == variable), None)
    time_column = next((key for key, name in columns.items() if name in ("date", "time")), None)
    if source_column is None or time_column is None:
        raise ValueError(f"TerraClimate NCSS CSV must contain date/time and {variable} columns")
    if not re.search(r"(?:unit[s]?\s*=\s*[\"']?mm|\(mm\))", source_column, re.I):
        raise ValueError("TerraClimate CSV must declare decoded mm units in each variable header; packed NetCDF integers are not accepted")
    if site is not None:
        for names, expected in ((('latitude', 'lat'), site[0]), (('longitude', 'lon'), site[1])):
            coordinate = next((key for key, name in columns.items() if name in names), None)
            if coordinate is None:
                raise ValueError("TerraClimate NCSS point CSV must include latitude and longitude for site matching")
            points = pd.to_numeric(frame[coordinate], errors="raise")
            distances = (points - expected).abs()
            if names[0] == 'longitude':
                distances = np.minimum(distances, (360-distances).abs())
            if not np.isfinite(points).all() or (distances > .025).any():
                raise ValueError("TerraClimate point coordinates do not match the requested site/grid cell")
    dates = pd.to_datetime(frame[time_column], utc=True, errors="raise").dt.tz_localize(None)
    periods = dates.dt.to_period("M")
    result = pd.DataFrame({"date": periods.dt.start_time.dt.strftime("%Y-%m-%d"),
                           "period_end": periods.dt.end_time.dt.strftime("%Y-%m-%d"),
                           f"{variable}_mm": pd.to_numeric(frame[source_column], errors="raise")})
    if result.date.duplicated().any() or not np.isfinite(result[f"{variable}_mm"]).all() or (result[f"{variable}_mm"] < 0).any():
        raise ValueError("TerraClimate monthly values must be unique, finite and nonnegative")
    return result


def parse_terra_netcdf(content, variable, site):
    """Decode CF packing from point NetCDF, avoiding NCSS CSV's packed-mm bug."""
    from scipy.io import netcdf_file
    with netcdf_file(BytesIO(content), mmap=False) as dataset:
        def attr_text(value):
            return value.decode() if isinstance(value, bytes) else str(value)
        value = dataset.variables[variable]
        if attr_text(getattr(value,'units','')).strip() != 'mm':
            raise ValueError('TerraClimate NetCDF variable must use mm')
        raw = np.asarray(value[:],dtype=float).reshape(-1).copy()
        valid = np.isfinite(raw)
        for attr in ('_FillValue','missing_value'):
            if hasattr(value,attr):
                valid &= raw != float(getattr(value,attr))
        scale, offset = float(getattr(value,'scale_factor',1)),float(getattr(value,'add_offset',0))
        decoded = raw*scale+offset
        time = dataset.variables['time']
        calendar = attr_text(getattr(time,'calendar','standard'))
        if calendar not in ('standard','gregorian','proleptic_gregorian'):
            raise ValueError('Unsupported TerraClimate calendar')
        units = attr_text(time.units)
        match = re.match(r'(days|hours|seconds) since (.+)',units)
        if not match:
            raise ValueError('Unsupported TerraClimate time units')
        base = pd.Timestamp(match[2],tz='UTC')
        stamps = base+pd.to_timedelta(np.asarray(time[:]).reshape(-1),unit={'days':'D','hours':'h','seconds':'s'}[match[1]])
        if len(stamps) != len(raw):
            raise ValueError('TerraClimate must be a single-point time series')
        coords = []
        for names, expected in ((('latitude','lat'),site[0]),(('longitude','lon'),site[1])):
            name = next((key for key in names if key in dataset.variables),None)
            if name is None:
                raise ValueError('TerraClimate point coordinates missing')
            values = np.asarray(dataset.variables[name][:],dtype=float).reshape(-1)
            delta = abs(values-expected)
            if names[0]=='longitude':
                delta = np.minimum(delta,abs(360-delta))
            if not np.isfinite(values).all() or (delta > .025).any():
                raise ValueError('TerraClimate point coordinates do not match requested site')
            coords.append(float(values[0]))
        valid &= np.isfinite(decoded) & (decoded >= 0)
        csv = pd.DataFrame({'time':stamps.astype(str),'latitude':coords[0],'longitude':coords[1],f'{variable}[unit="mm"]':decoded})
        result = parse_terraclimate(csv.loc[valid].to_csv(index=False),variable,site)
        return result, {'scale_factor':scale,'add_offset':offset,'rejected_records':int((~valid).sum()),
                        'decoding':'CF packed NetCDF values; scaled exactly once',
                        'version':attr_text(getattr(dataset,'version','unreported'))}


def load_terraclimate(request: DataRequest) -> DataBlock:
    frames, sources = [], []
    for variable in ("aet", "pet"):
        local = request.options.get(f"terraclimate_{variable}_path")
        candidate = request.data_dir / "terraclimate" / f"{variable}.csv"
        if not local and candidate.is_file():
            local = candidate
        override = request.options.get("terraclimate_url")
        url = override.format(variable=variable) if override else (None if local else discover_ncss(variable))
        if local:
            text, source = csv_text(local)
            frames.append(parse_terraclimate(text, variable, (request.latitude, request.longitude)))
            sources.append({'variable':variable,'source':source,'sha256':sha256(text.encode()).hexdigest(),'decoding':'local CSV must already contain decoded mm'})
        else:
            first,last,coverage = ncss_period(url,request.start,request.end)
            response = requests.get(url, params={"var": variable, "latitude": request.latitude, "longitude": request.longitude,
                "time_start": f"{first}T00:00:00Z", "time_end": f"{last}T23:59:59Z", "accept": "netcdf"},timeout=60)
            response.raise_for_status()
            frame, metadata = parse_terra_netcdf(response.content,variable,(request.latitude,request.longitude))
            frames.append(frame)
            sources.append({'variable':variable,'source':response.url,'sha256':sha256(response.content).hexdigest(),**metadata,**coverage})
    frame = frames[0].merge(frames[1], on=["date", "period_end"], validate="one_to_one")
    frame = frame[(frame.date <= str(request.end)) & (frame.period_end >= str(request.start))].copy()
    return DataBlock("terraclimate", frame, "monthly", {"aet_mm": "mm/month", "pet_mm": "mm/month"},
                     {"source": "TerraClimate THREDDS NCSS", "assets": sources,
                      "version": request.options.get("terraclimate_version", "See source asset paths; do not mix product versions"),
                      "use": "AET model comparison; PET is a separate reference quantity, not modeled AET"})


def parse_modis(payload,quality='recommended'):
    if quality not in ('recommended','strict'):
        raise ValueError('Unknown satellite quality policy')
    rows,rejected={},0
    reasons=Counter();fill_codes=Counter()
    ncols, nrows = int(payload.get("ncols", 1)), int(payload.get("nrows", 1))
    center = (nrows//2)*ncols+ncols//2
    for record in payload.get("subset", []):
        reported = record.get('version')
        version = str(reported).replace(".", "").zfill(3) if reported is not None else None
        if version is not None and version != "061":
            raise ValueError(f"Expected MOD16A2GF collection 061; received {version}. No silent collection substitution.")
        band = record["band"]
        key = {"ET_500m": "aet_mm", "PET_500m": "pet_mm", "ET_QC_500m": "qc"}.get(band)
        if key is None:
            continue
        stamp = record["modis_date"]
        when = pd.to_datetime(stamp, format="A%Y%j").date()
        if (when-date(when.year, 1, 1)).days % 8:
            raise ValueError("MOD16 date is not aligned to an 8-day composite")
        data = record["data"]
        if len(data) != nrows*ncols:
            raise ValueError("MOD16 subset has the wrong pixel count")
        row = rows.setdefault(stamp, {"date": str(when), "period_end": str(min(when+timedelta(days=7), date(when.year, 12, 31)))})
        row[key] = float(data[center])
    good = []
    for row in rows.values():
        if not {"aet_mm", "pet_mm", "qc"}.issubset(row):
            raise ValueError("MOD16 requires ET, PET and QA for every composite")
        qc = row["qc"]
        if not all(np.isfinite(row[key]) for key in ("aet_mm", "pet_mm", "qc")):
            rejected += 1; reasons['missing_or_nonfinite']+=1
            continue
        if any(not -32767<=row[key]<=32700 for key in ('aet_mm','pet_mm')):
            rejected+=1; reasons['nonvegetated_or_fill']+=1
            for code in set(row[key] for key in ('aet_mm','pet_mm') if row[key]>32700):
                fill_codes[str(int(code))]+=1
            continue
        if qc!=int(qc) or not 0<=qc<255:
            rejected+=1; reasons['invalid_qa']+=1
            continue
        code=int(qc); scf=(code>>5)&7; cloud=(code>>3)&3
        # GF inherits input LAI cloud bits. Main-algorithm SCF 0/1 remains
        # usable for gap-filled ET; strict mode additionally demands clear/best.
        bad=(code&1) or (code&4) or scf>1
        if quality=='strict':
            bad=bad or cloud!=0 or scf!=0
        if bad:
            rejected+=1; reasons['quality_policy']+=1
            continue
        for key in ("aet_mm", "pet_mm"):
            row[key] *= .1
        good.append(row)
    frame = pd.DataFrame(good, columns=["date", "period_end", "aet_mm", "pet_mm", "qc"])
    reported = [record.get('version') for record in payload.get('subset',[])]
    frame.attrs.update(rejection_reasons=dict(reasons),fill_codes=dict(fill_codes),quality_policy=quality)
    frame.attrs['collection'] = '061' if reported and all(v is not None for v in reported) else 'unreported by ORNL point API; v6.1 not verified'
    return frame, rejected


def modis_csv_payload(text):
    """CSV granule names retain collection numbers omitted by ORNL JSON."""
    records = []
    for fields in csv.reader(StringIO(text)):
        if not fields or not any(value.strip() for value in fields):
            continue
        parts = fields[0].split('.',5)
        if len(parts)!=6 or parts[0]!='MOD16A2GF':
            raise ValueError('ORNL CSV response has no verifiable MOD16A2GF granule names')
        product,stamp,tile,collection,processed,band = parts
        if len(fields)<6 or fields[1]!=product or fields[2]!=stamp:
            raise ValueError('ORNL CSV granule metadata disagree')
        data = [float(value) for value in fields[5:]]
        if len(data)!=1:
            raise ValueError('Expected a single center-pixel MODIS subset')
        records.append({'modis_date':stamp,'tile':tile,'version':collection,'proc_date':processed,'band':band,'data':data})
    if not records:
        raise ValueError('ORNL CSV returned no granules')
    return {'nrows':1,'ncols':1,'subset':records}


def load_mod16a2gf(request: DataRequest) -> DataBlock:
    available=get_json(MODIS_URL+'/dates',params={'latitude':request.latitude,'longitude':request.longitude})['dates']
    coverage={'available_first':min((d['calendar_date'] for d in available),default=None),
              'available_last':max((d['calendar_date'] for d in available),default=None),
              'requested_start':str(request.start),'requested_end':str(request.end)}
    dates=[d for d in available if str(request.start-timedelta(days=7))<=d['calendar_date']<=str(request.end)]
    if not dates:
        raise NoMatchingData('MOD16A2GF has no published composites overlapping the requested dates.',{**coverage,'reason':'no_overlap_time'})
    frames=[];rejected=0;collections=[];reasons=Counter();codes=Counter()
    quality=request.options.get('modis_quality','recommended')
    for offset in range(0,len(dates),10):
        group=dates[offset:offset+10]
        params={'latitude':request.latitude,'longitude':request.longitude,'startDate':group[0]['modis_date'],
                'endDate':group[-1]['modis_date'],'kmAboveBelow':0,'kmLeftRight':0}
        payload=get_json(MODIS_URL+'/subset',params=params)
        frame,bad=parse_modis(payload,quality)
        # Empty/rejected JSON can explain missing coverage without pretending
        # to verify its collection. Any data used still need collection 061.
        if not frame.empty and any(r.get('version') is None for r in payload.get('subset',[])):
            response=requests.get(MODIS_URL+'/subset',params=params,headers={'Accept':'text/csv'},timeout=60)
            response.raise_for_status()
            frame,bad=parse_modis(modis_csv_payload(response.text),quality)
        reasons.update(frame.attrs.get('rejection_reasons',{}));codes.update(frame.attrs.get('fill_codes',{}))
        rejected+=bad
        if not frame.empty:
            frames.append(frame);collections.append(frame.attrs['collection'])
    frame=pd.concat(frames,ignore_index=True) if frames else pd.DataFrame()
    details={**coverage,'requested_composites':len(dates),'rejected_composites':rejected,
             'rejection_reasons':dict(reasons),'fill_codes':dict(codes),'quality_policy':quality}
    if frame.empty:
        message=('MOD16A2GF marks this pixel as urban/built-up (32762); it has no ecosystem ET values.' if codes.get('32762')
                 else 'MOD16A2GF has no accepted ET composites at this pixel; inspect coverage and rejection counts.')
        raise NoMatchingData(message,details)
    if frame.date.duplicated().any():
        raise ValueError('MOD16 returned duplicate composites')
    return DataBlock('mod16a2gf',frame,'8-day composite',{'aet_mm':'mm/composite','pet_mm':'mm/composite'},
        {'source':MODIS_URL,'collection':'061' if set(collections)=={'061'} else 'unverified',
         'scale_factor':.1,**details,
         'qa':'Recommended: MODLAND good, functional detectors, main algorithm SCF 0/1; inherited cloud bits retained. Strict: clear sky and SCF 0.',
         'qa_reference':'https://modis-land.gsfc.nasa.gov/pdf/MOD16UsersGuideV2.022019.pdf',
         'citation':'NASA MOD16A2GF v6.1; ORNL DAAC TESViS REST service'})
