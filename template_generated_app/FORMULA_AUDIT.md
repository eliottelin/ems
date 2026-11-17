# Formula and coupling audit — calabrese-depth-v2

This audit distinguishes published relationships from explicit model closures.
The implementation is uncalibrated. Passing these checks establishes consistent
units and bookkeeping, not predictive accuracy or equivalence to a published
whole-ecosystem model.

| Process | Implemented relationship | Basis and qualification |
|---|---|---|
| Unsaturated conductivity | `K = Ksat × s^(2b+3)` | Clapp–Hornberger/Campbell power-law family; s is pore saturation, K in mm/day. Texture presets are estimates, not fitted retention curves. |
| Matric potential | `psi = -air_entry × s^(-b)` | Same retention family; psi and air-entry suction in mm. A small dry limit avoids a singular numerical head. Default air-entry values are explicit, editable estimates. |
| Interlayer water | `q_down = K_face × [1 + (psi_upper - psi_lower)/distance]` | Darcy law with gravity and center-to-center layer spacing. Harmonic interface conductivity; donor/receiver bounded finite-volume transfer. This replaces v1's saturation-difference heuristic, which omitted matric head and layer spacing. |
| Bottom drainage | `q_bottom = K(s_bottom)` | Free-drainage boundary with unit gravity gradient; donor bounded. Field capacity limits plant stress and initialization, not an artificial no-drainage threshold. |
| Canopy interception | `min(rain, capacity_per_LAI × LAI, ET0)` | Same-day canopy-storage closure adapted from the supplied reference, not a full canopy energy/storage model. |
| Light/canopy fraction | `f = 1 - exp(-k × LAI)` | Beer–Lambert canopy attenuation. The same editable k is used by plants and water. Partitioning ET0 using f is an approximation, not a calibrated FAO dual-crop-coefficient ET model. |
| Day length | `delta=.409 sin(2 pi J/365 -1.39)`; `N=24/pi × acos(-tan(latitude)tan(delta))` | FAO-56 solar geometry, equations 24, 25 and 34; polar argument bounded to [-1,1]. Replaces the earlier phase approximation. |
| Root fractions | Integrated `exp(-beta × z/root_depth)` over each rooted layer, normalized | Explicit exponential root-profile assumption; beta is editable. No soil layer or water is created when rooting depth changes. |
| LAI | `min(LAImax, leaf_carbon × SLA)` | Dimensional leaf-area/carbon conversion. SLA uses m² per g **carbon**, not per g dry biomass; TRY's categorical archive does not supply numerical SLA. |
| Plant NPP | `max_NPP × thermal × day_length_factor × water_stress × canopy_fraction` | Explicit potential-production closure adapted from the reference. **Not** a radiation-driven photosynthesis or published Monteith light-use-efficiency implementation: PAR/radiation, nutrients and autotrophic respiration are not resolved. Net production already includes implicit plant respiration. |
| Allocation and litter | NPP fractions sum to 1; turnover `C × (1-exp(-k))` | Conservative allocation and first-order stock turnover. Drought allocation and seasonal multipliers are editable assumptions. Aboveground litter enters topsoil; root litter follows root fractions. |
| Soil carbon | Five pools and a conservative transfer ledger; first-order exponential donor losses | Millennial-inspired pool organization, **not Millennial V2 rate laws**. Published V2 uses additional microbial/kinetic and sorption-capacity terms not implemented here. Current rates and initial pool partition are explicit estimates. |
| Temperature response | `Q10^((T-Tref)/10)`, capped at 10 | Conventional Q10 rate scaling. Air temperature is a proxy for soil temperature in every layer. |
| Moisture response | `clip(s/optimum) × clip((1-s)/anoxia_range)` | Explicit bounded dry/anoxic response approximation, not a fitted site response. Uses actual layer saturation when water is selected. |
| Source SOC stock | `SOC_g/kg × bulk_g/cm³ × thickness_cm × 10 × fine_earth_fraction` | Unit conversion to g C/m². Exact source-interval overlaps preserve carbon stock during regridding. |

## Primary references

- Clapp & Hornberger (1978), *Empirical equations for some soil hydraulic
  properties*, [doi:10.1029/WR014i004p00601](https://doi.org/10.1029/WR014i004p00601).
- Allen et al., FAO Irrigation and Drainage Paper 56,
  [meteorological and solar geometry equations](https://www.fao.org/4/x0490e/x0490e07.htm).
- Abramoff et al. (2022), *Improved global-scale predictions of soil carbon
  stocks with Millennial Version 2*,
  [doi:10.1016/j.soilbio.2021.108466](https://doi.org/10.1016/j.soilbio.2021.108466),
  [author-hosted open-access paper](https://www.kgeorgiou.com/uploads/1/0/5/4/105430785/abramoff_et_al-2022-sbb.pdf).
- Supplied Calabrese reference app and the attributed Jha et al. (2023)
  [published reference](https://doi.org/10.1029/2023JG007389). These are design
  references; no claim of unchanged equations, calibration or author endorsement.
- [ISRIC access documentation](https://docs.isric.org/globaldata/soilgrids/SoilGrids_faqs_02.html)
  for soil-property units and spatial support.

## Every model selection uses the same engine

| Selection | Active links | Explicit boundary assumptions |
|---|---|---|
| Water | Weather → water | Bare soil; LAI=0, no transpiration |
| Plants | Weather → NPP/phenology | No simulated water stress; rooting bounded by configured soil depth; litter exported |
| Soil carbon | Weather → temperature response | Moisture fixed at field capacity, no fresh plant litter |
| Water + plants | Plants → LAI/roots/shared k → water; water → stress → plants | Litter exported because carbon module is omitted |
| Water + soil carbon | Layer water saturation → carbon rates | Bare soil and no fresh litter |
| Plants + soil carbon | Plant litter → surface/rooted soil layers | Unstressed plants and field-capacity moisture |
| All three | Both canopy/root/stress directions plus litter and depth moisture → carbon | Soil carbon does not yet change hydraulic properties or porosity |

The order is beginning-of-day canopy/roots → water → plant production/turnover
→ litter → soil carbon. Newly grown roots/leaves affect the next day's water.
Carbon feedback on porosity, a soil heat solver, DOC advection, snow, nutrients,
management, explicit GPP/autotrophic respiration and calibration remain future
processes, not hidden active features.

## Comparison rules

Comparisons use actual matching dates and full native windows: months for
TerraClimate, composites for MODIS, complete qualified inferred annual windows
for SRDB Rh (calendar years for midpoint X.5; July–June for X.0).
Window mean rates divide by actual day counts without fabricating daily records.
Bias, RMSE and MAE describe agreement; R² is undefined for a single window or
constant observations. Gridded product agreement is not independent field
validation. Initializing SOC from a soil product and then comparing the same
initial value to itself is not reported as validation.

SRDB has specific study sites and years, not continuous global observations.
A no-match result is distinct from a failed CSV download. Data can offer real
qualified studies as explicit nearby proxies, with source coordinates and inferred
dates separate from the fixed simulation site/run dates. Proxy comparisons are
not field validation at that site. Backups are tried if the
primary comparison source fails **or has no complete comparison window**.
Independent source loads update the existing run's comparison without rerunning
or claiming that newly loaded soil was used by that earlier simulation.
