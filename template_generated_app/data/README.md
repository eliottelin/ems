# Data

Keep local datasets below this directory; loaders also support the documented
public services. The app never substitutes synthetic data after a failed load.

- `try/`: original TRY categorical XLSX, documentation, and source terms. A local
  archive is included in the ZIP when available in the builder. Citation:
  Kattge et al. (2011), Global Change Biology 17:2905–2935; TRY Categorical Traits
  Dataset v1.0 (2012). Preserve the included terms and review them before any
  public redistribution. Categories do not supply numeric plant rates.
- `hwsd/HWSD2_LAYERS.csv.gz`: compact selected-column export from the supplied
  official `HWSD2.mdb`, included when available. `source.json` records its source
  hash, transformation and citation. The original MDB can also be read directly
  anywhere under `data/`, using the Python access-parser package (no ODBC or
  Homebrew needed). `HWSD2.bil` is the large geographic soil map; `HWSD2.hdr`
  describes its grid. The app first checks its data folder and reuses a complete
  official pair in adjacent exports or Downloads read-only. If none is available,
  enable **Advanced → Allow first HWSD map download** or choose **Download HWSD
  map and retry** in recovery to retrieve it from FAO into `data/hwsd/`. The map
  is about 1.9 GB uncompressed, is intentionally excluded from exports, and never
  downloads without explicit opt-in. A known mapping-unit ID can be entered under
  Advanced → Data access when appropriate. The
  loader selects soil sequence 1 by default and records it; this represents one
  component of a mapping unit, not a measured point profile.
- `srdb/srdb-data.csv`: optional local database snapshot. Otherwise the SRDB
  block retrieves the public CSV and records its content hash.
- `terraclimate/aet.csv` and `terraclimate/pet.csv`: optional decoded NCSS point
  CSVs, with date/time, latitude, longitude and variable headers declaring mm, e.g.
  `aet[unit="mm"]`. Otherwise the current provider's NCSS point NetCDF endpoint is discovered automatically. CF scale, offset, fill codes and time units are decoded explicitly; remote CSV is avoided because it can label packed values as mm. A current endpoint override containing `{variable}` remains available.

The Model tab's **Soil data** choice controls mapped-source priority:
**SoilGrids · finer detail (250 m)** (default) or **HWSD · faster (~1 km)**. Both
mapped sources are tried in the chosen priority order before a preset is offered.
**Automatically try SoilGrids REST** in Advanced is on by default and gives a
batched REST query a 20-second foreground budget before numeric WMS and a WCS
3×3 center-pixel subset. Service failures start a shared five-minute cooldown;
later uncached lookups retry REST automatically after expiry. Site data gaps have
a separate one-minute cooldown. The REST budget remains five starts per minute
across the app process. Turning the option off uses maps only. Each complete map
route checks 36 values using four workers; map retrieval targets a two-minute
budget. Profiles must pass unit, depth, no-data and physical checks. A successful
saved profile preserves its source resolution; source switching can change the
model inputs.

After both mapped sources fail, **Use assumed soil preset** is a separate explicit
last-resort choice, requiring usable weather. Loam is the default: assumed
porosity 0.43, FC 0.60 and wilt 0.20 of saturation, b 5.4 and Ksat 250 mm/day.
The default carbon total is 6,200 g C/m² over the chosen profile, spread
exponentially with a 50 cm e-folding depth. These editable assumptions are not
observations. MOD16A2GF uses ORNL DAAC point subsets and
checks QA and valid values. CSV granule filenames recover collection metadata omitted by the JSON API; explicit non-061 collections are rejected and missing metadata is never fabricated. Future climate uses a separate
Open-Meteo climate-model block; it is not historical weather or an observation.

Each block returns a dataframe, its native resolution, units and provenance.
Models and blocks are independently editable. Soil properties map to the depth model through `soil_profile.py`; equations and estimated hydraulic values are documented in SCIENCE.md. TRY supplies categories, not measured numerical growth rates.

City-center coordinates can fall on urban cells with no ecosystem soil or ET
values. **Run ecosystem model** checks inputs before calculation. If a source
is missing, **Choose data for this run** opens automatically and explains every
failed soil source. Nearby checks start only after **Find nearby available data**.
Choose a proxy and continue, retry, download a missing HWSD map, or cancel. Only
**Use assumed soil preset** authorizes a preset after both mapped sources fail.
Comparisons load separately on request after results. Usable weather is always
required.
No site or date changes happen merely by opening the dialog; historical date
changes have a separate explicit action. Already checked sources are reused
while resolving choices, and selections are recorded in run details.

The secondary **Data → Find nearby available data** control searches a source and
offers up to three checked candidates within 25 km. SoilGrids candidates must
provide a complete profile, MODIS candidates must pass point-data quality and
collection checks, and time-series candidates need the requested support.
Finite sampling does not guarantee finding the globally nearest usable pixel.

Choosing **Use nearby data and continue** in the Run dialog, or **Use nearby data
for this source** in Data, keeps the simulation site and dates
and applies an explicitly labeled proxy to that source. Different sources can use
different nearby locations. Coordinates, distances and source windows appear in
source/run reports; proxy comparisons are not field validation at your site.
**Use data at my site instead** removes that choice. Changing the model site also
clears its proxy selections. TRY is a global species catalog, not a spatial map.

SRDB offers actual qualified historical studies with their distances and inferred
annual windows, which may be much farther away than 25 km. Choosing a study keeps
your site and dates; **Use these study dates for the run** changes only dates if
you want a matching historical comparison. A historical window cannot validate a
different year. Missing publication dates and provider outages require date or
connection remedies; relocating a source cannot create those records.

Manual map copying is optional. Keep an older downloaded app while a new one is
using its map; if it is removed, explicitly allow the new app to download the map
again. On another
computer/cloud host the map must be available there. Restart Streamlit in the new
project folder when updating the app.
