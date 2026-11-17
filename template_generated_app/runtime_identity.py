"""Identify the Python code and settings actually loaded by a running app."""
from collections.abc import Mapping
from hashlib import sha256
from inspect import unwrap
import json
from pathlib import Path
from types import CodeType, ModuleType


def _code_constant(value):
    """Describe immutable code constants without marshal's mutable ref flags."""
    if isinstance(value, CodeType):
        return ['code', _code_payload(value)]
    if isinstance(value, bytes):
        return ['bytes', value.hex()]
    if isinstance(value, tuple):
        return ['tuple', [_code_constant(item) for item in value]]
    if isinstance(value, frozenset):
        return ['frozenset', sorted((_code_constant(item) for item in value), key=lambda item: json.dumps(item, sort_keys=True))]
    if isinstance(value, slice):
        return ['slice', _code_constant(value.start), _code_constant(value.stop), _code_constant(value.step)]
    if isinstance(value, float):
        return ['float', value.hex()]
    if isinstance(value, complex):
        return ['complex', value.real.hex(), value.imag.hex()]
    if value is Ellipsis:
        return ['ellipsis']
    if value is None or isinstance(value, (str, int, bool)):
        return value
    return [type(value).__module__ + '.' + type(value).__qualname__, repr(value)]


def _code_payload(code: CodeType):
    # CPython can change marshal reference/interning flags as code executes.
    # Its public, unspecialized bytecode and structural fields remain stable.
    return {
        'bytecode': code.co_code.hex(),
        'constants': [_code_constant(value) for value in code.co_consts],
        'names': code.co_names, 'varnames': code.co_varnames,
        'freevars': code.co_freevars, 'cellvars': code.co_cellvars,
        'arguments': [code.co_argcount, code.co_posonlyargcount, code.co_kwonlyargcount],
        'locals': code.co_nlocals, 'stacksize': code.co_stacksize, 'flags': code.co_flags,
        'exceptiontable': code.co_exceptiontable.hex(),
        'line_table': code.co_linetable.hex(), 'first_line': code.co_firstlineno,
    }


def _loaded_fingerprint(module: ModuleType) -> str:
    """Hash loaded functions and public JSON-safe constants, without reading disk."""
    loaded = {}
    for name, value in sorted(vars(module).items()):
        candidate = unwrap(value) if callable(value) else value
        code = getattr(candidate, '__code__', None)
        if code is not None and getattr(candidate, '__module__', None) == module.__name__:
            loaded[name] = ['function', _code_payload(code)]
        elif name.isupper() and not name.startswith('_'):
            try:
                payload = json.dumps(value, sort_keys=True, allow_nan=False)
            except (TypeError, ValueError):
                continue
            loaded[name] = ['constant', payload]
    return sha256(json.dumps([module.__name__, loaded], sort_keys=True).encode()).hexdigest()


def get_application_identity(root: Path, modules: Mapping[str, ModuleType]) -> dict:
    """Return location, loaded control revision and memory fingerprints for runs.

    Pass the modules the current app imported, rather than importing a second
    copy here. File edits alone do not change a loaded module's fingerprint;
    the running process must actually load the new functions or constants.
    """
    loaded = {}
    revision = None
    for label, module in sorted(modules.items()):
        filename = getattr(module, '__file__', None)
        loaded[label] = {
            'module_name': module.__name__,
            'module_path': str(Path(filename).resolve()) if filename else None,
            'loaded_fingerprint': _loaded_fingerprint(module),
        }
        controls_revision = getattr(module, 'PLANT_CONTROLS_REVISION', None)
        if isinstance(controls_revision, str):
            revision = controls_revision
    build = sha256(json.dumps(loaded, sort_keys=True).encode()).hexdigest()
    return {
        'identity_schema': 1,
        'application_revision': revision,
        'project_directory': str(Path(root).resolve()),
        'loaded_build': build,
        'modules': loaded,
    }
