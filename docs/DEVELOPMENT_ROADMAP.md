# Development roadmap

## Implemented in this first coupled version

- Model-first builder, automatic dataset selection, optional extras, portable
  ZIPs, agent-neutral modification brief and local/Community Cloud setup guides.
- Runtime place suggestions, date-boundary adjustment, daily calculations, run
  progress and download progress; separate Model/Data/Advanced
  tabs; grouped charts and variable explanations.
- Calabrese-inspired water balance over configurable fixed depths (default six
  layers to 2 m): infiltration, runoff, interception, soil evaporation, root
  transpiration, interlayer gravity/capillary exchange and deep drainage.
- Coupled living leaf/stem/root carbon, LAI, rooting depth, temperature/day-length
  phenology, NPP allocation and litter turnover. NPP includes implicit plant
  respiration; GPP/autotrophic respiration are not resolved separately.
- Five carbon pools per soil layer and conservative internal transfers,
  heterotrophic respiration and temperature/moisture limitation.
- Versioned SoilGrids/HWSD property-to-parameter mapping, source SOC integration,
  initial-state provenance, and optional TRY species-to-functional-preset mapping.
  Hydraulic estimates and numeric biological rates remain uncalibrated.
- Direct reading of the supplied official HWSD MDB, a compact 408,835-record
  export with source hash, and automatic FAO geographic map retrieval at runtime.
- Current TerraClimate catalog discovery, available-date bounds and CF NetCDF
  decoding; MODIS collection recovery from CSV granule names, QA/fill checks;
  SRDB study matching; complete-window bias/RMSE/MAE summaries.
- Conservation, source parsing/mapping and UI tests, including extracted apps
  with only selected model files. Legacy scalar APIs remain for comparison.

See template_generated_app/SCIENCE.md for every implemented equation, default,
source mapping and omitted process. This is a first uncalibrated implementation,
not a claim of published-model equivalence or observational validation.

## Next scientific and operational work

1. **End-to-end live source checks from a network-enabled Python environment.**
   Public connector checks confirmed weather, current TerraClimate paths and
   packing metadata, ORNL JSON/bands and ISRIC service availability. NetCDF
   binary retrieval, MODIS CSV content negotiation and the large FAO map download
   have fixture tests but remain unverified through the local DNS-restricted
   HTTP client. A city-center pixel can legitimately lack mapped soil or ET.
2. **Establish a real study site and suitable observation windows.** Obtain or
   identify measured water balance/ET, soil moisture, biomass/LAI, soil carbon
   and partitioned respiration records with matching spatial support and units.
   Do not widen matching criteria merely to produce a comparison. A successful
   product comparison is not independent site validation.
3. **Scientific review and calibration.** Review estimated retention/conductivity,
   preset plant/soil rates, initial pool partitioning and rooting/phenology laws.
   Evaluate spin-up/equilibrium, numerical substep convergence, seasonal trends
   and sensitivity, then calibrate and evaluate on separate observations.
4. **Extend processes with explicit evidence.** Soil temperature, snow/freeze,
   groundwater, mineral capacity, enzyme/microbial kinetics, DOC transport,
   soil-carbon feedback onto porosity, nutrients, management and explicit
   GPP/autotrophic respiration remain future work, each with a conservative
   flux ledger and appropriate observations.
5. **Deployment verification.** Run the generated app in a clean environment
   and on Streamlit Community Cloud; no deployment has been performed here.
   Keep source data terms, required attribution and permitted access intact.

The user confirmed grassland direction (initial default C3) with six layers to 2 m as the initial
configuration; soil boundaries and ecosystem choices are editable in Advanced.
Changing visualization summaries does not change the daily integration contract.

## Deliberately outside the current task

Agent-dependent runtime, LLM API keys, automatic publishing, interactive wiring
canvas, Cesium globe, and a multi-user high-performance simulation backend.

## Formula/UX audit completed

Advanced now displays preset numbers and grouped controls for all active
processes, a searchable TRY dropdown, initial conditions and editable per-layer
hydraulics. The common engine implements Darcy exchange with retention heads
and layer spacing, and FAO-56 day length. FORMULA_AUDIT.md separates published
relationships from simplified NPP/carbon closures and enumerates all seven
selections. These seven exported apps have run/UI coverage. A real captured
SoilGrids profile, captured annual ERA5 forcing and a decoded TerraClimate
December 2025 window demonstrate a functioning gridded-product comparison,
including model/product disagreement. This is not calibration or field validation.
