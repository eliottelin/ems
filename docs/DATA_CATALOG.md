# Data sources

Every library entry has an independent loader that returns a dataframe,
resolution, units and provenance. Loaders do not depend on the scientific
models or Streamlit. Model selection resolves a set of standard input,
validation and fallback blocks; any remaining block can be added as extra
information. The exported `data_blocks.json` records the resolved connections.

| Block | Access and supported inputs | Checks and role |
|---|---|---|
| [Open-Meteo historical](https://open-meteo.com/en/docs/historical-weather-api) | Daily ERA5 API; site and dates selected at runtime | Consecutive requested dates, required fields, finite P/T/ET0, nonnegative P/ET0, unit metadata; primary weather forcing |
| [Open-Meteo climate](https://open-meteo.com/en/docs/climate-api) | Daily climate API, 1950–2050, explicit climate model | Separate scenario provenance and daily forcing checks; optional scenario simulation |
| [SoilGrids](https://docs.isric.org/globaldata/soilgrids/SoilGrids_faqs_02.html) | 250 m profiles through automatic batched REST, then numeric WMS and WCS recovery | Texture, bulk density, SOC and coarse fragments; explicit mapped-unit conversions, no-data and profile checks |
| [HWSD v2](https://www.fao.org/soils-portal/data-hub/soil-maps-and-databases/harmonized-world-soil-database-v20/en/) | Supplied HWSD2.mdb or bundled compressed table; existing BIL/HDR map, opt-in FAO map download or explicit mapping-unit override | Depth, component/sequence, texture, bulk density and SOC units; soil backup without a hidden service substitution |
| [TRY categorical](https://www.try-db.org/TryWeb/Data.php#3) | Supplied XLSX under `data/`; copied to plant-enabled local exports with original terms/docs | Actual columns, species IDs, missing identifiers, exact duplicates and conflicting records; conservative categorical PFT classification |
| [SRDB](https://github.com/bpbond/srdb) | Downloaded CSV or public repository CSV | Site/radius/window candidates; hash snapshot; full-coverage single-year baseline Rh records with no quality flags (blank or Q0); inferred annual chronology requires review |
| [TerraClimate](https://www.climatologylab.org/terraclimate.html) | THREDDS NCSS catalog discovery, configurable current NCSS endpoint, or local decoded point CSVs | Remote CF NetCDF packing/fill/time decoding and site coordinates; decoded local CSV; complete-month AET comparison |
| [MOD16A2GF](https://modis.ornl.gov/data/modis_webservice.html) | ORNL DAAC REST point subsets, at most ten composite dates per request | Accepted data require collection 061 recovered from CSV granule names when JSON omits it; ET/PET fill and scale checks; main-algorithm QA by default, optional strict clear/best policy; native composite-window comparison |

## Automatic connections

- **Water:** Open-Meteo, SoilGrids, HWSD backup, TerraClimate comparison and MOD16 backup.
- **Plants:** Open-Meteo and TRY categories.
- **Soil carbon:** Open-Meteo, SoilGrids, HWSD backup and SRDB comparison.
- **Future climate:** optional extra block; it never replaces historical observations implicitly.

Backup blocks are included as components. During a model run they are attempted
when their primary block fails; both the failure and chosen backup are recorded.
Each block can also be loaded independently from the exported app's data library.

**Soil data** on Model selects the preferred source: **SoilGrids · finer detail
(250 m)** (default) or **HWSD · faster (~1 km)**. A normal run tries both mapped
sources in the preferred order until one supplies complete valid coverage.
Source choice and every failure are recorded. Grid spacing describes spatial
resolution, not a uniform accuracy guarantee or a measured field profile.
The legacy block ID `soilgrids_wcs` remains compatible with existing projects.

SoilGrids normally tries a batched REST request, then numeric WMS GetFeatureInfo
and WCS center-pixel recovery. The REST query requests the six model properties
at all six depths, rather than accepting the API's broader default property set.
Each complete map route needs 36 values, with four workers after a first
fail-fast probe. The two-minute map budget is best effort, not a guarantee about
DNS, slow bodies or in-flight socket cleanup. Multiple-layer WMS batching was
probed on 2026-11-17: HTML returned six distinct values but mislabeled every depth
as the first requested layer. GML/plain text omitted numeric values, and JSON was
unsupported. The loader therefore keeps independent checked layer requests and
rejects ambiguous multi-record HTML; it does not infer depth from response order.
See `verification/austin_soil_sources.json` for the capability observations.

**Advanced → Automatically try SoilGrids REST** is on by default. It prepends
REST with a 20-second foreground budget and preserves the five-starts-per-minute
limit across the app process. Timeouts, connection failures, HTTP 429 and server
errors open a shared five-minute cooldown; queries during that interval use maps
without repeating the REST wait. A later uncached lookup probes REST again after
expiry and selects it automatically if a complete checked profile is returned.
Missing soil values open only a one-minute cooldown at that coordinate. Explicit
retry bypasses cooldowns, but never the fair-use limit. Reports distinguish
actual attempts from deliberate cooldown/rate-limit skips. Turning the option
off preserves maps-only behavior. Healthy validated cached profiles remain
usable for 30 days regardless of which transport originally supplied them.

The API is beta. [ISRIC's access documentation](https://docs.isric.org/globaldata/soilgrids/SoilGrids_faqs_02.html)
continues to carry a pause notice, but live results do not establish a universal
outage. On 2026-11-17, the OpenAPI v2.0 document was available and described 14
properties, including water retention and array query parameters. A query at
42, −93.7 returned a complete six-depth profile in about 15.4 seconds; Austin
30.2672, −97.7431 responded in about 13.1 seconds but lacked the 0–5 cm
bulk-density mean. These point-in-time observations do not guarantee uptime or
coverage elsewhere. See [the current REST status brief](SOILGRIDS_REST_STATUS.md)
for the captured contract and verification details.

Only after both mapped sources fail does **Use assumed soil preset** authorize
the last resort, and usable weather is required. The default loam assumes
porosity 0.43, FC 0.60 and wilt 0.20 as relative saturation, b 5.4 and Ksat
250 mm/day. Default SOC is 6,200 g C/m² across the modeled profile, distributed
exponentially with a 50 cm e-folding depth; the initial profile is 2 m. These
editable assumptions are not soil observations. Recovery lists both source
failures and can explicitly download a missing HWSD map (about 1.9 GB
uncompressed) before retrying. Missing values are never filled with zero.

Successful, physically validated profiles use a 30-day memory cache and versioned
JSON in `.ems-cache/soil-profiles-v1/`, surviving app-server restarts. The original
retrieval time sets expiry; reading a disk entry does not extend it. Source,
coordinates, source policy, relevant soil settings and HWSD file metadata form
the identity; run dates, model parameters and comparison settings do not.
Checksum, schema and physical-profile checks reject damaged entries. Writes are
atomic, and a read-only project can still use the memory cache. Cache metadata
and original source provenance remain in reports. Parsed HWSD tables and source
hashes are separately reused by file path, modification time and size.

Nearby searches are manual and validate candidates through the same profile
cache used after selection. A complete HWSD fallback resolves the soil input;
a failed SoilGrids lookup remains diagnostic rather than forcing another search.
One loading panel follows weather, soil/plant inputs and calculation, with
elapsed time, fallback details and calculation-time estimates. Per-source and
calculation durations are retained in run details. Optional **Load comparisons**
starts observation downloads after results are ready; TerraClimate/MODIS backup
and complete-window rules still apply. Trait preparation can overlap soil.

## Readiness and scientific use

Loaders have provider-shaped fixture tests, including CF NetCDF decoding and
MODIS CSV collection recovery. The supplied MDB was actually parsed: 408,835
layer records across 29,538 mapping units. A selected-column compressed export
and source hash are bundled automatically; future MDB changes refresh it.
The original source bytes are unchanged. Geographic raster download is disabled
by default; Advanced provides an explicit first-download toggle. Historical
verification of the data adapters is documented separately below.

Live connector checks verified Open-Meteo weather, the current TerraClimate
v1.1 catalog/point CSV/CF metadata, ORNL MODIS JSON/band metadata, and ISRIC
service availability. The TerraClimate CSV exposes packed numbers with mm labels;
remote retrieval now uses NetCDF with explicit CF decoding. That binary path and
MODIS CSV content negotiation have fixture coverage and still need an end-to-end
check from a network-enabled Python runtime. SoilGrids/MODIS can legitimately
have masked urban/water pixels. See VERIFICATION.md for evidence and scope.

SoilGrids/HWSD properties now drive a versioned mapping to initial SOC and
estimated texture-based hydraulics. TRY categories optionally select broad
functional presets; numeric rates remain uncalibrated assumptions. SCIENCE.md
specifies every mapping and equation. SRDB total respiration is not comparable
to modeled heterotrophic respiration. Only complete compatible observation
windows are compared; bias, RMSE and MAE describe agreement, not validation.
TerraClimate is itself a modeled gridded product and shares ERA5 information
with the forcing. Missing comparison data are availability states, not proof
that a run is observationally validated.

## Local TRY inventory and use

The supplied workbook contains `Categorical Trait Data` and `Field Details`.
The loader reads `AccSpeciesID`, `AccSpeciesName`, `PlantGrowthForm`, `LeafType`,
`LeafPhenology`, `PhotosyntheticPathway`, `Woodiness` and source columns. It
preserves ambiguous categories as `unresolved`, normalizes name whitespace and
removes exact duplicate rows while recording the transformation. The supplied
archive has 66,043 identified rows and one exact duplicate, yielding 66,042
validated rows. Conflicting duplicate IDs require review.

The compact index verifies both its source workbook hash and its own content
hash. Source, CSV or metadata replacements invalidate the lookup cache; stale
derived files rebuild from the original workbook and persist where writable.
Lookup also works without write permission. The UI searches names literally by
tokens or exact ID, filters recorded categories, and displays all matching rows
in a native sortable table. Only **Use selected plant** changes model settings.
Forest combines deciduous and evergreen trees; crop use and habitat suitability
are not available in these categorical traits.

The included TRY source terms and package documentation were read before local
ZIP bundling. Original workbook bytes and source PDFs are preserved, with a
hash and the required citation: Kattge et al. (2011), Global Change Biology
17:2905–2935, and TRY Categorical Traits Dataset v1.0 (2012). Review the terms
before republishing. No dataset is uploaded to a third-party service by the builder.

See `template_generated_app/data/README.md` for expected local file layouts.

## Historical retrieval and usability audit (before REST restoration)

Before REST was restored as the primary route, the geographic one-pixel WCS path
failed to recover valid values, including College Station. A numeric ISRIC
GetFeatureInfo path recovered all 36 values
(six properties × six depths) there; live point checks also returned sand values
at a nearby Texas location and in Iowa. The ISRIC HTML template observed in that audit labeled
X/Y as latitude/longitude in reverse; the parser accepts the coordinate ordering
only after a distance check against the requested site, and rejects units,
coverage IDs or sampling cells that do not match. A failed numeric map-point
response can try a 3×3 WCS subset, using only its center. No neighboring soil is
substituted.

SRDB parsing uses low_memory=False and explicit numeric conversion for comparison
fields; mixed free-text error/depth columns no longer trigger chunk dtype
warnings. Blank source flags normalize to Q0; they are not flagged measurements.
The official rounded midpoint convention gives calendar year X for X.5 and
July X−1 through June X for X.0. These dates are inferred and require chronology
review in the original study. Multi-year means are excluded from automatic annual
comparisons. A no-match result includes actual qualified study sites/windows
for explicit exploration. Selecting a study now keeps the model site and dates
and labels its source coordinates and historical window as a proxy. A separate
date action changes the run period if desired; no incompatible years are compared.

## Historical Dallas source report and current recovery behavior

The supplied report is for 32.78306, −96.80667 during 2025-11-04–2026-11-03.
Weather, TRY and TerraClimate loaded. The optional climate block was not requested.
At this exact point, SoilGrids did not return a complete valid profile, HWSD's
actual geographic map resolves to urban unit 7001 with missing properties, and
the live MODIS sample returns urban fill 32762. Those are coverage limits, not
missing packages or a failed HWSD download. Missing soil is never converted to zero.

The current main flow checks required inputs when **Run ecosystem model** is
clicked. Missing required data opens **Choose data for this run**, where users
can request checked nearby choices, accept a proxy and resume, retry, cancel, or
explicitly use an assumed soil preset only after both mapped sources fail. A usable alternative profile
resolves the soil requirement immediately. Comparisons are optional downloads
after results rather than a prerequisite for calculation. Missing weather blocks
continuation. A proxy that fails its recheck pauses again. Prepared sources and
failures are reused across choices until retry or request changes. Changes to
site/dates/parameters clear stale pending choices. Historical-study dates require
a separate explicit action.

Data retains **Find nearby available data** as a secondary control. It checks the
selected source within 25 km; an HWSD cell is not advertised as SoilGrids or ET
coverage. SoilGrids uses a bounded sand overview or point sampling to seed complete
profile checks; MODIS uses its actual sinusoidal neighborhood and full point/QA/
collection checks. Other spatial time series have bounded point probes. Offered
comparison candidates require complete matching months/composites. This is finite
sampling, not an exhaustive nearest-pixel guarantee. Provider/decoding failures
stop the search; missing dates can offer a date adjustment instead.

Choosing a candidate keeps the simulation site and run dates and uses that source
as an explicit proxy. Each source can have its own proxy; original and source
coordinates, distance and windows are retained in downloadable reports. Proxy
comparisons are not field validation at the model site. The choice can be removed
in Data and is cleared if the model site changes. TRY already offers species
choices and has no geographic recovery requirement.

The public SRDB snapshot contains eligible studies, but their years end in 2021.
It cannot validate the Dallas 2025–26 run. Fixing blank quality flags and midpoint
interpretation restores actual study choices without increasing the radius or
claiming unrelated measurements validate Dallas. Recommended MODIS QA accepts
gap-filled main-method SCF 0/1 with good MODLAND and functioning detectors;
inherited cloud bits alone do not reject this GF product. Strict clear-sky/SCF 0
is available under Advanced. Fill values and non-061 collections remain excluded.

To update an already downloaded app, use a fresh export. The app automatically
looks for a complete existing HWSD2.bil/HWSD2.hdr pair in its data folder, adjacent
exports and Downloads (known dataset paths only, including one extra extraction
folder). External files are reused read-only and are not copied. Header/dimensions
and byte counts are checked. Missing maps download only after **Allow first HWSD
map download** is enabled under Advanced, or **Download HWSD map and retry** is
chosen in recovery. Manual copying is optional. Keep an
older folder while its map is being reused, or explicitly allow the new app to
download its own map. Restart Streamlit from the new project folder.
See VERIFICATION.md and its linked artifacts for the actual checks and limits.

Source statuses distinguish loaded data, soil actually used, unused backups,
no matching records, missing files, connection failures and schema/quality
failures. Source reports preserve exact exceptions and details. TerraClimate
loading alone does not guarantee a complete matching month; MODIS is attempted
during optional comparison loading when the primary has no full window. Independently loaded comparison data
refresh the comparison of the retained simulation.
