"""Versioned soil-property mappings; assumptions are exported with every run."""
import numpy as np
import pandas as pd

DEFAULT_DEPTHS = [0, 5, 15, 30, 60, 100, 200]
# Calabrese reference app's texture presets: porosity, relative FC/wilt, b, Ksat.
TEXTURES = {
    'sand': (.38, .45, .12, 4.1, 800.),
    'loam': (.43, .60, .20, 5.4, 250.),
    'clay': (.55, .75, .32, 11.4, 20.),
    'silt_loam': (.50, .70, .22, 5.3, 200.),
}


def validate_depths(depths=None):
    """Resolve 1–30 positive soil layers beginning at zero, at most 1000 cm deep."""
    try:
        edges = np.asarray(DEFAULT_DEPTHS if depths is None else depths, dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError('Layer boundaries must be finite numeric depths') from exc
    if (edges.ndim != 1 or not 2 <= len(edges) <= 31 or not np.isfinite(edges).all()
            or edges[0] != 0 or np.any(np.diff(edges) <= 0) or edges[-1] > 1000):
        raise ValueError('Layer boundaries must increase from 0, with 1–30 layers and a maximum depth of 1000 cm')
    return edges.tolist()


def resize_depths(depths, total_depth_cm):
    """Resize the domain while preserving the selected layer count and fractions."""
    edges = validate_depths(depths)
    try:
        total = float(total_depth_cm)
    except (TypeError, ValueError) as exc:
        raise ValueError('Total soil depth must be positive and at most 1000 cm') from exc
    if not np.isfinite(total) or not 0 < total <= 1000:
        raise ValueError('Total soil depth must be positive and at most 1000 cm')
    result = [value * total / edges[-1] for value in edges]
    result[-1] = total
    return validate_depths(result)


def make_profile(depths=None, source=None, texture='loam',initial=None,overrides=None):
    """Overlap-weight source properties, conserving SOC and pore volume.

    No extrapolation beyond a supplied profile is permitted. Hydraulic rates are
    texture-preset estimates, not measured values or a calibrated pedotransfer.
    """
    from parameters import INITIAL_DEFAULTS
    initial={**INITIAL_DEFAULTS,**(initial or {})}
    density,stock,scale=initial['particle_density'],initial['fallback_soc'],initial['soc_depth_scale_cm']
    if not np.isfinite([density,stock,scale]).all() or density<=0 or stock<0 or scale<=0:
        raise ValueError('Particle density and carbon depth scale must be positive; initial SOC must be nonnegative')
    edges = np.asarray(validate_depths(depths))
    if texture not in TEXTURES:
        raise ValueError('Unknown soil texture preset')
    if source is not None:
        from data_blocks.soil import validate_profile
        source = validate_profile(source).sort_values('top_cm')
    rows = []
    for top, bottom in zip(edges[:-1], edges[1:]):
        dz = bottom-top
        if source is None:
            n, fc, wp, b, ks = TEXTURES[texture]
            # Explicit fallback stock assumption, not a claimed observation.
            soc_stock = stock * (np.exp(-top/scale)-np.exp(-bottom/scale))/(1-np.exp(-edges[-1]/scale))
            bulk, coarse = (1-n)*density, 0.
            label = texture
        else:
            overlap = np.maximum(0., np.minimum(bottom, source.bottom_cm)-np.maximum(top, source.top_cm))
            if not np.isclose(overlap.sum(), dz):
                raise ValueError(f'Soil data do not cover the complete {top:g}–{bottom:g} cm layer')
            def mean(key):
                return float(np.dot(overlap, source[key])/dz)
            sand, clay, silt = mean('sand_pct'), mean('clay_pct'), mean('silt_pct')
            label = 'sand' if sand >= 70 else 'clay' if clay >= 40 else 'silt_loam' if silt >= 50 else 'loam'
            _, fc, wp, b, ks = TEXTURES[label]
            coarse = mean('coarse_fragments_pct')
            fine_volume = overlap * (1-source.coarse_fragments_pct/100)
            if fine_volume.sum() <= 0:
                raise ValueError('Each modeled soil layer needs positive fine-earth pore volume')
            # Bulk density describes fine earth. Weight it by that volume so
            # merging layers preserves pore space when coarse content varies.
            bulk = float(np.dot(fine_volume, source.bulk_density_g_cm3)/fine_volume.sum())
            n = 1-bulk/density
            if not .1 <= n <= .8:
                raise ValueError('Bulk-density-derived porosity is outside the supported range (0.1–0.8)')
            soc_stock = float(np.sum(overlap * source.soc_g_kg * source.bulk_density_g_cm3 * 10 * (1-source.coarse_fragments_pct/100)))
        # Water volumes include fine-earth fraction; theta describes fine earth.
        capacity = dz*10*n*(1-coarse/100)
        if capacity <= 0:
            raise ValueError('Each modeled soil layer needs positive fine-earth pore volume')
        rows.append(dict(top_cm=top, bottom_cm=bottom, thickness_mm=dz*10,
                         porosity=n, field_capacity=fc, wilting_point=wp,
                         b=b, ksat_mm_day=ks, air_entry_mm={'sand':100.,'loam':300.,'clay':400.,'silt_loam':300.}[label], capacity_mm=capacity,
                         initial_soc_gC_m2=soc_stock, texture=label,
                         bulk_density_g_cm3=bulk, coarse_fragments_pct=coarse))
    frame = pd.DataFrame(rows)
    if overrides is not None:
        custom=pd.DataFrame(overrides)
        if len(custom)!=len(frame) or not np.allclose(custom[['top_cm','bottom_cm']],frame[['top_cm','bottom_cm']]):
            raise ValueError('Custom soil layer boundaries must match the configured profile')
        for key in ('porosity','field_capacity','wilting_point','ksat_mm_day','b','air_entry_mm'):
            values=pd.to_numeric(custom[key],errors='raise').to_numpy()
            if not np.isfinite(values).all():
                raise ValueError('Custom soil parameters must be finite')
            frame[key]=values
        if (not frame.porosity.between(.1,.8).all() or not frame.field_capacity.between(.01,.99).all()
                or (frame.wilting_point<0).any() or (frame.wilting_point>=frame.field_capacity).any()
                or (frame.ksat_mm_day<0).any() or not frame.b.between(.1,20).all() or (frame.air_entry_mm<=0).any()):
            raise ValueError('Custom soil needs 0 ≤ wilting < field capacity < 1, valid porosity, Ksat ≥ 0, 0.1 ≤ b ≤ 20 and positive air entry')
        frame['capacity_mm']=frame.thickness_mm*frame.porosity*(1-frame.coarse_fragments_pct/100)
    frame.attrs['mapping'] = {
        'version': 'calabrese-depth-v2', 'source': 'supplied soil profile' if source is not None else 'explicit loam/preset fallback',
        'porosity': '1 - bulk_density_g_cm3 / particle_density_g_cm3; assumed mineral particle density',
        'bulk_density_aggregation': 'Overlap-weighted fine-earth volume; conserves total pore-water capacity across layer changes',
        'hydraulics': 'Calabrese reference texture presets; sand>=70%, clay>=40%, silt>=50%, otherwise loam. FC/wilt are relative saturation.',
        'soc_stock': 'SOC(g/kg) × bulk density(g/cm³) × thickness(cm) × 10 × fine-earth volume fraction',
        'soc_fallback': 'Configured whole-profile SOC total redistributed exponentially over the chosen depth; changing depth does not add fallback carbon',
        'manual_hydraulic_overrides': overrides is not None, 'particle_density_g_cm3':density,
        'fallback_soc_gC_m2':stock,'fallback_carbon_depth_scale_cm':scale,
        'layer_depths_cm': edges.tolist(), 'profile_depth_cm': float(edges[-1]),
        'air_entry': 'Explicit estimated texture defaults: sand 100, loam 300, clay 400, silt loam 300 mm; editable and uncalibrated',
        'initial_water': 'field capacity × initial water fraction', 'pool_partition': 'POM .15, dissolved .01, microbes .02, mineral .72, aggregate .10; assumed',
    }
    return frame


def root_weights(profile, depth_cm,beta=3.):
    """Exponential root density integrated over the rooted part of each layer."""
    depth = min(float(depth_cm), float(profile.bottom_cm.iloc[-1]))
    if not np.isfinite([depth,beta]).all() or depth<=0 or beta<=0:
        raise ValueError('Root depth must be finite and positive')
    top = np.minimum(profile.top_cm.to_numpy(), depth)
    bottom = np.minimum(profile.bottom_cm.to_numpy(), depth)
    weights = np.exp(-beta*top/depth)-np.exp(-beta*bottom/depth)
    return weights/weights.sum()
