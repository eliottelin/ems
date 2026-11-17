"""Deterministic, agent-neutral project export with model/data dependency closure."""
from __future__ import annotations
import io
import json
import re
from pathlib import Path
from datetime import date
from zipfile import ZipFile, ZIP_DEFLATED
from .registry import DATASETS, MODELS, resolve_data_blocks
from .local_data import try_assets, try_inventory, hwsd_assets
from .onboarding import getting_started_markdown
from template_generated_app.soil_profile import validate_depths, resize_depths

TEMPLATE = Path(__file__).resolve().parents[1] / "template_generated_app"
VALID_SLUG = re.compile(r"[^a-z0-9_-]+")


def configured_depths(config):
    """Saved layer boundaries are authoritative; total depth can resize defaults."""
    if 'layer_depths_cm' in config:
        return validate_depths(config['layer_depths_cm'])
    if 'soil_depth_cm' in config:
        return resize_depths(None, config['soil_depth_cm'])
    return validate_depths()


def clean_name(value: str) -> str:
    slug = VALID_SLUG.sub("-", value.lower().strip().replace(" ", "-")).strip("-_")
    return slug[:60] or "ecohydro-project"


def validate(config: dict) -> None:
    if not isinstance(config, dict):
        raise ValueError("Configuration must be a mapping")
    chosen = config.get("models", [])
    if not isinstance(chosen, list) or not chosen or any(not isinstance(key, str) or key not in MODELS for key in chosen):
        raise ValueError("Choose one or more supported model components")
    if config.get("weather_source", "open_meteo") != "open_meteo":
        raise ValueError("Weather forcing is Open-Meteo")
    extras = config.get("additional_datasets", [])
    if not isinstance(extras, list) or any(not isinstance(key, str) or key not in DATASETS for key in extras):
        raise ValueError("Unknown extra data block")
    days = config.get("initial_run_days", config.get("simulation_steps", config.get("days", 365)))
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 40000:
        raise ValueError("Initial run length must be an integer between 1 and 40000 days")
    for key, limit in (("latitude", 90), ("longitude", 180)):
        if key in config:
            try:
                valid = -limit <= float(config[key]) <= limit
            except (ValueError, TypeError):
                valid = False
            if not valid:
                raise ValueError(f"{key} out of range")
    if "start_date" in config:
        try:
            date.fromisoformat(config["start_date"])
        except (TypeError, ValueError) as exc:
            raise ValueError("Start date must be ISO YYYY-MM-DD") from exc
    if not isinstance(config.get("request", ""), str):
        raise ValueError("Coding agent instructions must be text")
    configured_depths(config)


def _agent_brief(config):
    return f"""# Project development brief (agent-neutral)

This working standalone app requires no coding agent. Give this project to any coding assistant or developer for optional extensions.

## Generated specification
- Project: {config['project_name']}
- Selected ecosystem models: {', '.join(config['models'])}
- Weather: Open-Meteo historical / ERA5, with a separate climate scenario block when selected.
- Calculation interval: daily (one update per day).
- Run length: chosen from the date range in the app; 365 daily updates cover 365 days.
- Site and time frame: chosen at runtime; the project is not tied to fixed coordinates or dates.
- Independent data blocks: {', '.join(block['id'] for block in config['data_blocks'])}.
- Automatic inputs, validation sources and backups are recorded in data_blocks.json.
- Initial soil depth: {config['soil_depth_cm']:g} cm; layer boundaries: {config['layer_depths_cm']}. Both are editable in the exported app.

## User customization request
{config['request'].strip() or '(none)'}

## Instructions for further development
1. Read model_config.json, MODEL_INTERFACES.md, MODEL_CARD.md, DATA_PROVENANCE.md and data_blocks.json.
2. Models and data blocks are independent components. Preserve units, depth intervals, temporal support and provenance when connecting them.
3. Keep `streamlit run app.py` and `python -m pytest -q` working without the builder or a proprietary agent SDK.
4. Keep water and carbon conservation checks passing. Use full observation windows; do not interpolate monthly or composite validation data into invented daily forcing.
5. Read SCIENCE.md for versioned soil mappings and plant/carbon assumptions. TRY categories do not define numeric plant rates; preserve the distinction between source properties, estimated hydraulics, preset rates and calibrated parameters.
6. Never replace a failed provider with synthetic data. Report unavailable data or matching observations explicitly.
7. The engine uses calabrese-depth-v2: configurable fixed layers, root uptake, leaf/stem/root stocks, LAI and five carbon pools per layer. It is uncalibrated; it is not an unchanged implementation of a published model.
8. Preserve supplied data terms, attribution, and source hashes. Review terms before republishing source archives.
9. Explain changes, assumptions, and test evidence. Scientific validation requires appropriate observations, not just a passing test suite.

## Availability
All selected data blocks include loaders. Public services still need internet. Model's Soil data control chooses SoilGrids (250 m, default finer detail) or HWSD (~1 km, faster) first; both mapped sources are checked before an explicitly authorized assumed preset. Advanced → Automatically try SoilGrids REST is on by default: a batched REST query has a 20-second foreground budget, followed by numeric WMS then WCS when required. A REST service failure opens a shared five-minute cooldown; later uncached lookups automatically retry after expiry. Site data gaps have a separate one-minute cooldown, and the five-starts-per-minute REST budget remains in force. The Advanced option can disable REST. Complete map profiles check 36 values with four workers. Existing HWSD maps are reused read-only; downloading a missing map (~1.9 GB uncompressed) requires the Advanced opt-in or explicit recovery action. A compact HWSD table is included when available. Validated profiles persist for 30 days across restarts. Optional comparisons load after results. TRY uses the included local archive when available. Unit tests use fixtures; live provider success is not implied.
"""


def _model_card(config):
    return f"""# Model card

**Project:** {config['project_name']}  
**Status:** First coupled depth implementation; not calibrated or validated.  
**Time step:** one day; current model contracts require daily forcing.  
**Selected modules:** {', '.join(config['models'])}.

The engine uses **calabrese-depth-v2**: {len(config['layer_depths_cm'])-1} initial soil layers to {config['soil_depth_cm']:g} cm (editable), conservative interlayer water exchange, soil evaporation, interception and root transpiration; living leaf/stem/root carbon with LAI, phenology and rooting dynamics; five soil-carbon pools per layer. Equations, defaults and source mappings are documented in SCIENCE.md and MODEL_INTERFACES.md.

SoilGrids/HWSD properties initialize carbon and estimate porosity/hydraulic parameters through a versioned mapping. Initial pool fractions and numeric plant/carbon rates are uncalibrated assumptions. TRY categories can inform functional presets but do not define numerical rates. Only after both mapped soil sources fail can Use assumed soil preset explicitly authorize generic soil assumptions; usable weather is required. Default fallback loam and 6,200 g C/m² whole-profile SOC with a 50 cm e-folding depth are assumptions, not observations. No source failure generates fake observations.

Comparisons summarize matching complete windows with bias, RMSE and MAE; they do not automatically establish validation. SRDB total soil respiration is not equated with heterotrophic respiration. TerraClimate is a gridded modeled water-balance product; MOD16 spatial support and QA differ from site measurements. When ORNL JSON omits collection metadata, the loader verifies collection 061 from CSV granule names rather than inventing a version.

Remaining limitations include no soil thermal/snow solver, groundwater, nutrients, management, DOC advection, carbon feedback on porosity, calibration or uncertainty propagation. Conservation tests verify accounting only.
"""


def _data_provenance(config):
    lines = ["# Data provenance and availability", "", "Site and dates are selected at runtime. Download per-run provenance from the app for actual assets, hashes, errors and comparisons.", ""]
    for block in config['data_blocks']:
        lines += [f"## {block['label']}", f"- Block: `{block['id']}`; role: {block['role']}; resolution: {block['resolution']}.",
                  f"- Selection: {'automatic' if block['automatic'] else 'extra'}; access: {block['access']}.",
                  f"- Source: {block['url']}", f"- {block['notes']}", ""]
    if 'local_try' in config:
        lines += ["## Supplied TRY archive", json.dumps(config['local_try'], indent=2),
                  "Citation: Kattge et al. (2011), TRY – a global database of plant traits, Global Change Biology 17:2905–2935; TRY Categorical Traits Dataset v1.0 (2012).",
                  "Original workbook and included source terms/documentation are preserved in data/try/. Review those terms before republishing.", ""]
    if 'local_hwsd' in config:
        lines += ['## Supplied HWSD soil table',json.dumps(config['local_hwsd'],indent=2),
                  'The compact layer table and its provenance are included in data/hwsd/. The original Access database is unchanged. Existing geographic maps are reused read-only. Enable Allow first HWSD map download under Advanced, or choose Download HWSD map and retry in recovery, to retrieve a missing ~1.9 GB uncompressed FAO raster; downloads are disabled by default.','']
    lines += ["## Validation", "Schema/unit/QA and conservation checks are included. Comparative evaluation only uses matching full observation windows. Live provider availability must be checked in the target environment.", ""]
    return '\n'.join(lines)


def make_project_zip(config: dict) -> bytes:
    validate(config)
    models = sorted(set(config['models']))
    blocks = resolve_data_blocks(models, config.get('additional_datasets', []))
    depths = configured_depths(config)
    cfg = {
        'schema_version': 2, 'project_name': str(config.get('project_name') or 'Ecosystem model')[:120],
        'slug': clean_name(str(config.get('project_name') or 'Ecosystem model')),
        'models': models, 'weather_source': 'open_meteo',
        'initial_run_days': config.get('initial_run_days', config.get('simulation_steps', config.get('days', 365))),
        'time_selection': 'runtime', 'step_days': 1,
        'scientific_version': 'calabrese-depth-v2', 'layer_depths_cm': depths,
        'soil_depth_cm': depths[-1],
        'additional_datasets': sorted(set(config.get('additional_datasets', []))),
        'data_blocks': blocks, 'request': config.get('request', ''),
    }
    assets = []
    soil_assets = hwsd_assets() if any(block['id']=='hwsd' for block in blocks) else []
    if soil_assets:
        cfg['local_hwsd'] = json.loads(soil_assets[1].read_text())
    if any(block['id'] == 'try_categorical' for block in blocks):
        cfg['local_try'] = try_inventory()
        if cfg['local_try']['available']:
            assets = try_assets()
    project = cfg['slug']
    modules = {block['module'] for block in blocks} | {'validation'}
    output = io.BytesIO()
    with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        for path in sorted(TEMPLATE.rglob('*')):
            rel = path.relative_to(TEMPLATE)
            hidden = any(part.startswith('.') or part == '__pycache__' for part in rel.parts)
            if not path.is_file() or (hidden and rel.as_posix() not in ('.gitignore', '.streamlit/config.toml')) or path.suffix in ('.pyc', '.pyo'):
                continue
            if rel.parts[0] == 'models' and path.suffix == '.py' and path.stem not in models and path.name != '__init__.py':
                continue
            if rel.parts[0] == 'data_blocks' and path.suffix == '.py' and path.stem not in modules and path.name != '__init__.py':
                continue
            archive.write(path, f'{project}/{rel.as_posix()}')
        for asset in assets:
            archive.write(asset, f'{project}/data/try/{asset.name}')
        for asset in soil_assets:
            archive.write(asset,f'{project}/data/hwsd/{asset.name}')
        archive.writestr(f'{project}/model_config.json', json.dumps(cfg, indent=2))
        archive.writestr(f'{project}/data_blocks.json', json.dumps({'schema_version': 1, 'blocks': blocks}, indent=2))
        archive.writestr(f'{project}/AGENT_BRIEF.md', _agent_brief(cfg))
        archive.writestr(f'{project}/MODEL_CARD.md', _model_card(cfg))
        archive.writestr(f'{project}/DATA_PROVENANCE.md', _data_provenance(cfg))
        archive.writestr(f'{project}/GETTING_STARTED.md', getting_started_markdown())
    return output.getvalue()
