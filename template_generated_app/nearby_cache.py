"""Cache nearby candidates without replaying progress into an old page layout."""
from contextvars import copy_context
from pathlib import Path

import streamlit as st

from data_blocks import DataRequest


@st.cache_data(ttl=86400, max_entries=16, show_spinner=False)
def _cached_nearby(key, latitude, longitude, first, last, data_dir, signature,
                   settings, _search, _progress=None):
    request = DataRequest(latitude, longitude, first, last, Path(data_dir), settings)
    return _search(key, request, progress=_progress)


def load_cached_nearby(key, request: DataRequest, signature=(), _progress=None,
                       _search=None):
    """Reuse search data; update caller-owned UI only during a real search.

    Capture the caller before entering the outer cache. Nested soil caches must
    not record these updates either, because their target belongs to this run's
    page or recovery dialog and may no longer exist on a later cache hit.
    """
    if _search is None:
        from nearby_data import nearby_data_options
        _search = nearby_data_options
    caller_context = copy_context()

    def progress(done, total):
        if _progress is not None:
            return caller_context.copy().run(_progress, done, total)

    settings = {name: value for name, value in request.options.items()
                if name not in ('_progress', '_source_progress')}
    return _cached_nearby(key, request.latitude, request.longitude,
        request.start, request.end, str(request.data_dir.resolve()), signature,
        settings, _search=_search, _progress=progress if _progress else None)
