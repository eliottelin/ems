# Ecohydrological Model Studio (EMS)

[![Checks](https://github.com/eliottelin/ems/actions/workflows/checks.yml/badge.svg)](https://github.com/eliottelin/ems/actions/workflows/checks.yml)

EMS builds an independent Streamlit ecosystem sandbox from reusable model and
data blocks. Choose water, plants, soil carbon, or any combination. Standard
inputs, comparison datasets and backup sources follow the model selection;
only extra information needs a toggle. Weather uses Open-Meteo.

For Streamlit Community Cloud, deploy **`builder_app.py`** from the repository's
`main` branch with **Python 3.14**. See [deployment instructions](docs/DEPLOYMENT.md).

The builder has no fixed site or time frame. In the exported app, choose a site
by Open-Meteo place search or precise coordinates, then choose run dates. Requests beyond the available weather boundaries adjust automatically. Archive boundary probes check the first and
latest complete daily records for that site. The current model
contracts use daily updates. Choose the run length by selecting dates in the
app; the builder has no simulation-step or duration setting.

## Start

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m streamlit run builder_app.py
```

Download a project ZIP, extract it, then in that independent folder run:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m streamlit run app.py
```

The generated app runs without a coding agent. Optional instructions entered
at the bottom of the builder become `AGENT_BRIEF.md`, usable with any coding
assistant. No proprietary agent SDK or LLM key is required.

## Model and data library

| Component | Behavior |
|---|---|
| Water, plants, soil carbon | Selectable depth-resolved water, leaf/stem/root growth and five-pool soil carbon; daily conservation checks |
| Open-Meteo historical | Daily ERA5 weather forcing, live site/date checks, precise missing-variable diagnostics; no synthetic fallback |
| Open-Meteo climate | Separate daily scenario block; climate-model provenance retained |
| SoilGrids | 250 m profiles via automatic REST, then checked numeric WMS/WCS recovery |
| HWSD v2 | Uses an existing geographic map and supplied MDB/compact table; first map download is opt-in under Advanced |
| TRY | Source-checked species table with search, group filtering, sorting and explicit application of functional presets |
| SRDB | Public or local CSV, site/year candidates and qualified heterotrophic respiration comparisons |
| TerraClimate | Current NCSS NetCDF with CF packing, time/unit/site checks or already-decoded local CSV |
| MOD16A2GF v6.1 | ORNL DAAC point subsets, collection/QA/scale checks and actual composite durations |

Adapters are implemented and tested with provider-shaped fixtures. Service
availability is separate from code readiness. HWSD needs downloaded assets;
provider migrations or outages can require a current endpoint or local data.
The app reports unavailable blocks and never fabricates observations.

The Model tab's **Soil data** choice selects **SoilGrids · finer detail (250 m)**
or **HWSD · faster (~1 km)**. Both mapped sources are checked in the chosen
priority order; finer grid spacing is not a guarantee of site accuracy.
**Advanced → Automatically try SoilGrids REST** is on by default. SoilGrids first
requests all 36 required values together through REST, with a 20-second foreground
budget, then uses checked numeric WMS and WCS when needed. A transient REST service
failure triggers a shared five-minute cooldown, so other lookups use maps
immediately; a later lookup retries REST automatically after the cooldown ends.
Missing soil values affect only that site's one-minute cooldown. The shared REST
request budget remains five calls per minute. Disabling the Advanced option uses
maps only. Complete map profiles check 36 values with four workers and target a
two-minute budget, with provider and socket delays possible.

Provider status is checked from actual validated responses, rather than assuming
that the website's pause notice proves a universal outage. Recent checks include
a complete six-depth REST profile at 42, -93.7 and an Austin response with missing
surface bulk density. See [the REST status brief](docs/SOILGRIDS_REST_STATUS.md).

If both mapped sources fail, the recovery dialog shows their reasons. Only then
can **Use assumed soil preset** explicitly authorize generic soil assumptions;
weather is still required. **Allow first HWSD map download** in Advanced, or
**Download HWSD map and retry** in recovery, explicitly downloads the large FAO
map when missing (about 1.9 GB uncompressed). Existing maps are reused read-only.
Nearby searches also require a click.

Validated soil profiles are stored as versioned JSON for 30 days in the project's
`.ems-cache/soil-profiles-v1/` folder and survive server restarts. Dates and model
parameters do not invalidate soil, while source policy and relevant HWSD file
changes do. Nearby candidate checks reuse the same profiles. Parsed HWSD tables
and hashes are cached separately. One loading panel follows weather, soil/plant
inputs and calculation, showing elapsed time and fallback details. Run details
include per-source times, route diagnostics and cache storage. Optional
**Load comparisons** downloads observation data after model results are ready.

Plant browsing uses three controls and one scrollable table: search by name or
ID, filter the recorded group/ecosystem, and sort or group the results. Select a
row and choose **Use selected plant** to apply it. Browsing preserves the applied
plant and manual rates. Choosing an ecosystem directly clears the species and
loads its defaults. TRY supplies categories rather than measured numeric rates.
Both tree groups use Forest; the archive does not identify crop use.

New exports include polling/reload settings. Restart Streamlit from a fresh
export to update an older downloaded app. **Download source report** on the
**Data** tab and downloaded run details identify the project and modules actually
loaded in memory.

The supplied TRY workbook remains under `data/`. Plant-enabled exports include
it with the supplied terms, documentation and citation. The loader removes one
exact duplicate row in the supplied archive, records that transformation, and
preserves the source bytes. Review the included terms before public reuse.

## Scientific status

The engine now uses **calabrese-depth-v2**: six default layers to 2 m, editable
boundaries, conservative water exchange, root uptake, living leaf/stem/root
carbon, LAI/phenology and five carbon pools per layer. SoilGrids/HWSD properties
map to initial stocks and estimated hydraulics; numerical plant/carbon rates
remain uncalibrated presets. Source mappings, equations and assumptions are in
[SCIENCE.md](template_generated_app/SCIENCE.md).
Depth and boundaries stay synchronized across 1–30 layers to 1000 cm. Merging
source layers conserves SOC and fine-earth pore volume. Changing depth starts a
fresh initialized domain; matching respiration dates does not prove depth support.

The Model tab focuses on setup, one loading panel and grouped results. Run checks
required inputs before calculation. A missing input opens a recovery dialog;
nearby searches start only when requested. A usable HWSD profile resolves a failed
SoilGrids requirement without an unnecessary recovery dialog. Proxy selections
keep the original site; only an explicit last-resort action can use an assumed
soil preset after both mapped sources fail. Comparisons
load on request after the simulation. Weather is required. Data retains its
independent nearby-search controls. Advanced
contains scientific parameters and provider settings. Data has
its own tab with summaries/charts and expandable source records. Full-window
comparisons report bias/RMSE/MAE; unavailable observations do not establish
validation. Conservation tests verify accounting, not predictive accuracy.

Generated projects can also run on **Streamlit Community Cloud**: upload the
extracted project to GitHub, select its `app.py` at https://share.streamlit.io/,
and deploy. The exported getting-started guide includes this alternative and
local Python/pip commands. No deployment occurs automatically.

## Key files

- `builder_app.py`: simplified model-first builder.
- `studio_builder/registry.py`: independent blocks, access requirements and model/data connections.
- `studio_builder/generator.py`: standalone ZIP, manifests, provenance and agent brief.
- `template_generated_app/data_blocks/`: source loaders and comparison logic.
- `template_generated_app/models/`: selectable scientific modules.
- `template_generated_app/MODEL_INTERFACES.md`: daily inputs, outputs, units and balances.
- `docs/DATA_CATALOG.md`: adapter access and validation details.
- `docs/DEVELOPMENT_ROADMAP.md`: agreed scientific development direction.

## Verify

```bash
.venv/bin/python -m pytest -q
```

Tests cover scientific conservation, source parsing and unit conversions,
observation matching, builder interactions, and independent extracted projects.
Synthetic forcing is an offline test utility, not a builder option or API
fallback. Live checks require provider access and appropriate local assets.

Open-Meteo geocoding and a 365-day College Station weather request were verified
live on 2026-11-09 through a network-capable connector; captured JSON also passed
the local parser and model conservation checks. See `docs/VERIFICATION.md` for
the verified scope, archive-release issue and network limitations.
