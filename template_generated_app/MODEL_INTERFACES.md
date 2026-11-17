# Daily scientific module contracts

The engine uses `calabrese-depth-v2`; equations, defaults, parameter mappings,
source attribution and limitations are in **SCIENCE.md**. Each daily call takes
beginning-of-day stocks and returns end-of-day stocks plus integrated daily
fluxes. Inputs must be finite and within their physical domains.

| Module/API | State | Inputs | Output |
|---|---|---|---|
| water_balance.step_profile | Layer water, mm | Daily rain/ET0, mm; profile; root fractions; LAI; active water parameters | Layer water; AET, soil evaporation, transpiration, interception, runoff, drainage, mm/day; stress 0–1; per-layer/profile residuals |
| vegetation.step_pools | Leaf/stem/root C, g C/m² | Temperature, °C; water stress; day of year; latitude; preset | Living pools; net uptake and above/root litter, g C/m²/day; residual |
| soil_carbon.step_layers | Layer × five C pools, g C/m² | Layer litter, g C/m²/day; temperature °C; saturation; rates day⁻¹, Q10, CUE | Layer × five C pools; heterotrophic respiration, g C/m²/day; layer residuals |
| soil_profile.make_profile | Source property table | Layer boundaries, cm; optional soil source; fallback texture | Fixed layer properties, initial SOC, water capacities, versioned mapping metadata |
| engine.simulate | Initial defaults in SCIENCE.md | Selected config; parameters; optional daily forcing/profile; progress(done,total) callback | Daily dataframe, layer states, budget checks, attrs with initial stocks/mappings/assumptions |

Daily order: beginning-of-day canopy/roots → substepped water → plant production
and turnover → depth-distributed litter → carbon transfers. New plants affect
the next day's water balance. Carbon does not feed back onto porosity yet.

```text
Δprofile water = precipitation - runoff - AET - bottom drainage
Δplant carbon = net production - litter
Δsoil carbon = litter - heterotrophic respiration
Δecosystem carbon = net production - heterotrophic respiration
```

The last identity applies when both plant and soil-carbon models are selected.
Flux bounds prevent negative stocks; storage is not clipped to repair budgets.
Source soil intervals must cover every requested model layer without gaps.
Initial carbon is conserved under overlap-based regridding. Root depth changes
only root fractions, never soil volumes. All comparisons retain native temporal
support and require complete windows.

Legacy scalar `step` APIs are preserved for existing callers and demonstration
comparisons. Their original one-bucket/one-biomass/two-carbon-pool tests remain;
the generated engine does not use them. New tests in tests/test_depth_science.py
exercise the actual depth engine, responses and external budgets.

Run `python -m pytest -q`. Only tests for deliberately omitted model files skip;
selected components must import and pass their checks.

FORMULA_AUDIT.md records source equations, Darcy head gradients, FAO day length,
and active/omitted-model assumptions for every selection. Parameters are shared
by the UI and engine through parameters.py, with populated editable defaults.
