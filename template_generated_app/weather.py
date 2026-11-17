"""Weather forcing. Real Open-Meteo historical data or labeled synthetic demo."""
from datetime import date,timedelta
import math
import pandas as pd
import requests
import numpy as np

ENDPOINT="https://archive-api.open-meteo.com/v1/archive"
HISTORICAL_MODEL="era5"
ARCHIVE_DEFAULT_LAG_DAYS=7  # Conservative initial dates; actual responses still need checking.


class WeatherDataError(ValueError):
    """Incomplete or incompatible provider data with shareable diagnostics."""
    def __init__(self, message, diagnostics=None):
        super().__init__(message)
        self.diagnostics = diagnostics or {}

def demo_weather(days:int,start=date(2023,1,1))->pd.DataFrame:
    rows=[]
    for i in range(days):
        annual=math.sin(2*math.pi*i/365.25)
        wet=(i%13 in (0,1,2,8))
        rows.append({"date":str(start+timedelta(days=i)),"temp_c":19+11*annual,
                     "precip_mm": (9.0 if i%13==0 else 3.2) if wet else 0.,
                     "et0_mm":max(0.2,3.4+2.0*annual),"forcing_source":"SYNTHETIC DEMO"})
    return pd.DataFrame(rows)

def fetch_open_meteo(latitude:float,longitude:float,days:int,start=date(2023,1,1))->pd.DataFrame:
    if isinstance(days, bool) or not isinstance(days, int) or days < 1:
        raise ValueError("Daily simulation steps must be a positive integer")
    if not math.isfinite(latitude) or not -90 <= latitude <= 90 or not math.isfinite(longitude) or not -180 <= longitude <= 180:
        raise ValueError("Invalid site coordinates")
    end=start+timedelta(days=days-1)
    params={"latitude":latitude,"longitude":longitude,"start_date":str(start),"end_date":str(end),"timezone":"UTC",
            "daily":"temperature_2m_mean,precipitation_sum,et0_fao_evapotranspiration", "models": HISTORICAL_MODEL,
            "temperature_unit":"celsius", "precipitation_unit":"mm"}
    r=requests.get(ENDPOINT,params=params,timeout=25)
    r.raise_for_status()
    try:
        frame = parse_daily(r.json(), start, end, "Open-Meteo historical / ERA5")
    except WeatherDataError as exc:
        exc.diagnostics.update({"latitude":latitude, "longitude":longitude,
            "model":HISTORICAL_MODEL, "request_url":requests.Request("GET", ENDPOINT, params=params).prepare().url})
        raise
    frame.attrs["provider_model"] = HISTORICAL_MODEL
    return frame


def available_historical_period(latitude, longitude, today=None):
    """Probe both archive edges; a full run must still pass strict gap checks."""
    today = today or date.today()
    lower = date(1940, 1, 1)
    upper = today-timedelta(days=1)

    def complete_days(start, end):
        params={"latitude":latitude,"longitude":longitude,"start_date":str(start),"end_date":str(end),
            "timezone":"UTC","models":HISTORICAL_MODEL,
            "daily":"temperature_2m_mean,precipitation_sum,et0_fao_evapotranspiration",
            "temperature_unit":"celsius","precipitation_unit":"mm"}
        response=requests.get(ENDPOINT,params=params,timeout=30)
        response.raise_for_status()
        payload=response.json()
        data=payload.get("daily",{})
        fields=("temperature_2m_mean","precipitation_sum","et0_fao_evapotranspiration")
        expected=[str(start+timedelta(days=i)) for i in range((end-start).days+1)]
        if data.get("time") != expected or any(not isinstance(data.get(key),list) or len(data[key])!=len(expected) for key in fields):
            raise WeatherDataError("Open-Meteo availability probe returned incomplete dates or fields",
                                   {"requested_start":str(start),"requested_end":str(end),"model":HISTORICAL_MODEL})
        valid=[]
        for i, stamp in enumerate(expected):
            values=[data[key][i] for key in fields]
            if any(value is None for value in values):
                continue
            try:
                checked=parse_daily({"daily":{"time":[stamp],**{key:[data[key][i]] for key in fields}},
                                     "daily_units":payload.get("daily_units",{})},
                                    date.fromisoformat(stamp),date.fromisoformat(stamp),"Open-Meteo availability probe")
            except WeatherDataError:
                continue
            valid.append(date.fromisoformat(checked.date.iloc[0]))
        return valid

    first=complete_days(lower,lower+timedelta(days=13))
    last=complete_days(upper-timedelta(days=20),upper)
    if not first or not last:
        raise WeatherDataError("Could not establish a complete archive boundary for this site. Choose a known historical period and run again; some locations or recent data may be unavailable.",
                               {"model":HISTORICAL_MODEL,"first_probe_complete_days":len(first),"recent_probe_complete_days":len(last)})
    return first[0], last[-1]


def parse_daily(payload, start, end, source, et_key="et0_fao_evapotranspiration"):
    diagnostics={"source":source,"requested_start":str(start),"requested_end":str(end)}
    if not isinstance(payload,dict) or not isinstance(payload.get("daily"),dict):
        raise WeatherDataError("Open-Meteo response has no daily data object", diagnostics)
    data=payload.get("daily",{})
    days=(end-start).days+1
    required=["time","temperature_2m_mean","precipitation_sum",et_key]
    if any(k not in data for k in required):
        diagnostics["missing_variables"]=[key for key in required if key not in data]
        raise WeatherDataError("Open-Meteo response missing requested variables: " + ", ".join(diagnostics["missing_variables"]), diagnostics)
    lengths={key:len(data[key]) if isinstance(data[key],list) else None for key in required}
    if any(length!=days for length in lengths.values()):
        diagnostics.update({"expected_days":days,"received_lengths":lengths})
        raise WeatherDataError(f"Open-Meteo returned incomplete daily data: expected {days} dates and values; received {lengths}", diagnostics)
    expected=[str(start+timedelta(days=i)) for i in range(days)]
    if data["time"] != expected:
        raise WeatherDataError("Open-Meteo dates must be consecutive and match the requested period", diagnostics)
    missing={key:[stamp for stamp,value in zip(expected,data[key]) if value is None]
             for key in required[1:] if any(value is None for value in data[key])}
    if missing:
        diagnostics["missing_values"]={key:{"count":len(stamps),"first_dates":stamps[:10],"last_date":stamps[-1]} for key,stamps in missing.items()}
        summary="; ".join(f"{key}: {len(stamps)} missing, first on {stamps[0]}" for key,stamps in missing.items())
        complete=[all(data[key][i] is not None for key in required[1:]) for i in range(days)]
        first_missing=next(i for i,valid in enumerate(complete) if not valid)
        suggestion=""
        if first_missing > 0 and not any(complete[first_missing:]):
            diagnostics["suggested_end_date"]=expected[first_missing-1]
            suggestion=f" The response is complete through {expected[first_missing-1]}; choose that or an earlier end date, or retry after the archive updates."
        raise WeatherDataError("Open-Meteo returned incomplete daily data: " + summary + "." + suggestion + " Missing values were not filled or dropped.", diagnostics)
    frame=pd.DataFrame({"date":data["time"],"temp_c":data["temperature_2m_mean"],
                        "precip_mm":data["precipitation_sum"],"et0_mm":data[et_key],
                        "forcing_source":source})
    try:
        numeric=frame[["temp_c","precip_mm","et0_mm"]].apply(pd.to_numeric,errors="raise")
    except (ValueError, TypeError) as exc:
        raise WeatherDataError("Open-Meteo forcing must contain numeric daily values", diagnostics) from exc
    if not np.isfinite(numeric.to_numpy()).all() or (numeric[["precip_mm","et0_mm"]]<0).any().any():
        raise WeatherDataError("Open-Meteo forcing must be finite; precipitation/ET0 cannot be negative", diagnostics)
    units=payload.get("daily_units",{})
    for key, unit in (("temperature_2m_mean","°C"),("precipitation_sum","mm"),(et_key,"mm")):
        if key in units and units[key] != unit:
            raise WeatherDataError(f"Unexpected Open-Meteo units for {key}: {units[key]}", diagnostics)
    frame[numeric.columns]=numeric
    return frame


def fetch_climate(latitude, longitude, start, end, model="MRI_AGCM3_2_S"):
    et_key="et0_fao_evapotranspiration_sum"
    response=requests.get("https://climate-api.open-meteo.com/v1/climate", params={
        "latitude":latitude,"longitude":longitude,"start_date":str(start),"end_date":str(end),
        "models":model,"daily":f"temperature_2m_mean,precipitation_sum,{et_key}",
        "temperature_unit":"celsius","precipitation_unit":"mm"}, timeout=60)
    response.raise_for_status()
    return parse_daily(response.json(),start,end,f"Open-Meteo climate / {model}",et_key)
