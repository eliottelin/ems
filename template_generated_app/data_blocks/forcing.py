"""Daily observed-period reanalysis and separate climate-scenario forcing."""
from datetime import date
from . import DataBlock, DataRequest,NoMatchingData
from weather import HISTORICAL_MODEL, fetch_open_meteo, fetch_climate


def load_open_meteo(request: DataRequest) -> DataBlock:
    frame = fetch_open_meteo(request.latitude, request.longitude,
                             (request.end-request.start).days+1, request.start)
    return DataBlock("open_meteo", frame, "daily",
                     {"precip_mm": "mm/day", "et0_mm": "mm/day", "temp_c": "degC"},
                     {"source": "Open-Meteo historical / ERA5", "model": HISTORICAL_MODEL,
                      "latitude": request.latitude, "longitude": request.longitude,
                      "start": str(request.start), "end": str(request.end)})


def load_open_meteo_climate(request: DataRequest) -> DataBlock:
    if request.start < date(1950, 1, 1) or request.end > date(2050, 12, 31):
        raise NoMatchingData('Climate API dates must be within 1950–2050. Nearby locations cannot extend this period.',
            {'reason':'no_overlap_time','available_first':'1950-01-01','available_last':'2050-12-31'})
    model = request.options.get("climate_model", "MRI_AGCM3_2_S")
    frame = fetch_climate(request.latitude, request.longitude, request.start, request.end, model)
    return DataBlock("open_meteo_climate", frame, "daily",
                     {"precip_mm": "mm/day", "et0_mm": "mm/day", "temp_c": "degC"},
                     {"source": "Open-Meteo Climate / HighResMIP", "model": model,
                      "scenario": "HighResMIP high-emissions experiment; not historical observations",
                      "start": str(request.start), "end": str(request.end)})
