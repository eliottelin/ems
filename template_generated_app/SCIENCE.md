# Scientific specification: calabrese-depth-v2

This is the first **uncalibrated** coupled depth implementation. It is based on
the supplied Calabrese reference app's saturation/conductivity concepts, grassland
LAI/root limits and five-pool soil structure. It extends fixed layers to arbitrary
boundaries. It is not a literal port or a validated published model. The original
reference sources are unchanged. The former scalar `step` functions remain as
legacy demonstrations for compatibility; the engine calls the depth APIs.

## Default state and units

- C3 grassland; soil boundaries 0, 5, 15, 30, 60, 100, 200 cm.
- Saved soil boundaries are honored. The domain supports 1–30 layers from the
  surface to a positive total depth of at most 1000 cm. Changing total depth or
  boundaries starts a new domain from the configured initial states when Run is
  pressed; it does not continue or extrapolate an earlier simulation.
- One daily weather record advances one day. Water uses 24 internal substeps,
  assuming uniform rain and evaporative demand within that day. These are not
  observed hourly records. Time-step changes require a different model contract.
- Water states and daily fluxes: mm over ground area. Relative saturation: 0–1.
- All carbon stocks: g C/m² ground; carbon fluxes: g C/m²/day.
- Living leaf/stem/root stocks start at 100/100/200 g C/m².
- Water starts at estimated field capacity. Carbon starts from a complete soil
  source profile, or an explicitly recorded fallback 6200 g C/m² stock with a
  50 cm exponential depth distribution. No implicit equilibrium spin-up.
  Fallback SOC is a configured **whole-profile total**. Increasing profile depth
  redistributes that same total; it does not add carbon in proportion to depth.

## Data-to-parameter mapping

`soil_profile.make_profile` records mapping version and every resulting layer in
run provenance. SoilGrids REST is tried first, followed by its numeric map-point
queries and WCS center-pixel recovery, then the supplied HWSD table. Source
intervals are overlap-integrated; gaps and extrapolation beyond a supplied
profile are rejected. A failed source can use a labeled texture preset, never a
fabricated observation. A site without a usable source remains uncalibrated.

| Source property | Result | Assumption / conversion |
|---|---|---|
| SoilGrids sand/silt/clay raw g/kg | % texture | Divide by 10 |
| SoilGrids SOC dg/kg | g C/kg | Divide by 10 |
| SoilGrids bdod cg/cm³ | g/cm³ | Divide by 100 |
| SoilGrids cfvo cm³/dm³ | % coarse volume | Divide by 10 |
| HWSD ORG_CARBON % mass | g C/kg | Multiply by 10 |
| HWSD BULK, COARSE | g/cm³, % volume | Preserve units confirmed in MDB metadata |
| Bulk density | Fine-earth porosity `n` | `1 - bulk_density/2.65`; assumed mineral particle density 2.65 g/cm³; reject n outside 0.1–0.8 |
| Texture fractions | Hydraulic preset | sand≥70%→sand; otherwise clay≥40%→clay; otherwise silt≥50%→silt loam; otherwise loam. This simple classification is not USDA texture classification or a calibrated pedotransfer. |
| SOC, bulk density, coarse fraction | Initial layer carbon | `SOC_g/kg × bulk_g/cm³ × thickness_cm × 10 × (1 - coarse_pct/100)` |
| Depth and fine-earth porosity | Layer water capacity | `thickness_mm × n × (1 - coarse_pct/100)` |
| TRY growth form/pathway/woodiness | Functional category | Conservative categorical classification. Numeric growth, SLA and turnover rates are independent preset assumptions, not measured TRY rates. An optional TRY species search in Advanced selects the matching grass/forest functional preset; unresolved categories retain the user-selected preset and are recorded. |

Texture preset values originate from `references/dr. calabrese model/app.py`:

| Preset | Porosity if no source | FC / saturation | Wilt / saturation | Conductivity exponent b | Ksat mm/day |
|---|---:|---:|---:|---:|---:|
| Sand | .38 | .45 | .12 | 4.1 | 800 |
| Loam | .43 | .60 | .20 | 5.4 | 250 |
| Clay | .55 | .75 | .32 | 11.4 | 20 |
| Silt loam | .50 | .70 | .22 | 5.3 | 200 |

With source bulk density, derived porosity replaces preset porosity. FC and
wilting thresholds remain preset fractions of saturation. All these hydraulic
values are **estimates**, not measured conductivity/retention curves. Regridding
conserves the initial carbon stock, including changing bulk density/coarse
fractions. Bulk density is averaged using overlap thickness multiplied by the
fine-earth volume fraction, while coarse volume is averaged by overlap
thickness. Thus `sum(thickness_mm × (1 - bulk_density/particle_density) ×
fine_earth_fraction)` is unchanged when source layers are merged or subdivided.
This preserves pore-water capacity even when coarse fragments and bulk density
covary. Hydraulic presets are computed from overlap-weighted texture; manual
hydraulic overrides explicitly replace the source-derived capacities.

Run provenance records the resolved boundaries, total soil depth, effective
configuration and initial state totals. Roots are capped at the profile bottom;
all selected layers require complete source coverage, without filling gaps or
extrapolating deeper properties.

## Water solver

For saturation `s = storage/capacity`, unsaturated conductivity is
`K(s) = Ksat × s^(2b+3)`. Canopy interception evaporates on the same day, up to
`min(rain, 0.2×LAI, ET0)`. Remaining ET0 splits between potential transpiration
and bare-soil evaporation using `1 - exp(-0.5×LAI)` canopy fraction.

Each internal substep applies infiltration, soil evaporation, root uptake and
Darcy water exchange. Infiltration is bounded by surface Ksat and pore space;
unaccepted water is runoff. Root density is an integrated exponential profile
with editable depth coefficient. Root uptake is limited by local water above
wilting storage and by `(s-wilt)/(FC-wilt)` stress, bounded to 0–1.

Interlayer flux is `K_face × [1 + (psi_upper-psi_lower)/center_distance]`, with
`psi=-air_entry×s^(-b)` and harmonic interface conductivity. These are the
Clapp–Hornberger retention/conductivity family and Darcy head-gradient law.
Every flux is bounded by donor water and receiver space. The bottom freely
drains at K(s). This finite-volume approximation is not a full implicit Richards
solver; numerical substep sensitivity still matters. Air-entry defaults of
100/300/400/300 mm for sand/loam/clay/silt loam are editable estimates.

Every layer and the entire profile retain a flux ledger:
`Δwater = precipitation - runoff - AET - bottom drainage`.

## Plants

Leaf, stem and root carbon are distinct pools. `LAI = min(LAImax, leafC×SLA)`.
Rooting depth increases asymptotically with root carbon, between preset limits:
`z_root = z_min + (z_max-z_min)×(1-exp(-rootC/200))`, capped by soil depth.
C3 grassland LAImax=3, root range 15–40 cm and base/optimum temperatures 5/20°C
follow the supplied reference. SLA=.02 m²/g C, potential NPP=5 g C/m²/day,
leaf turnover=.008/day, stem turnover=.001/day and root turnover=.003/day are
uncalibrated assumptions (root turnover matches the reference grass preset).
Other selectable presets are scenarios requiring their own calibration.

Daily NPP is potential NPP times canopy interception fraction, water stress,
temperature response, and day-length factor. Temperature increases linearly
from base to optimum, then declines to zero 20°C above optimum. Day length uses FAO-56 equations 24, 25 and 34
with latitude and solar declination and scales as `min(daylength/12,1)`.
This does not invent daily solar radiation. It is a simple phenology proxy.
NPP already includes implicit plant respiration; GPP and autotrophic respiration
are not resolved separately.

Root allocation is `.35 + .20×(1-water_stress)`. Of the remaining production,
45% goes to leaves and 55% to stems. Turnover uses `stock×(1-exp(-rate))`;
leaf turnover rises up to fourfold under cold/short-day conditions. Leaf/stem
litter enters topsoil; root litter follows the same day's root distribution.
`ΔplantC = NPP - litter`. Newly grown leaves affect the next day's water balance.

## Depth-resolved soil carbon

Each layer carries particulate (POM), dissolved (DOC/LMWC), microbial (MBC),
mineral-associated (MAOM) and aggregate (AGG) carbon. Initial total SOC partitions
as .15/.01/.02/.72/.10. Those fractions are assumed, not inferred from measured
total SOC. The pool structure follows the supplied Calabrese and Millennial
references; the following rate laws are a new simplified implementation.

| Transfer | Default day⁻¹ | Destination |
|---|---:|---|
| POM depolymerization | .003 | DOC |
| DOC uptake | .1 | CUE=.45 to microbes, remainder respired |
| Microbial turnover | .01 | Half POM, half DOC |
| DOC sorption | .02 | MAOM |
| MAOM desorption | .00015 | DOC |
| MAOM aggregation | .001 | AGG |
| Aggregate breakdown | .0005 | POM |

Rates scale with `min(10,Q10^((T-20)/10))`, Q10=2, and moisture
`clip(s/.6,0,1) × clip((1-s)/.2,0,1)`. Air temperature drives every layer;
there is no soil thermal solver. Fluxes use exponential donor loss
`stock×(1-exp(-rate×scalar))`. Competing DOC losses are proportionally bounded
by incoming DOC. MAOM pathways share one exponential donor loss, partitioned
by their rates. All internal transfers have both a debit and credit; fresh
litter enters after decay. `ΔsoilC = litter - microbial respiration` in every
layer. Water and roots affect carbon, but carbon does not alter hydraulic
properties in this first version. There is no DOC advection, mineral saturation
capacity, microbial enzyme kinetics or dynamic porosity yet.

Related published framework: Jha et al. (2023),
[doi:10.1029/2023JG007389](https://doi.org/10.1029/2023JG007389);
[deposited reference code](https://doi.org/10.4211/hs.6e4f08d8380a49f99314bae8a7ac41e2).
The provided published equations are references, not claimed as implemented
unchanged. No author endorsement or published calibration is implied.

## Independent selections

Water omitted: plants are unstressed and carbon uses field-capacity moisture.
Plants omitted: LAI=0 (bare soil), no root transpiration or fresh litter.
Carbon omitted: plant litter leaves the modeled system and is still reported.
Soil-only runs therefore respire their initial carbon with no added litter;
they are not steady-state ecosystem simulations.

## Comparison and validation

The app summarizes window counts, bias, RMSE and MAE, retaining every matched
record in run provenance. Only complete simulation months/composites/years are
compared. No extrapolated observations or fake daily downscaling is used.

- TerraClimate v1.1: gridded modeled AET comparison, not independent site truth.
  Remote NCSS NetCDF is decoded from its actual CF scale/offset/fill metadata;
  NCSS CSV can expose packed integers despite mm headers and is not used for
  remote retrieval. Local CSV must already be decoded. Both this product and
  Open-Meteo ERA5 share climate information, limiting independence.
- MOD16A2GF: ORNL point ET/PET with fill and QA rejection and real composite
  duration (the final composite of a year is shorter). The JSON API currently
  omits collection metadata. When JSON omits collection, the loader requests CSV and verifies collection 061 from actual granule filenames. Explicit non-061 collections are rejected; missing JSON metadata is never fabricated as 000. The standalone JSON parser can record an unverified collection, but the remote loader requires verifiable CSV metadata before using such a response. Urban/water/nonvegetated pixels can legitimately have no valid ET.
- SRDB: candidates within the configured geographic radius and years, with
  one-year complete baseline unflagged Rh observations only (blank flags normalize
  to Q0). Official rounded midpoints X.5 imply calendar X; X.0 implies July X−1
  through June X. These are inferred dates; check the original study chronology.
  Multi-year means are not treated as individual annual measurements. Total soil respiration
  includes root respiration and is never equated to heterotrophic respiration.
  Nearby observations still require ecological/site-equivalence review.
  Matching annual dates establishes temporal support only. It does not establish
  that the study's respiration represents the selected model depth. Comparisons
  record the modeled depth and leave depth equivalence unverified until the
  study methods have been reviewed; carbon-sampling depth is not silently
  substituted for respiration depth support.

MOD16A2GF's recommended quality policy requires good MODLAND, functional
detectors and main-algorithm SCF 0/1; it retains inherited input-cloud flags
instead of automatically rejecting gap-filled ET. Advanced also offers strict
clear-sky/SCF 0. Both policies reject fill codes, including urban 32762, and
verify collection 061 for accepted data. See the
[NASA MOD16 user guide](https://modis-land.gsfc.nasa.gov/pdf/MOD16UsersGuideV2.022019.pdf).

An urban HWSD map cell can have no soil properties despite a successful map
download. Data's nearby search checks the selected source before offering a
candidate. An explicit proxy selection keeps the simulation site and dates,
requests only that source at its chosen coordinate, and records both sites and
their distance. Different source proxies may represent different landscapes and
are not independent validation at the simulation site. SoilGrids candidates need
the full six-property profile; MODIS candidates pass the full point loader,
including collection 061. Grid/point sampling within 25 km is bounded, not an
exhaustive search for the nearest usable data. A connection/schema failure stops
the search rather than being mislabeled as absence at every nearby site.

SRDB's actual study choices preserve the simulation site too. They use their
inferred source annual dates, which are kept separate from the run dates; no
comparison is possible unless the run covers the entire source window. A separate
date action lets the user choose that historical run period without moving the
site. Resulting comparisons are explicitly labeled nearby proxies. Missing time
coverage cannot be repaired by changing a spatial coordinate.

No matching observations means **observational accuracy has not been
established**. Conservation tests verify bookkeeping, not scientific validation.
Numerical residuals around 1e-12 are normal floating-point roundoff. The model
omits snow/freeze-thaw, groundwater, lateral flow, nutrients, energy balance,
management, species competition, calibration and uncertainty propagation.
These must be explicit future additions before predictive research use.

## Audit and editable parameters

See FORMULA_AUDIT.md for published laws, reference links, explicit approximations,
and a seven-combination coupling matrix. The plants' potential-NPP response and
soil reaction rates remain simplified closures, not a complete published
photosynthesis/Millennial implementation. Advanced now shows all active preset
values: canopy and production, thermal response, root distribution/allocation,
turnover, water demand and numerical integration, seven soil transfer rates,
temperature/moisture responses, initial living/pool stocks, particle density and
per-layer retention/conductivity overrides. Species selection resets plant
fields to the mapped preset; user edits then pass directly to the common engine.
