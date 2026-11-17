# Plant browser: design and verification

The generated app follows the round-two proposal: **Search plant species**,
**Filter group / ecosystem**, **Sort / group**, and one read-only table. All
matching rows are available. Select a row and press **Use selected plant** to
apply it; browsing and highlighting preserve the applied species and manual rates.

The design uses [Streamlit's native sorting, search and scrolling](https://docs.streamlit.io/develop/concepts/design/dataframes)
and [single-row selections](https://docs.streamlit.io/develop/tutorials/elements/dataframe-row-selections).
The table explicitly uses `lazy=False`: the [dataframe API](https://docs.streamlit.io/develop/api-reference/data/st.dataframe)
documents that lazy tables do not support selections or table search. Carbon's
[search guidance](https://www.carbondesignsystem.com/building-blocks/core/patterns/search)
informed the visible result count and default All categories scope.

Forest combines the recorded deciduous and evergreen tree groups. Crop use and
habitat suitability are absent from the archive; no crop or habitat classes are
inferred. TRY supplies categorical mappings rather than measured numerical
production, SLA or turnover rates. Explicit ecosystem changes clear the species
and load that preset; an explicit plant application loads its supported preset.
Manual changes remain until another deliberate application or reset. Initial
biomass and soil inputs remain independently editable.

The compact index verifies source and derived-file hashes and invalidates its
cache when the workbook, CSV or metadata changes. Derived files rebuild from the
source and persist where writable. Original archives, terms and attribution are
retained. Missing or invalid optional data leave ecosystem presets usable.

Downloaded source reports and run details record `plant-table-v5`, the project
location, and stable fingerprints of loaded function code and settings,
independent of source edits on disk. The source report is available on the
**Data** tab before running the model. `.streamlit/config.toml` enables polling
and automatic reruns. Replacing a project requires restarting Streamlit from the
new folder; a file hash alone is not treated as proof of a loaded update.

## Local checks

- Actual bundled species index: **66,042 records**.
- Initial complete table visible in Chrome: **1.04 s** in the measured session.
- Five warm browser searches averaged **839 ms**, including a 400 ms debounce.
- Fifty warm server searches averaged **12.20 ms**.
- Search-only interactions did not rerun the main app, weather, place search or model.
- Native pointer selection applied Quercus robur, source ID **45470**, with Forest defaults **6/6**.
- Browsing preserved an edited NPP of 11. Crop cleared the species and applied **8/5**.
- Actual complete fixture-based runs and downloaded details recorded effective
  depth **100 cm**, boundaries, settings and loaded revision. Water residual was
  **4.8e-13 mm**; carbon residual was **1.6e-12 g C/m²**.
- The optional `tests/browser_plant_controls.cjs` regression also passed after a
  browser reload. The Python suite covers source updates, unavailable sources,
  rows beyond the old 50-result limit, domain changes and overlap conservation.

These are local UI measurements with the real catalog and fixture-based forcing
and source retrieval for the model-run checks. They are not universal timing,
live-service availability or scientific calibration claims.

Raw measurements: [plant_table_v5_browser.json](verification/plant_table_v5_browser.json).
