import numpy as np
import pandas as pd
import pytest
from astropy.coordinates import SkyCoord
from conftest import SEQUENCE_ID, build_index, frame_document, write_sequence

from panoptes.data import documents
from panoptes.data import search as search_mod
from panoptes.data.search import (
    MetadataUnavailableError,
    add_frame_facts,
    add_pointing,
    as_utc,
    duration_window,
    find_simultaneous,
    get_all_observations,
    parse_duration,
    search_observations,
)


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
            "start_time": [
                "2023-01-01T00:00:00+00:00",
                "2023-01-02T00:00:00+00:00",
                "2023-06-01T00:00:00+00:00",
            ],
            "end_time": [
                "2023-01-01T00:10:00+00:00",
                "2023-01-02T00:04:00+00:00",
                "2023-06-01T03:20:00+00:00",
            ],
            "iso": [100.0, 200.0, 100.0],
            "airmass": [1.1, 2.5, 1.3],
            "moonfrac": [0.1, 0.9, 0.2],
            "moonsep": [80.0, 20.0, 70.0],
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


class TestAddFrameFacts:
    """panoptes/panoptes-data#14: the header facts only per-frame records carry."""

    def test_each_fact_is_the_sequence_mean(self):
        frames = pd.DataFrame(
            {
                "sequence_sequence_id": ["S1", "S1", "S2"],
                "image_camera_iso": [100, 100, 200],
                "sequence_coordinates_airmass": [1.0, 2.0, 1.5],
                "image_environment_moonfrac": [0.2, 0.4, 0.8],
                "image_environment_moonsep": [70.0, 90.0, 30.0],
            }
        )
        observations = pd.DataFrame({"sequence_sequence_id": ["S1", "S2"]})

        result = add_frame_facts(observations, frames).set_index("sequence_sequence_id")

        assert result.loc["S1"].iso == pytest.approx(100.0)
        assert result.loc["S1"].airmass == pytest.approx(1.5)
        assert result.loc["S1"].moonfrac == pytest.approx(0.3)
        assert result.loc["S1"].moonsep == pytest.approx(80.0)
        assert result.loc["S2"].airmass == pytest.approx(1.5)

    def test_a_fact_no_document_carried_is_null_not_missing(self):
        """A cut on it selects nothing; it does not raise. Same rule as pointing."""
        frames = pd.DataFrame(
            {"sequence_sequence_id": ["S1"], "image_camera_iso": [100]},
        )

        result = add_frame_facts(pd.DataFrame({"sequence_sequence_id": ["S1"]}), frames)

        assert result.iso.iloc[0] == pytest.approx(100.0)
        for name in ("airmass", "moonfrac", "moonsep"):
            assert name in result.columns
            assert result[name].isna().all()

    def test_an_index_with_no_facts_at_all(self):
        frames = pd.DataFrame({"sequence_sequence_id": ["S1"]})

        result = add_frame_facts(pd.DataFrame({"sequence_sequence_id": ["S1"]}), frames)

        assert set(search_mod.FRAME_FACT_COLUMNS.values()) <= set(result.columns)
        assert result[list(search_mod.FRAME_FACT_COLUMNS.values())].isna().all().all()

    def test_an_unparseable_reading_is_dropped_rather_than_poisoning_the_mean(self):
        """`iso` is an unparsed header value and is a string in some eras."""
        frames = pd.DataFrame(
            {
                "sequence_sequence_id": ["S1", "S1", "S1"],
                "image_camera_iso": ["100", "200", "AUTO"],
            }
        )

        result = add_frame_facts(pd.DataFrame({"sequence_sequence_id": ["S1"]}), frames)

        assert result.iso.iloc[0] == pytest.approx(150.0)

    def test_the_index_read_attaches_them(self, indexed_root):
        result = get_all_observations().set_index("sequence_sequence_id").loc[SEQUENCE_ID]

        assert result.iso == pytest.approx(100.0)
        assert result.airmass == pytest.approx(1.2)
        assert result.moonfrac == pytest.approx(0.3)
        assert result.moonsep == pytest.approx(60.0)


class TestDuration:
    """A window given as a length rather than a far end."""

    ANCHOR = "3/12/2024"

    def window(self, duration, default="forward"):
        start, end = duration_window(duration, as_utc(self.ANCHOR), default)
        return str(start.date()), str(end.date())

    def test_a_bare_duration_runs_from_the_anchor(self):
        assert self.window("90 days") == ("2024-03-12", "2024-06-10")

    def test_before_and_after_puts_the_window_on_both_sides(self):
        """The case a single parsed datetime cannot express."""
        assert self.window("10 days before and after") == ("2024-03-02", "2024-03-22")
        assert self.window("1 year either side of") == ("2023-03-12", "2025-03-12")

    def test_a_direction_word_overrides_the_default(self):
        assert self.window("2 weeks before") == ("2024-02-27", "2024-03-12")
        assert self.window("3 weeks after", default="backward") == ("2024-03-12", "2024-04-02")

    def test_the_default_applies_when_the_duration_is_silent(self):
        assert self.window("6 months", default="backward") == ("2023-09-12", "2024-03-12")

    def test_months_and_years_are_calendar_spans(self):
        """Six months from March 12 is September 12, not 182.6 days later."""
        assert self.window("6 months") == ("2024-03-12", "2024-09-12")
        assert self.window("1 year") == ("2024-03-12", "2025-03-12")

    def test_a_timedelta_is_taken_as_given(self):
        from datetime import timedelta

        assert self.window(timedelta(days=30)) == ("2024-03-12", "2024-04-11")

    def test_hours_are_a_unit(self):
        assert self.window("48 hours") == ("2024-03-12", "2024-03-14")

    def test_an_unreadable_duration_lists_the_forms_that_work(self):
        with pytest.raises(ValueError, match="not a duration this understands"):
            parse_duration("a fortnight")
        with pytest.raises(ValueError, match="not a duration this understands"):
            parse_duration("90 parsecs")

    def test_a_search_window_from_a_duration(self):
        results = search_observations(
            source=observations_table(), start_date="2023-01-01", duration="30 days"
        )

        assert list(results.sequence_sequence_id) == [
            "PAN001_aaaaaa_20230101T000000",
            "PAN001_bbbbbb_20230102T000000",
        ]

    def test_a_two_sided_search_window(self):
        """Anchored on 2023-06-01, ten days either way keeps only the June sequence."""
        results = search_observations(
            source=observations_table(),
            start_date="2023-06-01",
            duration="10 days before and after",
        )

        assert list(results.sequence_sequence_id) == ["PAN002_cccccc_20230601T000000"]

    def test_end_date_and_duration_together_are_refused(self):
        """Both name the same edge, and nothing says which wins."""
        with pytest.raises(ValueError, match="Give one"):
            search_observations(
                source=observations_table(),
                start_date="2023-01-01",
                end_date="2023-12-31",
                duration="30 days",
            )

    def test_with_no_start_date_the_window_runs_backward_from_now(self):
        """Forward from now there is nothing to find."""
        table = observations_table()
        recent = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=3)
        table.loc[0, "sequence_time"] = recent.isoformat()

        results = search_observations(source=table, duration="30 days")

        assert list(results.sequence_sequence_id) == ["PAN001_aaaaaa_20230101T000000"]


class TestAllSkySearch:
    """panoptes/panoptes-data#14: a position was required, so the CLI faked one."""

    def test_no_position_searches_everything(self):
        results = search_observations(
            source=observations_table(), start_date="2023-01-01", end_date="2023-12-31"
        )

        assert len(results) == 3

    def test_an_index_with_no_pointing_is_still_searchable_all_sky(self):
        """The cone columns are never touched, so a null pointing is not a null result."""
        table = observations_table(mount_ra=np.nan, mount_dec=np.nan)

        results = search_observations(source=table, start_date="2023-01-01", end_date="2023-12-31")

        assert len(results) == 3

    def test_half_a_position_is_an_error_not_the_whole_sky(self):
        with pytest.raises(ValueError, match="needs both"):
            search_observations(ra=10, source=observations_table(), start_date="2023-01-01")

        with pytest.raises(ValueError, match="needs both"):
            search_observations(dec=20, source=observations_table(), start_date="2023-01-01")


class TestBenchmarkFilters:
    """panoptes/panoptes-data#14: the cuts data contract 9 says selection makes."""

    def test_min_num_usable_is_not_min_num_frames(self):
        table = observations_table(num_frames=[372, 5, 5], num_usable=[310, 5, 5])

        by_frames = search_observations(
            source=table, start_date="2023-01-01", end_date="2023-12-31", min_num_frames=350
        )
        by_usable = search_observations(
            source=table, start_date="2023-01-01", end_date="2023-12-31", min_num_usable=350
        )

        assert len(by_frames) == 1
        assert len(by_usable) == 0

    def test_min_duration_minutes(self):
        results = search_observations(
            source=observations_table(),
            start_date="2023-01-01",
            end_date="2023-12-31",
            min_duration_minutes=180,
        )

        assert list(results.sequence_sequence_id) == ["PAN002_cccccc_20230601T000000"]

    def test_an_unmeasurable_duration_is_excluded_not_assumed_long_enough(self):
        table = observations_table(duration_minutes=[np.nan, 4.0, 200.0])

        results = search_observations(
            source=table,
            start_date="2023-01-01",
            end_date="2023-12-31",
            min_duration_minutes=1,
        )

        assert list(results.sequence_sequence_id) == [
            "PAN001_bbbbbb_20230102T000000",
            "PAN002_cccccc_20230601T000000",
        ]

    def test_filters_by_field_name_and_camera_id(self):
        by_field = search_observations(
            source=observations_table(),
            start_date="2023-01-01",
            end_date="2023-12-31",
            field_name="B",
        )
        by_camera = search_observations(
            source=observations_table(),
            start_date="2023-01-01",
            end_date="2023-12-31",
            camera_id=["aaaaaa", "cccccc"],
        )

        assert list(by_field.field_name) == ["B"]
        assert list(by_camera.camera_id) == ["aaaaaa", "cccccc"]

    def test_the_query_reaches_the_frame_facts(self):
        results = search_observations(
            source=observations_table(),
            start_date="2023-01-01",
            end_date="2023-12-31",
            query="iso == 100 and moonfrac < 0.25 and airmass < 1.5",
        )

        assert list(results.sequence_sequence_id) == [
            "PAN001_aaaaaa_20230101T000000",
            "PAN002_cccccc_20230601T000000",
        ]

    def test_the_query_is_applied_after_exptime_is_derived(self):
        """It runs last precisely so it can mention a column search itself adds."""
        results = search_observations(
            source=observations_table(),
            start_date="2023-01-01",
            end_date="2023-12-31",
            query="exptime < 50",
        )

        assert list(results.sequence_sequence_id) == ["PAN002_cccccc_20230601T000000"]

    def test_a_query_naming_no_column_says_which_columns_there_are(self):
        with pytest.raises(ValueError, match="Available columns"):
            search_observations(
                source=observations_table(),
                start_date="2023-01-01",
                end_date="2023-12-31",
                query="seeing < 3",
            )


class TestFindSimultaneous:
    """panoptes/panoptes-data#14: same night, same field, two different bodies."""

    def pairs_table(self, **overrides):
        """Two cameras on PAN007 on the same field, plus one unrelated sequence."""
        table = pd.DataFrame(
            {
                "sequence_sequence_id": [
                    "PAN007_d37295_20250407T061910",
                    "PAN007_f6eb3d_20250407T061910",
                    "PAN012_358d0f_20250407T061910",
                ],
                "unit_id": ["PAN007", "PAN007", "PAN012"],
                "camera_id": ["d37295", "f6eb3d", "358d0f"],
                "field_name": ["M42", "M42", "Andromeda"],
                "num_frames": [372, 372, 10],
                "num_usable": [310, 372, 10],
                "start_time": [
                    "2025-04-07T06:19:10+00:00",
                    "2025-04-07T06:20:10+00:00",
                    "2025-04-07T06:19:10+00:00",
                ],
                "end_time": [
                    "2025-04-07T10:19:10+00:00",
                    "2025-04-07T10:20:10+00:00",
                    "2025-04-07T10:19:10+00:00",
                ],
            }
        )
        return table.assign(**overrides)

    def test_two_cameras_on_one_unit_are_a_pair(self):
        pairs = find_simultaneous(self.pairs_table())

        assert len(pairs) == 1
        assert pairs.camera_id_a.iloc[0] == "d37295"
        assert pairs.camera_id_b.iloc[0] == "f6eb3d"
        assert pairs.field_name.iloc[0] == "M42"
        # Started a minute late, ended a minute late: four hours less a minute.
        assert pairs.overlap_minutes.iloc[0] == pytest.approx(239.0)

    def test_a_different_field_is_not_a_pair_unless_asked(self):
        table = self.pairs_table(field_name=["M42", "Andromeda", "Triangulum"])

        assert len(find_simultaneous(table)) == 0
        assert len(find_simultaneous(table, same_field=False)) == 3

    def test_across_unit_id_ignores_two_cameras_on_one_unit(self):
        table = self.pairs_table(field_name=["M42", "M42", "M42"])

        by_camera = find_simultaneous(table, across="camera_id")
        by_unit = find_simultaneous(table, across="unit_id")

        assert len(by_camera) == 3
        assert len(by_unit) == 2
        assert set(by_unit.unit_id_a) | set(by_unit.unit_id_b) == {"PAN007", "PAN012"}

    def test_sequences_that_did_not_overlap_are_not_a_pair(self):
        table = self.pairs_table(
            start_time=[
                "2025-04-07T06:19:10+00:00",
                "2025-04-07T11:00:00+00:00",
                "2025-04-07T06:19:10+00:00",
            ],
            end_time=[
                "2025-04-07T10:19:10+00:00",
                "2025-04-07T14:00:00+00:00",
                "2025-04-07T10:19:10+00:00",
            ],
        )

        assert len(find_simultaneous(table)) == 0

    def test_min_overlap_minutes_discards_a_brief_coincidence(self):
        table = self.pairs_table(
            start_time=[
                "2025-04-07T06:19:10+00:00",
                "2025-04-07T10:14:10+00:00",
                "2025-04-07T06:19:10+00:00",
            ],
            end_time=[
                "2025-04-07T10:19:10+00:00",
                "2025-04-07T14:00:00+00:00",
                "2025-04-07T10:19:10+00:00",
            ],
        )

        assert len(find_simultaneous(table, min_overlap_minutes=1)) == 1
        assert len(find_simultaneous(table, min_overlap_minutes=30)) == 0

    def test_an_unrecorded_field_is_not_a_matched_field(self):
        """Two sequences that never said where they pointed are not a known pair."""
        table = self.pairs_table(field_name=[np.nan, np.nan, np.nan])

        assert len(find_simultaneous(table)) == 0
        assert len(find_simultaneous(table, same_field=False)) == 3

    def test_an_unknown_extent_overlaps_nothing(self):
        table = self.pairs_table(end_time=[None, None, None])

        assert len(find_simultaneous(table)) == 0

    def test_a_table_missing_an_optional_column_still_reports_it(self):
        """The result's columns must not depend on what the archive happened to hold."""
        table = self.pairs_table().drop(columns=["num_usable"])

        pairs = find_simultaneous(table)

        assert len(pairs) == 1
        assert pairs.num_usable_a.isna().all()
        assert pairs.num_frames_a.iloc[0] == 372

    def test_an_empty_result_still_has_its_columns(self):
        pairs = find_simultaneous(self.pairs_table(field_name=["A", "B", "C"]))

        assert len(pairs) == 0
        assert "overlap_minutes" in pairs.columns
        assert "sequence_sequence_id_a" in pairs.columns

    def test_the_input_is_not_mutated(self):
        table = self.pairs_table()
        before = table.copy()

        find_simultaneous(table)

        pd.testing.assert_frame_equal(table, before)

    def test_an_unpairable_column_says_what_the_two_ways_are(self):
        with pytest.raises(ValueError, match="camera_id"):
            find_simultaneous(self.pairs_table(), across="field_name")

    def test_a_table_without_the_extent_columns_says_so(self):
        with pytest.raises(ValueError, match="start_time"):
            find_simultaneous(pd.DataFrame({"sequence_sequence_id": ["S1"], "camera_id": ["a"]}))

    def test_a_search_result_feeds_straight_in(self, indexed_root):
        """The whole point: the pairing runs over what `search_observations` returns."""
        results = search_observations(source=get_all_observations(), start_date="2018-01-01")

        assert len(find_simultaneous(results)) == 0


class TestGetMetadata:
    """panoptes/panoptes-data#13: `except Exception: pass` over the whole loop."""

    def failing_reader(self, monkeypatch, fail_on):
        class FakeObsInfo:
            def __init__(self, meta=None):
                name = meta["sequence_sequence_id"]
                if name in fail_on:
                    raise RuntimeError(f"no documents for {name}")
                self.image_metadata = pd.DataFrame({"a": [1]})

        monkeypatch.setattr(search_mod, "ObservationInfo", FakeObsInfo)

    def observations(self):
        return pd.DataFrame({"sequence_sequence_id": ["S1", "S2", "S3"]})

    def test_a_failure_is_raised_rather_than_passed_over(self, monkeypatch):
        self.failing_reader(monkeypatch, {"S2"})

        with pytest.raises(MetadataUnavailableError, match="1 of 3"):
            search_mod.get_metadata(self.observations())

    def test_every_sequence_failing_is_not_an_empty_table(self, monkeypatch):
        """The defect exactly: a total failure read as an archive with nothing in it."""
        self.failing_reader(monkeypatch, {"S1", "S2", "S3"})

        with pytest.raises(MetadataUnavailableError) as excinfo:
            search_mod.get_metadata(self.observations())

        assert len(excinfo.value.partial) == 0
        assert set(excinfo.value.failures) == {"S1", "S2", "S3"}

    def test_the_exception_carries_the_failures_and_what_did_read(self, monkeypatch):
        self.failing_reader(monkeypatch, {"S2"})

        with pytest.raises(MetadataUnavailableError) as excinfo:
            search_mod.get_metadata(self.observations())

        assert list(excinfo.value.failures) == ["S2"]
        assert isinstance(excinfo.value.failures["S2"], RuntimeError)
        assert len(excinfo.value.partial) == 2

    def test_warn_returns_the_partial_table(self, monkeypatch, caplog):
        self.failing_reader(monkeypatch, {"S2"})

        with caplog.at_level("WARNING"):
            result = search_mod.get_metadata(self.observations(), errors="warn")

        assert len(result) == 2
        assert "1 of 3" in caplog.text

    def test_no_failures_is_just_the_table(self, monkeypatch):
        self.failing_reader(monkeypatch, set())

        assert len(search_mod.get_metadata(self.observations())) == 3

    def test_nothing_to_read_is_an_empty_table_not_a_concat_error(self, monkeypatch):
        self.failing_reader(monkeypatch, set())

        assert len(search_mod.get_metadata(pd.DataFrame({"sequence_sequence_id": []}))) == 0

    def test_an_unknown_errors_mode_is_refused(self):
        with pytest.raises(ValueError, match="'raise' or 'warn'"):
            search_mod.get_metadata(pd.DataFrame(), errors="ignore")


def test_get_metadata_concatenates(monkeypatch):
    class FakeObsInfo:
        def __init__(self, meta=None):
            self.image_metadata = pd.DataFrame({"a": [1]})

    monkeypatch.setattr(search_mod, "ObservationInfo", FakeObsInfo)

    assert len(search_mod.get_metadata(pd.DataFrame({"id": [1, 2]}))) == 2


if __name__ == "__main__":
    pytest.main(["-q"])
