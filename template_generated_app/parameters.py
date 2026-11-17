"""Editable, versioned model parameters shared by UI and numerical modules."""
from copy import deepcopy

# Values are explicit uncalibrated presets; references and assumptions in SCIENCE.md.
PLANT_PRESETS = {
    'grass_c3': dict(lai_max=3.,root_min_cm=15.,root_max_cm=40.,t_base=5.,t_opt=20.,sla_m2_gC=.02,max_npp=5.,leaf_turnover=.008,root_turnover=.003),
    'grass_c4': dict(lai_max=4.,root_min_cm=20.,root_max_cm=100.,t_base=10.,t_opt=30.,sla_m2_gC=.018,max_npp=6.,leaf_turnover=.008,root_turnover=.003),
    'forest': dict(lai_max=6.,root_min_cm=30.,root_max_cm=200.,t_base=0.,t_opt=22.,sla_m2_gC=.012,max_npp=6.,leaf_turnover=.002,root_turnover=.0015),
    'crop': dict(lai_max=5.,root_min_cm=15.,root_max_cm=120.,t_base=5.,t_opt=25.,sla_m2_gC=.02,max_npp=8.,leaf_turnover=.01,root_turnover=.004),
}
for preset in PLANT_PRESETS.values():
    preset.update(t_max=preset['t_opt']+20,stem_turnover=.001,light_extinction=.5,
                  root_carbon_scale=200.,root_density_beta=3.,root_allocation=.35,
                  drought_root_bonus=.20,leaf_allocation=.45,senescence_multiplier=3.,daylength_reference=12.)

WATER_DEFAULTS = dict(substeps=24,interception_mm_per_lai=.2,light_extinction=.5,
                      soil_evaporation_coefficient=1.,transpiration_coefficient=1.)
CARBON_DEFAULTS = dict(k_particulate=.003,k_uptake=.1,k_microbial=.01,k_desorption=.00015,
                       k_aggregate=.0005,k_sorption=.02,k_aggregation=.001,q10=2.,cue=.45,
                       reference_temp_c=20.,moisture_optimum=.6,anoxia_range=.2)
INITIAL_DEFAULTS = dict(leaf_carbon=100.,stem_carbon=100.,root_carbon=200.,water_fraction_fc=1.,
    fallback_soc=6200.,soc_depth_scale_cm=50.,particulate_fraction=.15,dissolved_fraction=.01,
    microbial_fraction=.02,mineral_fraction=.72,aggregate_fraction=.10,particle_density=2.65)

# label, minimum, maximum, increment, explanation (units are in labels).
PLANT_FIELDS = {
 'Canopy and production': {
  'max_npp':('Maximum net production (g C/m²/day)',0.,100.,.1,'Potential NPP, already net of implicit plant respiration; limited by canopy, temperature, day length and water.'),
  'lai_max':('Maximum leaf area index (m²/m²)',.01,20.,.1,'Leaf area ceiling.'),
  'sla_m2_gC':('Specific leaf area (m²/g C)',.0001,.2,.001,'Converts leaf carbon to LAI. Categorical TRY data do not measure this number.'),
  'light_extinction':('Canopy extinction coefficient',.01,3.,.05,'Beer–Lambert attenuation, 1 − exp(−k × LAI); shared by plants and water.'),
  't_base':('Base growth temperature (°C)',-30.,40.,1.,'Below this temperature NPP is zero.'),
  't_opt':('Optimum growth temperature (°C)',-10.,55.,1.,'Peak of the triangular temperature response; must exceed base temperature.'),
  't_max':('Maximum growth temperature (°C)',0.,80.,1.,'NPP declines to zero here; must exceed optimum.'),
  'daylength_reference':('Day-length reference (hours)',1.,24.,.5,'Day-length limitation reaches one at this many daylight hours; daylight follows FAO-56 solar geometry.'),
 },
 'Roots and allocation': {
  'root_min_cm':('Minimum root depth (cm)',.1,1000.,1.,'Lower rooting-depth limit; actual depth cannot exceed the soil profile.'),
  'root_max_cm':('Maximum root depth (cm)',.1,1000.,1.,'Upper rooting-depth limit.'),
  'root_carbon_scale':('Root growth carbon scale (g C/m²)',1.,20000.,10.,'Controls how quickly root depth approaches its maximum as root carbon increases.'),
  'root_density_beta':('Root density depth coefficient',.01,20.,.1,'Larger values concentrate roots near the surface; layer fractions integrate exp(−beta × depth/root depth).'),
  'root_allocation':('Baseline allocation to roots',0.,1.,.01,'Fraction of NPP allocated to roots without water stress.'),
  'drought_root_bonus':('Extra root allocation during drought',0.,1.,.01,'Added as bonus × (1 − water availability); baseline + bonus must not exceed one.'),
  'leaf_allocation':('Leaf share of aboveground production',0.,1.,.01,'Remaining aboveground production goes to stems.'),
 },
 'Turnover': {
  'leaf_turnover':('Leaf turnover (day⁻¹)',0.,1.,.001,'First-order leaf loss, increased by seasonal senescence.'),
  'stem_turnover':('Stem turnover (day⁻¹)',0.,1.,.001,'First-order stem litter input.'),
  'root_turnover':('Root turnover (day⁻¹)',0.,1.,.001,'Root litter is distributed by rooted layer fractions.'),
  'senescence_multiplier':('Seasonal senescence increase',0.,20.,.5,'Maximum additional multiple of baseline leaf turnover under cold/short days.'),
 },
}
WATER_FIELDS = {
 'substeps':('Water calculations per day',1,240,1,'Numerical subdivisions of daily forcing, not observed hourly records.'),
 'interception_mm_per_lai':('Canopy rain storage (mm per unit LAI)',0.,5.,.05,'Maximum same-day canopy interception; also limited by rain and ET0.'),
 'light_extinction':('Canopy extinction coefficient',.01,3.,.05,'Used only in water-only mode; a selected plant model supplies this shared parameter.'),
 'soil_evaporation_coefficient':('Soil evaporation multiplier',0.,2.,.05,'Scales the bare-soil part of ET0 before soil moisture limitation.'),
 'transpiration_coefficient':('Transpiration multiplier',0.,2.,.05,'Scales plant canopy demand before root water limitation.'),
}
CARBON_FIELDS = {
 'k_particulate':('Particulate breakdown (day⁻¹)',0.,10.,.0001,'POM → dissolved carbon.'),
 'k_uptake':('Microbial uptake (day⁻¹)',0.,10.,.001,'Dissolved carbon → microbial growth and respiration.'),
 'k_microbial':('Microbial turnover (day⁻¹)',0.,10.,.001,'Microbial carbon → particulate and dissolved carbon.'),
 'k_desorption':('Mineral desorption (day⁻¹)',0.,10.,.00001,'Mineral-associated carbon → dissolved carbon.'),
 'k_aggregate':('Aggregate breakdown (day⁻¹)',0.,10.,.0001,'Aggregate carbon → particulate carbon.'),
 'k_sorption':('Mineral sorption (day⁻¹)',0.,10.,.001,'Dissolved carbon → mineral-associated carbon.'),
 'k_aggregation':('Aggregate formation (day⁻¹)',0.,10.,.0001,'Mineral-associated carbon → aggregate carbon.'),
 'q10':('Temperature sensitivity (Q10)',.1,5.,.1,'Rate multiplier per 10°C warming; total temperature scalar is capped at 10.'),
 'cue':('Microbial carbon-use efficiency',0.,1.,.01,'Fraction of uptake retained in microbial biomass; the rest is respired.'),
 'reference_temp_c':('Reference soil temperature (°C)',-20.,50.,1.,'Temperature at which the Q10 multiplier equals one. Air temperature is currently the soil-temperature proxy.'),
 'moisture_optimum':('Optimal pore saturation',.05,.95,.01,'Dry-side moisture limitation reaches one here.'),
 'anoxia_range':('Saturated-soil inhibition range',.01,.95,.01,'Wet-side scalar is (1 − saturation)/range, bounded to 0–1.'),
}


def default_settings():
    return deepcopy({'water_balance':WATER_DEFAULTS,'vegetation':{'preset':'grass_c3'},
                     'soil_carbon':CARBON_DEFAULTS,'initial':INITIAL_DEFAULTS})


def plant_parameters(preset='grass_c3',overrides=None):
    if preset not in PLANT_PRESETS:
        raise ValueError('Unknown ecosystem preset')
    result={**PLANT_PRESETS[preset],**(overrides or {})}
    import numpy as np
    if not np.isfinite(list(result.values())).all():
        raise ValueError('Plant parameters must be finite')
    if not result['t_base']<result['t_opt']<result['t_max']:
        raise ValueError('Plant temperatures must satisfy base < optimum < maximum')
    if not 0<result['root_min_cm']<=result['root_max_cm'] or min(result[k] for k in ('sla_m2_gC','root_carbon_scale','root_density_beta','daylength_reference'))<=0:
        raise ValueError('Root depths, SLA and response scales must be positive')
    if not 0<=result['root_allocation']<=1 or not 0<=result['drought_root_bonus']<=1-result['root_allocation'] or not 0<=result['leaf_allocation']<=1:
        raise ValueError('Plant allocation fractions must remain within 0–1')
    for k in ('lai_max','max_npp','light_extinction','leaf_turnover','stem_turnover','root_turnover','senescence_multiplier'):
        if result[k]<0:
            raise ValueError(f'{k} must be nonnegative')
    return result
