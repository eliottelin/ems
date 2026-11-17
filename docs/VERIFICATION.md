# Verification record (2026-11-09)

## Automated checks

The project suite covers:

- Builder defaults and optional extras, stale-helper recovery, source-based ZIP
  invalidation, and Community Cloud/local setup instructions.
- Exported app interactions using Streamlit AppTest: live-query suggestions,
  selected coordinates, date clamping, runs, retained/invalidated results,
  grouped charts, separate tabs, diagnostics and weather failure handling.
- Independent extracted projects with individual components and all models.
- New depth engine: profile/layer water budgets under drought/overflow, root
  distribution and uptake, plant allocation/turnover, five-pool flux ledgers,
  stock-preserving source regridding, and annual external ecosystem budgets.
- Source adapters: unit/QA/schema/date checks, CF NetCDF scale/offset/fill/time
  decoding, current TerraClimate discovery and availability clipping, MODIS
  collection recovery from CSV when JSON omits it, and archive-shaped HWSD
  automatic map download tests.
- Actual supplied HWSD database and converted-table source hashes, layer units,
  a full-depth mapping-unit profile, and explicit asset export/exclusion checks.

The v2 project suite includes the seven exported selection/UI checks, source-recovery and warning regressions, formula checks, and retained-run comparison updates. Final counts are recorded below. Python compilation and pip
consistency checks also run. Local server socket binding is restricted, so the
Streamlit agent HTTP API/browser check could not start; AppTest executes the app
with mocked network responses. Static checking remains unverified because ty
and mypy are unavailable in the permitted package sources; ty is declared in
requirements-dev.txt for a network-enabled environment.

## Actual local data

`data/private/HWSD2.mdb` was read with Python access-parser. It contains 408,835
layer records and 29,538 mapping units. Metadata confirms cm depths, percent
organic-carbon mass, g/cm³ bulk density and percent coarse-fragment volume.
`data/hwsd/HWSD2_LAYERS.csv.gz` contains selected source columns with original
units; negative missing codes become blanks. `source.json` retains the original
MDB hash and transformation/citation. Builder exports refresh this table if the
MDB changes; original bytes remain unchanged. The geographic raster is a
separate FAO asset, automatically downloaded and cached at runtime when missing.
That large live download was not performed in this environment.
The user's existing downloaded BIL/HDR was subsequently inspected read-only;
the actual map resolves Dallas to unit 7001, and complete profiles load at
nearby mapped cells and rural Texas/Iowa sites (see the Dallas audit below).

The supplied TRY archive produces 66,042 validated species after removing one
exact duplicate from 66,043 identified rows. Ambiguous functional categories
remain unresolved. Numeric rates are preset assumptions, not measured traits.
Original workbook and supplied terms/docs are retained in plant-enabled exports.

## Live public-provider evidence

Live retrieval used the TinyFish public-web connector because the local Python
HTTP client cannot resolve external provider hosts. The connector successfully
retrieved:

- Open-Meteo College Station geocoding and a complete 365-day ERA5 request,
  2025-11-03 through 2026-11-02. Recent days beyond 2026-11-03 had null required
  fields; Jan 1, 1940 was also incomplete. Boundary probes select actual complete
  edge dates; whole runs still reject missing interior records.
- TerraClimate's current `tds-proxy.nkn.uidaho.edu` aggregated catalog and point
  CSV, CF DAS metadata, and NCSS coverage through December 2025. The live point
  CSV had values 646/658/897 labeled mm, while CF metadata declares scale 0.1:
  those packed CSV numbers cannot safely be treated as decoded millimetres.
  Remote retrieval now requests NetCDF and explicitly applies actual metadata.
- ORNL MOD16A2GF JSON and band metadata. JSON has no per-record version field,
  explaining the former bogus “received 000” rejection. The loader recovers
  verifiable collection numbers from CSV granule filenames (per ORNL's documented
  CSV format). The actual College Station Jan 1, 2025 point had ET/PET=32762 and
  QA=157; local parsing correctly rejected it as a fill/quality case, not a valid
  observation. No nearby pixel or fake data is substituted.
- ISRIC's new per-property WCS capability endpoint; exact-point no-data is
  still possible, including urban/water masks. The adapter retries the exact
  point at the alternative ISRIC service before HWSD/preset fallback.

Captured evidence:

- `verification/open_meteo_live_2026-11-09.json`
- `verification/open_meteo_live_year_2026-11-09.json`
- `verification/data_provider_repairs_2026-11-09.json` (connector extractions;
  XML/HTML extraction is not a raw binary response)
- `verification/depth_model_live_forcing_2026-11-09.json`

The final file runs the new coupled solver with the actual captured annual
weather and an explicitly labeled loam preset. Maximum daily residuals were
4.63e-13 mm water, 6.83e-13 g C/m² soil carbon and 7.01e-13 g C/m² ecosystem
carbon. These are numerical roundoff, **not observational validation**.

The TerraClimate binary transfer/decoding, MODIS CSV content negotiation, full
HWSD geographic map retrieval and automatic cross-provider fallback still need
an end-to-end check with a network-enabled Python client. Their parser/loader
logic has fixture tests; live provider availability is not inferred from those.
No suitable complete site observations have yet established predictive accuracy.

## Reproduce

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q studio_builder template_generated_app
.venv/bin/python -m pip check
```

Export a fresh ZIP, install its requirements and run `streamlit run app.py`.
Choose a precise vegetated study site and available dates, then Run. The Model
tab provides grouped results; Data shows source availability,
charts and records; Advanced contains scientific and access options. Run details
retain every source, estimated mapping, initial condition, error and comparison.
Community Cloud deployment is documented but has not been performed here.

## v2 retrieval, formula and interaction audit

The earlier geographic 1×1 WCS request was not recovering valid SoilGrids data;
it was not justified to attribute repeated failures to urban masks. The numeric
point service returned all 36 values at College Station (six properties through
200 cm). Units, returned layer IDs and source-cell coordinates are checked,
including ISRIC's currently reversed X/Y labels in its HTML template. Live sand
queries also succeeded at a nearby Texas point and an Iowa point. Full captured
point responses replay through the actual adapter and source-unit conversions.
A 3×3 WCS center-pixel subset provides recovery without searching nearby cells.

Artifacts: verification/soilgrids_live_profile_audit.json and
verification/soilgrids_college_station_profile.csv. Source initial carbon over
2 m is 18,308.3731 g C/m², conserved under the modeled layer mapping.

FORMULA_AUDIT.md records the scientific review: interlayer water now uses Darcy
head gradients, actual layer spacing and Clapp–Hornberger retention/conductivity;
day length follows FAO-56. Potential NPP and soil reaction kinetics remain
explicit simplified closures, not full published photosynthesis/Millennial V2.
All active numerical defaults are editable and passed through the same engine.

verification/coupled_live_comparison_v2.json combines the actual captured annual
ERA5 weather, the actual captured SoilGrids profile and a complete December 2025
TerraClimate window. The connector's packed CSV value 198.0 was decoded using
independently captured CF scale 0.1 from the same aggregate, giving 19.8 mm. The
model produced 45.91 mm over that month: a real, visible product disagreement,
not calibration or independent observational validation. One comparison window
does not define R². This capture validates the comparison calculation, not the
live Python binary transfer. Maximum daily residuals were 3.72e-13 mm water and
2.81e-12 g C/m² ecosystem carbon.

The builder regression now simulates a missing cached species_index function
and an older bootstrap protocol, then verifies startup and export recover.
SRDB mixed text/numeric fields load without DtypeWarning; no matching studies
retain explicit qualified-study site/year choices instead of claiming a provider
failure. Backups are tested when a primary loads but has no complete window.
Loading a comparison source later updates the retained run and its downloadable
comparison records without recomputing the model. Single-window bars, native
window totals/mean rates and excluded-window counts are tested.

All seven extracted model selections run with relevant populated controls,
selected-only result tabs, expected directed links and daily conservation. The
latest UI regression also verifies source-derived defaults refresh after a run.
The known local DNS/socket/type-checking limits above still apply. Automatic HWSD
map retrieval has archive-shaped fixture coverage but no successful live download
in this sandbox; no universal SRDB coverage or field validation is asserted.

The preceding v2 verification passed 195 repository tests and 133 included
scientific tests independently from an extracted export. Updated results for
the Dallas source fixes are recorded below.

## Dallas report audit and repairs

The supplied source report selects 32.78306, −96.80667 for 2025-11-04 through
2026-11-03. Weather, TRY and TerraClimate loaded; climate is an optional unrequested
block. The following checks distinguish parser faults from actual coverage:

- The user's actual 43,200 × 21,600 HWSD BIL/HDR resolves this point to 7001.
  Its official source SMU has FAO90 UR / WRB TC classifications and no physical
  soil properties. The former generic invalid-profile exception now reports
  urban/no-profile coverage. No reinstall or additional table download fixes it.
  A bounded memmapped search found complete seven-layer soil profiles about
  1.39, 2.30 and 3.11 km away; the loader validated each. Rural Texas
  (30.6, −96.45; unit 4884) and Iowa (41.99, −93.77; unit 4725) also load.
  Map files in Downloads were only read. The new explicit selection action
  preserves dates and does not claim mapped cells are measured field sites.
- The exact Dallas SoilGrids numeric point query returned no content; the user's
  WCS attempt returned missing sand. This does not demonstrate global SoilGrids
  failure. If both adapter routes fail, details now retain both causes; a timeout
  followed by a missing pixel is no longer mislabeled as definite coverage loss.
- The actual public SRDB CSV has 13,619 records with usable coordinates/years.
  Blank Quality_flag means no flags; requiring literal Q0 erroneously discarded
  eligible data. Official expanded field notes define rounded midpoints X.5 as
  calendar X and X.0 as July X−1 through June X. The previous integer/calendar
  assumption also excluded or misdated valid records. The corrected adapter finds
  456 eligible baseline annual Rh records, with catalog years 1961–2021, and
  returns actual historical study choices. Dallas 2025–26 still has no match.
  A nearest record at Kessler's Farm (34.98, −97.52), midpoint 2004.0, now uses
  inferred 2003-07-01 through 2004-06-30. Dates remain approximate; study chronology
  and ecological equivalence need review. A constant synthetic-flux replay checks
  aggregation over that window, not model skill.
- A real December 3, 2025 MODIS Dallas request returns ET/PET 32762 and QA 157:
  urban fill, never ecosystem ET. The same date at rural Texas 30.6, −96.45
  gives ET 67, PET 138 and QA 16: 6.7/13.8 mm per composite after scaling.
  Recommended main-algorithm quality now accepts that gap-filled observation;
  strict clear/best remains optional. Rejected fill values include their codes
  and counts, and accepted data still require verified collection 061.
  The captured JSON samples omit collection metadata and do not independently
  establish collection 061 or test the Python CSV content-negotiation transfer.

Primary conventions: [SRDB expanded field notes](https://github.com/bpbond/srdb/blob/master/expanded_field_notes.txt)
and [NASA MOD16 user guide](https://modis-land.gsfc.nasa.gov/pdf/MOD16UsersGuideV2.022019.pdf).

Artifacts:

- `verification/dallas_modis_live_2026-11-09.json`: actual public JSON samples.
- `verification/dallas_source_resolution_2026-11-09.json`: real map/table results,
  full-catalog SRDB replay with snapshot hash, qualified historical windows, and
  live MODIS parser results. No fabricated environmental values are substituted.

Regressions cover blank flags, both midpoint conventions, overlapping inferred
windows, flagged/multi-year exclusions, urban HWSD profiles, complete nearby
cells, recommended/strict MODIS QA, fill diagnostics and dual-route SoilGrids
failures. An extracted-app AppTest verifies that finding soils leaves coordinates
alone, selecting a cell preserves dates, and selecting an SRDB study uses its
actual inferred annual window. Existing all-seven model selection checks remain.

Final Dallas-fix verification: **217 repository tests passed**, and the fresh
all-model export independently passed **133 included scientific tests**. Python
compilation and pip dependency checks pass. The export includes all eight data
blocks and passes ZIP integrity checks. Its SHA-256 is
`06ca982dcb3bf92dd00b7f07d9730972c4e73d6ac3568653282c10b7243cfcc6`.
The local DNS/socket/type-checking limits above remain; no new universal coverage
or predictive-validation claim follows from these tests.

## Nearby source proxies (user-requested site preservation)

This supersedes the earlier recovery action that moved the simulation location.
**Find nearby available data** now checks the selected source, and selecting one
keeps the original simulation coordinates and dates. The data request uses only
that source's chosen coordinate, with both locations, their distance and periods
recorded. Different blocks can use different explicitly selected proxies.
Source statuses/charts identify proxies; comparisons explicitly say they are not
observations or independent validation at the simulation site. A source choice
can be removed; changing the model site clears stale choices.

SoilGrids seeds checks from a bounded WCS sand overview, with bounded numeric-point
sampling if the overview cannot provide candidates. Every option must pass the
complete six-property, six-depth loader. MODIS seeds candidates from a provider
neighborhood, rejects fill/bad QA, inverts the actual sinusoidal pixel grid using
the [NASA reference sphere](https://modis-land.gsfc.nasa.gov/GCTP.html), and then
requires the full point loader and collection verification for offered options.
HWSD checks complete mapped profiles. Other spatial time series use bounded point
probes and require complete comparison windows where applicable. Searches are
limited to 25 km and finite candidates; they do not promise an exhaustive nearest
pixel. Connection/schema failures stop the search. Missing time coverage can offer
an explicit date action; spatial proximity cannot create unpublished data.

SRDB offers its actual qualified studies and inferred historical annual windows,
which may be more than 25 km away. Selection now loads the chosen record as a
proxy without changing model coordinates or dates. A separate action changes only
run dates to the source window. Nonoverlapping years never produce comparisons.
TRY already offers global species options and has no geographic requirement.

Actual public checks recovered all 36 SoilGrids values at 32.78301454, −96.91363937,
10 km west of the reported Dallas point. All six layers through 200 cm pass the
actual profile/unit/coordinate validation. Artifacts:
`verification/dallas_nearby_soilgrids_live.json` and
`verification/dallas_nearby_soilgrids_profile.csv`. A 41×41 MODIS neighborhood
on Dec 3, 2025 yielded a usable-value pixel 1.83 km away at 32.78541633, −96.82603962.
A second real point request corroborated ET 46 / PET 123 / QA 8, scaling to
4.6 / 12.3 mm for Dec 3–10 (`verification/dallas_nearby_modis_live.json`).
These actual JSON samples do not verify collection 061; the remote loader still
requires CSV collection metadata before offering/using such data. Connector
captures establish sample availability, not unrestricted Python HTTP transfer or
predictive model accuracy.

HWSD maps are reused read-only from this app, known adjacent export paths and
Downloads when a complete official header/byte count is present. Otherwise they
download automatically. No manual copying is required. Sparse official-size
fixtures verify external reuse, local precedence, no mutation, and truncated-map
rejection; archive fixtures still verify downloading. Existing externally reused
files must remain accessible, or a later request needs a download.

Verification: the full suite passed **235 tests**; the subsequently added
selected-SRDB-record regression passed with all **22 source-recovery tests**.
The updated extracted-app interaction also passed after its final site-change
assertion, verifying fixed original-site weather, proxy soil in an actual model
run, two-site run provenance, unchanged dates, optional historical-date selection,
proxy removal and clearing choices on a site change. Compilation and pip checks
pass. Starting the isolated exported Streamlit agent HTTP API again failed with
socket bind PermissionError in this sandbox; AppTest was used for interactions.
The current export has 46 files and SHA-256
`66ef99c2e9a90cdb16b1cd746ffa8a76622b1c47b9ef1478c453696e3b173366`.
Its **133 included scientific tests** also pass independently after extraction.

## Missing-data choices in the Run flow

This supersedes Data as the primary recovery entry point. Run now loads and
checks the requested sources before calling the scientific engine. Missing
sources open the native **Choose data for this run** dialog automatically.
The selected missing spatial source searches for checked nearby candidates
without requiring a visit to Data or a separate search click. Actual qualified
SRDB studies and their inferred windows are offered from catalog diagnostics.
Data's independent search remains available.

Choosing a proxy resumes the checks with that source's coordinates while keeping
the simulation site/dates. Historical-study dates have a separate explicit action.
Other prepared inputs and failures are reused until the source/request changes
or the user retries. A newly unavailable proxy pauses again. Cancellation never
computes the model. Explicit **Run with available data** records accepted missing
sources, uses a soil preset if necessary and omits unavailable comparisons. It is
disabled when usable weather is missing; weather cannot be bypassed with an
acknowledgment. Closing the dialog cancels the pending run.

Six extracted-app interaction regressions verify no simulation before choosing,
automatic nearby options, fixed original coordinates/dates, actual proxy soil
use, reuse of prepared sources, cancellation, explicit preset continuation,
required-weather blocking/retry, rechecking a failed proxy, and explicitly chosen
historical SRDB dates with a labeled matching proxy comparison. Existing app tests
were updated to acknowledge missing inputs rather than assuming silent fallback.

Compilation and dependency checks pass. The extracted export independently passes
**133 scientific tests**, contains all eight blocks, and passes ZIP integrity
checks (48 files, SHA-256
`5a3287294a61013c5e978dca27de56412b686349877f16bab50c6e8faf9656b8`).
The established DNS/socket/type-checking limits remain; this UI change does not
claim additional live provider availability or predictive validation.

Final run-dialog suite: **242 repository tests passed**. An earlier full-suite
attempt timed out in one existing AppTest independent-comparison reload; that
test passed in isolation and the complete rerun passed. No timeout threshold
was increased and no failing assertion was removed.

## Faster loading and one progress panel — 2026-11-17

This supersedes the automatic nearby-search and comparison-before-calculation
behavior described in the historical run-dialog audit above. Required inputs
load first; nearby searches and slow SoilGrids map recovery require explicit
requests. Comparisons load on request after the simulation. A usable HWSD
fallback resolves the soil requirement while retaining the failed primary's
diagnostics. Large first HWSD map downloads are opt-in.

The final repository suite passes **425 tests**. Type checking passes for all
11 edited application/cache/retrieval/engine modules. Extracted-project tests
cover all model combinations, shared loading feedback, persistent profile
validation/expiry/invalidation, optional comparison loading and stale comparison
replacement, source recovery, and water/carbon accounting.

A temporary running app driven through Streamlit's agent API and Chrome used
controlled source fixtures. It displayed exactly one progress bar throughout
weather, soil and calculation, including REST-to-HWSD fallback. Required inputs
produced results before any optional comparison request. Browser errors and
server warnings were absent. Screenshots and observations are in
`verification/fast_loading_soil.png`, `verification/fast_loading_fallback.png`
and `verification/fast_loading_browser.json`. These UI checks do not assert live
provider availability.

A separate live College Station REST request exceeded the 20-second budget;
the foreground caller returned in 20.07 seconds, with no map route started.
`verification/fast_loading_live_rest_budget.json` records that one observation.
In-flight sockets can finish after foreground control returns; outstanding REST
workers are capped at four. Explicit map recovery has a best-effort two-minute
budget, rather than a hard DNS/body deadline.

The CPU-only benchmark in `verification/fast_loading_compute_benchmark.json`
used all three modules, six layers and 24 water substeps/day. A 365-day simulation
improved from 2.125 to 0.372 seconds (median of three); 3,650 days improved from
20.809 to 3.645 seconds (one sample). Every numeric output, column and date
exactly matched the saved pre-change result. Static hydraulic arrays and fixed
layer geometry are now prepared once; equations, numerical settings and
conservation residuals remain unchanged. These timings exclude source retrieval
and UI rendering.

Updated export: `exports/my-ecosystem-model-fast-loading-v6.zip`. The archive
preserves the previous project configuration, includes the new loading module,
passes ZIP integrity checks and excludes test previews and soil cache entries.

## Soil source choice and explicit assumed soil — 2026-11-17

This supersedes v6's REST-first/optional-map policy. Model now exposes Soil data:
SoilGrids (250 m, finer detail; default) or HWSD (~1 km, faster). The selected
source is tried first, then the other mapped source if required. SoilGrids uses
numeric WMS followed by WCS. REST is optional and off by default, with the
existing 20-second foreground deadline when explicitly enabled. Compatible
validated v6 profiles remain reusable from the persistent cache.

Preparation records selected, failed, not-needed and not-attempted soil sources.
Recovery lists the actual failures and offers a separate **Use assumed soil
preset** action only after all configured mapped sources fail and weather is
available. Generic continuation cannot silently choose assumed soil. The default
loam hydraulics and 6,200 g C/m² whole-profile carbon stock are identified as
assumptions. A missing HWSD map offers an explicit **Download HWSD map and retry**
action; it does not start the large download without that action or the Advanced
opt-in. Preferred-source selection opens the corresponding failed source first.

The repository suite passes **437 tests**. Edited application/retrieval/cache/
preparation/recovery modules pass type checking. Running-app agent API checks
verify both source priorities, invalidation after changing the source choice,
default REST exclusion, pausing before assumed-soil calculation, and explicit
HWSD-install recovery with controlled fixtures. Chrome confirms exactly one
visible progress bar through source retrieval and model calculation, and no
browser errors. See `verification/soil_choice_agent_api.json`,
`verification/soil_choice_browser.json`, `verification/soil_choice_setup.png`
and `verification/soil_choice_recovery.png`. The temporary server console showed
no new warnings or exceptions. Browser/source fixtures do not claim live provider
success.

Separate live Austin checks at 30.2672, -97.7431 are captured in
`verification/austin_soil_sources.json`. REST responded in 11.53 seconds but
lacked mean bulk density at 0–5 cm. WMS and WCS surface-sand probes each returned
no usable value in about half a second. The current exported project contained
its HWSD property table but no geographic map; no HWSD mapping unit was therefore
verified for Austin. These are point-in-time checks, not a global outage claim.

The map adapter still needs six properties at six depths. It sends 36 single-layer
requests, with a first-value probe then up to four concurrent requests. Live
six-depth WMS batching at a reference location returned six different numbers
but labeled all HTML records as the first requested depth, on both endpoint
forms. Plain text/GML omitted numeric values and JSON was unsupported. Values
were not assigned to depths by response order; ambiguous multi-record HTML is
rejected. The evidence captures these limitations rather than claiming a safe
six-request implementation.

Updated artifact: `exports/my-ecosystem-model-soil-choice-v7.zip`, preserving the
previous project configuration and excluding test previews and cached data.

## Nearby-search cache layout fix — 2026-11-17

Reproduced the reported `CacheReplayClosureError`: a cold `cached_nearby` search
updated an external progress bar successfully, but a cache hit after inserting
another layout element tried to replay that update against the old block.

`cached_nearby` is now an uncached UI-facing wrapper. `nearby_cache.py` retains
the one-day/16-entry candidate cache and invokes progress in a copy of the
caller's context captured before entering that cache. External progress updates
are therefore not recorded by either the outer candidate cache or nested soil
caches. Cache hits reuse candidate data without replaying progress into the page
or recovery dialog.

Five new regressions cover changed layouts, caller-thread/miss-only progress,
retry after failure, detached candidate results and nested soil-cache progress.
All **85 relevant tests passed** across source caching, nearby recovery, loading
feedback, all generated-app combinations and export checks. Type checking passes
for the new helper and edited application.

A temporary running app completed cold search → independent soil load (adding
three metrics to the layout) → cached search, with one underlying search and no
cache-replay exception or new server warnings. Controlled-fixture evidence is
in `verification/nearby_cache_layout_fix.json`; this does not assert live provider
availability.

The active downloaded project `/Users/eliottelin/Downloads/test-baby` received
only the helper import, wrapper replacement and new helper file. Its original
`app.py` was backed up under `.codex-backups/nearby-cache-fix-20261117T220111Z/`.
The builder template is fixed for future exports. Updated archive:
`exports/my-ecosystem-model-nearby-cache-fix-v8.zip`.

## Deployment-readiness review — 2026-11-17

The final local repository suite passes **466 tests**. Type checks pass for the
builder/export helpers and changed model-app UI, scenario, retrieval and cache
modules. Dependency checks found no broken requirements. Public package metadata
confirms Streamlit1.67.0 meets the app minimum, and the configured GitHub Actions
v7 tags exist. Target Python3.14 matches the locally tested interpreter family.

Recovery now has a single primary action for its current stage, compact statuses,
collapsed provider diagnostics and a separate collapsed last-resort assumptions
section. Mixed REST connection failures do not hide nearby recovery when numeric
map routes establish a local data gap. Genuine outages and missing date coverage
still receive the appropriate retry/date actions.

The scenario audit fixed an implicit-preset path: water/carbon climate scenarios
now reuse a validated loaded mapped profile or require prior explicit assumption
acceptance. Plant-only scenarios retain their validated rooting domain without
claiming mapped soil or preset acceptance. Scenario detail downloads preserve
forcing, soil source and assumption provenance; site/settings changes invalidate
old results.

Chrome and the running-app agent API verified nearby selection, original-site
preservation, downloaded proxy provenance, collapsed diagnostics and assumptions,
one visible progress bar, climate guards and cached searches after layout changes.
The actual running builder generated/downloaded all seven model combinations;
every ZIP passed integrity/configuration checks, included the new helper files
and .gitignore, and excluded the original MDB, map raster and secrets. The clean
builder regression also runs without the private MDB or any Downloads folders.
See recovery_polished*, recovery_proxy_selected.png, builder_deployment_ready.json
and deployment_dependency_check.json in docs/verification.

Automatic REST recovery uses validated data responses, a five-minute transient
service cooldown and automatic later lookup retries; local no-data gaps remain
scoped to the site. The current official documentation and live observations are
summarized separately in docs/SOILGRIDS_REST_STATUS.md, with the schema and live
check evidence retained. Local tests and UI fixtures do not guarantee provider
uptime or establish ecological calibration.

The active test-baby project received the tested app/helper changes; its model
configuration and data were preserved and previous files were backed up under
.codex-backups/deployment-polish-20261117T223827Z. The private builder repository
uses builder_app.py, main and Python3.14, with Linux CI and source/data exclusions
configured. docs/DEPLOYMENT.md documents the Community Cloud authorization step.
