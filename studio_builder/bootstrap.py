"""Refresh builder helpers together when their source or exported template changes."""
from hashlib import sha256
from importlib import import_module, invalidate_caches, reload
from pathlib import Path
from threading import RLock

PACKAGE_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = PACKAGE_DIR.parent / "template_generated_app"
_lock = RLock()
_loaded_revision = None
REFRESH_SCHEMA = 5


def source_revision(package_dir=None, template_dir=None):
    """Include template content so cached ZIPs also follow exported-app edits."""
    digest = sha256()
    for prefix, root in (("builder", package_dir or PACKAGE_DIR),
                         ("template", template_dir or TEMPLATE_DIR)):
        for path in sorted(root.rglob("*")):
            rel = path.relative_to(root)
            hidden=any(part.startswith(".") or part == "__pycache__" for part in rel.parts)
            if (not path.is_file() or path.suffix in (".pyc", ".pyo")
                    or (hidden and rel.as_posix() not in ('.gitignore', '.streamlit/config.toml'))):
                continue
            digest.update(f"{prefix}/{rel.as_posix()}\0".encode())
            digest.update(path.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest()


def refresh_builder_modules():
    """Recover pre-upgrade modules on first use; refresh on later source changes."""
    global _loaded_revision
    with _lock:
        revision = source_revision()
        if revision != _loaded_revision:
            invalidate_caches()
            # Refresh dependencies before consumers that import their attributes.
            for module in ('template_generated_app.data_blocks',
                           'template_generated_app.data_blocks.traits',
                           'template_generated_app.data_blocks.soil',
                           'template_generated_app.soil_profile'):
                reload(import_module(module))
            for name in ("registry", "onboarding", "local_data", "generator"):
                reload(import_module("studio_builder." + name))
            _loaded_revision = revision
        return revision
