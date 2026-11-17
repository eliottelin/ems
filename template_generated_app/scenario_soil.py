"""Resolve scenario soil with the same explicit assumptions as historical runs."""
from soil_profile import make_profile


def resolve_scenario_soil(loaded_blocks, depths, texture, initial,
                          soil_overrides=None, preference='soilgrids',
                          assumed_accepted=False, requires_soil=True):
    """Reuse a valid mapped profile; never silently initialize assumed soil.

    Static soil is independent of the climate-forcing dates. The caller must
    supply blocks for the current simulation site, including any labeled proxy.
    """
    if preference not in ('soilgrids', 'hwsd'):
        raise ValueError('Choose SoilGrids or HWSD as the preferred soil source')
    order = ('hwsd', 'soilgrids_wcs') if preference == 'hwsd' else ('soilgrids_wcs', 'hwsd')
    failures = []
    for key in order:
        block = loaded_blocks.get(key)
        if block is None:
            continue
        try:
            make_profile(depths, block.frame, texture, initial, soil_overrides)
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            failures.append(f'{"HWSD" if key == "hwsd" else "SoilGrids"}: {exc}')
            continue
        return {'ready': True, 'soil': block.frame.copy(deep=True),
                'soil_source': key, 'message': 'Using the loaded mapped soil profile.'}
    if not requires_soil:
        try:
            make_profile(depths, None, texture, initial, soil_overrides)
        except (ValueError, TypeError, KeyError) as exc:
            return {'ready': False, 'soil': None, 'soil_source': None,
                    'message': f'The configured rooting domain is invalid: {exc}'}
        return {'ready': True, 'soil': None,
                'soil_source': 'not required by selected models',
                'message': ('The selected models do not require mapped soil. The configured '
                            'rooting domain is a model setting, not a site soil measurement.')}
    if assumed_accepted:
        try:
            make_profile(depths, None, texture, initial, soil_overrides)
        except (ValueError, TypeError, KeyError) as exc:
            return {'ready': False, 'soil': None, 'soil_source': None,
                    'message': f'The accepted soil assumptions are invalid: {exc}'}
        return {'ready': True, 'soil': None,
                'soil_source': 'explicit texture preset fallback',
                'message': 'Using the soil assumptions explicitly accepted for this site.'}
    message = ('Load a complete SoilGrids or HWSD profile in Data before running this scenario. '
               'If both sources fail, explicitly accept assumed soil in a model run first.')
    if failures:
        message = ' '.join(failures) + ' ' + message
    return {'ready': False, 'soil': None, 'soil_source': None, 'message': message}
