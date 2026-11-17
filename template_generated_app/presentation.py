"""Human-readable output names, units, and chart/data-block summaries."""
import pandas as pd
import streamlit as st
import re

VARIABLES = {
    'precip_mm': ('Precipitation (mm/day)', 'Rain and snowfall water equivalent from Open-Meteo. Snow storage is not resolved by this model.'),
    'et0_mm': ('Reference evaporation demand (mm/day)', 'FAO reference evapotranspiration; atmospheric demand, not actual ecosystem water loss.'),
    'temp_c': ('Air temperature (°C)', 'Daily mean air temperature. Also used as a soil-temperature proxy.'),
    'soil_water_mm': ('Soil water (mm)', 'Water stored across all modeled soil layers, expressed as depth over ground area.'),
    'aet_mm': ('Actual evapotranspiration (mm/day)', 'Soil evaporation, plant transpiration and evaporated canopy interception.'),
    'evaporation_mm': ('Soil evaporation (mm/day)', 'Water leaving the upper soil layer directly to the atmosphere.'),
    'transpiration_mm': ('Plant water use (mm/day)', 'Root uptake returned to the atmosphere through plants.'),
    'interception_mm': ('Canopy evaporation (mm/day)', 'Rain intercepted and evaporated from leaves within the day.'),
    'runoff_mm': ('Surface runoff (mm/day)', 'Rain that cannot infiltrate the upper layer.'),
    'drainage_mm': ('Deep drainage (mm/day)', 'Water leaving the bottom of the modeled profile.'),
    'water_stress': ('Water availability (0–1)', 'Fraction of potential root water demand supplied. One means unstressed.'),
    'plant_biomass_gC_m2': ('Plant carbon (g C/m²)', 'Sum of living leaf, stem and root carbon.'),
    'leaf_carbon_gC_m2': ('Leaf carbon (g C/m²)', 'Living leaf stock; used to calculate leaf area.'),
    'stem_carbon_gC_m2': ('Stem carbon (g C/m²)', 'Living stem stock.'),
    'root_carbon_gC_m2': ('Root carbon (g C/m²)', 'Living root stock; controls rooting depth.'),
    'plant_uptake_gC_m2_day': ('Net plant production (g C/m²/day)', 'Net carbon assimilated after implicit plant respiration (NPP); this is not GPP.'),
    'litter_gC_m2_day': ('Litter input (g C/m²/day)', 'Leaf/stem turnover enters surface soil; root turnover follows the root distribution.'),
    'lai': ('Leaf area index (m²/m²)', 'One-sided leaf area per ground area, derived from leaf carbon and specific leaf area.'),
    'root_depth_cm': ('Root depth (cm)', 'Maximum rooting depth, limited by the soil profile and ecosystem preset.'),
    'soil_carbon_gC_m2': ('Soil carbon (g C/m²)', 'Sum of five soil organic carbon pools across all layers.'),
    'soil_respiration_gC_m2_day': ('Microbial respiration (g C/m²/day)', 'Heterotrophic carbon release. Excludes living root respiration.'),
}
for _pool,_description in {
    'particulate':'Plant residues and other particulate organic matter.',
    'dissolved':'Dissolved organic carbon available for microbial uptake and sorption.',
    'microbial':'Living soil microbial carbon.',
    'mineral':'Organic carbon associated with mineral surfaces.',
    'aggregate':'Organic carbon held within soil aggregates.',
}.items():
    VARIABLES[f'{_pool}_carbon_gC_m2'] = (f'{_pool.capitalize()} carbon (g C/m²)',_description)


def label(key):
    depth = re.fullmatch(r'(saturation|soc|water)_(\d+(?:\.\d+)?)_(\d+(?:\.\d+)?)cm(?:_gC_m2|_mm)?',key)
    if depth:
        kind,top,bottom = depth.groups()
        return f'{top}–{bottom} cm'+(' (g C/m²)' if kind=='soc' else ' (mm)' if kind=='water' else '')
    return VARIABLES.get(key, (key.replace('_',' ').capitalize(), ''))[0]


def availability_message(key,error):
    if 'no usable records' in error and key=='srdb':
        return 'No respiration study matched this site and period. The simulation can run, but this source cannot validate it.'
    if 'no usable records' in error and key=='mod16a2gf':
        return 'No valid satellite ET windows at this point. Urban, water and low-quality pixels can have no usable values.'
    if key=='soilgrids_wcs' and 'no valid' in error:
        return 'SoilGrids has no mapped soil value at this exact point. Urban areas and water may be masked; the run can use HWSD or an explicit soil preset.'
    return 'No usable data from this source for the selected site and dates.'


def chart(frame, columns, title, explanation):
    columns = [key for key in columns if key in frame]
    if not columns:
        return
    with st.container(border=True):
        st.subheader(title, help=explanation)
        st.line_chart(frame.set_index('date')[columns].rename(columns=label), alt=explanation)


def show_block(block):
    frame = block.frame
    proxy=block.provenance.get('spatial_proxy')
    if proxy:
        site=proxy['source_site']
        st.caption(f"Nearby proxy · {proxy['distance_km']:.1f} km away · {site['latitude']:.5f}, {site['longitude']:.5f}",
            help='The simulation site stays fixed. These are data from a different location; their coordinates, distance and date window are preserved in source/run reports.')
    with st.container(horizontal=True):
        st.metric('Records', f'{len(frame):,}', border=True)
        st.metric('Resolution', block.resolution, border=True)
        if 'aet_mm' in frame:
            st.metric('Mean ET per window', f'{frame.aet_mm.mean():.1f} mm', border=True,
                      help='Mean of accepted native observation windows; partial dates and missing windows are not filled.')
        if 'precip_mm' in frame:
            st.metric('Total precipitation', f'{frame.precip_mm.sum():.0f} mm', border=True)
        if 'soc_g_kg' in frame:
            stock = (frame.soc_g_kg*frame.bulk_density_g_cm3*(frame.bottom_cm-frame.top_cm)*10*(1-frame.coarse_fragments_pct/100)).sum()
            st.metric('Profile soil carbon', f'{stock/1000:.2f} kg C/m²', border=True,
                      help='Carbon concentration × bulk density × layer thickness, adjusted for coarse fragments.')
    if {'top_cm','bottom_cm','sand_pct'}.issubset(frame):
        depth = frame.apply(lambda r:f'{r.top_cm:g}–{r.bottom_cm:g} cm',axis=1)
        texture = frame[['sand_pct','silt_pct','clay_pct']].rename(columns={'sand_pct':'Sand (%)','silt_pct':'Silt (%)','clay_pct':'Clay (%)'})
        texture.index = depth
        st.bar_chart(texture, stack=True, horizontal=True, alt='Soil texture fractions by depth')
        carbon = pd.DataFrame({'Depth':depth,'Organic carbon (g/kg)':frame.soc_g_kg})
        st.bar_chart(carbon,x='Depth',y='Organic carbon (g/kg)',horizontal=True,alt='Organic carbon concentration by soil depth')
    elif 'date' in frame:
        keys = [k for k in ('precip_mm','et0_mm','aet_mm','pet_mm') if k in frame]
        if keys:
            names = {'precip_mm':'Precipitation','et0_mm':'Reference ET','aet_mm':'Actual ET','pet_mm':'Potential ET'}
            st.line_chart(frame.set_index('date')[keys].rename(columns=lambda k:f'{names[k]} ({block.units.get(k,"mm/native window")})'),
                alt='Selected data block water variables at their native temporal resolution')
        if 'temp_c' in frame:
            st.line_chart(frame.set_index('date')[['temp_c']].rename(columns=label),alt='Daily air temperature')
    elif 'pft' in frame:
        counts = frame.pft.value_counts().rename('Species')
        st.bar_chart(counts,alt='Species counts by functional type; unresolved categories remain unresolved')
    elif 'Rh_annual' in frame:
        st.scatter_chart(frame,x='Study_midyear',y='Rh_annual',alt='Available annual heterotrophic respiration observations by study year')
    with st.expander('Records, units and source details',on_change='rerun') as records:
        if records.open:
            st.dataframe(frame,hide_index=True,alt='Source records at their original resolution')
            st.json({'units':block.units,'provenance':block.provenance})
    st.download_button('Download data CSV',frame.to_csv(index=False),file_name=block.id+'.csv',mime='text/csv',key='csv_'+block.id)
