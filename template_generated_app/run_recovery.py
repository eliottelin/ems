"""The primary missing-data workflow appears before a model run proceeds."""
from datetime import date,timedelta
import pandas as pd
import streamlit as st
from data_status import source_issue
from nearby_data import SPATIAL_SOURCES
from source_requests import proxy_selection,distance_km

SOIL_SOURCES=('soilgrids_wcs','hwsd')


def soil_recovery_state(recovery):
    """Keep assumed soil separate from proceeding with real usable inputs."""
    sources=recovery.get('soil_sources')
    if sources is None:
        known=set(recovery.get('missing',{}))|set(recovery.get('loaded',{}))
        sources=[key for key in SOIL_SOURCES if key in known]
    sources=set(sources)
    loaded=recovery.get('loaded',{})
    needs_soil=bool(sources) and not sources.intersection(loaded)
    attempts=recovery.get('soil_attempts')
    failed=({row['source_id'] for row in attempts if row['status']=='failed'}
            if attempts is not None else sources.intersection(recovery.get('missing',{})))
    return needs_soil, bool(sources) and sources.issubset(failed)


def cancel_run():
    st.session_state['run_recovery']=None
    st.session_state['resume_run']=False
    st.session_state['run_cancelled']=True


def continue_run(use_assumed_soil=False):
    recovery=st.session_state['run_recovery']
    if 'open_meteo' not in recovery['loaded']:
        return
    needs_soil,all_soil_failed=soil_recovery_state(recovery)
    if needs_soil and not (use_assumed_soil and all_soil_failed):
        return
    if needs_soil:
        st.session_state['run_assumed_soil']=True
    st.session_state['run_acknowledged']=list(set(st.session_state.get('run_acknowledged',[]))|set(recovery['missing']))
    st.session_state['run_recovery']=None
    st.session_state['resume_run']=True


def retry_source(key):
    st.session_state.get('run_fetches',{}).pop(key,None)
    st.session_state.get('run_alternatives',{}).pop(key,None)
    st.session_state['run_recovery']=None
    st.session_state['resume_run']=True
    st.session_state['retry_source_once']=key


def download_hwsd_and_retry():
    st.session_state['hwsd_download_map']=True
    retry_source('hwsd')


def accept_proxy(key,candidate,change_dates=False):
    lat,lon=st.session_state['latitude'],st.session_state['longitude']
    selection=proxy_selection(candidate,lat,lon)
    st.session_state['source_proxies']={**st.session_state['source_proxies'],key:selection}
    if key=='srdb' and selection.get('period_start') and not change_dates:
        st.session_state['run_proxy_comparison_skips']=list(set(st.session_state.get('run_proxy_comparison_skips',[]))|{key})
    if change_dates:
        st.session_state['period']=(date.fromisoformat(selection['period_start']),date.fromisoformat(selection['period_end']))
    st.session_state['run_recovery']=None
    st.session_state['resume_run']=True


def available_dates(key,details,start,end):
    first,last=details.get('available_first'),details.get('available_last')
    if not first or not last:
        return None
    first,last=date.fromisoformat(first),date.fromisoformat(last)
    if not details.get('bounds_are_periods'):
        if key=='terraclimate':
            last=(pd.Timestamp(last)+pd.offsets.MonthEnd(0)).date()
        elif key=='mod16a2gf':
            last=min(last+timedelta(days=7),date(last.year,12,31))
    first=max(first,last-timedelta(days=max(365,(end-start).days+1)-1))
    return first,last


def change_run_dates(first,last):
    st.session_state['period']=(first,last)
    st.session_state['run_recovery']=None
    st.session_state['resume_run']=True


def source_label(key,blocks):
    return {'soilgrids_wcs':'SoilGrids','hwsd':'HWSD','open_meteo':'Weather'}.get(key,blocks[key]['label'])


def has_soilgrids_map_gap(key,issue):
    """A REST outage must not hide a confirmed mapped-cell data gap."""
    if key!='soilgrids_wcs':
        return False
    return any(row.get('exception')=='NoMatchingData'
        and str(row.get('method','')).startswith(('WMS','WCS'))
        for row in issue.get('details',{}).get('attempts',[]))


def source_summary(key,issue):
    status=issue.get('status')
    details=issue.get('details',{})
    if status=='Missing local files':
        return 'map not installed' if key=='hwsd' and 'map' in issue.get('message','').lower() else 'files not installed'
    if details.get('reason') in ('no_overlap_time','no_complete_time_window'):
        return 'no data for these dates'
    if has_soilgrids_map_gap(key,issue):
        return 'no usable profile at this site'
    if status=='Connection failed':
        return 'service unavailable'
    if status=='No matching records':
        return 'no profile at this site' if key in SOIL_SOURCES else 'no matching records'
    return 'no usable data returned'


@st.dialog('Complete your run',width='large',on_dismiss=cancel_run)
def show_run_recovery(blocks,search,signature,settings):
    # Decisions resume the whole app so the selected data are checked before
    # calculation. Searches stay explicit and their results are retained here.
    recovery=st.session_state.get('run_recovery')
    if not recovery or st.session_state.get('resume_run'):
        st.rerun()
    lat,lon=recovery['site'];start,end=recovery['dates']
    recovery.setdefault('soil_sources',[key for key in SOIL_SOURCES if key in blocks])
    ordered=[row['source_id'] for row in recovery.get('soil_attempts',[]) if row['source_id'] in blocks]
    ordered.extend(key for key in blocks if key not in ordered)
    missing=[key for key in ordered if key in recovery['missing']]
    if not missing:
        st.rerun()
    if not recovery.get('_source_choice_initialized'):
        st.session_state['run_missing_source']=missing[0]
        recovery['_source_choice_initialized']=True
    needs_soil,all_soil_failed=soil_recovery_state(recovery)
    weather_ready='open_meteo' in recovery['loaded']
    can_continue=weather_ready and not needs_soil
    if not weather_ready:
        st.write('Daily weather is required before this model can run.')
    elif needs_soil:
        st.write('A mapped soil profile is still needed. Choose how to supply it below.')
    else:
        st.write('Optional inputs are unavailable. You can continue with the data already loaded.')
    st.caption(' · '.join(source_label(key,blocks)+': '+source_summary(key,recovery['missing'][key]) for key in missing))
    if len(missing)>1:
        key=st.selectbox('Data source',missing,format_func=lambda value:source_label(value,blocks),key='run_missing_source')
    else:
        key=missing[0]
    issue=recovery['missing'][key];details=issue.get('details',{})
    status=issue.get('status')
    install_map=key=='hwsd' and status=='Missing local files' and 'map' in issue.get('message','').lower()
    time_issue=details.get('reason') in ('no_overlap_time','no_complete_time_window')
    offered=available_dates(key,details,start,end) if time_issue else None
    can_search=(key in SPATIAL_SOURCES and not time_issue
        and (status=='No matching records' or has_soilgrids_map_gap(key,issue)))
    choices=[];search_issue=None;search_slot=None
    if key=='srdb':
        for row in details.get('nearest_qualified_studies',[]):
            choices.append({'latitude':row['Latitude'],'longitude':row['Longitude'],'record':row['Record_number'],
                'period_start':row['period_start'],'period_end':row['period_end'],
                'distance_km':distance_km(lat,lon,row['Latitude'],row['Longitude'])})
    elif can_search:
        alternatives=st.session_state.setdefault('run_alternatives',{})
        result=alternatives.get(key,{})
        choices=result.get('candidates',[])
        if not choices:
            st.caption('Search within 25 km. Only this data source changes; your model site and dates stay fixed.')
            search_slot=st.empty()
            requested=search_slot.button('Find nearby data',key='run_find_nearby',
                type='secondary' if can_continue else 'primary')
            if requested:
                bar=st.progress(0,text='Checking nearby locations…')
                try:
                    candidates=search(key,lat,lon,start,end,signature,{k:v for k,v in settings.items() if k!='source_proxies'},
                        _progress=lambda done,total:bar.progress(done/max(1,total),text=f'Checking location {done} of {total}…'))
                    alternatives[key]={'candidates':candidates}
                except Exception as exc:
                    alternatives[key]={'issue':source_issue(exc)}
                finally:
                    bar.empty()
                result=alternatives[key]
                choices=result.get('candidates',[])
                if choices:
                    search_slot.empty()
        search_issue=result.get('issue')
        if search_issue:
            st.caption({'No matching records':'No usable nearby data were found within the search area.',
                'Connection failed':'The nearby search could not reach the data service.',
                'Missing local files':'The nearby search needs data files that are not installed.'}.get(
                    search_issue.get('status'),'The nearby search did not return usable data.'))
        elif result and not choices:
            st.caption('No usable nearby data were found within the search area.')
    if choices:
        def label(index):
            row=choices[index]
            text=f"{row['distance_km']:.1f} km away · {row['latitude']:.5f}, {row['longitude']:.5f}"
            if row.get('period_start'):
                text+=f" · {row['period_start']} to {row['period_end']}"
            return text
        index=st.selectbox('Choose nearby data',range(len(choices)),format_func=label,key='run_proxy_choice_'+key)
        choice=choices[index]
        st.caption('Your model site stays fixed. This source will use the selected location, recorded as a nearby proxy.')
        historical=choice.get('period_start') and (choice['period_start'],choice['period_end'])!=(str(start),str(end))
        if historical:
            st.caption('This study covers different dates. Keep your dates to load it, or explicitly switch to the study period for comparison.')
        st.button('Use nearby data and continue',key='run_use_proxy',type='secondary' if can_continue else 'primary',
            on_click=accept_proxy,args=(key,choice))
        if historical:
            st.button('Use study dates too',key='run_use_study_dates',on_click=accept_proxy,args=(key,choice,True),
                help='Changes the run dates to the study window; keeps your model site fixed.')
    elif offered:
        st.caption(f'Available period: {offered[0]} to {offered[1]}.')
        st.button('Use available dates and check again',key='run_use_dates',on_click=change_run_dates,args=offered,
            type='secondary' if can_continue else 'primary')
    elif install_map:
        st.caption('Install the HWSD map once (about 1.9 GB uncompressed). The download can take several minutes; later runs reuse it.')
        st.button('Download HWSD map and retry',key='run_download_hwsd',on_click=download_hwsd_and_retry,
            type='secondary' if can_continue else 'primary')
    retry_primary=not(can_continue or choices or offered or install_map or can_search)
    if can_continue:
        st.button('Run with available data',key='continue_run',on_click=continue_run,type='primary')
    elif retry_primary:
        st.button('Retry this source',key='retry_run_source',on_click=retry_source,args=(key,),type='primary')
    if weather_ready and needs_soil:
        with st.expander('Use assumed soil instead'):
            assumptions=recovery.get('preset_assumptions',{})
            texture=assumptions.get('texture',settings.get('_fallback_texture','configured texture'))
            carbon=assumptions.get('soc_gC_m2',settings.get('_fallback_soc_gC_m2'))
            depth=assumptions.get('depth_cm',settings.get('_fallback_depth_cm'))
            detail=f'The {str(texture).replace("_"," ")} preset uses assumed hydraulic properties'
            if carbon is not None:
                detail+=f' and {float(carbon):,.0f} g C/m² of whole-profile soil carbon'
            if depth is not None:
                detail+=f' across {float(depth):g} cm'
            st.warning(detail+'. These are model assumptions, not measurements at your site.')
            if not all_soil_failed:
                st.caption('Try the remaining mapped soil sources first.')
            st.button('Use assumed soil preset',key='use_assumed_soil',on_click=continue_run,args=(True,),
                disabled=not all_soil_failed,help='Last resort after all mapped soil sources failed; recorded in the run report.')
    with st.expander('Source details'):
        st.json({'selected_source':key,'source_issues':recovery['missing'],
            'soil_attempts':recovery.get('soil_attempts',[]),**({'nearby_search':search_issue} if search_issue else {})})
    with st.container(horizontal=True):
        if not retry_primary:
            st.button('Retry this source',key='retry_run_source',on_click=retry_source,args=(key,))
        st.button('Cancel run',key='cancel_run',on_click=cancel_run)
