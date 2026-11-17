"""Independent ecosystem app: set up, run, explore; detailed settings separate."""
import json
from datetime import date, timedelta
from pathlib import Path
from time import monotonic
from importlib import import_module
import pandas as pd
import streamlit as st
import advanced as advanced_module
import engine as engine_module
import parameters as parameters_module
import soil_profile as soil_profile_module
from runtime_identity import get_application_identity
from engine import simulate
from advanced import render_advanced
from data_blocks import DataRequest, load_block
from comparison_view import collect_comparisons,show_comparisons
from data_status import source_issue,source_status
from geocoding import search_places, place_label
from presentation import VARIABLES, chart, show_block
from weather import ARCHIVE_DEFAULT_LAG_DAYS, available_historical_period
from nearby_data import nearby_data_options,SPATIAL_SOURCES
from nearby_cache import load_cached_nearby
from source_requests import source_request,tag_source_proxy,proxy_selection,distance_km
from source_cache import load_cached_soil, SOIL_CACHE_POLICY
from run_preparation import prepare_sources, prepare_comparisons
from loading_feedback import LoadingFeedback
from run_recovery import show_run_recovery
from scenario_soil import resolve_scenario_soil

ROOT = Path(__file__).resolve().parent
cfg = json.loads((ROOT/'model_config.json').read_text())
blocks = {block['id']:block for block in cfg['data_blocks']}
identity_modules={'advanced':advanced_module,'engine':engine_module,
    'parameters':parameters_module,'soil_profile':soil_profile_module}
if 'try_categorical' in blocks:
    identity_modules['traits']=import_module('data_blocks.traits')
if set(blocks)&{'soilgrids_wcs','hwsd'}:
    identity_modules['soil_sources']=import_module('data_blocks.soil')
application_identity=get_application_identity(ROOT,identity_modules)


def local_signature():
    return tuple((str(p.relative_to(ROOT)),p.stat().st_mtime_ns,p.stat().st_size) for p in (ROOT/'data').rglob('*') if p.is_file())

st.set_page_config(page_title=cfg['project_name'],page_icon=':material/eco:',layout='wide')
st.title(cfg['project_name'])
st.caption(' · '.join({'water_balance':'Water','vegetation':'Plants','soil_carbon':'Soil carbon'}[key] for key in cfg['models']))
model_tab, data_tab, advanced_tab = st.tabs(['Model','Data','Advanced'])
for key, initial in {'simulation':None,'loaded_blocks':{},'data_errors':{},'independent_block':None,
                     'geocoding_places':[],'latitude':None,'longitude':None,'data_issues':{},'source_proxies':{}}.items():
    if key not in st.session_state:
        st.session_state[key] = initial

@st.cache_data(ttl=86400,max_entries=64,show_spinner=False)
def cached_places(query):
    return search_places(query)

@st.cache_data(ttl=3600,max_entries=24,show_spinner=False)
def cached_bounds(lat,lon,today):
    return available_historical_period(lat,lon,today)

@st.cache_data(ttl=3600,max_entries=24,show_spinner=False)
def cached_source_data(key,lat,lon,first,last,local_signature,settings,source_policy,_progress=None):
    # Include retrieval priority in the cache key when the source policy changes.
    return load_block(key,DataRequest(lat,lon,first,last,ROOT/'data',{**settings,'_progress':_progress}))


def cached_block(key,lat,lon,first,last,local_signature,settings,_progress=None,_source_progress=None):
    original=DataRequest(lat,lon,first,last,ROOT/'data',{**settings,'_progress':_progress})
    effective=source_request(key,original)
    if key in ('soilgrids_wcs','hwsd'):
        block=load_cached_soil(key,effective,local_signature,SOIL_CACHE_POLICY,
            _progress=_progress,_source_progress=_source_progress)
    else:
        effective_settings={k:v for k,v in effective.options.items() if k not in ('source_proxies','_progress','_source_progress','soil_source_preference')
            and not k.startswith(('soilgrids_','hwsd_'))}
        block=cached_source_data(key,effective.latitude,effective.longitude,effective.start,effective.end,
            local_signature,effective_settings,'',_progress=_progress)
    return tag_source_proxy(key,block,original,effective)


def load_with_feedback(key,lat,lon,first,last,signature,settings,_progress=None,_feedback=None,_run=False):
    if key not in ('soilgrids_wcs','hwsd'):
        return cached_block(key,lat,lon,first,last,signature,settings,_progress=_progress)
    feedback=_feedback or LoadingFeedback('Loading soil profile')
    own_feedback=_feedback is None
    began=monotonic()
    retrieving=False
    def source_progress(fraction,message):
        nonlocal retrieving
        retrieving=True
        base=.2 if _run else 0.
        span=.36 if _run else .95
        feedback.update(base+span*fraction,'Step 2 of 3 · Soil' if _run else 'Loading soil profile',message)
    if key=='hwsd':
        detail='Loading the local HWSD profile. An enabled first map download can take several minutes.'
    else:
        detail=('Trying REST first, then the SoilGrids map services. ' if settings.get('soilgrids_use_rest')
            else 'Loading SoilGrids from its map services. ')
        detail+='The first lookup can take about two minutes; saved profiles are reused.'
    feedback.update(.2 if _run else 0.,'Step 2 of 3 · Soil' if _run else 'Loading soil profile',detail)
    try:
        block=cached_block(key,lat,lon,first,last,signature,settings,
            _progress=_progress,_source_progress=source_progress)
        cached=block.provenance.get('cache',{}).get('hit',not retrieving)
        st.session_state['soil_load_timing']={'key':key,'site':[lat,lon],
            'seconds':monotonic()-began,'cached':cached}
        if cached:
            feedback.update(.56 if _run else .95,
                'Step 2 of 3 · Soil profile ready' if _run else 'Soil profile ready',
                'Reused a saved soil profile; no soil download needed.')
        if own_feedback:
            feedback.finish('Soil profile ready')
        return block
    except Exception:
        if own_feedback:
            feedback.finish('Soil source unavailable',error=True)
        raise


def show_soil_load_time(key,latitude,longitude):
    timing=st.session_state.get('soil_load_timing',{})
    if timing.get('key')==key and timing.get('site')==[latitude,longitude]:
        action='Reused cached soil profile' if timing['cached'] else 'Soil profile ready'
        st.caption(f"{action} in {timing['seconds']:.1f}s. Changing dates or model parameters reuses this site's profile.")


def cached_nearby(key,lat,lon,first,last,signature,settings,_progress=None):
    return load_cached_nearby(key,DataRequest(lat,lon,first,last,ROOT/'data',settings),
        signature,_progress=_progress,_search=nearby_data_options)


def use_proxy(key,candidate):
    selection=proxy_selection(candidate,st.session_state['latitude'],st.session_state['longitude'])
    st.session_state['source_proxies']={**st.session_state['source_proxies'],key:selection}
    st.session_state['load_proxy']=key


def remove_proxy(key):
    st.session_state['source_proxies']={k:v for k,v in st.session_state['source_proxies'].items() if k!=key}
    st.session_state['load_proxy']=key


def use_available_dates(key,first,last):
    first,last=date.fromisoformat(first),date.fromisoformat(last)
    if key=='open_meteo_climate':
        st.session_state['climate_period']=(first,last)
        st.session_state['scenario']=None
    else:
        st.session_state['period']=(first,last)
    st.session_state['load_proxy']=key


def clear_place():
    st.session_state['latitude'] = st.session_state['longitude'] = None
    st.session_state['place_result'] = None
    st.session_state['geocoding_places'] = []


def use_place():
    selected = next((p for p in st.session_state['geocoding_places'] if p['id']==st.session_state.get('place_result')),None)
    if selected:
        st.session_state['latitude'],st.session_state['longitude'] = float(selected['latitude']),float(selected['longitude'])


def use_study(record):
    if record.get('period_start') and record.get('period_end'):
        first,last=date.fromisoformat(record['period_start']),date.fromisoformat(record['period_end'])
    else:
        from data_blocks.observations import srdb_annual_window
        first,last=srdb_annual_window(record['Study_midyear'])
    use_proxy('srdb',{'latitude':record['Latitude'],'longitude':record['Longitude'],
        'record':record['Record_number'],'period_start':str(first),'period_end':str(last)})


def clamp_period():
    bounds = st.session_state.get('archive_bounds')
    period = st.session_state.get('period')
    if bounds and period and len(period)==2:
        clipped = tuple(min(bounds[1],max(bounds[0],day)) for day in period)
        if clipped != tuple(period):
            st.session_state['period'] = clipped
            st.session_state['date_notice'] = f'Dates adjusted to available weather: {clipped[0]} to {clipped[1]}.'


with advanced_tab:
    params,options,depths,texture,soil_overrides,trait_selection,settings_error=render_advanced(cfg,blocks,ROOT)
from soil_profile import make_profile
signature = local_signature()

with model_tab:
    st.subheader('Set up')
    query = st.text_input('Find a place',placeholder='Start typing a city or postal code…',key='place_query',
                         type='search',live='400ms',on_change=clear_place,help='Open-Meteo / GeoNames place search. Select a match below.')
    if len(query.strip()) >= 3:
        try:
            st.session_state['geocoding_places'] = cached_places(query.strip())
            places = {p['id']:p for p in st.session_state['geocoding_places']}
            if places:
                st.selectbox('Matching places',list(places),index=None,placeholder='Choose a match…',
                    format_func=lambda key:place_label(places[key]),key='place_result',on_change=use_place,label_visibility='collapsed')
            else:
                st.info('No matches. Try a nearby city or enter coordinates.')
        except Exception:
            st.info('Place search is unavailable. You can enter coordinates below.')
    with st.expander('Precise coordinates'):
        left,right = st.columns(2)
        latitude = left.number_input('Latitude',min_value=-90.,max_value=90.,value=None,key='latitude',format='%.5f')
        longitude = right.number_input('Longitude',min_value=-180.,max_value=180.,value=None,key='longitude',format='%.5f')
    if st.session_state.get('proxy_origin')!=(latitude,longitude):
        st.session_state['source_proxies']={}
        st.session_state['proxy_origin']=(latitude,longitude)
    options={**options,'source_proxies':st.session_state['source_proxies']}
    if options['source_proxies']:
        labels=[blocks[k]['label'] for k in options['source_proxies'] if k in blocks]
        st.caption('Selected nearby proxies: '+', '.join(labels),help='Your site stays fixed. Each proxy uses another location, explicitly labeled in Data and run/source reports. Backups are used only when needed. Remove or change a proxy in Data.')
    earliest,latest = date(1940,1,1),date.today()-timedelta(days=ARCHIVE_DEFAULT_LAG_DAYS)
    bounds_problem = None
    if latitude is not None and longitude is not None:
        try:
            with st.spinner('Finding available dates…'):
                earliest,latest = cached_bounds(latitude,longitude,date.today())
        except Exception as exc:
            bounds_problem = str(exc)
        st.session_state['archive_bounds'] = (earliest,latest)
        if st.session_state.get('dates_site') != (latitude,longitude):
            st.session_state['period'] = (max(earliest,latest-timedelta(days=cfg.get('initial_run_days',365)-1)),latest)
            st.session_state['dates_site'] = (latitude,longitude)
        clamp_period()
        period = st.date_input('Run dates',key='period',on_change=clamp_period,min_value=date(1900,1,1),max_value=date(2100,12,31),
            help=f'Available boundary records: {earliest} to {latest}. Dates outside this range are adjusted automatically. Interior gaps stop a run.')
        period=(period,) if isinstance(period,date) else period
        start,end = period if len(period)==2 else (None,None)
        if st.session_state.pop('date_notice',None):
            st.caption(f'Using available weather dates: {period[0]} to {period[-1]}.')
    else:
        start = end = None
    st.caption('Time step: daily',help='One model update per daily weather record; 365 daily updates cover 365 days. The water solver uses internal substeps, which do not create hourly weather observations.')
    if {'soilgrids_wcs','hwsd'}<=set(blocks):
        preference=st.segmented_control('Soil data',['soilgrids','hwsd'],default='soilgrids',required=True,
            key='soil_source_preference',format_func=lambda value:{'soilgrids':'SoilGrids · finer detail (250 m)',
                'hwsd':'HWSD · faster (~1 km)'}[value],
            help='Chooses which mapped source to try first. If it cannot supply a valid complete profile, the other source is tried. A texture preset requires a separate explicit choice after both fail.')
        options['soil_source_preference']=preference or 'soilgrids'
        st.caption('SoilGrids first, then HWSD. The first lookup can take about two minutes; saved profiles are fast.'
            if preference=='soilgrids' else 'Local HWSD first. If unavailable, SoilGrids is tried next and can take about two minutes.')
    ready = latitude is not None and longitude is not None and start is not None and end is not None and not settings_error
    context = {'site':(latitude,longitude),'dates':(start,end),'data_options':options,'parameters':params,
               'depths':depths,'texture':texture,'soil_overrides':soil_overrides,'local_files':signature,'traits':trait_selection,
               'application_build':application_identity['loaded_build']}
    if st.session_state.get('context') != context:
        st.session_state.update(context=context,simulation=None,loaded_blocks={},data_errors={},data_issues={},independent_block=None,scenario=None,comparisons_requested=False,run_assumed_soil=False)
    run_context={**context,'data_options':{k:v for k,v in options.items() if k!='source_proxies'}}
    if st.session_state.get('run_context')!=run_context:
        st.session_state.update(run_context=run_context,run_recovery=None,run_acknowledged=[],run_fetches={},run_alternatives={},run_proxy_comparison_skips=[])
    clicked_run=st.button('Run ecosystem model',key='run_model',type='primary',icon=':material/play_arrow:',disabled=not ready)
    if clicked_run:
        st.session_state.update(run_recovery=None,run_acknowledged=[],run_fetches={},run_alternatives={},run_cancelled=False,simulation=None,run_proxy_comparison_skips=[],run_stage_timings=[],comparisons_requested=False,run_assumed_soil=False)
    resume_run=st.session_state.pop('resume_run',False)
    feedback=None
    if (clicked_run or resume_run) and ready:
        assert latitude is not None and longitude is not None and start is not None and end is not None
        run_latitude,run_longitude,run_start,run_end=latitude,longitude,start,end
        feedback=LoadingFeedback()
        run_feedback=feedback
        feedback.update(0.,'Step 1 of 3 · Loading weather','Fetching the daily weather required by the model.')
        try:
            def map_progress(done,total):
                fraction = done/total if total else 0.
                amount = f'{done/1e6:.0f} of {total/1e6:.0f} MB' if total else f'{done/1e6:.0f} MB'
                run_feedback.update(.3+.2*fraction,'Step 2 of 3 · Downloading HWSD map',f'One-time map download · {amount}. Later runs reuse the map.')
            run_memo=st.session_state['run_fetches']
            stage_timings=st.session_state.setdefault('run_stage_timings',[])
            retry_key=st.session_state.pop('retry_source_once',None)
            def fetch_for_run(key):
                original=DataRequest(run_latitude,run_longitude,run_start,run_end,ROOT/'data',options)
                effective=source_request(key,original)
                token=json.dumps({'site':[effective.latitude,effective.longitude],'period':[str(effective.start),str(effective.end)],
                    'settings':{k:v for k,v in effective.options.items() if k!='source_proxies'}},sort_keys=True,default=str)
                memo=run_memo
                existing=memo.get(key)
                if existing and existing['token']==token:
                    if 'error' in existing:
                        raise existing['error']
                    return existing['block']
                began=monotonic()
                timing={'stage':key,'status':'ready'}
                try:
                    source_options={**options,'soilgrids_retry':True} if key==retry_key=='soilgrids_wcs' else options
                    block=load_with_feedback(key,latitude,longitude,start,end,signature,source_options,
                        _progress=map_progress,_feedback=feedback,_run=True)
                    timing.update(cache=block.provenance.get('cache',{}),method=block.provenance.get('method'),
                        attempts=block.provenance.get('attempts',[]))
                    memo[key]={'token':token,'block':block}
                    return block
                except Exception as exc:
                    timing.update(status='unavailable',details=getattr(exc,'details',{}))
                    memo[key]={'token':token,'error':exc}
                    raise
                finally:
                    timing['seconds']=round(monotonic()-began,3)
                    stage_timings.append(timing)
            prepared=prepare_sources(blocks,cfg['models'],start,end,fetch_for_run,
                lambda frame:make_profile(depths,frame,texture,params['initial'],soil_overrides),
                run_feedback.update,defer_comparisons=True,soil_preference=options.get('soil_source_preference','soilgrids'))
            loaded,errors,issues=prepared['loaded'],prepared['errors'],prepared['issues']
            soil,soil_source=prepared['soil'],prepared['soil_source']
            signature=local_signature()
            st.session_state['context']={**context,'local_files':signature}
            st.session_state['run_context']={**run_context,'local_files':signature}
            acknowledged=set(st.session_state.get('run_acknowledged',[]))
            accepted_historical=set(st.session_state.get('run_proxy_comparison_skips',[]))
            missing={k:v for k,v in prepared['missing'].items() if
                (k not in acknowledged and not (k in loaded and k in accepted_historical)) or (k=='open_meteo' and k not in loaded)}
            if 'open_meteo' not in loaded:
                st.error('The run could not finish: '+errors['open_meteo'])
                with st.expander('Retrieval details'):
                    st.json(issues['open_meteo'])
            if missing:
                feedback.pause('Choose available inputs to continue')
                st.session_state.update(run_recovery={'site':(latitude,longitude),'dates':(start,end),'missing':missing,'loaded':loaded,
                    'soil_attempts':prepared.get('soil_attempts',[]),
                    'soil_sources':[source for source in ('soilgrids_wcs','hwsd') if source in blocks],
                    'preset_assumptions':{'texture':texture,'soc_gC_m2':params['initial']['fallback_soc'],
                        'depth_cm':depths[-1],'soc_depth_scale_cm':params['initial']['soc_depth_scale_cm']}},
                    loaded_blocks=loaded,data_errors=errors,data_issues=issues)
            else:
                st.session_state['prepared_run']=prepared
                st.session_state['calculate_run']=True
        except Exception as exc:
            if feedback:
                feedback.finish('Run could not finish',error=True)
            st.error(f'The run could not finish: {exc}')
            with st.expander('Retrieval details'):
                st.json(getattr(exc,'diagnostics',{'message':str(exc)}))
    if st.session_state.pop('calculate_run',False):
        prepared=st.session_state.pop('prepared_run')
        loaded,errors,issues=prepared['loaded'],prepared['errors'],prepared['issues']
        soil,soil_source=prepared['soil'],prepared['soil_source']
        feedback=feedback or LoadingFeedback()
        calculation_feedback=feedback
        feedback.update(.65,'Step 3 of 3 · Calculating model','Inputs ready. Calculating daily water, plant and soil-carbon changes.')
        try:
            st.session_state['parameter_soil_source']=soil
            st.session_state['parameter_soil_site']=(latitude,longitude)
            began = monotonic()
            def on_progress(done,total):
                elapsed = monotonic()-began
                remaining = elapsed*(total-done)/done
                estimate = f'about {remaining:.0f}s remaining' if done >= 5 else 'estimating time…'
                calculation_feedback.update(.65+.34*done/total,'Step 3 of 3 · Calculating model',f'Day {done:,} of {total:,} · {estimate}')
            run_configuration={**cfg,'latitude':latitude,'longitude':longitude,'layer_depths_cm':depths,
                'soil_depth_cm':depths[-1],'start_date':str(start),'end_date':str(end),
                'days':len(loaded['open_meteo'].frame),'soil_texture':texture,'soil_source':soil_source,
                'soil_source_preference':options.get('soil_source_preference'),
                'trait_selection':trait_selection,'soil_overrides':soil_overrides}
            simulation = simulate(run_configuration,params,forcing=loaded['open_meteo'].frame,soil=soil,progress=on_progress)
            st.session_state.setdefault('run_stage_timings',[]).append({'stage':'model_calculation','status':'ready','seconds':round(monotonic()-began,3)})
            simulation.attrs['application']=application_identity
            simulation.attrs['application_revision']=application_identity['application_revision']
            comparisons,comparison_coverage=collect_comparisons(simulation,loaded)
            provenance = {'application':application_identity,'application_revision':application_identity['application_revision'],
                          'configuration':simulation.attrs['effective_configuration'],'project_configuration':cfg,
                          'site':{'latitude':latitude,'longitude':longitude},'start':str(start),'end':str(end),
                          'model':simulation.attrs,'sources':{key:b.provenance for key,b in loaded.items()},
                          'stage_timings':st.session_state.get('run_stage_timings',[]),
                          'soil_attempts':prepared.get('soil_attempts',[]),
                          'comparisons_loading':'on request after simulation',
                          'unavailable':errors,'source_issues':issues,'comparisons':comparisons.to_dict('records'),'comparison_coverage':comparison_coverage.to_dict('records'),
                          'run_data_choices':{'accepted_missing_sources':st.session_state.get('run_acknowledged',[]),'proxies':options['source_proxies'],
                              'soil_source_preference':options.get('soil_source_preference'),
                              'assumed_soil_explicitly_accepted':st.session_state.get('run_assumed_soil',False)}}
            st.session_state.update(simulation=simulation,loaded_blocks=loaded,data_errors=errors,data_issues=issues,
                                    comparisons=comparisons,run_provenance=provenance)
            # A first-use map download is part of this successful run, rather
            # than an external source edit that should erase results on rerun.
            signature = local_signature()
            st.session_state['context'] = {**context,'local_files':signature}
            feedback.finish('Run complete')
            st.rerun()  # Show newly loaded source-derived defaults in Advanced.
        except Exception as exc:
            if feedback:
                feedback.finish('Run could not finish',error=True)
            st.error(f'The run could not finish: {exc}')
            with st.expander('Retrieval details'):
                st.json(getattr(exc,'diagnostics',{'message':str(exc)}))
    if st.session_state.pop('run_cancelled',False):
        st.info('Run cancelled. You can change the setup or inspect sources in Data.')
    simulation = st.session_state['simulation']
    if simulation is not None:
        st.success(f'Simulated {len(simulation):,} days with daily updates')
        soil_dataset=simulation.attrs['soil_mapping']['dataset']
        soil_description={'soilgrids_wcs':'SoilGrids · 250 m','hwsd':'HWSD · about 1 km'}.get(soil_dataset,
            'Assumed soil preset ('+texture.replace('_',' ')+')')
        st.caption('Soil: '+soil_description+' · Model: uncalibrated',
                   help='A texture preset is used if neither soil source supplies a complete profile. The choice and parameter estimates are recorded in run details.')
        show_soil_load_time(simulation.attrs['soil_mapping']['dataset'],latitude,longitude)
        st.subheader('Explore results')
        with st.container(horizontal=True):
            for key,title in [('precip_mm','Total precipitation'),('aet_mm','Total water loss'),('plant_uptake_gC_m2_day','Net plant production')]:
                if key in simulation:
                    st.metric(title,f'{simulation[key].sum():,.1f} '+('mm' if key.endswith('_mm') else 'g C/m²'),
                              help=VARIABLES[key][1],border=True)
        names=[label for model,label in [('water_balance','Water'),('vegetation','Plants'),('soil_carbon','Soil carbon')] if model in cfg['models']]
        if set(cfg['models'])&{'water_balance','soil_carbon'}:
            names.append('Comparisons')
        sections=dict(zip(names,st.tabs(names)))
        if 'Water' in sections:
          with sections['Water']:
              chart(simulation,['precip_mm','aet_mm','runoff_mm','drainage_mm'],'Water fluxes','Daily inputs and losses over ground area, in mm/day.')
              chart(simulation,['soil_water_mm'],'Water stored in soil','Total water across the configured profile, in mm.')
              saturation = [key for key in simulation if key.startswith('saturation_')]
              chart(simulation,saturation,'Moisture by depth','Fraction of pore space filled with water in each fixed soil layer, from 0 to 1.')
        if 'Plants' in sections:
          with sections['Plants']:
              chart(simulation,['leaf_carbon_gC_m2','stem_carbon_gC_m2','root_carbon_gC_m2'],'Living plant carbon','Carbon stocks in leaves, stems and roots, in g C/m².')
              chart(simulation,['lai'],'Leaf area','Leaf area index: m² of leaf per m² of ground.')
              chart(simulation,['root_depth_cm'],'Rooting depth','Maximum rooting depth in cm; roots are distributed exponentially within this depth.')
              chart(simulation,['plant_uptake_gC_m2_day','litter_gC_m2_day'],'Plant carbon fluxes','Net plant production and litter transferred to soil, in g C/m²/day.')
        if 'Soil carbon' in sections:
          with sections['Soil carbon']:
              chart(simulation,[f'{name}_carbon_gC_m2' for name in ('particulate','dissolved','microbial','mineral','aggregate')],
                    'Soil carbon pools','Particulate, dissolved, microbial, mineral-associated and aggregate carbon, in g C/m².')
              chart(simulation,[key for key in simulation if key.startswith('soc_')],'Carbon by depth','Total organic carbon in each soil layer, in g C/m².')
              chart(simulation,['soil_respiration_gC_m2_day'],'Microbial respiration','Heterotrophic respiration, in g C/m²/day; excludes living root respiration.')
        if 'Comparisons' in sections:
            with sections['Comparisons']:
                if not st.session_state.get('comparisons_requested'):
                    st.caption('Comparison downloads are optional. Load them here after viewing your model results.')
                if st.button('Load comparisons' if not st.session_state.get('comparisons_requested') else 'Reload comparisons',key='load_comparisons'):
                    comparison_feedback=LoadingFeedback('Loading comparisons')
                    comparison_feedback.update(0.,'Loading comparison sources','Model results are ready. These downloads do not change the simulation.')
                    comparison_timings=[]
                    def fetch_comparison(source):
                        began=monotonic()
                        try:
                            return cached_block(source,latitude,longitude,start,end,signature,options)
                        finally:
                            comparison_timings.append({'stage':source,'seconds':round(monotonic()-began,3)})
                    additional=prepare_comparisons(blocks,cfg['models'],start,end,fetch_comparison,comparison_feedback.update)
                    comparison_sources={'srdb','terraclimate','mod16a2gf'}
                    for source in comparison_sources:
                        st.session_state['loaded_blocks'].pop(source,None)
                        st.session_state['data_errors'].pop(source,None)
                        st.session_state['data_issues'].pop(source,None)
                        st.session_state['run_provenance']['sources'].pop(source,None)
                    st.session_state['loaded_blocks'].update(additional['loaded'])
                    st.session_state['data_errors'].update(additional['errors'])
                    st.session_state['data_issues'].update(additional['issues'])
                    st.session_state['run_provenance']['unavailable']=st.session_state['data_errors'].copy()
                    st.session_state['run_provenance']['source_issues']=st.session_state['data_issues'].copy()
                    st.session_state['run_provenance']['comparison_timings']=comparison_timings
                    st.session_state['comparisons_requested']=True
                    # Keep newly downloaded local comparison files part of this
                    # run, so a rerun does not discard its existing results.
                    signature=local_signature()
                    st.session_state['context']={**context,'local_files':signature}
                    st.session_state['run_context']={**run_context,'local_files':signature}
                    comparison_feedback.finish('Comparison sources checked')
                    st.rerun()
                if st.session_state.get('comparisons_requested') or any(source in st.session_state['loaded_blocks'] for source in ('srdb','terraclimate','mod16a2gf')):
                    comparisons,audit=show_comparisons(simulation,st.session_state['loaded_blocks'],st.session_state['data_issues'],blocks)
                else:
                    comparisons,audit=pd.DataFrame(),pd.DataFrame()
                st.session_state['comparisons']=comparisons
                st.session_state['run_provenance']['comparisons']=comparisons.to_dict('records')
                st.session_state['run_provenance']['comparison_coverage']=audit.to_dict('records')
                st.session_state['run_provenance']['sources'].update({k:b.provenance for k,b in st.session_state['loaded_blocks'].items() if k in ('srdb','terraclimate','mod16a2gf')})
        with st.expander('Variables and units'):
            st.dataframe([{'Variable':key,'Label':title,'Meaning':meaning} for key,(title,meaning) in VARIABLES.items() if key in simulation],
                         hide_index=True,alt='Definitions and units of model outputs')
        with st.expander('Diagnostics and data availability'):
            st.caption('Balance residuals near zero are numerical roundoff, not a model error. Balance closure verifies accounting; it does not validate ecological predictions.')
            for key in ('water_residual_mm','carbon_residual_gC_m2','ecosystem_carbon_residual_gC_m2'):
                if key in simulation:
                    st.write(f'{key}: maximum absolute daily residual {simulation[key].abs().max():.3g}')
            st.write('Soil input: '+simulation.attrs['soil_mapping']['source'])
            st.dataframe([{'Source or stage':row['stage'],'Seconds':row['seconds'],'Status':row.get('status','ready'),
                'Cache':row.get('cache',{}).get('storage','—')} for row in st.session_state['run_provenance'].get('stage_timings',[])],
                hide_index=True,alt='Elapsed time and cache status for each input source and model calculation')
            if st.session_state['data_errors']:
                st.json(st.session_state['data_errors'])
            if bounds_problem:
                st.caption('Archive boundary lookup: '+bounds_problem)
            st.json(simulation.attrs)
        with st.container(horizontal=True):
            st.download_button('Download results',simulation.to_csv(index=False),file_name='simulation.csv',mime='text/csv')
            st.download_button('Download run details',json.dumps(st.session_state['run_provenance'],indent=2,default=str),file_name='run_provenance.json',mime='application/json')

with data_tab:
    st.subheader('Data',help='Use any selected data source independently. Each keeps its native resolution, units and provenance.')
    status = []
    for key,b in blocks.items():
        soil_used=st.session_state['simulation'].attrs['soil_mapping']['dataset'] if st.session_state['simulation'] is not None else None
        state=source_status(key,b,st.session_state['loaded_blocks'],st.session_state['data_errors'],st.session_state['data_issues'],soil_used)
        role={'forcing':'Weather forcing','soil_properties':'Soil input','traits':'Plant categories','fallback':'Backup','validation':'Comparison','scenario':'Climate scenario'}.get(b['role'],b['role'])
        status.append({'Source':b['label'],'Purpose':role,'Status':state})
    with st.expander('Sources and status'):
        st.dataframe(status,hide_index=True,alt='Selected data blocks and their current loading status')
    key = st.selectbox('Explore a source',list(blocks),format_func=lambda key:blocks[key]['label'],key='block_choice',help='Load a source without running the ecosystem model.')
    first,last = start,end
    if key == 'open_meteo_climate':
        def clear_scenario():
            st.session_state['scenario'] = None
            st.session_state['loaded_blocks'].pop('open_meteo_climate',None)
        period = st.date_input('Climate scenario dates',value=(date(2030,1,1),date(2030,12,31)),key='climate_period',on_change=clear_scenario)
        first,last = period if len(period)==2 else (None,None)
    standalone_ready = key=='try_categorical' or (latitude is not None and longitude is not None and first is not None and last is not None)
    pending_proxy=st.session_state.get('load_proxy')==key
    load_data=st.button('Load data',key='load_data_block',disabled=not standalone_ready)
    if load_data or (pending_proxy and standalone_ready):
        st.session_state.pop('load_proxy',None)
        try:
            source_feedback=LoadingFeedback('Loading '+blocks[key]['label'])
            source_feedback.update(0.,'Loading '+blocks[key]['label'])
            source_options=({**options,'soilgrids_retry':True,'soilgrids_map_recovery':True}
                if key=='soilgrids_wcs' else options)
            def source_download(done,total):
                amount=f'{done/1e6:.0f} of {total/1e6:.0f} MB' if total else f'{done/1e6:.0f} MB'
                source_feedback.update(.3,'Downloading HWSD map',amount)
            independent = load_with_feedback(key,latitude if latitude is not None else 0.,longitude if longitude is not None else 0.,first or date.today(),last or date.today(),signature,source_options,_progress=source_download,_feedback=source_feedback)
            source_feedback.finish('Source ready')
            st.session_state['loaded_blocks'][key] = independent
            st.session_state['data_errors'].pop(key,None)
            st.session_state['data_issues'].pop(key,None)
            signature = local_signature()
            st.session_state['context'] = {**context,'local_files':signature}
            st.rerun()
        except Exception as exc:
            source_feedback.finish('Source unavailable',error=True)
            st.session_state['data_errors'][key] = str(exc)
            st.session_state['data_issues'][key]=source_issue(exc)
            st.rerun()
    block = st.session_state['loaded_blocks'].get(key)
    if block is not None:
        show_soil_load_time(key,latitude,longitude)
        show_block(block)
        scenario_inputs=None
        if key=='open_meteo_climate':
            scenario_inputs=resolve_scenario_soil(st.session_state['loaded_blocks'],depths,texture,
                params['initial'],soil_overrides,options.get('soil_source_preference','soilgrids'),
                st.session_state.get('run_assumed_soil',False),
                requires_soil=bool({'soilgrids_wcs','hwsd'}&set(blocks)))
            st.caption(scenario_inputs['message'])
        if scenario_inputs is not None and st.button('Run climate scenario',key='run_scenario',disabled=not scenario_inputs['ready']):
            bar = st.progress(0,text='Calculating climate scenario…')
            try:
                scenario = simulate({**cfg,'latitude':latitude,'longitude':longitude,'weather_source':'open_meteo_climate',
                    'layer_depths_cm':depths,'soil_depth_cm':depths[-1],'soil_texture':texture,
                    'soil_source':scenario_inputs['soil_source'],
                    'trait_selection':trait_selection,'soil_overrides':soil_overrides},params,forcing=block.frame,soil=scenario_inputs['soil'],
                    progress=lambda done,total:bar.progress(done/total,text=f'Day {done:,} of {total:,}'))
                scenario.attrs['application']=application_identity
                scenario.attrs['assumed_soil_explicitly_accepted']=st.session_state.get('run_assumed_soil',False)
                scenario.attrs['forcing_provenance']=block.provenance
                if scenario_inputs['soil_source'] in st.session_state['loaded_blocks']:
                    scenario.attrs['soil_provenance']=st.session_state['loaded_blocks'][scenario_inputs['soil_source']].provenance
                st.session_state['scenario'] = scenario
            except Exception as exc:
                st.error(str(exc))
            finally:
                bar.empty()
        if key=='open_meteo_climate' and st.session_state.get('scenario') is not None:
            scenario=st.session_state['scenario']
            source=scenario.attrs['soil_mapping']['dataset']
            soil_label=('Mapped soil not required for the selected models' if source=='not required by selected models'
                else blocks.get(source,{}).get('label','Explicitly accepted soil assumptions'))
            chart(scenario,['soil_water_mm'],'Projected soil water','Climate-model scenario, not historical observations. Soil: '+soil_label+'.')
            st.download_button('Download scenario',st.session_state['scenario'].to_csv(index=False),file_name='scenario.csv',mime='text/csv')
            st.download_button('Download scenario details',json.dumps(scenario.attrs,indent=2,default=str),file_name='scenario_provenance.json',mime='application/json')
    elif key in st.session_state['data_errors']:
        issue=st.session_state['data_issues'].get(key,{'message':st.session_state['data_errors'][key],'details':{}})
        st.info(issue['message'])
        with st.expander('Source details'):
            st.write(st.session_state['data_errors'][key])
            st.json(issue)
    selection=options['source_proxies'].get(key)
    issue=st.session_state['data_issues'].get(key,{})
    if selection:
        st.button('Use data at my site instead',key='remove_proxy',on_click=remove_proxy,args=(key,))
        if key=='srdb' and (selection.get('period_start'),selection.get('period_end'))!=(str(start),str(end)):
            st.info(f"Study data: {selection['period_start']} to {selection['period_end']}. Your run dates are unchanged; only complete matching windows can be compared.")
            st.button('Use these study dates for the run',key='use_source_dates',on_click=use_available_dates,
                args=(key,selection['period_start'],selection['period_end']),help='Changes dates only. Your original simulation site stays fixed, and the study remains a nearby proxy.')
    elif key=='srdb' and standalone_ready:
        details=block.provenance if block is not None else issue.get('details',{})
        choices=details.get('nearest_qualified_studies',[])
        if choices:
            choices=[{**c,'distance_km':distance_km(latitude,longitude,c['Latitude'],c['Longitude'])} for c in choices]
            st.subheader('Available respiration studies',help='The nearest qualifying studies may be far away or from older years. Using one keeps your site and dates and explicitly labels the study as a proxy. Annual dates are inferred; confirm chronology in the source study.')
            selected=st.selectbox('Choose a study',range(len(choices)),format_func=lambda i:f"{choices[i].get('Site_name') or 'Study '+str(choices[i]['Record_number'])} · {choices[i]['distance_km']:.1f} km · {choices[i]['period_start']} to {choices[i]['period_end']}",key='study_choice')
            st.button('Use study as nearby proxy',on_click=use_study,args=(choices[selected],),key='use_study')
    if key in SPATIAL_SOURCES and standalone_ready:
        search_context={'key':key,'site':(latitude,longitude),'dates':(first,last),
            'settings':{k:v for k,v in options.items() if k!='source_proxies'}}
        if st.button('Find nearby available data',key='find_nearby_data',
            help='Checks this source within 25 km and offers up to three locations with usable data. Your site and dates stay fixed. Search results are source-specific; failures caused by dates or provider outages may need a different remedy.'):
            st.session_state['nearby_result']=None
            bar=st.progress(0,text='Checking nearby source cells…')
            try:
                candidates=cached_nearby(key,latitude,longitude,first,last,signature,search_context['settings'],
                    _progress=lambda done,total:bar.progress(done/max(1,total),text=f'Checking candidate {done} of {total}…'))
                st.session_state['nearby_result']={'context':search_context,'candidates':candidates}
                signature=local_signature()
                st.session_state['context']={**context,'local_files':signature}
            except Exception as exc:
                st.session_state['nearby_result']={'context':search_context,'issue':source_issue(exc)}
            finally:
                bar.empty()
        result=st.session_state.get('nearby_result')
        if result and result['context']==search_context:
            if result.get('candidates'):
                candidates=result['candidates']
                index=st.selectbox('Nearby source locations',range(len(candidates)),key='nearby_choice',
                    format_func=lambda i:f"{candidates[i]['distance_km']:.1f} km · {candidates[i]['latitude']:.5f}, {candidates[i]['longitude']:.5f} · {candidates[i]['records']:,} records")
                st.button('Use nearby data for this source',key='use_nearby_proxy',on_click=use_proxy,args=(key,candidates[index]),
                    help='Keeps the simulation site and dates. Only this data source uses the selected nearby location, labeled as a proxy.')
            else:
                st.info(result['issue']['message'])
                issue=result['issue']
    coverage=issue.get('details',{})
    available_first,available_last=coverage.get('available_first'),coverage.get('available_last')
    if available_first and available_last and coverage.get('reason') in ('no_overlap_time','no_complete_time_window'):
        available_first,available_last=date.fromisoformat(available_first),date.fromisoformat(available_last)
        if key=='terraclimate':
            available_last=(pd.Timestamp(available_last)+pd.offsets.MonthEnd(0)).date()
        elif key=='mod16a2gf':
            available_last=min(available_last+timedelta(days=7),date(available_last.year,12,31))
        count=(last-first).days+1 if first and last else 365
        offered_first=max(available_first,available_last-timedelta(days=max(365,count)-1))
        st.button('Use available dates',key='use_available_dates',on_click=use_available_dates,
            args=(key,str(offered_first),str(available_last)),help=f'Uses {offered_first} to {available_last}; your location stays fixed.')

    st.download_button('Download source report',json.dumps({'application':application_identity,'site':{'latitude':latitude,'longitude':longitude},'start':str(start),'end':str(end),'status':status,'issues':st.session_state['data_issues'],
        'selected_proxies':options['source_proxies'],'sources':{k:b.provenance for k,b in st.session_state['loaded_blocks'].items()}},indent=2,default=str),file_name='source_report.json',mime='application/json')

if st.session_state.get('run_recovery') and ready:
    show_run_recovery(blocks,cached_nearby,signature,options)
