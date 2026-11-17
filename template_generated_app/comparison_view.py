"""Explain comparison coverage and display native windows without interpolation."""
import pandas as pd
import streamlit as st
from data_blocks.validation import compare,comparison_summary,comparison_audit

RESPIRATION_DEPTH_NOTE = ('Matching annual respiration dates does not establish matching depth support. '
                          'Review the study methods before comparing respiration with the modeled soil depth.')


def collect_comparisons(simulation,loaded):
    frames=[];audits=[]
    for key,block in loaded.items():
        if key not in ('srdb','terraclimate','mod16a2gf'):
            continue
        frame=compare(simulation,block)
        audit=comparison_audit(simulation,block)
        if key=='srdb':
            depth=simulation.attrs.get('profile_depth_cm')
            if not frame.empty:
                frame=frame.assign(modeled_depth_cm=depth,depth_support=RESPIRATION_DEPTH_NOTE)
            audit.update(modeled_depth_cm=depth,depth_support=RESPIRATION_DEPTH_NOTE)
        if not frame.empty:
            frames.append(frame)
        audits.append(audit)
    return (pd.concat(frames,ignore_index=True) if frames else pd.DataFrame()),pd.DataFrame(audits)


def show_comparisons(simulation,loaded,issues,blocks):
    comparisons,audit=collect_comparisons(simulation,loaded)
    if 'srdb' in loaded:
        depth=simulation.attrs.get('profile_depth_cm')
        prefix=f'Modeled soil depth: {depth:g} cm. ' if depth is not None else ''
        st.caption(prefix+RESPIRATION_DEPTH_NOTE)
    if comparisons.empty:
        st.info('No matching complete observation windows. Observational accuracy has not been established.',icon=':material/info:')
        if not audit.empty:
            st.dataframe(audit[['Source','Source records','Matched windows','Incomplete simulation windows','Excluded by study quality/coverage']],
                hide_index=True,alt='Why loaded comparison records did not match complete simulation windows')
        for key,issue in issues.items():
            if key in ('srdb','terraclimate','mod16a2gf'):
                st.caption(blocks[key]['label']+': '+issue['message'])
        st.caption('Use Data to inspect source coverage or select an actual respiration study site and year.')
    else:
        st.success(f'Compared {len(comparisons):,} complete windows')
        summary=comparison_summary(comparisons)
        summary['Source']=summary.Source.map(lambda key:blocks[key]['label'])
        st.dataframe(summary,hide_index=True,alt='Window counts, bias, RMSE, MAE and R-squared; undefined R-squared remains blank')
        for source,group in comparisons.groupby('block'):
            with st.container(border=True):
                st.subheader(blocks[source]['label'],help='SRDB is field respiration with site-equivalence checks. TerraClimate and MODIS are gridded product comparisons, not independent field validation.')
                st.caption(group.evidence.iloc[0])
                mode=st.segmented_control('Quantity',['Window totals','Mean daily rate'],default='Window totals',key='compare_quantity_'+source)
                columns=['modeled','observed'] if mode=='Window totals' else ['modeled_daily_mean','observed_daily_mean']
                unit=group.units.iloc[0] if mode=='Window totals' else ('g C/m²/day' if source=='srdb' else 'mm/day')
                plot=group.copy()
                plot['Window']=plot.start+' to '+plot.end+' · '+plot.record
                plot=plot.set_index('Window')[columns]
                plot.columns=[f'Model ({unit})',f'Source ({unit})']
                st.bar_chart(plot,stack=False,alt='Model and source for each actual complete observation window; even a single window is visible')
                st.caption(f'{len(group):,} windows · {group.start.min()} to {group.end.max()} · {unit}',
                    help='Mean rates divide each native window total by its actual day count. They are not invented daily observations. Gaps remain gaps.')
        with st.expander('Comparison records and coverage'):
            st.dataframe(comparisons,hide_index=True,alt='Every matched source window, model value, source value and temporal support')
            st.dataframe(audit,hide_index=True,alt='Complete and excluded observation windows by source')
    return comparisons,audit
