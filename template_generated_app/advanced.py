"""Grouped parameter editors; every field shows the effective preset value."""
from pathlib import Path
from hashlib import sha256
import numpy as np
import pandas as pd
import streamlit as st
from parameters import (default_settings,PLANT_FIELDS,WATER_FIELDS,CARBON_FIELDS,plant_parameters)
from soil_profile import DEFAULT_DEPTHS,make_profile,validate_depths,resize_depths
from engine import coupling_contract

ECOSYSTEM_LABELS={'grass_c3':'C3 grassland','grass_c4':'C4 grassland','forest':'Forest','crop':'Crop'}
SPECIES_CATEGORIES={'all':'All categories','grass_c3':'C3 grassland','grass_c4':'C4 grassland',
    'forest':'Forest','tree_deciduous':'Deciduous trees','tree_evergreen':'Evergreen trees',
    'shrub':'Shrubs','unresolved':'Unresolved traits','crop':'Crop (not recorded)'}
PLANT_CONTROLS_REVISION='plant-table-v5'

@st.cache_data(max_entries=4,show_spinner=False)
def cached_species(root,signature):
    from data_blocks.traits import species_index
    return species_index(Path(root)/'data')


def reset_plant_fields():
    defaults=plant_parameters(st.session_state.get('ecosystem','grass_c3'))
    for fields in PLANT_FIELDS.values():
        for name in fields:
            st.session_state[f'parameter_vegetation_{name}']=defaults[name]


def ecosystem_changed():
    st.session_state['active_trait_species']=None
    st.session_state['trait_species']=None
    st.session_state.pop('species_source_notice',None)
    reset_plant_fields()


def apply_species(record,source_hash):
    """An explicit application updates the stable record and its numeric preset."""
    from data_blocks.traits import preset_for_pft
    mapped=preset_for_pft(record['pft'])
    st.session_state['active_trait_species']={
        'species_id':int(record['AccSpeciesID']),'species':str(record['AccSpeciesName']),
        'pft':str(record['pft']),'preset':mapped,'sha256':source_hash,
        'mapping':'Broad functional category only; numeric plant traits are not measured by this categorical archive.'}
    if mapped:
        st.session_state['ecosystem']=mapped
        reset_plant_fields()
    st.session_state.pop('species_source_notice',None)
    st.session_state['_species_selection_changed']=True


def clear_species():
    st.session_state['active_trait_species']=None
    st.session_state['trait_species']=None
    st.session_state['_species_selection_changed']=True


def selected_species_record(matches,rows):
    """Native table positions refer to the supplied dataframe, even after sorting."""
    if not rows or isinstance(rows[0],bool) or not isinstance(rows[0],(int,np.integer)):
        return None
    position=int(rows[0])
    return matches.iloc[position].to_dict() if 0<=position<len(matches) else None


@st.fragment(key='species_search')
def render_species_search(root):
    from data_blocks.traits import species_index_fingerprint,search_species,preset_for_pft,PFT_LABELS,species_filter_categories
    search_col,filter_col,sort_col=st.columns([3,2,2])
    query=search_col.text_input('Search plant species',key='species_query',type='search',live='400ms',
        placeholder='Scientific name or species ID…',
        help='Use two or more characters for names, or enter an exact species ID.')
    # Older browser sessions may retain a sort value from the previous controls.
    if st.session_state.get('species_sort') not in (None,'name_asc','name_desc','group_name'):
        st.session_state['species_sort']='name_asc'
    category=filter_col.selectbox('Filter group / ecosystem',list(SPECIES_CATEGORIES),key='species_category',
        format_func=lambda value:SPECIES_CATEGORIES[value],
        help='TRY functional categories. Forest combines both tree groups; crop use and habitat are not recorded.')
    sort=sort_col.selectbox('Sort / group',['name_asc','name_desc','group_name'],key='species_sort',
        format_func=lambda value:{'name_asc':'Name A–Z','name_desc':'Name Z–A','group_name':'Group, then name'}[value])
    try:
        signature=species_index_fingerprint(root/'data')
        species,info=cached_species(str(root),signature)
        active=st.session_state.get('active_trait_species')
        if active:
            current=species[species.AccSpeciesID==active['species_id']]
            changed=current.empty or active['sha256']!=info['source_sha256']
            if not current.empty:
                row=current.iloc[0]
                changed=changed or str(row.AccSpeciesName)!=active['species'] or str(row.pft)!=active['pft']
            if changed:
                clear_species()
                st.session_state['species_source_notice']='The species source changed. Current model values are preserved; select and apply a plant again.'
                st.rerun()
        matches,total=search_species(species,query or '',limit=None,
            categories=species_filter_categories(category),sort_by='name',
            descending=sort=='name_desc',group_by=sort=='group_name',browse=True)
        matches=matches.reset_index(drop=True)
        if sort=='group_name':
            matches=matches.assign(_group=matches.pft.map(PFT_LABELS),_name=matches.AccSpeciesName.str.casefold()).sort_values(
                ['_group','_name','AccSpeciesID'],kind='stable').drop(columns=['_group','_name']).reset_index(drop=True)
        table=pd.DataFrame({'Species':matches.AccSpeciesName,'Group':matches.pft.map(PFT_LABELS),
            'Model preset':matches.pft.map(preset_for_pft).map(ECOSYSTEM_LABELS).fillna('No automatic preset'),
            'ID':matches.AccSpeciesID})
        fingerprint=sha256(repr((query,category,sort,signature,info['source_sha256'])).encode())
        fingerprint.update(matches.AccSpeciesID.to_numpy(dtype=np.int64).tobytes())
        table_key='species_table_'+fingerprint.hexdigest()[:16]
        event=st.dataframe(table,key=table_key,hide_index=True,height=320,
            on_select='rerun',selection_mode='single-row',lazy=False,
            column_config={'Species':st.column_config.TextColumn(width='large'),
                           'ID':st.column_config.NumberColumn(format='%d')},
            alt='Plant species with sortable name, functional group, model preset and source ID')
        st.caption(f'{total:,} plants · Click a column to sort. Select a row, then use it below.')
        if category=='crop':
            st.info('TRY does not record crop use. Choose Crop in Ecosystem to use its editable preset; species are not inferred to be crops.')
        elif (query or '').strip() and len((query or '').strip())<2 and not (query or '').strip().isdigit():
            st.caption('Type at least two characters or an exact species ID to search.')
        rows=event.selection.rows
        candidate=selected_species_record(matches,rows)
        st.button('Use selected plant',key='use_selected_plant',type='primary',disabled=candidate is None,
            on_click=apply_species,args=(candidate,info['source_sha256']))
        if candidate and preset_for_pft(candidate['pft']) is None:
            st.caption('This species has no numeric preset. Applying it keeps the current ecosystem parameters.')
    except Exception as exc:
        if st.session_state.get('active_trait_species'):
            clear_species()
            st.rerun()
        st.info('The TRY species source is unavailable. Ecosystem presets remain usable.')
        with st.expander('Trait source details'):
            st.write(str(exc))
    if st.session_state.get('species_source_notice'):
        st.info(st.session_state['species_source_notice'])
    active=st.session_state.get('active_trait_species')
    if active:
        st.caption('Applied plant: '+active['species']+' · '+SPECIES_CATEGORIES.get(active['pft'],active['pft']))
        st.button('Clear species',key='clear_trait_species',on_click=clear_species)
    if st.session_state.pop('_species_selection_changed',False):
        st.rerun()


def _format_depths(depths):
    return ', '.join(f'{value:g}' for value in depths)


def _reset_soil_editor():
    for key in list(st.session_state):
        if key.startswith('soil_editor_'):
            del st.session_state[key]
    st.session_state['edit_soil']=False


def depth_total_changed():
    try:
        depths=resize_depths([float(value.strip()) for value in st.session_state['layer_depths'].split(',')],
            st.session_state['soil_depth_cm'])
        st.session_state['layer_depths']=_format_depths(depths)
        st.session_state.pop('depth_control_error',None)
        _reset_soil_editor()
    except (ValueError,TypeError) as exc:
        st.session_state['depth_control_error']=str(exc)


def depth_boundaries_changed():
    try:
        depths=validate_depths([float(value.strip()) for value in st.session_state['layer_depths'].split(',')])
        st.session_state['soil_depth_cm']=float(depths[-1])
        st.session_state.pop('depth_control_error',None)
        _reset_soil_editor()
    except (ValueError,TypeError) as exc:
        st.session_state['depth_control_error']=str(exc)


def reset_parameters():
    for key in list(st.session_state):
        if key.startswith('parameter_') or key.startswith('soil_editor_') or key=='particle_density':
            del st.session_state[key]
    st.session_state['edit_soil']=False
    if 'ecosystem' in st.session_state:
        reset_plant_fields()


def numeric_fields(fields,defaults,module):
    values={}
    columns=st.columns(2)
    for i,(name,(label,low,high,step,help_text)) in enumerate(fields.items()):
        default=defaults[name]
        key=f'parameter_{module}_{name}'
        if isinstance(low,int) and isinstance(high,int):
            if key not in st.session_state:
                st.session_state[key]=int(default)
            value=columns[i%2].number_input(label,min_value=low,max_value=high,step=int(step),
                key=key,help=help_text,persist_state='session')
        else:
            if key not in st.session_state:
                st.session_state[key]=float(default)
            value=columns[i%2].number_input(label,min_value=float(low),max_value=float(high),step=float(step),
                format='%.5g',key=key,help=help_text,persist_state='session')
        values[name]=value
    return values


def render_advanced(cfg,blocks,root):
    params=default_settings(); options={}; errors=[]; trait_selection=None
    saved_depths=validate_depths(cfg.get('layer_depths_cm',DEFAULT_DEPTHS))
    if 'layer_depths' not in st.session_state:
        st.session_state['layer_depths']=_format_depths(saved_depths)
    if 'soil_depth_cm' not in st.session_state:
        st.session_state['soil_depth_cm']=float(saved_depths[-1])
    models=set(cfg['models'])
    st.button('Restore active preset values',on_click=reset_parameters,key='reset_parameters',
        help='Resets numerical parameters and manual hydraulic overrides. Preserves the selected site, dates, ecosystem, species and layer boundaries.')
    if 'vegetation' in models:
        with st.expander('Plants'):
            preset=st.selectbox('Ecosystem',list(ECOSYSTEM_LABELS),key='ecosystem',
                format_func=lambda key:ECOSYSTEM_LABELS[key],on_change=ecosystem_changed,
                help='Choosing an ecosystem clears the selected species and loads that ecosystem’s plant defaults.')
            if 'try_categorical' in blocks:
                render_species_search(root)
                trait_selection=st.session_state.get('active_trait_species')
            preset=st.session_state.get('ecosystem',preset)
            st.caption('Active plant preset: '+ECOSYSTEM_LABELS[preset])
            st.caption('Species select a category, not measured numeric rates. Same-category species share defaults; both tree types use Forest. Press Run to update the simulation.')
            if trait_selection and trait_selection['preset'] is None:
                st.info('This species has no supported unambiguous category; the selected ecosystem and its editable parameters remain active.')
            defaults=plant_parameters(preset)
            groups=st.tabs(list(PLANT_FIELDS))
            values={}
            for group,(title,fields) in zip(groups,PLANT_FIELDS.items()):
                with group:
                    values.update(numeric_fields(fields,defaults,'vegetation'))
            params['vegetation']={'preset':preset,**values}
            try:
                plant_parameters(preset,values)
            except ValueError as exc:
                errors.append(str(exc))
    if 'water_balance' in models:
        with st.expander('Water'):
            inactive={'light_extinction'}
            if 'vegetation' not in models:
                inactive|={'interception_mm_per_lai','transpiration_coefficient'}
            fields={k:v for k,v in WATER_FIELDS.items() if k not in inactive}
            params['water_balance'].update(numeric_fields(fields,params['water_balance'],'water_balance'))
    if 'soil_carbon' in models:
        with st.expander('Soil carbon'):
            rates,response=st.tabs(['Transfer rates','Temperature and moisture'])
            with rates:
                params['soil_carbon'].update(numeric_fields({k:v for k,v in CARBON_FIELDS.items() if k.startswith('k_')},params['soil_carbon'],'soil_carbon'))
            with response:
                params['soil_carbon'].update(numeric_fields({k:v for k,v in CARBON_FIELDS.items() if not k.startswith('k_')},params['soil_carbon'],'soil_carbon'))
    with st.expander('Initial conditions'):
        existing=st.session_state.get('parameter_soil_source')
        site=(st.session_state.get('latitude'),st.session_state.get('longitude'))
        if 'soil_carbon' in models and existing is not None and st.session_state.get('parameter_soil_site')==site:
            try:
                edges=[float(x) for x in st.session_state.get('layer_depths',','.join(map(str,DEFAULT_DEPTHS))).split(',')]
                source_profile=make_profile(edges,existing,initial=params['initial'])
                st.metric('Source initial soil carbon',f'{source_profile.initial_soc_gC_m2.sum():,.0f} g C/m²',
                    help='Integrated source SOC over the configured soil depth; this is used instead of the fallback stock below.')
            except (ValueError,TypeError):
                pass
        fields={}
        if 'water_balance' in models:
            fields['water_fraction_fc']=('Initial water / field capacity',0.,1.,.05,'One starts at each layer’s estimated field capacity; zero starts dry.')
        if 'vegetation' in models:
            for name in ('leaf','stem','root'):
                fields[name+'_carbon']=(f'Initial {name} carbon (g C/m²)',0.,100000.,10.,'Living carbon stock at the first simulated day.')
        if 'soil_carbon' in models:
            fields.update(fallback_soc=('Fallback soil carbon (g C/m²)',0.,100000.,100.,'Configured total over the whole modeled profile, even when depth changes. Used only if source SOC is unavailable.'),
                soc_depth_scale_cm=('Fallback carbon depth scale (cm)',.1,1000.,5.,'Exponential e-folding depth for distributing the fallback carbon stock.'))
            for name in ('particulate','dissolved','microbial','mineral','aggregate'):
                fields[name+'_fraction']=(f'Initial {name} carbon fraction',0.,1.,.01,'Fraction of each layer’s total initial SOC; all five fractions must sum to one.')
        params['initial'].update(numeric_fields(fields,params['initial'],'initial'))
        if 'soil_carbon' in models:
            total=sum(params['initial'][p+'_fraction'] for p in ('particulate','dissolved','microbial','mineral','aggregate'))
            if not np.isclose(total,1.,atol=1e-8,rtol=0):
                errors.append(f'Initial soil carbon fractions sum to {total:.8g}; they must sum to 1.')
    soil_overrides=None
    with st.expander('Soil profile'):
        st.number_input('Total soil depth (cm)',min_value=0.,max_value=1000.,step=10.,key='soil_depth_cm',
            on_change=depth_total_changed,
            help='A positive depth up to 1000 cm. Changing total depth scales the existing layer boundaries and starts a new initialized domain on Run.')
        text=st.text_input('Layer boundaries (cm)',key='layer_depths',on_change=depth_boundaries_changed,
            help='Start at zero and increase through 1–30 layers. Editing boundaries synchronizes total depth and clears manual hydraulic edits.')
        if st.session_state.get('depth_control_error'):
            errors.append(st.session_state['depth_control_error'])
        texture=st.selectbox('Fallback soil texture',['loam','sand','clay','silt_loam'],key='soil_texture',
            help='An assumed soil profile. Used only after both mapped sources fail and you explicitly choose Use assumed soil preset.')
        if 'particle_density' not in st.session_state:
            st.session_state['particle_density']=float(params['initial']['particle_density'])
        params['initial']['particle_density']=st.number_input('Mineral particle density (g/cm³)',min_value=1.,max_value=4.,step=.05,key='particle_density',
            help='Porosity = 1 − bulk density / particle density; an assumed mineral-particle value.')
        try:
            depths=validate_depths([float(x.strip()) for x in text.split(',')])
            site=(st.session_state.get('latitude'),st.session_state.get('longitude'))
            source=st.session_state.get('parameter_soil_source') if st.session_state.get('parameter_soil_site')==site else None
            if source is not None and float(source.bottom_cm.max())<depths[-1]:
                st.info('The loaded soil source does not cover this depth. Run will check complete source coverage before offering an explicit texture preset.')
                source=None
            preview=make_profile(depths,source,texture,params['initial'])
            if source is None:
                st.caption('Assumed preset preview. Run will try mapped soil data first; this preview is not a downloaded soil profile.')
            columns=['top_cm','bottom_cm','porosity','field_capacity','wilting_point','ksat_mm_day','b','air_entry_mm']
            manual=st.toggle('Edit soil hydraulic properties',key='edit_soil',help='Initialize from the last loaded soil profile or fallback preset. Edited values override source-derived hydraulics and are recorded in run details.')
            if manual:
                shape=tuple(depths)
                if st.session_state.get('soil_editor_shape')!=shape:
                    st.session_state['soil_editor_base']=preview[columns]
                    st.session_state['soil_editor_shape']=shape
                edited=st.data_editor(st.session_state['soil_editor_base'],hide_index=True,disabled=['top_cm','bottom_cm'],
                    key='soil_editor_'+str(shape),alt='Editable soil parameters by fixed layer',column_config={
                        'top_cm':st.column_config.NumberColumn('Top (cm)'), 'bottom_cm':st.column_config.NumberColumn('Bottom (cm)'),
                        'porosity':st.column_config.NumberColumn('Porosity',min_value=.1,max_value=.8),
                        'field_capacity':st.column_config.NumberColumn('FC / saturation',min_value=.01,max_value=.99),
                        'wilting_point':st.column_config.NumberColumn('Wilt / saturation',min_value=0.,max_value=.98),
                        'ksat_mm_day':st.column_config.NumberColumn('Ksat (mm/day)',min_value=0.),
                        'b':st.column_config.NumberColumn('Retention exponent b',min_value=.1,max_value=20.),
                        'air_entry_mm':st.column_config.NumberColumn('Air-entry suction (mm)',min_value=.01),
                    })
                soil_overrides=edited.to_dict('records')
                make_profile(depths,source,texture,params['initial'],soil_overrides)
            else:
                st.dataframe(preview[columns],hide_index=True,alt='Current source-derived or fallback soil hydraulic defaults')
        except (ValueError,TypeError) as exc:
            depths=DEFAULT_DEPTHS; errors.append(str(exc))
    with st.expander('Data access'):
        if 'soilgrids_wcs' in blocks:
            options['soilgrids_use_rest']=st.toggle('Automatically try SoilGrids REST',value=True,key='soilgrids_auto_rest',
                help='Uses the batched REST API when it returns a valid profile, with a 20-second wait limit. During an outage, map services take over and REST is checked again after five minutes. Turn this off to use map services only.')
            options['soilgrids_map_recovery']=True
        if 'hwsd' in blocks:
            unit=st.number_input('HWSD mapping-unit override',min_value=1,value=None,key='hwsd_unit_id',help='Normally selected by the geographic map; leave unset for automatic lookup.')
            if unit is not None:
                options['hwsd_unit_id']=int(unit)
            options['hwsd_download_map']=st.toggle('Allow first HWSD map download',value=False,key='hwsd_download_map',help='Existing maps are reused automatically. Turn this on to download the large official map if none is available; the first download can take several minutes. Later runs reuse it.')
        if 'srdb' in blocks:
            options['srdb_radius_km']=st.number_input('Respiration site radius (km)',min_value=.01,max_value=100.,value=1.,key='srdb_radius',help='Nearby studies still need matching years, complete records and ecological/site equivalence.')
        if 'terraclimate' in blocks:
            endpoint=st.text_input('TerraClimate endpoint override',help='Normally discovered automatically; {variable} is replaced by aet or pet.')
            if endpoint.strip():
                options['terraclimate_url']=endpoint.strip()
        if 'mod16a2gf' in blocks:
            options['modis_quality']=st.selectbox('Satellite ET quality',['recommended','strict'],
                format_func=lambda key:'Main algorithm (recommended)' if key=='recommended' else 'Best algorithm and clear sky',
                help='Gap-filled MODIS inherits cloud flags from LAI inputs. Recommended accepts MODLAND-good main-algorithm SCF 0/1 with functional detectors; strict additionally requires best/clear. Fill and nonvegetated pixels are always excluded.')
        st.caption('Local data paths and formats: data/README.md.')
    with st.expander('Model connections and equations'):
        links=coupling_contract(models)
        if links:
            st.dataframe(links,hide_index=True,alt='Active data exchanges between the selected model components')
        if 'water_balance' not in models:
            st.caption('Without water: plants are unstressed and soil-carbon moisture is held at field capacity.')
        if 'vegetation' not in models:
            st.caption('Without plants: bare-soil evaporation, no transpiration, no fresh litter input.')
        if 'soil_carbon' not in models and 'vegetation' in models:
            st.caption('Without soil carbon: litter leaves the modeled domain and is reported as an export.')
        st.caption('Soil carbon does not dynamically alter soil porosity in this version. See SCIENCE.md and FORMULA_AUDIT.md for formulas, references and explicit approximations.')
    audit=root/'FORMULA_AUDIT.md'
    if audit.is_file():
        st.download_button('Download formula audit',audit.read_text(),file_name='FORMULA_AUDIT.md',mime='text/markdown')
    with st.expander('Optional model changes'):
        st.markdown(cfg.get('request') or 'No custom changes requested.')
        st.code('Read AGENT_BRIEF.md, SCIENCE.md and FORMULA_AUDIT.md. Implement the requested changes, preserve water and carbon balances, and run the included tests.',language=None)
    for message in errors:
        st.error(message)
    return params,options,depths,texture,soil_overrides,trait_selection,bool(errors)
