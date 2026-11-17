"""Separate provider failures, missing matches, and deliberately unused backups."""
import requests
from data_blocks import NoMatchingData


def source_issue(exc):
    raw=str(exc)
    cause=exc.__cause__ or exc
    if isinstance(exc,NoMatchingData):
        status='No matching records'
    elif isinstance(cause,requests.RequestException):
        status='Connection failed'
    elif isinstance(exc,FileNotFoundError):
        status='Missing local files'
    else:
        status='Data check failed'
    return {'status':status,'message':raw,'details':getattr(exc,'details',{}),'exception':type(exc).__name__}


def source_status(key,spec,loaded,errors,issues,soil_source=None):
    if key in issues:
        return issues[key]['status']
    if key in errors:
        return 'Could not load'
    if key in loaded:
        if loaded[key].provenance.get('spatial_proxy'):
            distance=loaded[key].provenance['spatial_proxy']['distance_km']
            return ('Used as soil proxy' if key==soil_source else 'Loaded nearby proxy')+f' ({distance:.1f} km)'
        return 'Used as soil input' if key==soil_source else 'Loaded'
    if spec.get('role')=='fallback' and spec.get('fallback_for') in loaded:
        return 'Backup not needed'
    return 'Not loaded'
