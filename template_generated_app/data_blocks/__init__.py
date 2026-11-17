"""Independent data blocks; no Streamlit or scientific-model dependency."""
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from importlib import import_module
from math import isfinite
from pathlib import Path
from typing import Any

import pandas as pd


class NoMatchingData(ValueError):
    """The source was reached, but has no usable matching records."""
    def __init__(self,message,details=None):
        super().__init__(message)
        self.details=details or {}


class DataSourceError(ValueError):
    """A retrieval/decoding failure with diagnostics from every attempted route."""
    def __init__(self,message,details=None):
        super().__init__(message)
        self.details=details or {}


@dataclass
class DataRequest:
    latitude: float
    longitude: float
    start: date
    end: date
    data_dir: Path = Path("data")
    options: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isfinite(self.latitude) or not -90 <= self.latitude <= 90:
            raise ValueError("Latitude must be finite and between -90 and 90")
        if not isfinite(self.longitude) or not -180 <= self.longitude <= 180:
            raise ValueError("Longitude must be finite and between -180 and 180")
        if self.start > self.end:
            raise ValueError("Start must be on or before end")


@dataclass
class DataBlock:
    id: str
    frame: pd.DataFrame
    resolution: str
    units: dict[str, str]
    provenance: dict[str, Any]

    def __post_init__(self):
        if self.frame.empty:
            raise NoMatchingData(f"{self.id}: no usable records for the requested site/period")


def load_block(key: str, request: DataRequest) -> DataBlock:
    """Load any block by itself; raise on missing files, network or invalid data."""
    modules = {
        "open_meteo": "forcing", "open_meteo_climate": "forcing",
        "soilgrids_wcs": "soil", "hwsd": "soil", "try_categorical": "traits",
        "srdb": "observations", "terraclimate": "observations", "mod16a2gf": "observations",
    }
    if key not in modules:
        raise ValueError(f"Unknown data block: {key}")
    result = getattr(import_module("data_blocks." + modules[key]), "load_" + key)(request)
    result.provenance = {**result.provenance,
        "request": {"latitude": request.latitude, "longitude": request.longitude,
                    "start": str(request.start), "end": str(request.end)},
        "loaded_at_utc": datetime.now(timezone.utc).isoformat()}
    return result


def require_columns(frame: pd.DataFrame, columns) -> None:
    missing = set(columns) - set(frame.columns)
    if missing:
        raise ValueError("Missing data columns: " + ", ".join(sorted(missing)))


def get_json(url, **kwargs):
    import requests
    response = requests.get(url, timeout=60, **kwargs)
    response.raise_for_status()
    return response.json()
