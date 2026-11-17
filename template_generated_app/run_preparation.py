"""Load and check run inputs before computing or choosing nearby proxies."""
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import date
import pandas as pd
from data_status import source_issue
from data_blocks import NoMatchingData


def has_complete_windows(block,start,end):
    frame=block.frame
    if block.id=='srdb':
        from data_blocks.observations import srdb_annual_window
        windows=[]
        for row in frame.itertuples():
            if not getattr(row,'comparable_rh',False):
                continue
            if getattr(row,'period_start',None) and getattr(row,'period_end',None):
                windows.append((date.fromisoformat(str(row.period_start)),date.fromisoformat(str(row.period_end))))
            else:
                window=srdb_annual_window(row.Study_midyear,getattr(row,'YearsOfData',1))
                if window:
                    windows.append(window)
    else:
        windows=[(date.fromisoformat(str(row.date)[:10]),date.fromisoformat(str(row.period_end)[:10])) for row in frame.itertuples()]
    return any(start<=first<=last<=end for first,last in windows)


def prepare_sources(blocks,models,start,end,fetch,check_soil,progress,*,defer_comparisons=False,
                    soil_preference='soilgrids'):
    """No model calculations occur here. Errors are retained for run choices."""
    if soil_preference not in ('soilgrids','hwsd'):
        raise ValueError('Choose SoilGrids or HWSD as the preferred soil source')
    loaded,errors,issues={},{},{}
    soil,soil_source=None,'explicit texture preset fallback'
    soil_order=('hwsd','soilgrids_wcs') if soil_preference=='hwsd' else ('soilgrids_wcs','hwsd')
    soil_attempts=[{'source_id':key,'status':'not_attempted','message':'Waiting for required daily weather'}
                   for key in soil_order if key in blocks]

    def failed(key,exc):
        errors[key]=str(exc);issues[key]=source_issue(exc)

    try:
        loaded['open_meteo']=fetch('open_meteo')
    except Exception as exc:
        failed('open_meteo',exc)
        return {'loaded':loaded,'errors':errors,'issues':issues,'missing':issues.copy(),
                'soil':soil,'soil_source':soil_source,'soil_attempts':soil_attempts}
    # Comparisons and traits do not depend on soil. Start them now while soil
    # loads on this thread, where its progress callbacks can update the UI.
    ancillary=[key for key,b in blocks.items()
        if key not in ('open_meteo','soilgrids_wcs','hwsd','open_meteo_climate') and b['role']!='fallback'
        and not (defer_comparisons and b['role']=='validation')]
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures={executor.submit(fetch,key):key for key in ancillary}
        progress(.2,'Step 2 of 3 · Loading soil profile')
        for attempt in soil_attempts:
            key=attempt['source_id']
            if soil is not None:
                attempt.update(status='not_needed',message='A complete usable soil profile is already loaded')
                continue
            label=blocks[key].get('label','HWSD' if key=='hwsd' else 'SoilGrids')
            progress(.2,'Step 2 of 3 · Loading '+label)
            try:
                block=fetch(key)
                check_soil(block.frame)
                loaded[key]=block;soil=block.frame;soil_source=key
                attempt.update(status='selected',message='Complete soil profile passed model-depth and property checks')
            except Exception as exc:
                failed(key,exc)
                attempt.update(status='failed',message=str(exc),issue=issues[key])
        for done,future in enumerate(as_completed(futures),1):
            key=futures[future]
            progress(.58+.06*done/max(1,len(ancillary)),'Step 2 of 3 · Checking plant inputs')
            try:
                loaded[key]=future.result()
            except Exception as exc:
                failed(key,exc)
    terra_complete='terraclimate' in loaded and has_complete_windows(loaded['terraclimate'],start,end)
    if not defer_comparisons and 'water_balance' in models and not terra_complete and 'mod16a2gf' in blocks:
        progress(.72,'Checking satellite ET backup…')
        try:
            loaded['mod16a2gf']=fetch('mod16a2gf')
        except Exception as exc:
            failed('mod16a2gf',exc)
    missing=issues.copy()
    # A complete alternative soil profile resolves the required model input.
    # Retain diagnostics without asking to recover a source we no longer need.
    if soil is not None:
        missing.pop('soilgrids_wcs',None)
        missing.pop('hwsd',None)
    for key in ('terraclimate','mod16a2gf','srdb'):
        if key in loaded and not has_complete_windows(loaded[key],start,end):
            details={'reason':'no_complete_time_window',**loaded[key].provenance}
            frame=loaded[key].frame
            if key!='srdb' and not frame.empty:
                details.update(available_first=str(frame.date.min()),available_last=str(frame.period_end.max()),bounds_are_periods=True)
            missing[key]=source_issue(NoMatchingData('No complete qualifying comparison window fits the selected run dates.',details))
    return {'loaded':loaded,'errors':errors,'issues':issues,'missing':missing,
            'soil':soil,'soil_source':soil_source,'soil_attempts':soil_attempts}


def prepare_comparisons(blocks,models,start,end,fetch,progress):
    """Optional downloads requested after model results are already available."""
    keys=[key for key,block in blocks.items() if block['role']=='validation' and key!='mod16a2gf']
    loaded,errors,issues={},{},{}
    # The progress callback runs on the caller, never inside worker threads.
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures={executor.submit(fetch,key):key for key in keys}
        for index,future in enumerate(as_completed(futures),1):
            key=futures[future]
            try:
                loaded[key]=future.result()
            except Exception as exc:
                errors[key]=str(exc);issues[key]=source_issue(exc)
            progress(.8*index/max(1,len(keys)),f'Checked {blocks[key]["label"]}')
    complete='terraclimate' in loaded and has_complete_windows(loaded['terraclimate'],start,end)
    if 'water_balance' in models and not complete and 'mod16a2gf' in blocks:
        progress(.85,'Checking satellite ET backup')
        try:
            loaded['mod16a2gf']=fetch('mod16a2gf')
        except Exception as exc:
            errors['mod16a2gf']=str(exc);issues['mod16a2gf']=source_issue(exc)
    return {'loaded':loaded,'errors':errors,'issues':issues}
