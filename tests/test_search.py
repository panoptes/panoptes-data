import numpy as np
import pandas as pd
import pytest
from astropy.coordinates import SkyCoord
from conftest import SEQUENCE_ID, build_index, frame_document, write_sequence

from panoptes.data import documents
from panoptes.data import search as search_mod
from panoptes.data.search import add_pointing, get_all_observations, search_observations


def observations_table(**overrides):
    """One row per sequence, as `get_all_observations` returns it."""
    table = pd.DataFrame(
        {
            "sequence_sequence_id": [
                "PAN001_aaaaaa_20230101T000000",
                "PAN001_bbbbbb_20230102T000000",
                "PAN002_cccccc_20230601T000000",
            ],
            "unit_id": ["PAN001", "PAN001", "PAN002"],
            "camera_id": ["aaaaaa", "bbbbbb", "cccccc"],
            "field_name": ["A", "B", "C"],
            "sequence_time": [
                "2023-01-01T00:00:00+00:00",
                "2023-01-02T00:00:00+00:00",
                "2023-06-01T00:00:00+00:00",
            ],
            "num_frames": [5, 2, 100],
            "num_usable": [5, 1, 90],
            "total_exptime": [500.0, 200.0, 1000.0],
            "duration_minutes": [10.0, 4.0, 200.0],
            "mount_ra": [10.0, 10.5, 100.0],
            "mount_dec": [20.0, 20.1, -10.0],
            "mount_ra_drift": [0.0, 0.0, 0.0],
            "mount_dec_drift": [0.0, 0.0, 0.0],
        }
    )
    return table.assign(**overrides)


class TestWrapDegrees:
    def test_folds_onto_a_signed_half_turn(self):
        assert search_mod.wrap_degrees(359.9 - 0.1) == pytest.approx(-0.2)
        assert search_mod.wrap_degrees(0.1 - 359.9) == pytest.approx(0.2)
        assert search_mod.wrap_degrees(10.0) == pytest.approx(10.0)


class TestAddPointing:
    def test_mean_and_drift_per_sequence(self):
        frames = pd.DataFrame(
            {
                "sequence_sequence_id": ["S1"] * 3,
                "sequence_coordinates_mount_ra": [10.0, 11.0, 12.0],
                "sequence_coordinates_mount_dec": [20.0, 20.0, 21.0],
            }
        )
        observations = pd.DataFrame({"sequence_sequence_id": ["S1"]})

        result = add_pointing(observations, frames)

        assert result.mount_ra.iloc[0] == pytest.approx(11.0, abs=1e-6)
        assert result.mount_dec.iloc[0] == pytest.approx(20.3333, abs=1e-3)
        assert result.mount_ra_drift.iloc[0] == pytest.approx(1.0, abs=1e-6)
        assert result.mount_dec_drift.iloc[0] == pytest.approx(0.6667, abs=1e-3)

    def test_ra_is_averaged_as_an_angle(self):
        """A sequence straddling 0h averages to 0, not to 180."""
        frames = pd.DataFrame(
            {
                "sequence_sequence_id": ["S1"] * 2,
                "sequence_coordinates_mount_ra": [359.0, 1.0],
                "sequence_coordinates_mount_dec": [0.0, 0.0],
            }
        )

        result = add_pointing(pd.DataFrame({"sequence_sequence_id": ["S1"]}), frames)

        assert result.mount_ra.iloc[0] == pytest.approx(0.0, abs=1e-6)
        assert result.mount_ra_drift.iloc[0] == pytest.approx(1.0, abs=1e-6)

    def test_drift_is_per_sequence_not_pooled(self):
        frames = pd.DataFrame(
            {
                "sequence_sequence_id": ["S1", "S1", "S2", "S2"],
                "sequence_coordinates_mount_ra": [10.0, 14.0, 80.0, 80.1],
                "sequence_coordinates_mount_dec": [0.0, 0.0, 0.0, 0.0],
            }
        )

        result = add_pointing(pd.DataFrame({"sequence_sequence_id": ["S1", "S2"]}), frames)

        assert result.mount_ra_drift.tolist() == pytest.approx([2.0, 0.05], abs=1e-6)

    def test_a_sequence_with_no_coordinates_gets_nulls(self):
        frames = pd.DataFrame(
            {
                "sequence_sequence_id": ["S1"],
                "sequence_coordinates_mount_ra": [10.0],
                "sequence_coordinates_mount_dec": [20.0],
            }
        )

        result = add_pointing(pd.DataFrame({"sequence_sequence_id": ["S1", "S2"]}), frames)

        assert result.set_index("sequence_sequence_id").loc["S2"].isna().all()

    def test_an_index_with_no_coordinates_at_all(self):
        frames = pd.DataFrame(
            {
                "sequence_sequence_id": ["S1"],
                "sequence_coordinates_mount_ra": [np.nan],
                "sequence_coordinates_mount_dec": [np.nan],
            }
        )

        result = add_pointing(pd.DataFrame({"sequence_sequence_id": ["S1"]}), frames)

        assert list(search_mod.POINTING_RESULT_COLUMNS) == [
            c for c in search_mod.POINTING_RESULT_COLUMNS if c in result.columns
        ]
        assert result.mount_ra.isna().all()


class TestIndexWithoutCoordinates:
    """An index whose documents never carried a mount position is still valid."""

    def test_pointing_is_null_rather_than_a_refusal_to_read(self, tmp_path, monkeypatch):
        no_coords = frame_document(image_time="20180824T040118")
        del no_coords["sequence"]["coordinates"]
        root = tmp_path / "processed"
        write_sequence(root, [no_coords])
        build_index(root)
        monkeypatch.setenv("PANOPTES_PROCESSED_ROOT", str(root))

        table = get_all_observations()

        assert table.sequence_sequence_id.tolist() == [SEQUENCE_ID]
        assert table.mount_ra.isna().all()

    def test_such_a_sequence_matches_no_cone(self, tmp_path, monkeypatch):
        no_coords = frame_document(image_time="20180824T040118")
        del no_coords["sequence"]["coordinates"]
        root = tmp_path / "processed"
        write_sequence(root, [no_coords])
        build_index(root)
        monkeypatch.setenv("PANOPTES_PROCESSED_ROOT", str(root))

        results = search_observations(
            ra=83.8, dec=-5.4, radius=180.0, start_date="2018-01-01", end_date="2018-12-31"
        )

        assert len(results) == 0


class TestSearchObservations:
    def test_filters_by_cone_date_and_frame_count(self):
        results = search_observations(
            coords=SkyCoord(ra=10.0, dec=20.0, unit="deg"),
            radius=1.0,
            min_num_frames=3,
            source=observations_table(),
            start_date="2023-01-01",
            end_date="2023-12-31",
        )

        assert results.sequence_sequence_id.tolist() == ["PAN001_aaaaaa_20230101T000000"]

    def test_exptime_is_the_mean_over_frames(self):
        results = search_observations(
            coords=SkyCoord(ra=10.0, dec=20.0, unit="deg"),
            radius=5.0,
            source=observations_table(),
            start_date="2023-01-01",
            end_date="2023-12-31",
        )

        assert (results.exptime == results.total_exptime / results.num_frames).all()

    def test_the_source_table_is_not_mutated(self):
        """`query(..., inplace=True)` used to rewrite the caller's DataFrame."""
        source = observations_table()

        search_observations(
            coords=SkyCoord(ra=10.0, dec=20.0, unit="deg"),
            radius=1.0,
            source=source,
            start_date="2023-01-01",
            end_date="2023-12-31",
        )

        assert len(source) == 3
        assert "exptime" not in source.columns

    def test_filters_by_unit_id(self):
        results = search_observations(
            coords=SkyCoord(ra=50.0, dec=5.0, unit="deg"),
            radius=180.0,
            unit_id="PAN002",
            source=observations_table(),
            start_date="2023-01-01",
            end_date="2023-12-31",
        )

        assert results.unit_id.unique().tolist() == ["PAN002"]

    def test_a_cone_across_zero_hours_is_found(self):
        """The old degree comparison returned nothing for any target near 0h RA."""
        source = observations_table(mount_ra=[359.5, 0.5, 100.0])

        results = search_observations(
            coords=SkyCoord(ra=0.0, dec=20.0, unit="deg"),
            radius=1.0,
            source=source,
            start_date="2023-01-01",
            end_date="2023-12-31",
        )

        assert len(results) == 2

    def test_a_sequence_that_drifted_into_the_cone_is_found(self):
        """The mean is outside the box; the observation still spent time inside it."""
        source = observations_table(mount_ra=[12.0, 10.5, 100.0], mount_ra_drift=[1.5, 0.0, 0.0])

        results = search_observations(
            coords=SkyCoord(ra=10.0, dec=20.0, unit="deg"),
            radius=1.0,
            min_num_frames=3,
            source=source,
            start_date="2023-01-01",
            end_date="2023-12-31",
        )

        assert results.sequence_sequence_id.tolist() == ["PAN001_aaaaaa_20230101T000000"]

    def test_unknown_drift_does_not_widen_the_cone_to_everything(self):
        source = observations_table(mount_ra_drift=[np.nan] * 3)

        results = search_observations(
            coords=SkyCoord(ra=10.0, dec=20.0, unit="deg"),
            radius=1.0,
            source=source,
            start_date="2023-01-01",
            end_date="2023-12-31",
        )

        assert len(results) == 2

    def test_a_sequence_with_no_pointing_never_matches(self):
        source = observations_table(mount_ra=[np.nan, 10.5, 100.0])

        results = search_observations(
            coords=SkyCoord(ra=10.0, dec=20.0, unit="deg"),
            radius=1.0,
            source=source,
            start_date="2023-01-01",
            end_date="2023-12-31",
        )

        assert "PAN001_aaaaaa_20230101T000000" not in results.sequence_sequence_id.tolist()

    def test_dates_default_to_this_year_so_far(self):
        """The default range has to parse; `current_time` is an astropy Time."""
        source = observations_table(
            sequence_time=[
                f"{pd.Timestamp.now(tz='UTC').year}-01-02T00:00:00+00:00",
                "2023-01-02T00:00:00+00:00",
                "2023-06-01T00:00:00+00:00",
            ]
        )

        results = search_observations(
            coords=SkyCoord(ra=10.0, dec=20.0, unit="deg"), radius=1.0, source=source
        )

        assert len(results) == 1

    def test_by_name_resolves_coordinates(self, monkeypatch):
        monkeypatch.setattr(
            search_mod.SkyCoord,
            "from_name",
            classmethod(lambda cls, name: SkyCoord(ra=10.0, dec=20.0, unit="deg")),
        )

        results = search_observations(
            by_name="M42",
            radius=1.0,
            source=observations_table(),
            start_date="2023-01-01",
            end_date="2023-12-31",
        )

        assert len(results) == 2

    def test_an_empty_result_is_a_table_not_an_error(self):
        results = search_observations(
            coords=SkyCoord(ra=200.0, dec=70.0, unit="deg"),
            radius=1.0,
            source=observations_table(),
            start_date="2023-01-01",
            end_date="2023-12-31",
        )

        assert len(results) == 0
        assert "exptime" in results.columns

    def test_ra_and_dec_stand_in_for_coords(self):
        results = search_observations(
            ra=10.0,
            dec=20.0,
            radius=1.0,
            source=observations_table(),
            start_date="2023-01-01",
            end_date="2023-12-31",
        )

        assert len(results) == 2


class TestGetAllObservations:
    def test_reads_the_index_and_attaches_pointing(self, indexed_root):
        table = get_all_observations()

        assert table.sequence_sequence_id.tolist() == [SEQUENCE_ID]
        assert table.num_frames.tolist() == [2]
        assert table.mount_ra.iloc[0] == pytest.approx(83.8221)
        assert table.mount_ra_drift.iloc[0] == pytest.approx(0.0)

    def test_total_exptime_is_summed_from_the_frame_documents(self, indexed_root):
        """Null for every long sequence in the CSV, with nowhere to recover it from."""
        assert get_all_observations().total_exptime.tolist() == [240.0]

    def test_a_serial_keeps_its_leading_zero(self, tmp_path, frames, monkeypatch):
        """panoptes/panoptes-data#13: parquet carries the dtype, so nothing infers it."""
        root = tmp_path / "processed"
        write_sequence(root, frames)
        build_index(root)

        serials = documents.read_index(root, documents.FRAMES_FILENAME)

        assert serials.sequence_camera_serial_number.tolist() == ["032071000633"] * 2

    def test_an_index_root_separate_from_the_processed_tree(self, tmp_path, frames, monkeypatch):
        """The index is regenerable, so it need not sit in a read-only tree."""
        root = tmp_path / "processed"
        write_sequence(root, frames)
        index = build_index(root, tmp_path / "index")
        monkeypatch.setenv("PANOPTES_PROCESSED_ROOT", str(root))
        monkeypatch.setenv("PANOPTES_INDEX_ROOT", str(index))

        assert not (root / "observations.parquet").exists()
        assert get_all_observations().sequence_sequence_id.tolist() == [SEQUENCE_ID]

    def test_no_index_configured_says_where_to_point_it(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("PANOPTES_PROCESSED_ROOT", raising=False)
        monkeypatch.delenv("PANOPTES_INDEX_ROOT", raising=False)

        with pytest.raises(documents.DocumentsUnavailableError, match="PANOPTES_PROCESSED_ROOT"):
            get_all_observations()

    def test_no_index_built_yet_says_to_build_one(self, processed_root):
        with pytest.raises(documents.DocumentsUnavailableError, match="Build the index"):
            get_all_observations()


class TestEndToEnd:
    def test_a_search_result_feeds_straight_into_observation_info(self, tmp_path, monkeypatch):
        """The two halves of the package have to agree on where a sequence id lives."""
        from panoptes.data.observations import ObservationInfo

        root = tmp_path / "processed"
        write_sequence(
            root,
            [
                frame_document(image_time="20180824T040118", mount_ra=83.0),
                frame_document(image_time="20180824T040248", mount_ra=84.0),
            ],
        )
        build_index(root)
        monkeypatch.setenv("PANOPTES_PROCESSED_ROOT", str(root))
        monkeypatch.delenv("PANOPTES_ARCHIVE_ROOT", raising=False)

        results = search_observations(
            ra=83.5,
            dec=-5.39111,
            radius=1.0,
            start_date="2018-01-01",
            end_date="2018-12-31",
        )

        assert len(results) == 1
        obs_info = ObservationInfo(meta=results.iloc[0])
        assert obs_info.sequence_id == SEQUENCE_ID
        assert len(obs_info.image_metadata) == 2


def test_usable_query_agrees_with_num_usable(tmp_path, monkeypatch):
    """`USABLE_QUERY` and the index must not disagree about what "usable" counts.

    The index computes `num_usable` with equality on `MATCHED`, and so does
    `USABLE_QUERY`. This is what notices if either side's definition moves --
    `ImageStatus` is ordered, and `EXTRACTED` sorts above `MATCHED`.
    """
    from panoptes.data.observations import USABLE_QUERY, ObservationInfo

    root = tmp_path / "processed"
    write_sequence(
        root,
        [
            frame_document(image_time="20180824T040118", status="MATCHED"),
            frame_document(image_time="20180824T040248", status="ERROR"),
            frame_document(image_time="20180824T040418", status="MATCHED"),
        ],
    )
    build_index(root)
    monkeypatch.setenv("PANOPTES_PROCESSED_ROOT", str(root))
    monkeypatch.delenv("PANOPTES_ARCHIVE_ROOT", raising=False)

    num_usable = (
        get_all_observations().set_index("sequence_sequence_id").loc[SEQUENCE_ID].num_usable
    )
    filtered = ObservationInfo(sequence_id=SEQUENCE_ID, image_query=USABLE_QUERY)

    assert len(filtered.image_metadata) == num_usable == 2


def test_get_metadata_concatenates(monkeypatch):
    class FakeObsInfo:
        def __init__(self, meta=None):
            self.image_metadata = pd.DataFrame({"a": [1]})

    monkeypatch.setattr(search_mod, "ObservationInfo", FakeObsInfo)

    assert len(search_mod.get_metadata(pd.DataFrame({"id": [1, 2]}))) == 2


if __name__ == "__main__":
    pytest.main(["-q"])
