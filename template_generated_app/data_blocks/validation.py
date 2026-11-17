"""Compare compatible quantities only over complete observation windows."""
from datetime import date
import numpy as np
import pandas as pd
from . import DataBlock, require_columns


def comparison_summary(comparisons):
    """Descriptive agreement, never a claim of independent scientific validation."""
    rows = []
    if comparisons.empty:
        return pd.DataFrame()
    for (block,units), group in comparisons.groupby(['block','units']):
        errors = group.modeled-group.observed
        rows.append({'Source':block,'Units':units,'Windows':len(group),
                     'Bias':float(errors.mean()),'RMSE':float(np.sqrt(np.mean(errors**2))),
                     'MAE':float(errors.abs().mean()),
                     'R²':float(1-np.sum(errors**2)/np.sum((group.observed-group.observed.mean())**2)) if len(group)>1 and group.observed.var()>0 else np.nan})
    return pd.DataFrame(rows)


def compare(simulation: pd.DataFrame, observation: DataBlock) -> pd.DataFrame:
    require_columns(simulation, ["date"])
    sim = simulation.copy()
    sim["date"] = pd.to_datetime(sim.date).dt.normalize()
    if sim.date.duplicated().any():
        raise ValueError("Simulation dates must be unique for validation")
    records = []
    if observation.id == "srdb":
        if "soil_respiration_gC_m2_day" not in sim:
            return pd.DataFrame()
        for _, item in observation.frame.iterrows():
            if not item.comparable_rh:
                continue
            if hasattr(item,'period_start') and pd.notna(item.period_start) and pd.notna(item.period_end):
                start,end=date.fromisoformat(str(item.period_start)),date.fromisoformat(str(item.period_end))
            else:
                from .observations import srdb_annual_window
                window=srdb_annual_window(float(item.Study_midyear),float(getattr(item,'YearsOfData',1)))
                if window is None:
                    continue
                start,end=window
            records.append((str(item.Record_number), start, end, "soil_respiration_gC_m2_day", float(item.Rh_annual), "g C/m2/year"))
    elif observation.id in ("terraclimate", "mod16a2gf"):
        if "aet_mm" not in sim:
            return pd.DataFrame()
        for _, item in observation.frame.iterrows():
            start, end = date.fromisoformat(str(item.date)), date.fromisoformat(str(item.period_end))
            records.append((str(start), start, end, "aet_mm", float(item.aet_mm), "mm/window"))
    results = []
    for record, start, end, column, observed, units in records:
        window = sim[sim.date.between(pd.Timestamp(start), pd.Timestamp(end))].sort_values("date")
        expected = pd.date_range(start, end, freq="D")
        if window.date.dt.date.tolist() != expected.date.tolist():
            continue
        values = pd.to_numeric(window[column], errors="raise")
        if not np.isfinite(values).all() or not np.isfinite(observed):
            continue
        modeled = float(values.sum())
        evidence=('Nearby proxy comparison; not observations or independent validation at the simulation site'
            if observation.provenance.get('spatial_proxy') else
            'Field Rh; annual dates inferred from source midpoint; verify chronology and site equivalence' if observation.id=='srdb' else 'Gridded product comparison; not independent field validation')
        results.append({"block": observation.id, "record": record, "start": str(start), "end": str(end),
                        "modeled": modeled, "observed": observed, "difference": modeled-observed, "units": units,
                        'days':len(expected),'modeled_daily_mean':modeled/len(expected),'observed_daily_mean':observed/len(expected),
                        'evidence':evidence,'source_distance_km':observation.provenance.get('spatial_proxy',{}).get('distance_km',0.)})
    return pd.DataFrame(results)


def comparison_audit(simulation,observation):
    """Explain why records were excluded, rather than presenting an empty chart."""
    matched=compare(simulation,observation)
    candidates=len(observation.frame)
    required='soil_respiration_gC_m2_day' if observation.id=='srdb' else 'aet_mm'
    quality=int((~observation.frame.comparable_rh.astype(bool)).sum()) if observation.id=='srdb' else 0
    return {'Source':observation.id,'Source records':candidates,'Matched windows':len(matched),
            'Excluded by study quality/coverage':quality,
            'Incomplete simulation windows':max(0,candidates-quality-len(matched)) if required in simulation else 0,
            'Required output':required,'Model output present':required in simulation,
            'Meaning':'Complete inferred annual windows required for SRDB Rh; whole months/composites required for ET. No partial windows or extrapolated daily observations.'}
