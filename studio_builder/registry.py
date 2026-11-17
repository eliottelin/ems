"""Composable model/data catalog. Access requirements are separate from implementation."""
from copy import deepcopy

MODELS = {
    "water_balance": {"label": "Water", "status": "uncalibrated depth model", "units": "mm/day; mm",
                      "datasets": ["open_meteo", "soilgrids_wcs", "hwsd", "terraclimate", "mod16a2gf"]},
    "vegetation": {"label": "Plants", "status": "uncalibrated depth model", "units": "g C/m2",
                   "datasets": ["open_meteo", "try_categorical"]},
    "soil_carbon": {"label": "Soil carbon", "status": "uncalibrated depth model", "units": "g C/m2",
                    "datasets": ["open_meteo", "soilgrids_wcs", "hwsd", "srdb"]},
}
DATASETS = {
    "open_meteo": {
        "label": "Open-Meteo historical weather", "group": "weather", "status": "implemented",
        "role": "forcing", "resolution": "daily", "access": "public_api", "module": "forcing",
        "units": {"precip_mm": "mm/day", "et0_mm": "mm/day", "temp_c": "degC"},
        "url": "https://open-meteo.com/en/docs/historical-weather-api",
        "notes": "Daily forcing. The app includes Open-Meteo/GeoNames place search, checks archive boundary dates, and reports missing values by variable/date; no synthetic fallback.",
    },
    "soilgrids_wcs": {
        "label": "SoilGrids soil profile", "group": "soil", "status": "implemented",
        "role": "soil_properties", "resolution": "static depth profile", "access": "public_api", "module": "soil",
        "units": {"soc_g_kg": "g C/kg", "bulk_density_g_cm3": "g/cm3", "sand_pct": "%", "clay_pct": "%"},
        "url": "https://docs.isric.org/globaldata/soilgrids/SoilGrids_faqs_02.html",
        "notes": "SoilGrids supplies 250 m predictions at six depths through automatic batched REST, then numeric WMS queries and 3×3 WCS center-pixel recovery. REST has a 20-second foreground limit and five-starts-per-minute shared budget; a service failure opens a five-minute cooldown before automatic retry on a later lookup. Site data gaps have a separate one-minute cooldown. Model chooses SoilGrids or faster ~1 km HWSD first; both mapped sources are tried before an explicitly authorized assumed preset. Complete map profiles require 36 checked values with four workers. REST can be disabled under Advanced. Validated profiles persist for 30 days across restarts; no neighboring values are silently substituted.",
    },
    "hwsd": {
        "label": "HWSD v2 soil backup", "group": "soil", "status": "implemented",
        "role": "fallback", "fallback_for": "soilgrids_wcs", "resolution": "static depth profile",
        "access": "local_files", "module": "soil",
        "units": {"soc_g_kg": "g C/kg", "bulk_density_g_cm3": "g/cm3"},
        "url": "https://www.fao.org/soils-portal/data-hub/soil-maps-and-databases/harmonized-world-soil-database-v20/en/",
        "notes": "Reads HWSD2.mdb directly or the bundled compressed layer table; parsed tables and source hashes are reused across locations. Existing geographic BIL/HDR maps are reused read-only. About 1 km mapping-unit profiles can be selected first for faster loading. A missing ~1.9 GB uncompressed geographic map downloads only through the Advanced opt-in or explicit recovery action; a mapping-unit override remains optional.",
    },
    "try_categorical": {
        "label": "TRY plant traits", "group": "plant_traits", "status": "implemented",
        "role": "traits", "resolution": "species categories", "access": "local_files", "module": "traits",
        "units": {"traits": "categorical"}, "url": "https://www.try-db.org/TryWeb/Data.php#3",
        "notes": "Local XLSX schema/record validation and conservative PFT mapping; categories do not supply numeric growth rates. Local exports include the supplied archive with its original terms and attribution when available.",
    },
    "terraclimate": {
        "label": "TerraClimate water comparison", "group": "water_validation", "status": "implemented",
        "role": "validation", "resolution": "monthly", "access": "public_ncss_or_csv", "module": "observations",
        "units": {"aet_mm": "mm/month", "pet_mm": "mm/month"},
        "url": "https://www.climatologylab.org/terraclimate.html",
        "notes": "Current THREDDS NCSS point NetCDF with explicit CF packing/units/fill decoding, or already-decoded local CSV. Loaded on request after simulation results. Monthly AET comparison requires complete simulation months; version and scale recorded.",
    },
    "mod16a2gf": {
        "label": "MOD16A2GF ET backup", "group": "water_validation", "status": "implemented",
        "role": "fallback", "fallback_for": "terraclimate", "resolution": "8-day composite",
        "access": "public_subset_api", "module": "observations",
        "units": {"aet_mm": "mm/composite", "pet_mm": "mm/composite"},
        "url": "https://modis.ornl.gov/data/modis_webservice.html",
        "notes": "ORNL DAAC point subsets; QA, fill values, scaling and short year-end composites checked. CSV granule names recover collection numbers omitted by JSON. Non-061 or unverifiable remote collections are rejected; missing metadata is never invented.",
    },
    "srdb": {
        "label": "SRDB respiration observations", "group": "carbon_validation", "status": "implemented",
        "role": "validation", "resolution": "study annual", "access": "public_csv_or_local", "module": "observations",
        "units": {"Rh_annual": "g C/m2/year", "Rs_annual": "g C/m2/year"},
        "url": "https://github.com/bpbond/srdb",
        "notes": "Loaded on request after simulation results. Site/radius/year matching and annual coverage/quality checks. Only measured heterotrophic respiration is comparable with the current soil-carbon module.",
    },
    "open_meteo_climate": {
        "label": "Future climate", "group": "future_climate", "status": "implemented",
        "role": "scenario", "resolution": "daily", "access": "public_api", "module": "forcing",
        "units": {"precip_mm": "mm/day", "et0_mm": "mm/day", "temp_c": "degC"},
        "url": "https://open-meteo.com/en/docs/climate-api",
        "notes": "Separate HighResMIP scenario forcing with model provenance, not observed weather; available 1950–2050.",
    },
}


def resolve_data_blocks(models, extras=()):
    """Return independent blocks with model connections and selection reasons."""
    selected = set(models)
    if not selected or not selected.issubset(MODELS):
        raise ValueError("Choose one or more supported models")
    extra_keys = set(extras)
    if not extra_keys.issubset(DATASETS):
        raise ValueError("Unknown data block")
    automatic = {key for name in selected for key in MODELS[name]["datasets"]}
    return [dict(deepcopy(spec), id=key, automatic=key in automatic,
                 models=sorted(name for name in selected if key in MODELS[name]["datasets"]))
            for key, spec in DATASETS.items() if key in automatic | extra_keys]


def optional_data_blocks(models):
    automatic = {block["id"] for block in resolve_data_blocks(models)}
    return {key: value for key, value in DATASETS.items() if key not in automatic}
