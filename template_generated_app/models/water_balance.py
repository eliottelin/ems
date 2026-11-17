"""Conservative depth solver plus legacy bucket API; water in mm, dt=1 day."""
from typing import Mapping, TypedDict
from dataclasses import dataclass
import numpy as np
from numpy.typing import NDArray
from . import require_finite


@dataclass(frozen=True)
class HydraulicProfile:
    """Detached, read-only hydraulic inputs prepared once for a fixed domain."""

    capacity: NDArray[np.float64]
    field_capacity: NDArray[np.float64]
    wilting_point: NDArray[np.float64]
    ksat: NDArray[np.float64]
    b: NDArray[np.float64]
    air_entry: NDArray[np.float64]
    conductivity_exponent: NDArray[np.float64]
    spacing: NDArray[np.float64]


def prepare_profile(profile):
    """Snapshot static layer properties; supplied dataframes remain editable.

    The coupled engine prepares this once per simulation. Direct one-day and
    Darcy callers may still pass a dataframe, as in the original public API.
    """
    if isinstance(profile, HydraulicProfile):
        return profile

    def immutable(values):
        array = np.array(values, dtype=float, copy=True)
        array.flags.writeable = False
        return array

    b = immutable(profile.b)
    return HydraulicProfile(
        capacity=immutable(profile.capacity_mm),
        field_capacity=immutable(profile.field_capacity),
        wilting_point=immutable(profile.wilting_point),
        ksat=immutable(profile.ksat_mm_day),
        b=b,
        air_entry=immutable(profile.air_entry_mm if 'air_entry_mm' in profile else np.full(len(profile), 300.)),
        conductivity_exponent=immutable(2*b+3),
        spacing=immutable(np.diff(((profile.top_cm+profile.bottom_cm)*5).to_numpy(float))),
    )


class WaterStep(TypedDict):
    storage: float  # End-of-day water depth, mm.
    runoff: float  # Daily flux, mm/day.
    aet: float  # Daily flux, mm/day.
    drainage: float  # Daily flux, mm/day.
    water_stress: float  # Dimensionless end-of-day storage / field capacity.
    water_residual: float  # Change in storage minus net daily inputs, mm.


def step(storage:float,precip_mm:float,et0_mm:float,params:Mapping[str, float]) -> WaterStep:
    """Advance one day: infiltration/overflow, ET, then threshold drainage.

    Incoming storage is mm; precipitation and reference ET0 are mm/day.
    All inputs must be finite. Missing parameters use demonstration defaults.
    """
    capacity=float(params.get("capacity_mm",200.0))
    field=float(params.get("field_capacity_mm",150.0))
    infcap=float(params.get("infiltration_capacity_mm_day",60.0))
    kc=float(params.get("crop_coefficient",0.8))
    require_finite(storage=storage, precip_mm=precip_mm, et0_mm=et0_mm,
                   capacity_mm=capacity, field_capacity_mm=field,
                   infiltration_capacity_mm_day=infcap, crop_coefficient=kc)
    if capacity<=0 or not 0 <= field <= capacity or infcap<0 or kc<0:
        raise ValueError("Invalid bucket parameters")
    if not 0<=storage<=capacity or precip_mm<0 or et0_mm<0:
        raise ValueError("Invalid water balance state/forcing")
    infiltration=min(precip_mm,infcap)
    runoff=precip_mm-infiltration
    total=storage+infiltration
    overflow=max(0.0,total-capacity)
    runoff+=overflow
    total-=overflow
    aet=min(total,kc*et0_mm)
    total-=aet
    drainage=max(0.0,total-field)
    next_storage=total-drainage
    water_stress=min(1.0, next_storage / max(field,1e-6))
    residual=(next_storage-storage)-(precip_mm-runoff-aet-drainage)
    return {"storage":next_storage,"runoff":runoff,"aet":aet,"drainage":drainage,"water_stress":water_stress,"water_residual":residual}
def darcy_flux(saturation,profile):
    """Downward interface flux, mm/day: q=K_face[1+(psi_i-psi_j)/dz]."""
    sat=np.asarray(saturation,dtype=float)
    if isinstance(profile, HydraulicProfile):
        ks, b, entry = profile.ksat, profile.b, profile.air_entry
        exponent, dz = profile.conductivity_exponent, profile.spacing
    else:
        # Preserve the direct Darcy API, including minimal hydraulic dataframes.
        ks=profile.ksat_mm_day.to_numpy(float); b=profile.b.to_numpy(float)
        entry=profile.air_entry_mm.to_numpy(float) if 'air_entry_mm' in profile else np.full(len(profile),300.)
        exponent=2*b+3
        dz=np.diff(((profile.top_cm+profile.bottom_cm)*5).to_numpy(float))
    conductivity=ks*np.clip(sat,0,1)**exponent
    suction=-entry*np.maximum(sat,1e-6)**(-b)
    face=np.divide(2*conductivity[:-1]*conductivity[1:],conductivity[:-1]+conductivity[1:],
                   out=np.zeros(len(sat)-1),where=(conductivity[:-1]+conductivity[1:])>0)
    return face*(1+(suction[:-1]-suction[1:])/dz),conductivity[-1]


def step_profile(storage, rain, et0, profile, roots, lai=0., substeps=24,settings=None):
    """Conservative multilayer daily water balance (mm over ground area).

    Clapp–Hornberger conductivity/retention and Darcy head-gradient
    exchange with layer spacing and gravity. Uniform daily rain/ET are numerical assumptions, not hourly data.
    Every flux is donor/receiver bounded before changing storage.
    """
    from parameters import WATER_DEFAULTS
    settings={**WATER_DEFAULTS,**(settings or {})}
    numeric=[settings[k] for k in WATER_DEFAULTS]
    if not np.isfinite(numeric).all() or min(numeric)<0:
        raise ValueError('Water parameters must be finite and nonnegative')
    s = np.asarray(storage, dtype=float).copy()
    hydraulic = prepare_profile(profile)
    cap = hydraulic.capacity
    fc = hydraulic.field_capacity
    wp = hydraulic.wilting_point
    ks = hydraulic.ksat
    weights = np.asarray(roots, dtype=float)
    if (s.shape != cap.shape or weights.shape != s.shape or not np.isfinite(s).all()
            or np.any(s < 0) or np.any(s > cap+1e-9) or not np.isfinite(weights).all()
            or np.any(weights < 0) or not np.isclose(weights.sum(), 1)):
        raise ValueError('Invalid layer water state or root fractions')
    if not all(np.isfinite([rain, et0, lai])) or min(rain, et0, lai) < 0:
        raise ValueError('Rain, ET0 and LAI must be finite and nonnegative')
    if isinstance(substeps, bool) or int(substeps) != substeps or not 1 <= substeps <= 240:
        raise ValueError('Water solver substeps must be an integer from 1 to 240')
    before = s.copy()
    inflow = np.zeros(len(s)); outflow = np.zeros(len(s)); evap_layer = np.zeros(len(s))
    drainage_layer = np.zeros(len(s))
    uptake_layer = np.zeros(len(s))
    interception = min(rain, settings['interception_mm_per_lai']*lai, et0)
    throughfall = rain-interception
    canopy_fraction = 1-np.exp(-settings['light_extinction']*lai)
    potential_transp = (et0-interception)*canopy_fraction*settings['transpiration_coefficient']
    potential_evap = (et0-interception)*(1-canopy_fraction)*settings['soil_evaporation_coefficient']
    runoff = drainage = evap = transp = 0.
    dt = 1/int(substeps)
    for _ in range(int(substeps)):
        accepted = min(throughfall*dt, ks[0]*dt, max(0., cap[0]-s[0]))
        s[0] += accepted; inflow[0] += accepted
        runoff += throughfall*dt-accepted
        soil_evap = min(s[0], potential_evap*dt*min(1., s[0]/max(cap[0]*fc[0], 1e-12)))
        s[0] -= soil_evap; evap += soil_evap; evap_layer[0] += soil_evap
        available = np.maximum(0., s-cap*wp)
        stress = np.clip((s/cap-wp)/(fc-wp), 0., 1.)
        uptake = np.minimum(available, potential_transp*dt*weights*stress)
        s -= uptake; transp += float(uptake.sum()); evap_layer += uptake; uptake_layer += uptake
        face,bottom=darcy_flux(s/cap,hydraulic)
        for i,q in enumerate(face):
            donor,receiver=(i,i+1) if q>=0 else (i+1,i)
            flux=min(abs(q)*dt,s[donor],max(0.,cap[receiver]-s[receiver]))
            s[donor] -= flux; s[receiver] += flux
            outflow[donor] += flux; inflow[receiver] += flux
            if q>=0:
                drainage_layer[i]+=flux
        loss=min(s[-1],bottom*dt)
        s[-1]-=loss; outflow[-1]+=loss; drainage_layer[-1]+=loss; drainage+=loss
    aet = evap+transp+interception
    residual = float(s.sum()-before.sum()-(rain-runoff-drainage-aet))
    return dict(storage=s, aet=aet, evaporation=evap, transpiration=transp,
                interception=interception, runoff=runoff, drainage=drainage,
                drainage_layers=drainage_layer,
                uptake_layers=uptake_layer,
                water_stress=float(np.clip(transp/potential_transp if potential_transp > 1e-12 else np.dot(weights, np.clip((s/cap-wp)/(fc-wp),0,1)), 0, 1)),
                residual=residual, layer_residual=s-before-inflow+outflow+evap_layer)

