"""Five carbon pools per layer and legacy two-pool API; g C/m² and g C/m²/day."""
from math import exp, log
from typing import Mapping, TypedDict
from . import require_finite


class CarbonStep(TypedDict):
    fast: float  # End-of-day stock, g C/m2.
    slow: float  # End-of-day stock, g C/m2.
    respiration: float  # Heterotrophic carbon release, g C/m2/day.
    carbon_residual: float  # Stock change minus litter less respiration, g C/m2.


def step(fast:float,slow:float,litter:float,temp_c:float,moisture:float,params:Mapping[str, float]) -> CarbonStep:
    """Advance one day with stocks in g C/m2 and litter in g C/m2/day.

    Temperature is C; moisture is a dimensionless 0-1 proxy, currently the
    water module's end-of-day stress. Decay uses incoming pools; today's litter
    is added afterward. All inputs must be finite.
    """
    k_fast=float(params.get("k_fast_day",0.003))
    k_slow=float(params.get("k_slow_day",0.00015))
    q10=float(params.get("q10",2.0))
    fast_frac=float(params.get("fast_input_fraction",0.7))
    transfer_frac=float(params.get("fast_to_slow_fraction",0.2))
    require_finite(fast=fast, slow=slow, litter=litter, temp_c=temp_c,
                   moisture=moisture, k_fast_day=k_fast, k_slow_day=k_slow,
                   q10=q10, fast_input_fraction=fast_frac,
                   fast_to_slow_fraction=transfer_frac)
    if min(fast,slow,litter,k_fast,k_slow)<0 or q10<=0 or not 0<=moisture<=1 or not 0<=fast_frac<=1 or not 0<=transfer_frac<=1:
        raise ValueError("Invalid soil carbon state/parameter")
    # Apply the existing cap before exponentiation to avoid overflow.
    temp_factor=exp(min(log(10.0), log(q10)*((temp_c-20.0)/10.0)))
    # moisture response is purely schematic, not calibrated.
    scalar=temp_factor*(0.2+0.8*moisture)
    fast_loss=min(fast,k_fast*scalar*fast)
    slow_loss=min(slow,k_slow*scalar*slow)
    transfer=transfer_frac*fast_loss
    next_fast=fast+fast_frac*litter-fast_loss
    next_slow=slow+(1-fast_frac)*litter+transfer-slow_loss
    respiration=fast_loss-transfer+slow_loss
    residual=(next_fast+next_slow-fast-slow)-(litter-respiration)
    return {"fast":next_fast,"slow":next_slow,"respiration":respiration,"carbon_residual":residual}
POOL_NAMES = ('particulate', 'dissolved', 'microbial', 'mineral', 'aggregate')


def step_layers(state, litter, temperature, saturation, params):
    """Five pools per fixed soil layer, with an explicit conservative flux ledger.

    Millennial-style pool structure; these simplified rate laws are a new,
    uncalibrated implementation, not the published Millennial equations.
    """
    import numpy as np
    s = np.asarray(state, dtype=float).copy()
    litter = np.asarray(litter, dtype=float)
    saturation = np.asarray(saturation, dtype=float)
    temp = np.broadcast_to(np.asarray(temperature, dtype=float), (len(s),))
    if (s.ndim != 2 or s.shape[1] != 5 or litter.shape != (len(s),)
            or saturation.shape != (len(s),) or not all(np.isfinite(x).all() for x in (s,litter,saturation,temp))
            or np.any(s < 0) or np.any(litter < 0) or np.any(saturation < 0) or np.any(saturation > 1)):
        raise ValueError('Invalid depth-resolved soil carbon input')
    rates = np.array([params.get(k, default) for k, default in (
        ('k_particulate', .003), ('k_uptake', .1), ('k_microbial', .01),
        ('k_desorption', .00015), ('k_aggregate', .0005), ('k_sorption', .02), ('k_aggregation', .001))])
    q10, cue = params.get('q10', 2.), params.get('cue', .45)
    if not np.isfinite([*rates,q10,cue]).all() or np.any(rates < 0) or q10 <= 0 or not 0 <= cue <= 1:
        raise ValueError('Invalid soil carbon rate, Q10 or carbon-use efficiency')
    before = s.sum(axis=1)
    scalar = np.exp(np.clip(np.log(q10)*(temp-params.get('reference_temp_c',20.))/10, -700, np.log(10.)))
    # Suppressed when dry or saturated; continuous, bounded moisture response.
    optimum,anoxia=params.get('moisture_optimum',.6),params.get('anoxia_range',.2)
    if not np.isfinite([optimum,anoxia,params.get('reference_temp_c',20.)]).all() or not 0<optimum<1 or not 0<anoxia<1:
        raise ValueError('Soil carbon moisture scales must be finite and between 0 and 1')
    scalar *= np.clip(saturation/optimum,0,1)*np.clip((1-saturation)/anoxia,0,1)
    delta = np.zeros_like(s)
    def transfer(donor, receiver, rate):
        flux = s[:, donor]*(1-np.exp(-rate*scalar))
        delta[:, donor] -= flux; delta[:, receiver] += flux
        return flux
    transfer(0, 1, rates[0])
    uptake = s[:, 1]*(1-np.exp(-rates[1]*scalar))
    # Dissolved uptake and sorption compete for the same incoming donor.
    sorption = s[:, 1]*(1-np.exp(-rates[5]*scalar))
    combined = uptake+sorption
    factor = np.minimum(1., np.divide(s[:,1], combined, out=np.ones_like(combined), where=combined>0))
    uptake *= factor; sorption *= factor
    delta[:, 1] -= uptake+sorption
    delta[:, 2] += cue*uptake; delta[:, 3] += sorption
    respiration = (1-cue)*uptake
    mortality = transfer(2, 0, rates[2])
    # Microbial mortality partitions to particulate and dissolved pools.
    delta[:, 0] -= .5*mortality; delta[:, 1] += .5*mortality
    # Mineral desorption and aggregation also compete for a shared donor.
    mineral_loss = s[:,3]*(1-np.exp(-(rates[3]+rates[6])*scalar))
    frac = rates[3]/(rates[3]+rates[6]) if rates[3]+rates[6] else 0.
    delta[:,3] -= mineral_loss; delta[:,1] += mineral_loss*frac; delta[:,4] += mineral_loss*(1-frac)
    transfer(4, 0, rates[4])
    s += delta; s[:,0] += litter
    return dict(state=s, respiration=respiration,
                residual=s.sum(axis=1)-before-litter+respiration)

