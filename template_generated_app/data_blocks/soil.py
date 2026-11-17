"""Automatic SoilGrids REST recovery, checked numeric maps and local HWSD access."""
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FutureTimeout
from functools import lru_cache
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import math
import re
from threading import Lock
from time import monotonic
from html.parser import HTMLParser
import numpy as np
import pandas as pd
import requests
from PIL import Image
from PIL.TiffImagePlugin import TiffImageFile
from . import DataBlock, DataRequest, NoMatchingData, DataSourceError, require_columns

DEPTHS = [(0, 5), (5, 15), (15, 30), (30, 60), (60, 100), (100, 200)]
PROPERTIES = {
    "sand": ("sand_pct", 10.), "silt": ("silt_pct", 10.), "clay": ("clay_pct", 10.),
    "soc": ("soc_g_kg", 10.), "bdod": ("bulk_density_g_cm3", 100.),
    "cfvo": ("coarse_fragments_pct", 10.),
}
UNITS = {"top_cm": "cm", "bottom_cm": "cm", "sand_pct": "%", "silt_pct": "%",
         "clay_pct": "%", "soc_g_kg": "g C/kg", "bulk_density_g_cm3": "g/cm3",
         "coarse_fragments_pct": "%"}
REST_URL = 'https://rest.isric.org/soilgrids/v2.0/properties/query'
_REST_CALLS = deque()
_REST_LOCK = Lock()
REST_BUDGET_SECONDS = 20.
MAP_RECOVERY_BUDGET_SECONDS = 120.
FAILURE_COOLDOWN_SECONDS = 300.
SITE_FAILURE_COOLDOWN_SECONDS = 60.
_REST_COOLDOWNS = {}
# A caller can move on at its deadline without waiting for a socket to unwind.
# The pool bounds outstanding work; these workers never render Streamlit UI.
_REST_WORKERS = ThreadPoolExecutor(max_workers=4, thread_name_prefix='soilgrids-rest')


def _reserve_rest_call():
    """Share ISRIC's five-calls/minute budget, including failed attempts."""
    with _REST_LOCK:
        now = monotonic()
        while _REST_CALLS and now - _REST_CALLS[0] >= 60:
            _REST_CALLS.popleft()
        if len(_REST_CALLS) >= 5:
            remaining = max(1, math.ceil(60-(now-_REST_CALLS[0])))
            raise _rest_skip('SoilGrids REST fair-use limit reached (5 calls/minute); using map services until the budget renews.',
                             'rate_limit', remaining)
        _REST_CALLS.append(now)


def _rest_skip(message, scope, retry_after_seconds, missing=False):
    """Describe a route that was deliberately skipped without another HTTP call."""
    details = {'rest_attempted': False, 'rest_status': 'cooldown',
               'scope': scope, 'retry_after_seconds': retry_after_seconds}
    if missing:
        return NoMatchingData(message, details)
    error = requests.RequestException(message)
    setattr(error, 'details', details)
    return error


def _remaining_budget(request, default):
    deadline = request.options.get('_soil_deadline')
    remaining = default if deadline is None else min(default, float(deadline) - monotonic())
    if remaining <= 0:
        raise requests.Timeout('SoilGrids retrieval time budget exhausted; use an available fallback.')
    return remaining


def _request_timeout(request, connect, read):
    remaining = _remaining_budget(request, connect + read)
    return min(connect, remaining), min(read, remaining)


def _bounded_rest_profile(request):
    """Bound foreground waiting; a timed-out HTTP worker finishes in the pool.

    Requests' socket timeout cannot impose a whole-operation deadline. Waiting
    on a future bounds the caller independently, including DNS and slow bodies.
    Cancellation cannot stop an already active HTTP request, so concurrency and
    request starts remain bounded separately by the pool and fair-use gate.
    """
    site = (request.latitude, request.longitude)
    with _REST_LOCK:
        now = monotonic()
        for key in list(_REST_COOLDOWNS):
            if _REST_COOLDOWNS[key][0] <= now:
                del _REST_COOLDOWNS[key]
        if request.options.get('soilgrids_retry'):
            _REST_COOLDOWNS.pop('service', None)
            _REST_COOLDOWNS.pop(site, None)
        cached = _REST_COOLDOWNS.get('service') or _REST_COOLDOWNS.get(site)
    if cached:
        until, missing, message = cached
        remaining = max(1, math.ceil(until-monotonic()))
        scope = 'site' if missing else 'service'
        explanation = (f'SoilGrids REST recently lacked data for this cell; retrying automatically in {remaining}s. '
                       if missing else f'SoilGrids REST is temporarily unavailable; retrying automatically in {remaining}s. ')
        _source_progress(request, 0., explanation+'Using map services now.')
        raise _rest_skip(explanation+message, scope, remaining, missing)
    deadline = monotonic() + _remaining_budget(request, REST_BUDGET_SECONDS)
    future = _REST_WORKERS.submit(_rest_profile, request)
    try:
        while True:
            remaining = deadline-monotonic()
            if remaining <= 0:
                raise FutureTimeout()
            try:
                return future.result(timeout=min(1., remaining))
            except FutureTimeout:
                if future.done():
                    return future.result()
                if monotonic() >= deadline:
                    raise
                _source_progress(request, 0., f'Waiting for SoilGrids REST · up to {REST_BUDGET_SECONDS:g}s before trying an available fallback')
    except FutureTimeout as exc:
        future.cancel()
        error = requests.Timeout(f'SoilGrids REST exceeded its {REST_BUDGET_SECONDS:g}s foreground budget; trying an available fallback.')
        with _REST_LOCK:
            _REST_COOLDOWNS['service'] = (monotonic()+FAILURE_COOLDOWN_SECONDS, False, str(error))
        raise error from exc
    except (requests.RequestException, NoMatchingData) as exc:
        response = getattr(exc, 'response', None)
        status = getattr(response, 'status_code', None)
        missing = isinstance(exc, NoMatchingData)
        transient = isinstance(exc, (requests.Timeout, requests.ConnectionError)) or status == 429 or (status is not None and status >= 500)
        if missing or transient:
            with _REST_LOCK:
                interval = SITE_FAILURE_COOLDOWN_SECONDS if missing else FAILURE_COOLDOWN_SECONDS
                _REST_COOLDOWNS[site if missing else 'service'] = (monotonic()+interval, missing, str(exc))
                # Missing cells are local, never evidence of a service outage.
                if len(_REST_COOLDOWNS) > 128:
                    del _REST_COOLDOWNS[next(iter(_REST_COOLDOWNS))]
        raise


def _source_progress(request, fraction, message):
    progress = request.options.get('_source_progress')
    if progress:
        progress(fraction, message)


def validate_profile(frame):
    require_columns(frame, UNITS)
    values = frame[list(UNITS)].apply(pd.to_numeric, errors="raise")
    if not np.isfinite(values.to_numpy()).all() or (values < 0).any().any():
        raise ValueError("Soil profile has invalid, negative, or missing values")
    if (values.bottom_cm <= values.top_cm).any() or (values.bulk_density_g_cm3 <= 0).any():
        raise ValueError("Soil depth intervals and bulk density must be positive")
    ordered = values.sort_values("top_cm")
    if (ordered.top_cm.to_numpy()[1:] < ordered.bottom_cm.to_numpy()[:-1]).any():
        raise ValueError("Soil depth intervals must not overlap")
    for key in ("sand_pct", "silt_pct", "clay_pct", "coarse_fragments_pct"):
        if (values[key] > 100).any():
            raise ValueError(f"{key} must be between 0 and 100")
    if (values.soc_g_kg > 1000).any():
        raise ValueError("Soil carbon content cannot exceed 1000 g/kg")
    if ((values.sand_pct + values.silt_pct + values.clay_pct - 100).abs() > 5).any():
        raise ValueError("Soil texture fractions must sum to approximately 100%")
    return values


def wcs_value(request, prop, top, bottom, endpoint='https://maps.isric.org/mapserv'):
    # WCS 1.0 supports a geographic BBOX and explicit one-pixel output. This
    # avoids confusing native Homolosine X/Y coordinates with longitude/latitude.
    lon, lat = request.longitude, request.latitude
    coverage = f"{prop}_{top}-{bottom}cm_mean"
    response = requests.get(endpoint, params={
        "map": f"/map/{prop}.map", "SERVICE": "WCS", "VERSION": "1.0.0",
        "REQUEST": "GetCoverage", "COVERAGE": coverage, "CRS": "EPSG:4326",
        "RESPONSE_CRS": "EPSG:4326", "BBOX": f"{max(-180,lon-.001)},{max(-90,lat-.001)},{min(180,lon+.001)},{min(90,lat+.001)}",
        "WIDTH": 3, "HEIGHT": 3, "FORMAT": "GEOTIFF_INT16"}, timeout=_request_timeout(request, 10, 30))
    response.raise_for_status()
    if response.content.lstrip().startswith(b"<"):
        raise ValueError(f"SoilGrids WCS returned a service exception for {coverage}")
    with Image.open(BytesIO(response.content)) as image:
        if not isinstance(image, TiffImageFile) or image.size != (3, 3):
            raise ValueError("SoilGrids returned an unexpected spatial subset")
        pixel = image.getpixel((1, 1))
        if not isinstance(pixel, (int, float)):
            raise ValueError('SoilGrids returned a nonnumeric coverage pixel')
        value = float(pixel)
        nodata = str(image.tag_v2.get(42113, "-32768")).strip("\x00")
        if not math.isfinite(value) or value == float(nodata) or value < 0:
            raise NoMatchingData(f"SoilGrids has no valid {coverage} at this site")
    return top, bottom, PROPERTIES[prop][0], value / PROPERTIES[prop][1]


class _InfoText(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts=[]
    def handle_data(self,data):
        self.parts.append(data.strip())


def parse_soil_point(text,prop,top,bottom,latitude,longitude):
    """Read ISRIC's numeric feature-info response with units and site checks."""
    parser=_InfoText(); parser.feed(text)
    content=' '.join(parser.parts)
    coverage=f'{prop}_{top}-{bottom}cm_mean'
    number=r'([-+]?\d+(?:\.\d*)?(?:[Ee][-+]?\d+)?)'
    match=re.search(r'\bValue\s*:\s*'+number,content,re.I)
    if not match:
        from . import NoMatchingData
        raise NoMatchingData(f'SoilGrids returned no numeric value for {coverage}')
    layers=re.findall(r'Query\s+Layer\s*:\s*(\S+)',content,re.I)
    if len(layers)>1:
        # The current ISRIC HTML template repeats the first layer name in a
        # multi-layer response. Never infer the other depths from record order.
        raise ValueError('SoilGrids point response has ambiguous multiple layer records; query one layer at a time')
    if len(layers)!=1 or layers[0]!=coverage:
        raise ValueError('SoilGrids point response has the wrong layer')
    expected={'sand':'g/kg','silt':'g/kg','clay':'g/kg','soc':'dg/kg','bdod':'cg/cm3','cfvo':'cm3/dm3'}
    unit=re.search(r'\bUnit\s*:\s*(\S+)',content,re.I)
    if not unit or unit[1].replace('³','3')!=expected[prop]:
        raise ValueError(f'SoilGrids point response has unexpected {prop} units')
    a=re.search(r'\bLatitude\s*:\s*'+number,content,re.I)
    b=re.search(r'\bLongitude\s*:\s*'+number,content,re.I)
    if not a or not b:
        raise ValueError('SoilGrids point response has no sampling coordinates')
    def distance(lat,lon):
        phi1,phi2=map(math.radians,[latitude,lat])
        dphi=phi2-phi1; dlambda=math.radians(lon-longitude)
        h=math.sin(dphi/2)**2+math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
        return 6371008.8*2*math.asin(math.sqrt(min(1.,max(0.,h))))
    # The current ISRIC template labels X as Latitude and Y as Longitude.
    # Accept that documented response defect only after a distance check.
    candidates=[(float(a[1]),float(b[1])),(float(b[1]),float(a[1]))]
    candidates=[(lat,lon) for lat,lon in candidates if -90<=lat<=90 and -180<=lon<=180]
    if not candidates:
        raise ValueError('SoilGrids returned invalid sampling coordinates')
    lat,lon=min(candidates,key=lambda xy:distance(*xy))
    separation=distance(lat,lon)
    if separation>400:
        raise ValueError(f'SoilGrids sampled a cell {separation:.0f} m away; expected the 250 m site cell')
    value=float(match[1])
    if not math.isfinite(value) or value<0 or value in (32767,65535):
        from . import NoMatchingData
        raise NoMatchingData(f'SoilGrids has no valid {coverage} at this site')
    return top,bottom,PROPERTIES[prop][0],value/PROPERTIES[prop][1],{'latitude':lat,'longitude':lon,'distance_m':separation}


def point_value(request,prop,top,bottom):
    coverage=f'{prop}_{top}-{bottom}cm_mean'
    lon,lat=request.longitude,request.latitude
    response=requests.get(f'https://dev-maps.isric.org/mapserv/{prop}',params={
        'SERVICE':'WMS','VERSION':'1.1.1','REQUEST':'GetFeatureInfo','LAYERS':coverage,'QUERY_LAYERS':coverage,
        'STYLES':'','SRS':'EPSG:4326','BBOX':f'{max(-180,lon-.001)},{max(-90,lat-.001)},{min(180,lon+.001)},{min(90,lat+.001)}',
        'WIDTH':101,'HEIGHT':101,'X':50,'Y':50,'INFO_FORMAT':'text/html'},timeout=_request_timeout(request, 10, 30))
    response.raise_for_status()
    response.encoding='utf-8'
    return parse_soil_point(response.text,prop,top,bottom,lat,lon)


def _rest_profile(request: DataRequest):
    """Retrieve all 36 mean values in one request, preserving mapped units."""
    _reserve_rest_call()
    response = requests.get(REST_URL, params={
        'lon': request.longitude, 'lat': request.latitude,
        'property': list(PROPERTIES), 'depth': [f'{top}-{bottom}cm' for top, bottom in DEPTHS],
        'value': 'mean'}, timeout=_request_timeout(request, 5, REST_BUDGET_SECONDS))
    try:
        response.raise_for_status()
        payload = response.json()
    finally:
        close = getattr(response, 'close', None)
        if close:
            close()
    if not isinstance(payload, dict) or payload.get('type') != 'Feature':
        raise ValueError('SoilGrids REST returned an invalid GeoJSON feature')
    geometry = payload.get('geometry')
    if not isinstance(geometry, dict) or geometry.get('type') != 'Point':
        raise ValueError('SoilGrids REST returned invalid point geometry')
    coordinates = geometry.get('coordinates')
    if (not isinstance(coordinates, list) or len(coordinates) != 2
            or any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in coordinates)
            or not all(math.isclose(value, expected, rel_tol=0, abs_tol=1e-6)
                       for value, expected in zip(coordinates, (request.longitude, request.latitude)))):
        raise ValueError('SoilGrids REST returned coordinates for a different site')
    properties = payload.get('properties')
    if not isinstance(properties, dict) or not isinstance(properties.get('layers'), list):
        raise ValueError('SoilGrids REST returned invalid property layers')
    expected_units = {'sand': 'g/kg', 'silt': 'g/kg', 'clay': 'g/kg',
                      'soc': 'dg/kg', 'bdod': 'cg/cm3', 'cfvo': 'cm3/dm3'}
    values, seen = [], set()
    for layer in properties['layers']:
        if (not isinstance(layer, dict) or not isinstance(layer.get('name'), str)
                or layer['name'] not in PROPERTIES):
            raise ValueError('SoilGrids REST returned an unexpected property layer')
        prop = layer['name']
        if prop in seen:
            raise ValueError('SoilGrids REST returned a duplicate property layer')
        seen.add(prop)
        unit = layer.get('unit_measure')
        if (not isinstance(unit, dict)
                or str(unit.get('mapped_units', '')).replace('³', '3') != expected_units[prop]
                or unit.get('d_factor') != PROPERTIES[prop][1]):
            raise ValueError(f'SoilGrids REST returned unexpected {prop} units or conversion factor')
        depths = layer.get('depths')
        if not isinstance(depths, list):
            raise ValueError('SoilGrids REST returned invalid depth intervals')
        seen_depths = set()
        for depth in depths:
            if not isinstance(depth, dict) or not isinstance(depth.get('range'), dict):
                raise ValueError('SoilGrids REST returned an invalid depth interval')
            interval = depth['range']
            top, bottom = interval.get('top_depth'), interval.get('bottom_depth')
            if (not isinstance(top, (int, float)) or isinstance(top, bool)
                    or not isinstance(bottom, (int, float)) or isinstance(bottom, bool)
                    or (top, bottom) not in DEPTHS or interval.get('unit_depth') != 'cm'
                    or depth.get('label') != f'{int(top)}-{int(bottom)}cm'
                    or (top, bottom) in seen_depths):
                raise ValueError('SoilGrids REST returned unexpected or duplicate depth intervals')
            seen_depths.add((top, bottom))
            statistics = depth.get('values')
            if not isinstance(statistics, dict):
                raise ValueError('SoilGrids REST returned invalid depth values')
            value = statistics.get('mean')
            if value is None:
                raise NoMatchingData(f'SoilGrids REST has no mean {prop} for {top}-{bottom}cm at this site')
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError('SoilGrids REST returned a non-numeric mean value')
            if not math.isfinite(value) or value < 0 or value in (32767, 65535):
                raise NoMatchingData(f'SoilGrids REST has no valid {prop} for {top}-{bottom}cm at this site')
            values.append((top, bottom, PROPERTIES[prop][0], value / PROPERTIES[prop][1]))
        if seen_depths != set(DEPTHS):
            raise NoMatchingData(f'SoilGrids REST has no complete {prop} depth profile at this site')
    if seen != set(PROPERTIES):
        raise NoMatchingData('SoilGrids REST has no complete soil-property profile at this site')
    return values


def _map_profile(request, sample, progress=None):
    jobs = [(prop, top, bottom) for top, bottom in DEPTHS for prop in PROPERTIES]
    began = monotonic()

    def report(done):
        if progress:
            elapsed = max(0., monotonic() - began)
            estimate = (f'about {elapsed * (len(jobs) - done) / done:.0f}s remaining'
                        if done >= 5 else 'estimating remaining time…')
            progress(done / len(jobs), f'Loading SoilGrids map profile · {done} of {len(jobs)} values · {estimate}')

    # A failed first request stops quickly instead of flooding an unavailable service.
    values = {0: sample(request, *jobs[0])}
    report(1)
    executor = ThreadPoolExecutor(max_workers=4)
    try:
        futures = {executor.submit(sample, request, *job): index
                   for index, job in enumerate(jobs[1:], 1)}
        try:
            for future in as_completed(futures, timeout=_remaining_budget(request, MAP_RECOVERY_BUDGET_SECONDS)):
                values[futures[future]] = future.result()
                report(len(values))
        except FutureTimeout as exc:
            raise requests.Timeout('SoilGrids map recovery time budget exhausted.') from exc
        except Exception:
            # Stop queued work immediately; one missing value invalidates this
            # route, so downloading the remaining layers cannot repair it.
            for pending in futures:
                pending.cancel()
            raise
    finally:
        # Do not hold the UI open for in-flight map calls after failure/deadline.
        # Their socket timeouts still apply and they never invoke UI callbacks.
        executor.shutdown(wait=False, cancel_futures=True)
    return [values[index] for index in range(len(jobs))]


def _profile_frame(values):
    rows = {depth: {"top_cm": depth[0], "bottom_cm": depth[1]} for depth in DEPTHS}
    for top, bottom, key, value, *_ in values:
        rows[(top, bottom)][key] = value
    return validate_profile(pd.DataFrame(rows.values()))


def load_soilgrids_wcs(request: DataRequest) -> DataBlock:
    """Prefer valid REST profiles; automatically recover through checked maps.

    Failed service requests open a temporary shared circuit breaker. Later calls
    use maps immediately, then automatically probe REST once the cooldown ends.
    Explicitly disabling REST remains available for a maps-only run.
    """
    began = monotonic()
    use_rest = bool(request.options.get('soilgrids_use_rest', True))
    map_recovery = bool(request.options.get('soilgrids_map_recovery', True))
    if not use_rest and not map_recovery:
        raise ValueError('No SoilGrids retrieval route is enabled. Enable WMS/WCS maps or explicitly try the REST API.')
    budget = MAP_RECOVERY_BUDGET_SECONDS if map_recovery else REST_BUDGET_SECONDS
    deadline = min(float(request.options.get('_soil_deadline', began + budget)), began + budget)
    request = DataRequest(request.latitude, request.longitude, request.start, request.end,
                          request.data_dir, {**request.options, '_soil_deadline': deadline})
    reported = 0.

    def report(fraction, message):
        nonlocal reported
        reported = max(reported, fraction)
        _source_progress(request, reported, message)

    routes = [
        ('SoilGrids REST API: mean predictions, units and complete six-depth profile checks',
         REST_URL, lambda: _bounded_rest_profile(request), 0.,
         f'Loading SoilGrids REST profile · up to {REST_BUDGET_SECONDS:g}s before trying an available fallback'),
        ('WMS GetFeatureInfo: numeric value, units, layer and sampling-coordinate checks',
         'https://dev-maps.isric.org/mapserv/{prop}',
         lambda: _map_profile(request, point_value, lambda fraction, message: report(.1 + .44 * fraction, message)), .1,
         ('SoilGrids REST did not supply a usable profile · loading WMS numeric map values'
          if use_rest else 'Loading SoilGrids WMS numeric map values · 36 values across six depths')),
        ('WCS 1.0 geographic 3×3 subset; center pixel only',
         'https://maps.isric.org/mapserv',
         lambda: _map_profile(request, wcs_value, lambda fraction, message: report(.55 + .43 * fraction, message)), .55,
         'Checking SoilGrids WCS map backup · estimate updates as values arrive'),
    ]
    routes = ([routes[0]] if use_rest else []) + (routes[1:] if map_recovery else [])
    if not use_rest:
        method,endpoint,fetch,_,message=routes[0]
        routes[0]=(method,endpoint,fetch,0.,message)
    attempts, failures = [], []
    for method, endpoint, fetch, fraction, message in routes:
        report(fraction, message)
        route_began = monotonic()
        try:
            values = fetch()
            frame = _profile_frame(values)
        except (requests.RequestException, ValueError) as exc:
            failures.append(exc)
            rest_details = getattr(exc, 'details', {}) if endpoint == REST_URL else {}
            attempts.append({'method': method, 'endpoint': endpoint,
                             'exception': type(exc).__name__, 'message': str(exc),
                             'seconds': max(0., monotonic()-route_began),
                             'attempted': rest_details.get('rest_attempted', True),
                             **({'retry_after_seconds': rest_details['retry_after_seconds'],
                                 'cooldown_scope': rest_details['scope']}
                                if 'retry_after_seconds' in rest_details else {})})
            continue
        if endpoint == REST_URL:
            # A complete checked response is stronger evidence than a failure
            # from another concurrent request. Do not keep a recovered service
            # behind its previous outage cooldown.
            with _REST_LOCK:
                _REST_COOLDOWNS.pop('service', None)
                _REST_COOLDOWNS.pop((request.latitude, request.longitude), None)
        report(1., 'Soil profile ready · all six depths checked')
        sampled_cells = [{'layer': f'{top}-{bottom}cm', 'property': key, **metadata[0]}
                         for top, bottom, key, value, *metadata in values if metadata]
        return DataBlock("soilgrids_wcs", frame, "static depth profile", UNITS,
                         {"source": "ISRIC SoilGrids 2.0 mean predictions", "license": "CC BY 4.0",
                          "latitude": request.latitude, "longitude": request.longitude,
                          "method": method, "aggregation": "Nearest source grid cell at the requested site; no neighbor search or spatial filling",
                          "sampled_cells": sampled_cells, "attempts": attempts, "endpoint": endpoint,
                          "retrieval_seconds": max(0., monotonic()-began),
                          "route_seconds": max(0., monotonic()-route_began),
                          "map_recovery_requested": map_recovery,
                          "rest_requested": use_rest,
                          "rest_status": _rest_route_status(use_rest, attempts, endpoint),
                          "parameter_use": "Source properties map to parameters through soil_profile.make_profile; hydraulic values remain estimates"})
    report(.98, 'SoilGrids profile unavailable · all enabled routes checked')
    details = {'latitude': request.latitude, 'longitude': request.longitude, 'attempts': attempts,
               'retrieval_seconds': max(0., monotonic()-began), 'map_recovery_requested': map_recovery,
               'map_recovery_available': not map_recovery, 'rest_requested': use_rest,
               'rest_status': _rest_route_status(use_rest, attempts),
               'foreground_budget_seconds': REST_BUDGET_SECONDS if use_rest else None,
               'map_recovery_budget_seconds': MAP_RECOVERY_BUDGET_SECONDS if map_recovery else None,
               'map_recovery_budget_is_best_effort': map_recovery}
    if all(isinstance(exc, NoMatchingData) for exc in failures):
        raise NoMatchingData('SoilGrids has no valid complete profile at this point; choose field coordinates or a mapped-soil cell.', details) from failures[-1]
    cause = next((exc for exc in failures if isinstance(exc, requests.RequestException)), failures[-1])
    raise DataSourceError('SoilGrids could not retrieve a valid complete profile. See all method attempts in source details.', details) from cause


def _rest_route_status(enabled, attempts, selected_endpoint=None):
    if not enabled:
        return 'disabled'
    if selected_endpoint == REST_URL:
        return 'selected'
    rest_attempt = next((row for row in attempts if row['endpoint'] == REST_URL), None)
    return 'cooldown' if rest_attempt and not rest_attempt['attempted'] else 'unavailable'


def sample_hwsd(bil: Path, hdr: Path, latitude, longitude):
    header = {}
    for line in hdr.read_text().splitlines():
        parts = line.split()
        if len(parts) >= 2:
            header[parts[0].upper()] = parts[1]
    for key in ("NROWS", "NCOLS", "NBITS", "ULXMAP", "ULYMAP", "XDIM", "YDIM"):
        if key not in header:
            raise ValueError(f"HWSD BIL header missing {key}")
    if header.get("LAYOUT", "BIL").upper() != "BIL" or int(header.get("NBANDS", "1")) != 1:
        raise ValueError("HWSD mapping raster must be single-band BIL")
    rows, cols = int(header["NROWS"]), int(header["NCOLS"])
    dx, dy = float(header["XDIM"]), float(header["YDIM"])
    col = math.floor((longitude - float(header["ULXMAP"])) / dx + .5)
    row = math.floor((float(header["ULYMAP"]) - latitude) / dy + .5)
    if not 0 <= row < rows or not 0 <= col < cols:
        raise ValueError("Site is outside the HWSD raster")
    bits = int(header["NBITS"])
    if bits not in (16, 32):
        raise ValueError("HWSD BIL must contain 16- or 32-bit integer mapping-unit IDs")
    byteorder = "<" if header.get("BYTEORDER", "I").upper() in ("I", "LSBFIRST") else ">"
    kind = "i" if header.get("PIXELTYPE", "UNSIGNEDINT").upper() in ("SIGNED", "SIGNEDINT") else "u"
    dtype = np.dtype(f"{byteorder}{kind}{bits//8}")
    stride = int(header.get("TOTALROWBYTES", cols*dtype.itemsize))
    offset = int(header.get("SKIPBYTES", "0")) + row*stride + col*dtype.itemsize
    with bil.open("rb") as stream:
        stream.seek(offset)
        raw = stream.read(dtype.itemsize)
    if len(raw) != dtype.itemsize:
        raise ValueError("HWSD raster is truncated")
    unit_id = int(np.frombuffer(raw, dtype=dtype)[0])
    if unit_id <= 0 or unit_id == int(header.get("NODATA", "0")):
        raise NoMatchingData("HWSD raster has no mapping unit at this site")
    return unit_id


@lru_cache(maxsize=4)
def _read_hwsd_table(path, modified_ns, size):
    """Decode and hash once per file revision, shared by site and nearby loads."""
    table = Path(path)
    if table.suffix.lower() == '.mdb':
        frame = read_hwsd_mdb(table)
    else:
        frame = pd.read_csv(table, na_values=[-9999, -9, -8, -7, -6, -5, -4, -3, -2, -1])
    frame.columns = frame.columns.str.upper()
    digest = sha256()
    with table.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            digest.update(chunk)
    if (table.stat().st_mtime_ns, table.stat().st_size) != (modified_ns, size):
        raise ValueError('HWSD property table changed while being read; retry with the completed file.')
    return frame, digest.hexdigest()


def load_hwsd_table(path):
    """Return a detached table and source digest, invalidated by file metadata."""
    table = Path(path).resolve()
    status = table.stat()
    frame, digest = _read_hwsd_table(str(table), status.st_mtime_ns, status.st_size)
    return frame.copy(deep=True), digest


def load_hwsd(request: DataRequest) -> DataBlock:
    _source_progress(request, 0., 'Loading local HWSD soil backup…')
    folder = request.data_dir / "hwsd"
    table = Path(request.options.get("hwsd_table", folder / "HWSD2_LAYERS.csv"))
    if not table.is_file():
        compressed = folder / 'HWSD2_LAYERS.csv.gz'
        databases = sorted(request.data_dir.rglob('HWSD2.mdb'))
        if databases:
            table = databases[0]
        elif compressed.is_file():
            table = compressed
        else:
            raise FileNotFoundError('HWSD soil table unavailable. Add HWSD2.mdb anywhere in data/, or HWSD2_LAYERS.csv[.gz] in data/hwsd/.')
    unit_id = request.options.get("hwsd_unit_id")
    map_source=None
    if unit_id is None:
        bil,hdr=ensure_hwsd_map(request.data_dir,request.options)
        map_source=str(bil)
        unit_id = sample_hwsd(bil, hdr, request.latitude, request.longitude)
    _source_progress(request, .35, 'Reading HWSD soil properties…')
    frame, asset_hash = load_hwsd_table(table)
    mapping = {"TOPDEP": "top_cm", "BOTDEP": "bottom_cm", "SAND": "sand_pct",
               "SILT": "silt_pct", "CLAY": "clay_pct", "ORG_CARBON": "soc_g_kg",
               "BULK": "bulk_density_g_cm3", "COARSE": "coarse_fragments_pct"}
    require_columns(frame, ["HWSD2_SMU_ID", "SEQUENCE", *mapping])
    frame = frame[(frame.HWSD2_SMU_ID == int(unit_id)) & (frame.SEQUENCE == int(request.options.get("hwsd_sequence", 1)))].copy()
    if frame.empty:
        raise NoMatchingData(f'No HWSD layers match map unit {unit_id} and soil sequence {request.options.get("hwsd_sequence",1)}; check that table and raster versions agree.')
    frame = frame.rename(columns=mapping).sort_values("top_cm")
    frame["soc_g_kg"] = pd.to_numeric(frame.soc_g_kg) * 10  # mass percent -> g/kg
    physical=['sand_pct','silt_pct','clay_pct','soc_g_kg','bulk_density_g_cm3','coarse_fragments_pct']
    missing=frame[physical].apply(pd.to_numeric,errors='coerce').isna()
    if missing.any().any():
        all_empty=bool(missing.all().all())
        reason='urban/built-up' if int(unit_id)==7001 and all_empty else 'unmapped soil properties'
        details={'mapping_unit':int(unit_id),'sequence':int(request.options.get('hwsd_sequence',1)),'geographic_map':map_source,
            'reason':reason,'map_lookup_succeeded':True,
            'missing_fields':{key:frame.loc[missing[key],['top_cm','bottom_cm']].to_dict('records') for key in physical if missing[key].any()},
            'resolution':'Choose actual field coordinates, a nearby mapped soil cell, or an explicitly configured soil preset. Do not fill missing soil values with zero.'}
        message=(f'HWSD maps this point to urban/built-up unit {unit_id}, which has no soil-property profile.' if reason=='urban/built-up'
                 else f'HWSD map unit {unit_id} lacks a complete soil-property profile.')
        raise NoMatchingData(message,details)
    frame = validate_profile(frame)
    import json
    origin = folder/'source.json'
    origin_metadata = json.loads(origin.read_text()) if origin.is_file() else {'file':table.name}
    if table.suffix.lower()=='.mdb' and origin_metadata.get('source_sha256')!=asset_hash:
        origin_metadata = {'file':table.name,'source_table':'HWSD2_LAYERS','source_sha256':asset_hash}
    _source_progress(request, 1., 'HWSD soil profile ready · depth and property checks passed')
    return DataBlock("hwsd", frame, "static depth profile", UNITS,
                     {"source": "FAO/IIASA HWSD v2.0", "mapping_unit": int(unit_id),
                      "sequence": int(request.options.get("hwsd_sequence", 1)),
                      "geographic_map":map_source,
                      "sha256": asset_hash,
                      "table_origin": origin_metadata,
                      "spatial_support": "One selected soil sequence within a mapping unit, not a measured point profile"})


HWSD_RASTER_URL = 'https://s3.eu-west-1.amazonaws.com/data.gaezdev.aws.fao.org/HWSD/HWSD2_RASTER.zip'


def find_existing_hwsd_map(data_dir,reuse=True,search_roots=None):
    """Reuse official maps read-only; never copy a multi-GB map between exports.

    Search only known dataset paths in adjacent exports and Downloads, without
    recursively scanning the user's home. External pairs must have the official
    global dimensions and a complete byte count. Local custom maps remain valid.
    """
    data_dir=Path(data_dir)
    local=[data_dir/'hwsd'/'HWSD2.bil',*sorted(data_dir.glob('**/HWSD2.bil'))]
    for bil in local:
        if bil.is_file() and bil.with_suffix('.hdr').is_file():
            return bil,bil.with_suffix('.hdr')
    if not reuse:
        return None
    roots=search_roots if search_roots is not None else [data_dir.parent.parent,Path.home()/'Downloads']
    candidates=set()
    for root in roots:
        root=Path(root)
        try:
            candidates.update(root.glob('*/data/hwsd/HWSD2.bil'))
            candidates.update(root.glob('*/*/data/hwsd/HWSD2.bil'))
        except OSError:
            continue
    for bil in sorted(candidates,key=str):
        hdr=bil.with_suffix('.hdr')
        try:
            header={p[0].upper():p[1] for line in hdr.read_text().splitlines() if len(p:=line.split())>=2}
            if (int(header['NROWS']),int(header['NCOLS']),int(header['NBITS']))!=(21600,43200,16):
                continue
            if header.get('LAYOUT','BIL').upper()!='BIL' or int(header.get('NBANDS',1))!=1:
                continue
            if not math.isclose(float(header['ULXMAP']),-179.995833333333,abs_tol=1e-6) or not math.isclose(float(header['ULYMAP']),89.9958333333333,abs_tol=1e-6):
                continue
            if not all(math.isclose(float(header[k]),1/120,rel_tol=1e-6) for k in ('XDIM','YDIM')):
                continue
            size=int(header.get('SKIPBYTES',0))+21599*int(header.get('TOTALROWBYTES',86400))+86400
            if bil.stat().st_size>=size:
                return bil,hdr
        except (OSError,KeyError,ValueError):
            continue
    return None


def ensure_hwsd_map(data_dir,options=None):
    options=options or {}
    pair=find_existing_hwsd_map(data_dir,options.get('hwsd_reuse_existing',True))
    if pair:
        return pair
    if options.get('hwsd_download_map',False):
        return download_hwsd_map(Path(data_dir)/'hwsd',options.get('_progress'))
    raise FileNotFoundError('HWSD map not found locally or in existing downloaded apps. Enable automatic map download under Advanced → Data access.')


def read_hwsd_mdb(path):
    """Read official Access files on macOS/Linux/Windows, without ODBC/Homebrew."""
    from access_parser import AccessParser
    database = AccessParser(str(path))
    if 'HWSD2_LAYERS' not in database.catalog:
        raise ValueError('Access file has no official HWSD2_LAYERS table')
    return pd.DataFrame(database.parse_table('HWSD2_LAYERS')).replace([-9999,-9,-8,-7,-6,-5,-4,-3,-2,-1],np.nan)


def download_hwsd_map(folder,progress=None):
    """Retrieve the official mapping grid once; do not package the large raster."""
    import shutil
    import tempfile
    from zipfile import ZipFile
    folder.mkdir(parents=True,exist_ok=True)
    bil, hdr = folder/'HWSD2.bil',folder/'HWSD2.hdr'
    # Work in a private temporary directory; concurrent sessions publish only
    # complete files. A failed download leaves no apparently valid raster.
    with tempfile.TemporaryDirectory(dir=folder) as work:
        work = Path(work)
        archive = work/'raster.zip'
        with requests.get(HWSD_RASTER_URL,stream=True,timeout=(15,60)) as response:
            response.raise_for_status()
            total = int(response.headers.get('Content-Length',0))
            downloaded = 0
            with archive.open('wb') as target:
                for chunk in response.iter_content(1024*1024):
                    target.write(chunk)
                    downloaded += len(chunk)
                    if progress:
                        progress(downloaded,total)
        with ZipFile(archive) as zipped:
            for suffix in ('.bil','.hdr'):
                matches = [item for item in zipped.infolist() if Path(item.filename).name.lower()=='hwsd2'+suffix]
                if len(matches)!=1:
                    raise ValueError('Official HWSD map archive does not contain a unique BIL/HDR pair')
                target = work/('HWSD2'+suffix)
                with zipped.open(matches[0]) as source, target.open('wb') as stream:
                    shutil.copyfileobj(source,stream)
        # Header goes last so a complete pair marks successful installation.
        (work/'HWSD2.bil').replace(bil)
        (work/'HWSD2.hdr').replace(hdr)
    return bil,hdr
