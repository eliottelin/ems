"""Reuse static soil profiles across run dates without caching stale local inputs."""
from copy import deepcopy
from contextvars import copy_context
from pathlib import Path
from hashlib import sha256
import json
import math
import os
from tempfile import NamedTemporaryFile
from time import time

import pandas as pd

import streamlit as st

import data_blocks
from data_blocks import DataBlock, DataRequest

SOIL_CACHE_POLICY = 'rest-fast-v3'
CACHE_SCHEMA = 1
CACHE_TTL_SECONDS = 30 * 86400
CACHE_MAX_ENTRIES = 64


def soil_cache_settings(key, options):
    """Only settings that can change the selected soil profile belong in its key."""
    if key == 'soilgrids_wcs':
        return {}
    if key != 'hwsd':
        raise ValueError('Static soil caching only supports SoilGrids and HWSD')
    settings = {name: options.get(name, default) for name, default in (
        ('hwsd_sequence', 1), ('hwsd_reuse_existing', True), ('hwsd_download_map', False))}
    if options.get('hwsd_unit_id') is not None:
        settings['hwsd_unit_id'] = options['hwsd_unit_id']
    if 'hwsd_table' in options:
        settings['hwsd_table'] = str(options['hwsd_table'])
    return settings


def _file_stamp(path):
    path = Path(path).resolve()
    try:
        status = path.stat()
        return str(path), status.st_mtime_ns, status.st_size
    except OSError:
        return str(path), None, None


def soil_file_signature(key, request, local_signature, settings):
    if key == 'soilgrids_wcs':
        return ()
    # Ignore unrelated weather/traits/downloads; never read a multi-GB map to hash it.
    relevant = tuple(sorted((Path(item[0]).as_posix().removeprefix('data/'), *item[1:])
                            for item in local_signature if (
        Path(item[0]).name in ('HWSD2.mdb', 'HWSD2.bil', 'HWSD2.hdr')
        or Path(item[0]).as_posix().removeprefix('data/') in ('hwsd/HWSD2_LAYERS.csv',
                                      'hwsd/HWSD2_LAYERS.csv.gz', 'hwsd/source.json'))))
    external = []
    if 'hwsd_table' in settings:
        external.append(_file_stamp(settings['hwsd_table']))
    if settings.get('hwsd_unit_id') is None:
        # Reused maps can live outside data/ and therefore outside local_signature.
        from data_blocks.soil import find_existing_hwsd_map
        pair = find_existing_hwsd_map(request.data_dir, settings['hwsd_reuse_existing'])
        if pair:
            external.extend(_file_stamp(path) for path in pair)
    return str(request.data_dir.resolve()), relevant, tuple(external)


def soil_local_signature(data_dir):
    """Use the same local HWSD identity for candidate checks and chosen profiles."""
    data_dir = Path(data_dir)
    folder = data_dir / 'hwsd'
    if not folder.exists():
        return ()
    return tuple((str(path.relative_to(data_dir)), path.stat().st_mtime_ns,
                  path.stat().st_size) for path in folder.rglob('*') if path.is_file())


def _canonical(value):
    def encode(item):
        if isinstance(item, Path):
            return str(item)
        if hasattr(item, 'isoformat'):
            return item.isoformat()
        if hasattr(item, 'item'):
            return item.item()
        raise TypeError(f'Unsupported soil-cache value: {type(item).__name__}')
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False,
                      default=encode)


def _cache_identity(key, latitude, longitude, signature, settings, source_policy, namespace):
    return json.loads(_canonical({'schema': CACHE_SCHEMA, 'source': key,
        'site': [latitude, longitude], 'local_files': signature, 'settings': settings,
        'policy': source_policy, 'namespace': namespace}))


def _cache_path(identity):
    folder = Path(identity['namespace']).parent / '.ems-cache' / 'soil-profiles-v1'
    return folder / (sha256(_canonical(identity).encode()).hexdigest() + '.json')


def _valid_block(block, key):
    from data_blocks.soil import validate_profile
    if block.id != key:
        raise ValueError('Cached soil source does not match its requested source')
    validate_profile(block.frame)
    if not isinstance(block.units, dict) or not isinstance(block.provenance, dict):
        raise ValueError('Cached soil metadata is invalid')
    return block


def _read_disk(identity):
    path = _cache_path(identity)
    try:
        document = json.loads(path.read_text())
        created = document['created_at']
        if (document['identity'] != identity or not isinstance(created, (float, int))
                or not math.isfinite(created) or not 0 <= time()-created < CACHE_TTL_SECONDS):
            return None
        content = document['block']
        if document['checksum'] != sha256(_canonical(content).encode()).hexdigest():
            return None
        frame = pd.DataFrame(content['frame']['data'], columns=content['frame']['columns'])
        frame.attrs = content.get('frame_attrs', {})
        block = DataBlock(content['id'], frame, content['resolution'],
                          content['units'], content['provenance'])
        return _valid_block(block, identity['source']), float(created)
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        # A damaged or stale cache is replaceable; never reinterpret it as soil.
        return None


def _write_disk(identity, block, created):
    """Publish a complete JSON entry atomically; a read-only disk is nonfatal."""
    path = _cache_path(identity)
    temporary = None
    try:
        content = {'id': block.id, 'frame': block.frame.to_dict(orient='split'),
            'frame_attrs': block.frame.attrs, 'resolution': block.resolution,
            'units': block.units, 'provenance': block.provenance}
        document = {'identity': identity, 'created_at': created, 'block': content,
            'checksum': sha256(_canonical(content).encode()).hexdigest()}
        encoded = _canonical(document)
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(mode='w', dir=path.parent, suffix='.tmp',
                                 encoding='utf-8', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        # Static profiles are small, but retain the same practical entry bound
        # as the memory cache, including entries for old source policies.
        entries = sorted(path.parent.glob('*.json'), key=lambda item: item.stat().st_mtime,
                         reverse=True)
        for old in entries[CACHE_MAX_ENTRIES:]:
            old.unlink(missing_ok=True)
    except (OSError, ValueError, TypeError):
        pass
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


@st.cache_data(ttl=CACHE_TTL_SECONDS, max_entries=CACHE_MAX_ENTRIES, show_spinner=False)
def _cached_soil(key, latitude, longitude, signature, settings, source_policy, namespace,
                 _request, _activity, _progress=None, _source_progress=None):
    identity = _cache_identity(key, latitude, longitude, signature, settings, source_policy, namespace)
    persisted = _read_disk(identity)
    if persisted is not None:
        _activity.append('disk')
        return persisted
    # Dates describe the calling run, but these datasets are static at a site.
    # Automatic REST recovery, manual retry and time budgets affect retrieval,
    # not the identity of a complete checked SoilGrids mean profile. Preserve
    # valid cached map profiles when REST becomes available again.
    runtime = {name: _request.options[name] for name in ('soilgrids_use_rest', 'soilgrids_map_recovery', 'soilgrids_retry', '_soil_deadline')
               if name in _request.options}
    request = DataRequest(latitude, longitude, _request.start, _request.end,
                          _request.data_dir, {**settings, **runtime, '_progress': _progress,
                                              '_source_progress': _source_progress})
    block = _valid_block(data_blocks.load_block(key, request), key)
    created = time()
    _write_disk(identity, block, created)
    _activity.append('source')
    return block, created


def load_cached_soil(key, request: DataRequest, local_signature=(), source_policy=SOIL_CACHE_POLICY,
                     _progress=None, _source_progress=None) -> DataBlock:
    """Reuse checked static profiles across dates, sessions and process restarts.

    JSON entries expire by their original retrieval time, even after a disk hit
    populates Streamlit's memory cache. Invalid entries trigger a fresh retrieval.
    """
    settings = soil_cache_settings(key, request.options)
    signature = soil_file_signature(key, request, local_signature, settings)
    namespace = str(request.data_dir.resolve())
    caller_context = copy_context()
    activity = []

    def notify_in_caller(callback):
        if callback is None:
            return None

        def notify(*args, **kwargs):
            # Cache message capture is context-local. Preserve the caller's UI
            # context without recording its progress elements in the data cache.
            return caller_context.copy().run(callback, *args, **kwargs)
        return notify

    arguments = (key, request.latitude, request.longitude, signature,
                 settings, source_policy, namespace)
    callbacks = {'_request': request, '_activity': activity,
                 '_progress': notify_in_caller(_progress),
                 '_source_progress': notify_in_caller(_source_progress)}
    block, created = _cached_soil(*arguments, **callbacks)
    if not 0 <= time()-created < CACHE_TTL_SECONDS:
        # Disk hits must not extend freshness by another full memory-cache TTL.
        _cached_soil.clear(*arguments)
        activity.clear()
        block, created = _cached_soil(*arguments, **callbacks)
    block = deepcopy(block)
    storage = activity[-1] if activity else 'memory'
    block.provenance['cache'] = {'hit': storage != 'source', 'storage': storage,
        'age_seconds': max(0., time()-created), 'expires_after_seconds': CACHE_TTL_SECONDS}
    block.provenance['request'] = {
        'latitude': request.latitude, 'longitude': request.longitude,
        'start': str(request.start), 'end': str(request.end)}
    return block
