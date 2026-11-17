"""Inspect only explicitly supported local assets, never arbitrary user files."""
from pathlib import Path
from template_generated_app.data_blocks.traits import find_workbook, inspect_workbook

DATA_ROOT = Path(__file__).resolve().parents[1] / "data"


def try_inventory(data_root=DATA_ROOT):
    try:
        path = find_workbook(Path(data_root))
        return {"available": True, **inspect_workbook(path)}
    except FileNotFoundError:
        return {"available": False, "message": "Add your TRY workbook to data/try/."}
    except Exception as exc:
        # Optional source inspection must not prevent a preset-only export.
        return {"available": False, "message":
                f"TRY source unavailable ({type(exc).__name__}): {exc}. Ecosystem presets remain available."}


def try_assets(data_root=DATA_ROOT):
    """Explicit workbook and its source policy/docs; preserve their bytes."""
    try:
        path = find_workbook(data_root)
    except FileNotFoundError:
        return []
    from template_generated_app.data_blocks.traits import species_index
    index=path.with_name('TRY_species_index.csv.gz')
    metadata=path.with_name('TRY_species_index.json')
    _,info=species_index(data_root)
    derived=[index,metadata] if info['index_persisted'] else []
    return [path,*sorted(path.parent.glob('*.pdf')),*derived]


def hwsd_assets(data_root=DATA_ROOT):
    """Only the explicit converted public HWSD table and its provenance."""
    import json
    from hashlib import sha256
    folder = data_root/'hwsd'
    table = folder/'HWSD2_LAYERS.csv.gz'
    sources = sorted(data_root.rglob('HWSD2.mdb'))
    if len(sources)>1:
        raise ValueError('Keep one official HWSD2.mdb under data/ before exporting')
    if sources:
        source = sources[0]
        digest = sha256(source.read_bytes()).hexdigest()
        meta = json.loads((folder/'source.json').read_text()) if (folder/'source.json').is_file() else {}
        if not table.is_file() or meta.get('source_sha256')!=digest:
            from template_generated_app.data_blocks.soil import read_hwsd_mdb
            frame = read_hwsd_mdb(source)
            columns = ['HWSD2_SMU_ID','SEQUENCE','SHARE','TOPDEP','BOTDEP','SAND','SILT','CLAY','ORG_CARBON','BULK','COARSE']
            if not set(columns)<=set(frame):
                raise ValueError('HWSD2.mdb has an unsupported layer-table schema')
            folder.mkdir(parents=True,exist_ok=True)
            temporary = table.with_name('HWSD2_LAYERS.pending.gz')
            frame[columns].to_csv(temporary,index=False,compression={'method':'gzip','mtime':0})
            temporary.replace(table)
            meta = {'source_file':source.name,'source_sha256':digest,'source_table':'HWSD2_LAYERS','records':len(frame),'columns':columns,
                'transformations':['Select required columns; preserve source units','Official negative missing-value codes become blank','Gzip CSV export; original MDB unchanged'],
                'citation':'FAO and IIASA. 2023. Harmonized World Soil Database version 2.0. https://doi.org/10.4060/cc3823en',
                'raster_source':'https://s3.eu-west-1.amazonaws.com/data.gaezdev.aws.fao.org/HWSD/HWSD2_RASTER.zip'}
            (folder/'source.json').write_text(json.dumps(meta,indent=2)+'\n')
    return [table,folder/'source.json'] if table.is_file() and (folder/'source.json').is_file() else []
