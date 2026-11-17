# Generated Ecohydrological Model Studio project

This is a **standalone Streamlit app** generated from Ecohydrological Model Studio. It does not require the builder or an AI coding agent.

## Start

Open **GETTING_STARTED.md** for five setup steps, Python/pip installation links,
and copyable macOS/Linux and Windows commands. The dependency installation
installs Streamlit automatically; Homebrew is optional.

Optional: `pip install -r requirements-dev.txt && pytest -q`.

## Read first
- `model_config.json`: selected models/data blocks and the daily calculation contract; site and dates are chosen in the app.
- `MODEL_CARD.md`: scope, equations, and limitations.
- `MODEL_INTERFACES.md`: daily inputs, outputs, units, coupling order and parameter constraints.
- `DATA_PROVENANCE.md`: source access, limitations, attribution and bundled local data.
- `data_blocks.json`: independent dataset blocks and their model connections.
- `data/README.md`: supported local dataset paths and formats.
- `AGENT_BRIEF.md`: plain-English task for **any** coding assistant or developer.

The Model tab contains place suggestions as you type, run dates, a daily time-step
explanation, a Run button with one loading panel, and grouped water/plant/soil
charts. **Load comparisons** requests optional comparison data after results are
ready; comparison downloads do not change the simulation. Dates outside the
available weather boundaries adjust automatically.
Advanced contains soil boundaries, ecosystem presets, parameter overrides and
data access settings. Data can be explored independently with
summary metrics and charts; raw records and provenance are expandable.

The loading panel follows three stages: weather, soil/plant inputs and model
calculation. It shows elapsed time, the current source and any fallback; model
calculation also estimates remaining time from completed days. Run details retain
per-source durations, retrieval diagnostics and cache status.

**Soil data** chooses which mapped source to try first: **SoilGrids · finer detail
(250 m)** (default) or **HWSD · faster (~1 km)**. If the preferred source fails,
the other mapped source is checked before offering a preset. Finer pixels do not
guarantee better accuracy at every site. Nearby searches start only when requested.

**Advanced → Automatically try SoilGrids REST** is on by default. REST requests
the six required properties at all six depths together and gets a 20-second
foreground budget. A complete validated REST profile is used immediately;
otherwise numeric WMS and WCS center-pixel recovery check the same 36 values,
with four workers. The map budget targets two minutes; provider behavior and
sockets mean this is not a hard total-runtime guarantee.

A transient REST outage starts a shared five-minute cooldown. Other lookups use
maps immediately, then a later lookup automatically retries REST when the
cooldown expires. Missing soil values are remembered for one minute at that
location only. REST starts remain limited to five per minute across the app
process. Turning the Advanced option off uses maps only. The REST service is
beta: a responding API can still lack a usable profile for a particular site,
and an availability notice does not establish that every query fails.

If both mapped sources fail, recovery shows their individual reasons. With usable
weather, **Use assumed soil preset** explicitly authorizes the last resort. The
default loam assumes porosity 0.43, field capacity 0.60 and wilting point 0.20 as
fractions of saturation, retention exponent 5.4, and Ksat 250 mm/day. Default
whole-profile soil carbon is 6,200 g C/m², distributed exponentially with a 50 cm
e-folding depth across the chosen profile (initially 2 m). These are editable
assumptions, not observations; changing the source can change the results.

Successful validated soil profiles are cached as versioned JSON for 30 days in
`.ems-cache/soil-profiles-v1/`, including across app-server restarts. Run dates and
model parameters reuse them. Relevant local soil files, source policy or
mapping-unit changes invalidate them; damaged or expired entries are retrieved
again. A read-only project still runs with the memory cache. HWSD tables and
hashes are reused across locations. Existing HWSD maps are reused read-only;
**Advanced → Allow first HWSD map download** opts into downloading the large
map when none is available (about 1.9 GB uncompressed). Recovery also offers
**Download HWSD map and retry**. Downloads are disabled by default.

In **Advanced → Plants**, search by species name or ID, filter a functional group,
and sort or group the complete matching table. Select a row and press **Use
selected plant** to apply it. Search, filters, sorting and row highlighting keep
the current plant and manual values. Choosing Ecosystem directly clears the
species and loads its defaults. A new **Run** updates model results. TRY supplies
functional categories, not species-specific numeric rates; both tree groups use
Forest, and crop use is not recorded.

The synchronized soil depth and boundary controls accept 1–30 layers to 1000 cm.
Depth changes initialize a new domain on Run. Soil data must cover every layer.
Run details record the effective settings, depth, source hashes and loaded build.

**Download source report** on the **Data** tab and downloaded run details identify
the running project and imported controls. After replacing an old project,
restart Streamlit from the new folder. The included `.streamlit/config.toml`
enables polling and reruns after local edits.
An optional browser regression is `tests/browser_plant_controls.cjs`; it needs
Playwright and a running app, while normal use and Python tests do not.

The default is C3 grassland with six soil layers to 2 m. Water moves between
fixed layers; roots control water uptake, plants track leaf/stem/root carbon and
LAI, and five carbon pools are tracked per layer. Soil properties initialize
carbon and estimate hydraulics through an explicit versioned mapping. Read
**SCIENCE.md** for equations, assumptions and limits: this first implementation
is uncalibrated. Numerical closure is not proof of observational accuracy.

Open-Meteo provides strict daily historical forcing; failed weather stops a run.
Only after both mapped soil sources fail can an explicitly authorized assumed
texture preset be used. Missing
observations are reported without fabricated comparisons. Whole months,
composites and years are compared only when simulation windows are complete.
An included future-climate block supports separate scenario runs.

For a command-line live check from this extracted project:

```bash
.venv/bin/python check_live_data.py --place "College Station, Texas" --start 2023-01-01 --end 2023-01-07
```

## Hosting
You can run this app on **Streamlit Community Cloud** without a local Python installation. Upload the extracted project to GitHub, create an app at https://share.streamlit.io/, and select `app.py` and the correct branch. `requirements.txt` installs dependencies automatically. GETTING_STARTED.md links to the official deployment guide. Preserve dataset terms and use permitted assets. Keep credentials in Streamlit secrets.
