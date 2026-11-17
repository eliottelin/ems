"""TRY lookup stays bounded, literal, source-backed and safe to rebuild."""
from hashlib import sha256
import json

import pandas as pd
import pytest

from data_blocks import traits


def workbook(tmp_path):
    path = tmp_path / 'try' / 'TRY_Categorical_Traits_Lookup_Table_fixture.xlsx'
    path.parent.mkdir()
    records = [
        dict(AccSpeciesID=1, AccSpeciesName='Quercus rubra', PlantGrowthForm='tree',
             LeafType='broadleaved', LeafPhenology='deciduous', PhotosyntheticPathway='C3', Woodiness='woody'),
        dict(AccSpeciesID=12, AccSpeciesName='Poa pratensis', PlantGrowthForm='graminoid',
             LeafType='narrow', LeafPhenology='evergreen', PhotosyntheticPathway='C3', Woodiness='non-woody'),
        dict(AccSpeciesID=123, AccSpeciesName='Zea mays', PlantGrowthForm='graminoid',
             LeafType='narrow', LeafPhenology='annual', PhotosyntheticPathway='C4', Woodiness='non-woody'),
    ]
    pd.DataFrame(records).to_excel(path, sheet_name='Categorical Trait Data', index=False)
    return path, records


def index_paths(source):
    return source.with_name('TRY_species_index.csv.gz'), source.with_name('TRY_species_index.json')


def test_name_search_is_literal_case_insensitive_and_requires_all_tokens():
    frame = pd.DataFrame({'AccSpeciesID': [1, 2, 3, 4],
                          'AccSpeciesName': ['Quercus rubra', 'Quercus alba', 'Rubra Quercus', 'Poa [test].*'],
                          'pft': ['tree_deciduous'] * 4})
    selected, total = traits.search_species(frame, 'QUERCUS rub')
    assert total == 2 and selected.AccSpeciesID.tolist() == [1, 3]
    selected, total = traits.search_species(frame, '[test].*')
    assert total == 1 and selected.AccSpeciesID.tolist() == [4]
    assert traits.search_species(frame, 'Q')[1] == 0
    assert traits.search_species(frame, '  ')[1] == 0


def test_id_lookup_is_exact_including_a_one_digit_id():
    frame = pd.DataFrame({'AccSpeciesID': [1, 12, 123], 'AccSpeciesName': ['One', 'Twelve', 'Long'],
                          'pft': ['unresolved'] * 3})
    for query, identifier in [('1', 1), (' 12 ', 12), ('00123', 123)]:
        selected, total = traits.search_species(frame, query)
        assert total == 1 and selected.AccSpeciesID.tolist() == [identifier]
    assert traits.search_species(frame, '124')[1] == 0


def test_large_search_returns_only_fifty_choices_and_the_full_match_count():
    frame = pd.DataFrame({'AccSpeciesID': range(1, 66043),
                          'AccSpeciesName': [f'Test species {i}' for i in range(66042)],
                          'pft': 'unresolved'})
    selected, total = traits.search_species(frame, 'species')
    assert len(selected) == 50 and total == 66042
    selected.loc[selected.index[0], 'AccSpeciesName'] = 'changed'
    assert frame.AccSpeciesName.iloc[0] != 'changed'
    for limit in (0, 51, True, 1.5):
        with pytest.raises(ValueError, match='between 1 and 50'):
            traits.search_species(frame, 'species', limit=limit)


def test_filters_and_sorting_apply_before_the_result_limit():
    frame = pd.DataFrame({'AccSpeciesID': [40, 30, 20, 10],
                          'AccSpeciesName': ['Beta grass', 'zeta tree', 'Alpha grass', 'Delta tree'],
                          'pft': ['grass_c3', 'tree_evergreen', 'grass_c3', 'tree_deciduous']})
    selected, total = traits.search_species(frame, 'tree', limit=1, sort_by='name', descending=True)
    assert total == 2 and selected.AccSpeciesID.tolist() == [30]
    selected, total = traits.search_species(frame, '', limit=1, categories='grass_c3', browse=True,
                                           sort_by='id')
    assert total == 2 and selected.AccSpeciesID.tolist() == [20]
    assert traits.search_species(frame, '', browse=True)[1] == 4
    assert traits.search_species(frame, '')[1] == 0
    assert traits.search_species(frame, 'g', categories='grass_c3', browse=True)[1] == 0
    assert traits.search_species(frame, '30', categories='grass_c3')[1] == 0


def test_grouping_uses_complete_matches_before_bounding_choices():
    frame = pd.DataFrame({'AccSpeciesID': [3, 2, 1],
                          'AccSpeciesName': ['Species z', 'Species b', 'Species a'],
                          'pft': ['tree_deciduous', 'grass_c3', 'grass_c3']})
    selected, total = traits.search_species(frame, 'species', limit=2, group_by=True, descending=True)
    assert total == 3 and selected.AccSpeciesID.tolist() == [2, 1]
    assert traits.species_group_counts(frame, 'species') == {'grass_c3': 2, 'tree_deciduous': 1}
    with pytest.raises(ValueError, match='category'):
        traits.search_species(frame, 'species', categories=['invented'])
    with pytest.raises(ValueError, match='sort'):
        traits.search_species(frame, 'species', sort_by='invented')


def test_native_table_receives_all_matching_records_beyond_the_old_first_fifty():
    frame = pd.DataFrame({'AccSpeciesID': range(1, 66043),
                          'AccSpeciesName': [f'Species {i:05d}' for i in range(66042)],
                          'pft': 'grass_c3'})
    matched, total = traits.search_species(frame, '', limit=None, browse=True)
    assert total == len(matched) == 66042
    assert 60000 in matched.AccSpeciesID.to_numpy()
    descending, total = traits.search_species(frame, 'species', limit=None, descending=True)
    assert len(descending) == total == 66042
    assert descending.AccSpeciesID.iloc[0] == 66042
    assert descending.AccSpeciesID.iloc[-1] == 1
    assert len(traits.search_species(frame, 'species')[0]) == 50


def test_scientific_group_filters_preserve_categories_and_do_not_invent_crops():
    frame = pd.DataFrame({'AccSpeciesID': range(1, 7),
                          'AccSpeciesName': ['Poa pratensis', 'Zea mays', 'Quercus rubra',
                                             'Pinus strobus', 'Shrub fixture', 'Unknown fixture'],
                          'pft': list(traits.PFT_LABELS)})
    for group in traits.PFT_LABELS:
        matched, count = traits.search_species(frame, '', limit=None, browse=True,
            categories=traits.species_filter_categories(group))
        assert count == 1 and matched.pft.tolist() == [group]
    forest, count = traits.search_species(frame, '', limit=None, browse=True,
        categories=traits.species_filter_categories('forest'), group_by=True)
    assert count == 2 and set(forest.pft) == {'tree_deciduous', 'tree_evergreen'}
    crop, count = traits.search_species(frame, '', limit=None, browse=True,
        categories=traits.species_filter_categories('crop'))
    assert crop.empty and count == 0, 'An agricultural use cannot be inferred from graminoid traits'
    assert traits.species_filter_categories('all') is None
    with pytest.raises(ValueError, match='group or ecosystem'):
        traits.species_filter_categories('invented habitat')


def test_validated_index_persists_hashes_and_keeps_original_source_bytes(tmp_path, monkeypatch):
    source, _ = workbook(tmp_path)
    original = source.read_bytes()
    frame, info = traits.species_index(tmp_path)
    index, metadata = index_paths(source)
    assert info['index_persisted']
    assert info['records'] == 3
    assert info['source_sha256'] == sha256(original).hexdigest()
    assert info['index_sha256'] == sha256(index.read_bytes()).hexdigest()
    assert source.read_bytes() == original
    assert json.loads(metadata.read_text())['citation'] == traits.CITATION
    monkeypatch.setattr(traits, 'load_try_categorical', lambda request: (_ for _ in ()).throw(AssertionError('Valid index must not reread workbook rows')))
    reused, reused_info = traits.species_index(tmp_path)
    pd.testing.assert_frame_equal(frame, reused)
    assert reused_info['index_sha256'] == info['index_sha256']


@pytest.mark.parametrize('mutation', ['index', 'metadata', 'source', 'legacy_metadata'])
def test_changed_source_or_derived_files_invalidate_and_rebuild(tmp_path, mutation):
    source, records = workbook(tmp_path)
    original_frame, info = traits.species_index(tmp_path)
    before = traits.species_index_fingerprint(tmp_path)
    index, metadata = index_paths(source)
    if mutation == 'index':
        altered = original_frame.copy()
        altered.loc[0, 'AccSpeciesName'] = 'Fabricated species'
        altered.to_csv(index, index=False, compression='gzip')
    elif mutation == 'metadata':
        metadata.write_text('{malformed derived metadata')
    elif mutation == 'legacy_metadata':
        metadata.write_text(json.dumps({'source_sha256': info['source_sha256'], 'records': 3}))
    else:
        records[0]['AccSpeciesName'] = 'Quercus changed'
        pd.DataFrame(records).to_excel(source, sheet_name='Categorical Trait Data', index=False)
    assert before != traits.species_index_fingerprint(tmp_path)
    rebuilt, updated = traits.species_index(tmp_path)
    names = rebuilt.AccSpeciesName.tolist()
    assert 'Fabricated species' not in names
    assert ('Quercus changed' in names) == (mutation == 'source')
    assert updated['source_sha256'] == sha256(source.read_bytes()).hexdigest()
    assert updated['index_sha256'] == sha256(index.read_bytes()).hexdigest()
    assert updated['index_persisted']


def test_bad_index_schema_rebuilds_even_if_its_content_hash_matches(tmp_path):
    source, _ = workbook(tmp_path)
    _, info = traits.species_index(tmp_path)
    index, metadata = index_paths(source)
    pd.DataFrame({'AccSpeciesID': [1], 'AccSpeciesName': ['Fake']}).to_csv(index, index=False, compression='gzip')
    info['index_sha256'] = sha256(index.read_bytes()).hexdigest()
    metadata.write_text(json.dumps(info))
    rebuilt, updated = traits.species_index(tmp_path)
    assert len(rebuilt) == 3 and 'pft' in rebuilt
    assert updated['records'] == 3


def test_missing_source_never_uses_or_invents_cached_species(tmp_path):
    source, _ = workbook(tmp_path)
    traits.species_index(tmp_path)
    source.unlink()
    with pytest.raises(FileNotFoundError, match='TRY categorical XLSX'):
        traits.species_index(tmp_path)
    with pytest.raises(FileNotFoundError):
        traits.species_index_fingerprint(tmp_path)


def test_read_only_derived_directory_keeps_valid_lookup_available(tmp_path, monkeypatch):
    source, _ = workbook(tmp_path)
    monkeypatch.setattr(traits.tempfile, 'TemporaryDirectory', lambda **kwargs: (_ for _ in ()).throw(PermissionError('Read-only dataset')))
    frame, info = traits.species_index(tmp_path)
    assert len(frame) == 3
    assert info['index_persisted'] is False
    assert source.is_file() and not index_paths(source)[0].exists()
    assert traits.search_species(frame, 'Quercus')[1] == 1


def test_fingerprint_tracks_missing_and_replaced_derived_files(tmp_path):
    source, _ = workbook(tmp_path)
    missing = traits.species_index_fingerprint(tmp_path)
    traits.species_index(tmp_path)
    complete = traits.species_index_fingerprint(tmp_path)
    assert missing != complete
    index, metadata = index_paths(source)
    index.unlink()
    assert complete != traits.species_index_fingerprint(tmp_path)
    assert metadata.is_file()
