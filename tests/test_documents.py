"""Reading the pipeline's documents and the index built over them.

Regression cover for panoptes/panoptes-data#15.
"""

import json

import pytest
from conftest import SEQUENCE_ID, build_index, frame_document, write_sequence

from panoptes.data import documents


class TestFlatten:
    def test_nested_maps_become_joined_names(self):
        flat = documents.flatten({'image': {'camera': {'exptime': 120.0}}})

        assert flat == {'image_camera_exptime': 120.0}

    def test_the_separator_is_never_a_dot(self):
        """A dotted name is a *view* over a nested map, not storage (contract 3.2)."""
        assert documents.SEPARATOR == '_'
        assert '.' not in ''.join(documents.flatten({'a': {'b': 1}}))

    def test_lists_are_left_alone(self):
        flat = documents.flatten({'image': {'assets': ['a.jpg', 'b.jpg']}})

        assert flat == {'image_assets': ['a.jpg', 'b.jpg']}

    def test_a_declared_separator_is_honored(self):
        flat = documents.flatten({'image': {'uid': 'x'}}, separator='.')

        assert flat == {'image.uid': 'x'}


class TestSequenceDirectory:
    def test_mirrors_the_archive_layout(self, tmp_path):
        directory = documents.sequence_directory(tmp_path, SEQUENCE_ID)

        assert directory == tmp_path / 'PAN012' / '358d0f' / '20180824T035917'

    @pytest.mark.parametrize('sequence_id', ['nonsense', 'PAN012_358d0f', 'PAN012_358d0f_nope'])
    def test_a_malformed_id_says_what_one_looks_like(self, tmp_path, sequence_id):
        with pytest.raises(ValueError, match='well-formed sequence id'):
            documents.sequence_directory(tmp_path, sequence_id)


class TestRequireRoot:
    def test_no_root_points_at_the_setting(self):
        with pytest.raises(documents.DocumentsUnavailableError, match='PANOPTES_PROCESSED_ROOT'):
            documents.require_root(None, 'processed root')

    def test_a_mistyped_root_is_not_reported_as_a_missing_one(self, tmp_path):
        """Two different mistakes; reporting the second as the first sends you nowhere."""
        with pytest.raises(documents.DocumentsUnavailableError, match='is not a directory'):
            documents.require_root(tmp_path / 'typo', 'processed root')


class TestReadObservation:
    def test_returns_the_flattened_sequence_document(self, processed_root):
        observation = documents.read_observation(processed_root, SEQUENCE_ID)

        assert observation['sequence_id'] == SEQUENCE_ID
        assert observation['num_frames'] == 2
        assert observation['status'] == 'MATCHED'
        # The nested `sequence` block flattens to the frame index's names.
        assert observation['sequence_coordinates_mount_ra'] == 83.8221
        assert observation['sequence_camera_serial_number'] == '032071000633'

    def test_a_sequence_not_in_the_tree_says_so(self, processed_root):
        with pytest.raises(documents.DocumentsUnavailableError, match='PAN001_abc123_20200101T000000'):
            documents.read_observation(processed_root, 'PAN001_abc123_20200101T000000')

    def test_a_sequence_the_pipeline_has_not_aggregated_says_so(self, tmp_path, frames):
        """Frames processed, observation not yet written: a real intermediate state."""
        root = tmp_path / 'processed'
        write_sequence(root, frames, write_observation=False)

        with pytest.raises(documents.DocumentsUnavailableError, match='aggregated'):
            documents.read_observation(root, SEQUENCE_ID)


class TestReadFrames:
    def test_one_row_per_frame_with_contract_names(self, processed_root):
        table = documents.read_frames(processed_root, SEQUENCE_ID)

        assert len(table) == 2
        for name in ('image_uid', 'image_image_time', 'image_status',
                     'image_camera_exptime', 'sequence_coordinates_mount_ra',
                     'unit_unit_id'):
            assert name in table.columns

    def test_rows_come_back_in_image_time_order(self, processed_root):
        table = documents.read_frames(processed_root, SEQUENCE_ID)

        assert list(table.image_uid) == [
            'PAN012_358d0f_20180824T035917_20180824T040118',
            'PAN012_358d0f_20180824T035917_20180824T040248',
        ]

    def test_a_serial_keeps_its_leading_zero(self, processed_root):
        """panoptes/panoptes-data#13: JSON says string, so nothing infers float64."""
        table = documents.read_frames(processed_root, SEQUENCE_ID)

        assert list(table.sequence_camera_serial_number) == ['032071000633'] * 2

    def test_a_field_missing_from_one_document_is_null_not_an_error(self, tmp_path):
        """A tree written by an older pipeline still has to load."""
        older = frame_document(image_time='20180824T040118')
        del older['image']['camera']['white_lvln']
        root = tmp_path / 'processed'
        write_sequence(root, [older, frame_document(image_time='20180824T040248')])

        table = documents.read_frames(root, SEQUENCE_ID)

        assert table.image_camera_white_lvln.isna().tolist() == [True, False]

    def test_an_unreadable_document_raises_rather_than_shortening_the_observation(
        self, processed_root
    ):
        """The index walk skips these; one observation cannot afford to."""
        truncated = processed_root / 'PAN012/358d0f/20180824T035917/20180824T040248/metadata.json'
        truncated.write_text('{"image": ')

        with pytest.raises(documents.DocumentsUnavailableError, match='short by one frame'):
            documents.read_frames(processed_root, SEQUENCE_ID)

    def test_a_sequence_with_no_frames_says_so(self, processed_root):
        with pytest.raises(documents.DocumentsUnavailableError, match='No frame documents'):
            documents.read_frames(processed_root, 'PAN001_abc123_20200101T000000')


class TestSchema:
    def test_a_tree_with_no_manifest_is_not_an_error(self, processed_root):
        """Documents are readable without an index; an index is derived."""
        assert documents.read_schema(processed_root) is None
        assert documents.separator_for(processed_root) == documents.SEPARATOR

    def test_the_declared_separator_is_used(self, indexed_root):
        assert documents.separator_for(indexed_root) == '_'

    def test_an_unknown_schema_version_is_refused(self, tmp_path, frames):
        """The manifest exists so a contract change is visible, not so it is ignored."""
        root = tmp_path / 'processed'
        write_sequence(root, frames)
        build_index(root, version=99)

        with pytest.raises(documents.SchemaVersionError, match='version 99'):
            documents.read_schema(root)

    def test_an_unknown_version_stops_the_index_being_read(self, tmp_path, frames):
        root = tmp_path / 'processed'
        write_sequence(root, frames)
        build_index(root, version=99)

        with pytest.raises(documents.SchemaVersionError):
            documents.read_index(root, documents.OBSERVATIONS_FILENAME)

    def test_a_manifest_that_is_not_an_object_is_refused(self, indexed_root):
        (indexed_root / documents.SCHEMA_FILENAME).write_text('[1, 2, 3]')

        with pytest.raises(documents.SchemaVersionError, match='not an object'):
            documents.read_schema(indexed_root)

    def test_an_unreadable_manifest_is_refused(self, indexed_root):
        (indexed_root / documents.SCHEMA_FILENAME).write_text('{not json')

        with pytest.raises(documents.SchemaVersionError, match='could not be read'):
            documents.read_schema(indexed_root)


class TestReadIndex:
    def test_reads_the_observation_table(self, indexed_root):
        table = documents.read_index(indexed_root, documents.OBSERVATIONS_FILENAME)

        assert list(table.sequence_sequence_id) == [SEQUENCE_ID]
        assert table.num_frames.tolist() == [2]
        assert table.num_usable.tolist() == [2]
        assert table.total_exptime.tolist() == [240.0]

    def test_columns_are_pushed_down_into_the_read(self, indexed_root):
        table = documents.read_index(
            indexed_root, documents.FRAMES_FILENAME, columns=['image_uid', 'image_status']
        )

        assert list(table.columns) == ['image_uid', 'image_status']

    def test_a_column_that_is_not_there_says_which(self, indexed_root):
        with pytest.raises(documents.DocumentsUnavailableError, match='does not carry the column'):
            documents.read_index(indexed_root, documents.FRAMES_FILENAME, columns=['nope'])

    def test_no_index_built_yet_says_so(self, processed_root):
        with pytest.raises(documents.DocumentsUnavailableError, match='Build the index'):
            documents.read_index(processed_root, documents.OBSERVATIONS_FILENAME)

    def test_no_index_root_points_at_the_setting(self):
        with pytest.raises(documents.DocumentsUnavailableError, match='PANOPTES_PROCESSED_ROOT'):
            documents.read_index(None, documents.OBSERVATIONS_FILENAME)

    def test_columns_are_listed_without_reading_a_row(self, indexed_root):
        names = documents.index_columns(indexed_root, documents.OBSERVATIONS_FILENAME)

        assert 'sequence_sequence_id' in names
        assert 'num_usable' in names

    def test_listing_columns_of_an_index_that_is_not_there(self, processed_root):
        with pytest.raises(documents.DocumentsUnavailableError, match='Build the index'):
            documents.index_columns(processed_root, documents.FRAMES_FILENAME)

    def test_documents_and_the_index_agree_on_column_names(self, indexed_root):
        """The two paths into the same values must not name them differently."""
        from_documents = documents.read_frames(indexed_root, SEQUENCE_ID)
        from_index = documents.read_index(indexed_root, documents.FRAMES_FILENAME)

        assert set(from_documents.columns) == set(from_index.columns)


def test_read_document_tolerates_json_that_is_not_an_object(tmp_path):
    """`json.loads` will happily return a list; every caller here wants a mapping."""
    path = tmp_path / 'metadata.json'
    path.write_text(json.dumps([1, 2, 3]))

    assert documents.read_document(path) is None
