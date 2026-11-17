"""Local TRY categorical workbook loader; never infer numerical plant rates."""
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from datetime import date
import json
import tempfile
import warnings
import zlib
import numpy as np
import pandas as pd
from . import DataBlock, DataRequest, require_columns

REQUIRED = ["AccSpeciesID", "AccSpeciesName", "PlantGrowthForm", "LeafType",
            "LeafPhenology", "PhotosyntheticPathway", "Woodiness"]
INDEX_COLUMNS = ['AccSpeciesID', 'AccSpeciesName', 'pft']
INDEX_SCHEMA_VERSION = 1
CITATION = 'Kattge et al. (2011), Global Change Biology 17:2905–2935; TRY Categorical Traits v1.0 (2012)'
PFT_CATEGORIES = {'grass_c3', 'grass_c4', 'tree_deciduous', 'tree_evergreen', 'shrub', 'unresolved'}
PFT_LABELS = {
    'grass_c3': 'C3 grasses', 'grass_c4': 'C4 grasses',
    'tree_deciduous': 'Deciduous trees', 'tree_evergreen': 'Evergreen trees',
    'shrub': 'Shrubs', 'unresolved': 'Unresolved traits',
}


def find_workbook(data_dir: Path) -> Path:
    paths = sorted(data_dir.rglob("TRY_Categorical_Traits_Lookup_Table*.xlsx"))
    if not paths:
        raise FileNotFoundError("Place your TRY categorical XLSX archive under data/try/.")
    if len(paths) != 1:
        raise ValueError("More than one TRY workbook found; retain one version under data/.")
    return paths[0]


def inspect_workbook(path: Path) -> dict:
    from openpyxl import load_workbook
    book = load_workbook(path, read_only=True, data_only=True)
    try:
        if "Categorical Trait Data" not in book.sheetnames:
            raise ValueError("TRY workbook is missing the Categorical Trait Data sheet")
        sheet = book["Categorical Trait Data"]
        headers = list(next(sheet.iter_rows(values_only=True)))
        missing = set(REQUIRED) - set(headers)
        if missing:
            raise ValueError("TRY workbook missing columns: " + ", ".join(sorted(missing)))
        return {"filename": path.name, "declared_rows": sheet.max_row - 1,
                "schema_valid": True, "sha256": sha256(path.read_bytes()).hexdigest()}
    finally:
        book.close()


def classify_pft(row):
    """Preserve ambiguous/missing classifications instead of guessing a PFT."""
    growth = str(row.get("PlantGrowthForm", "")).strip().lower()
    phenology = str(row.get("LeafPhenology", "")).strip().lower()
    pathway = str(row.get("PhotosyntheticPathway", "")).strip().upper()
    if growth == "graminoid" and pathway in ("C3", "C4"):
        return f"grass_{pathway.lower()}"
    if growth == "tree" and phenology in ("deciduous", "evergreen"):
        return f"tree_{phenology}"
    if growth == "shrub":
        return "shrub"
    return "unresolved"


def preset_for_pft(category):
    """Broad functional presets only; no species-specific numeric inference."""
    return {'grass_c3':'grass_c3','grass_c4':'grass_c4',
            'tree_deciduous':'forest','tree_evergreen':'forest'}.get(category)


def species_filter_categories(group=None):
    """Translate recorded groups/model compatibility without inferring crop use.

    Forest combines the two tree categories. Crop has no membership information
    in this archive, so its filter intentionally has no matching categories.
    """
    if group is None or group == 'all':
        return None
    if group == 'forest':
        return ['tree_deciduous', 'tree_evergreen']
    if group == 'crop':
        return []
    if group in PFT_LABELS:
        return [group]
    raise ValueError('Unknown plant group or ecosystem filter')


def species_index_fingerprint(data_dir):
    """Invalidate cached lookups when the source or either derived file changes."""
    path = find_workbook(Path(data_dir))
    stamps = []
    for asset in (path, path.with_name('TRY_species_index.csv.gz'), path.with_name('TRY_species_index.json')):
        try:
            stat = asset.stat()
            stamps.append((str(asset.resolve()), stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size, stat.st_ino))
        except FileNotFoundError:
            stamps.append((str(asset.resolve()), None, None, None, None))
    return tuple(stamps)


def _validate_species_index(frame):
    require_columns(frame, INDEX_COLUMNS)
    frame = frame[INDEX_COLUMNS].copy()
    identifiers = pd.to_numeric(frame.AccSpeciesID, errors='raise')
    if (frame.empty or not np.isfinite(identifiers.to_numpy()).all()
            or (identifiers <= 0).any() or (identifiers % 1 != 0).any()
            or identifiers.duplicated().any() or frame.isna().any().any()):
        raise ValueError('TRY species index failed record/schema checks')
    frame['AccSpeciesID'] = identifiers.astype('int64')
    frame['AccSpeciesName'] = frame.AccSpeciesName.map(lambda value: ' '.join(str(value).split()))
    if (frame.AccSpeciesName == '').any() or not frame.pft.isin(PFT_CATEGORIES).all():
        raise ValueError('TRY species index has invalid names or categories')
    return frame.sort_values(['AccSpeciesName', 'AccSpeciesID'], key=lambda column: column.str.casefold()
                             if column.name == 'AccSpeciesName' else column, kind='stable').reset_index(drop=True)


def _persist_species_index(index, metadata, payload, info):
    """Publish complete derived files when writable; original assets stay untouched."""
    try:
        with tempfile.TemporaryDirectory(prefix='.try-index-', dir=index.parent) as work:
            temporary = Path(work)
            (temporary / index.name).write_bytes(payload)
            (temporary / metadata.name).write_text(json.dumps(info, indent=2) + '\n')
            (temporary / index.name).replace(index)
            (temporary / metadata.name).replace(metadata)
        return True
    except OSError:
        return False


def species_index(data_dir):
    """Verify compact data against its source, rebuilding stale or corrupt indexes."""
    data_dir = Path(data_dir)
    path = find_workbook(data_dir)
    index = path.with_name('TRY_species_index.csv.gz')
    metadata = path.with_name('TRY_species_index.json')
    source_digest = sha256(path.read_bytes()).hexdigest()
    if index.is_file() and metadata.is_file():
        try:
            info = json.loads(metadata.read_text())
            if (not isinstance(info, dict) or info.get('schema_version') != INDEX_SCHEMA_VERSION
                    or info.get('source_sha256') != source_digest or info.get('columns') != INDEX_COLUMNS):
                raise ValueError('TRY species index metadata is stale')
            payload = index.read_bytes()
            if info.get('index_sha256') != sha256(payload).hexdigest():
                raise ValueError('TRY species index content hash does not match')
            frame = _validate_species_index(pd.read_csv(BytesIO(payload), compression='gzip'))
            if type(info.get('records')) is not int or info['records'] != len(frame):
                raise ValueError('TRY species index record count does not match')
            return frame, {**info, 'index_persisted': True}
        except (OSError, ValueError, TypeError, EOFError, zlib.error):
            # Derived data are disposable; only a valid source may rebuild them.
            pass
    block = load_try_categorical(DataRequest(0., 0., date(2000, 1, 1), date(2000, 1, 1), data_dir))
    frame = _validate_species_index(block.frame[INDEX_COLUMNS])
    output = BytesIO()
    frame.to_csv(output, index=False, compression={'method': 'gzip', 'mtime': 0})
    payload = output.getvalue()
    info = {'schema_version': INDEX_SCHEMA_VERSION, 'source_sha256': block.provenance['sha256'],
            'index_sha256': sha256(payload).hexdigest(), 'records': len(frame), 'columns': INDEX_COLUMNS,
            'citation': CITATION, 'numeric_rates': 'Not provided by categorical traits'}
    persisted = _persist_species_index(index, metadata, payload, info)
    return frame, {**info, 'index_persisted': persisted}


def _matching_species(frame, query, categories=None, browse=False):
    if categories is not None:
        selected = [categories] if isinstance(categories, str) else list(categories)
        if not set(selected).issubset(PFT_CATEGORIES):
            raise ValueError('Unknown TRY functional category filter')
        frame = frame[frame.pft.isin(selected)]
    text = str(query).strip()
    if text.isdecimal():
        return frame[frame.AccSpeciesID == int(text)]
    if not text and browse:
        return frame
    if len(text) < 2:
        return frame.iloc[:0]
    names = frame.AccSpeciesName.str.casefold()
    mask = pd.Series(True, index=frame.index)
    for token in text.casefold().split():
        mask &= names.str.contains(token, regex=False, na=False)
    return frame[mask]


def search_species(frame, query, limit=50, *, categories=None, sort_by='name',
                   descending=False, group_by=False, browse=False):
    """Filter/sort the full index, returning matching records and the full count.

    Explicit browsing permits empty queries. Other name queries
    require two characters; names are matched literally with all tokens required.
    ``limit=None`` supplies every matching row to a native table. The default
    limit remains available to older clients that render bounded choice lists.
    """
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50):
        raise ValueError('Species search limit must be between 1 and 50')
    if sort_by not in ('name', 'id'):
        raise ValueError('Species sort must be name or id')
    matched = _matching_species(frame, query, categories, browse)
    column = 'AccSpeciesName' if sort_by == 'name' else 'AccSpeciesID'
    by = ['pft', column] if group_by else [column]
    ascending = [True, not descending] if group_by else [not descending]
    matched = matched.sort_values(by, ascending=ascending, kind='stable',
        key=lambda values: values.str.casefold() if values.name == 'AccSpeciesName' else values)
    return matched.iloc[:limit].copy(), len(matched)


def species_group_counts(frame, query, *, categories=None, browse=False):
    """Category counts describe all matching records, independently of the limit."""
    matched = _matching_species(frame, query, categories, browse)
    return {str(category): int(count) for category, count in matched.pft.value_counts().sort_index().items()}


def load_try_categorical(request: DataRequest) -> DataBlock:
    path = Path(request.options["try_path"]) if "try_path" in request.options else find_workbook(request.data_dir)
    inventory = inspect_workbook(path)
    with warnings.catch_warnings():
        # The archive has an Excel extension that openpyxl cannot represent.
        # This read never saves the workbook; preserve the original bytes.
        warnings.filterwarnings("ignore", message="Unknown extension is not supported and will be removed", category=UserWarning)
        frame = pd.read_excel(path, sheet_name="Categorical Trait Data", engine="openpyxl")
    require_columns(frame, REQUIRED)
    frame = frame.dropna(subset=["AccSpeciesID", "AccSpeciesName"]).copy()
    original_rows = len(frame)
    frame = frame.drop_duplicates().copy()
    exact_duplicates = original_rows - len(frame)
    if frame.AccSpeciesID.duplicated().any():
        raise ValueError("TRY species IDs must be unique after removing exact duplicate rows; conflicting records need review")
    frame["AccSpeciesName"] = frame.AccSpeciesName.map(lambda value: " ".join(str(value).split()))
    frame["pft"] = frame.apply(classify_pft, axis=1)
    return DataBlock("try_categorical", frame, "species categories", {},
                     {**inventory, "validated_rows": len(frame), "exact_duplicates_removed": exact_duplicates,
                      "citation": CITATION,
                      "mapping": "Exact unambiguous categories only; remaining rows unresolved",
                      "numeric_rates": "Not provided by categorical traits"})
