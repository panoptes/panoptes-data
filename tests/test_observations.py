import numpy as np
import pandas as pd
import pytest
from conftest import SEQUENCE_ID, frame_document, write_sequence

from panoptes.data import documents
from panoptes.data import observations as obs_mod

# The archive-relative path of the first frame, derived from its `image_uid`.
UID = "PAN012_358d0f_20180824T035917_20180824T040118"
UID_PATH = "PAN012/358d0f/20180824T035917/20180824T040118.fits.fz"
SECOND_UID_PATH = "PAN012/358d0f/20180824T035917/20180824T040248.fits.fz"


def plant_frames(root, *relative_paths):
    """Create empty files at `relative_paths` under `root`, as a local raw archive."""
    for relative_path in relative_paths:
        frame = root / relative_path
        frame.parent.mkdir(parents=True, exist_ok=True)
        frame.touch()
    return root


class TestSequenceIdOf:
    def test_reads_the_observation_index_column(self):
        assert obs_mod.sequence_id_of(pd.Series({"sequence_sequence_id": SEQUENCE_ID})) == (
            SEQUENCE_ID
        )

    def test_reads_a_flattened_observation_document(self):
        assert obs_mod.sequence_id_of({"sequence_id": SEQUENCE_ID}) == SEQUENCE_ID

    def test_reads_an_attribute_style_row(self):
        """`itertuples` and friends hand back an object, not a mapping."""
        row = next(pd.DataFrame({"sequence_sequence_id": [SEQUENCE_ID]}).itertuples(index=False))

        assert obs_mod.sequence_id_of(row) == SEQUENCE_ID

    def test_an_unrecognized_attribute_style_row_also_raises(self):
        row = next(pd.DataFrame({"seq": [SEQUENCE_ID]}).itertuples(index=False))

        with pytest.raises(ValueError, match="sequence_sequence_id"):
            obs_mod.sequence_id_of(row)

    def test_an_unrecognized_record_says_what_it_looked_for(self):
        """Guessing which field holds an id is what #12 and #13 both were."""
        with pytest.raises(ValueError, match="sequence_sequence_id"):
            obs_mod.sequence_id_of({"seq": SEQUENCE_ID})


class TestConstruction:
    def test_from_a_sequence_id(self, processed_root):
        obs_info = obs_mod.ObservationInfo(sequence_id=SEQUENCE_ID)

        assert obs_info.sequence_id == SEQUENCE_ID
        assert len(obs_info.image_list) == 2

    def test_a_sequence_id_alone_now_has_metadata(self, processed_root):
        """It used to be an empty dict, which made "from an id" the lesser way in."""
        obs_info = obs_mod.ObservationInfo(sequence_id=SEQUENCE_ID)

        assert obs_info.meta["num_frames"] == 2
        assert obs_info.meta["sequence_field_name"] == "M42"

    def test_from_a_row_of_search_results(self, processed_root):
        row = pd.Series({"sequence_sequence_id": SEQUENCE_ID, "num_usable": 2})

        obs_info = obs_mod.ObservationInfo(meta=row)

        assert obs_info.sequence_id == SEQUENCE_ID
        assert obs_info.meta is row

    def test_neither_a_sequence_id_nor_meta_is_an_error(self, processed_root):
        with pytest.raises(ValueError, match="required"):
            obs_mod.ObservationInfo()

    def test_no_processed_root_points_at_the_setting(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("PANOPTES_PROCESSED_ROOT", raising=False)

        with pytest.raises(documents.DocumentsUnavailableError, match="PANOPTES_PROCESSED_ROOT"):
            obs_mod.ObservationInfo(sequence_id=SEQUENCE_ID)

    def test_the_processed_root_argument_overrides_the_setting(self, tmp_path, frames, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("PANOPTES_PROCESSED_ROOT", raising=False)
        elsewhere = tmp_path / "elsewhere"
        write_sequence(elsewhere, frames)

        obs_info = obs_mod.ObservationInfo(sequence_id=SEQUENCE_ID, processed_root=elsewhere)

        assert len(obs_info.image_metadata) == 2


class TestMetadata:
    def test_columns_are_the_contract_names(self, processed_root):
        meta = obs_mod.ObservationInfo(sequence_id=SEQUENCE_ID).image_metadata

        assert "image_uid" in meta.columns
        assert "image_camera_exptime" in meta.columns
        assert "sequence_coordinates_mount_ra" in meta.columns
        # Not the Firestore summary's dotted view over the same map.
        assert "coordinates.mount_ra" not in meta.columns

    def test_indexed_on_image_time_and_sorted(self, processed_root):
        meta = obs_mod.ObservationInfo(sequence_id=SEQUENCE_ID).image_metadata

        assert isinstance(meta.index, pd.DatetimeIndex)
        assert list(meta.index) == sorted(meta.index)

    def test_the_time_column_survives_becoming_the_index(self, processed_root):
        """Dropping it would make it unreachable from an `image_query`."""
        meta = obs_mod.ObservationInfo(sequence_id=SEQUENCE_ID).image_metadata

        assert "image_image_time" in meta.columns

    def test_an_image_query_filters_on_contract_names(self, tmp_path, monkeypatch):
        root = tmp_path / "processed"
        write_sequence(
            root,
            [
                frame_document(image_time="20180824T040118", status="MATCHED"),
                frame_document(image_time="20180824T040248", status="ERROR"),
            ],
        )
        monkeypatch.setenv("PANOPTES_PROCESSED_ROOT", str(root))

        obs_info = obs_mod.ObservationInfo(
            sequence_id=SEQUENCE_ID, image_query='image_status != "ERROR"'
        )

        assert len(obs_info.image_metadata) == 1
        assert len(obs_info.image_list) == 1

    def test_usable_query_keeps_only_the_frames_the_pipeline_matched(self, tmp_path, monkeypatch):
        """The per-frame filter the old observation-level `status` never was."""
        root = tmp_path / "processed"
        write_sequence(
            root,
            [
                frame_document(image_time="20180824T040118", status="MATCHED"),
                frame_document(image_time="20180824T040248", status="ERROR"),
                frame_document(image_time="20180824T040418", status="MATCHED"),
            ],
        )
        monkeypatch.setenv("PANOPTES_PROCESSED_ROOT", str(root))

        obs_info = obs_mod.ObservationInfo(
            sequence_id=SEQUENCE_ID, image_query=obs_mod.USABLE_QUERY
        )

        assert list(obs_info.image_metadata.image_status) == ["MATCHED", "MATCHED"]
        # The frame list follows the query, so a failed frame is not read either.
        assert len(obs_info.image_list) == 2

    def test_the_default_is_usable_only(self, tmp_path, monkeypatch):
        """Reading pixels wants the frames that processed cleanly, by default."""
        root = tmp_path / "processed"
        write_sequence(
            root,
            [
                frame_document(image_time="20180824T040118", status="MATCHED"),
                frame_document(image_time="20180824T040248", status="ERROR"),
            ],
        )
        monkeypatch.setenv("PANOPTES_PROCESSED_ROOT", str(root))

        obs_info = obs_mod.ObservationInfo(sequence_id=SEQUENCE_ID)

        assert list(obs_info.image_metadata.image_status) == ["MATCHED"]

    def test_an_empty_query_gives_every_frame(self, tmp_path, monkeypatch):
        """The failed frames are still reachable; they are just not the default."""
        root = tmp_path / "processed"
        write_sequence(
            root,
            [
                frame_document(image_time="20180824T040118", status="MATCHED"),
                frame_document(image_time="20180824T040248", status="ERROR"),
            ],
        )
        monkeypatch.setenv("PANOPTES_PROCESSED_ROOT", str(root))

        obs_info = obs_mod.ObservationInfo(sequence_id=SEQUENCE_ID, image_query="")

        assert sorted(obs_info.image_metadata.image_status) == ["ERROR", "MATCHED"]

    def test_excluded_frames_are_visible_not_merely_absent(self, tmp_path, monkeypatch):
        """A filtered observation must not be mistakable for a smaller one."""
        root = tmp_path / "processed"
        write_sequence(
            root,
            [
                frame_document(image_time="20180824T040118", status="MATCHED"),
                frame_document(image_time="20180824T040248", status="ERROR"),
            ],
        )
        monkeypatch.setenv("PANOPTES_PROCESSED_ROOT", str(root))

        obs_info = obs_mod.ObservationInfo(sequence_id=SEQUENCE_ID)

        assert obs_info.num_frames == 2
        assert len(obs_info.image_metadata) == 1
        assert "num_frames=1 of 2" in repr(obs_info)

    def test_nothing_excluded_reads_plainly(self, processed_root):
        obs_info = obs_mod.ObservationInfo(sequence_id=SEQUENCE_ID)

        assert "num_frames=2" in repr(obs_info)
        assert " of " not in repr(obs_info)

    def test_a_document_missing_a_required_field_raises_naming_it(self, tmp_path, monkeypatch):
        no_uid = frame_document(image_time="20180824T040118")
        del no_uid["image"]["uid"]
        root = tmp_path / "processed"
        write_sequence(root, [no_uid])
        monkeypatch.setenv("PANOPTES_PROCESSED_ROOT", str(root))

        with pytest.raises(ValueError, match=f"{SEQUENCE_ID}.*image_uid"):
            obs_mod.ObservationInfo(sequence_id=SEQUENCE_ID)

    def test_the_observation_document_is_exposed(self, processed_root):
        obs_info = obs_mod.ObservationInfo(sequence_id=SEQUENCE_ID)

        assert obs_info.status == "MATCHED"
        assert obs_info.params_fingerprint == "a1b2c3d4e5f6"


class TestPublicUrls:
    def test_no_url_field_is_the_normal_case(self, processed_root):
        """URLs are decorations added by whatever uploads, not part of the document."""
        obs_info = obs_mod.ObservationInfo(sequence_id=SEQUENCE_ID)

        assert list(obs_info.public_urls.columns) == []
        assert len(obs_info.image_list) == 2

    def test_a_decorated_document_has_its_urls_read_back(self, tmp_path, monkeypatch):
        decorated = frame_document(image_time="20180824T040118")
        decorated["image"]["fits_public_url"] = "http://example.com/a.fits.fz"
        decorated["image"]["jpg_public_url"] = "http://example.com/a.jpg"
        root = tmp_path / "processed"
        write_sequence(root, [decorated])
        monkeypatch.setenv("PANOPTES_PROCESSED_ROOT", str(root))

        obs_info = obs_mod.ObservationInfo(sequence_id=SEQUENCE_ID)

        assert sorted(obs_info.public_urls.columns) == [
            "image_fits_public_url",
            "image_jpg_public_url",
        ]


class TestImageList:
    def test_urls_when_no_archive_root_is_configured(self, processed_root, monkeypatch):
        monkeypatch.setenv("PANOPTES_IMG_BASE_URL", "http://cdn/")
        monkeypatch.setenv("PANOPTES_IMG_BUCKET", "PANBUCKET")

        obs_info = obs_mod.ObservationInfo(sequence_id=SEQUENCE_ID)

        assert obs_info.image_list[0] == f"http://cdn/PANBUCKET/{UID_PATH}"

    def test_local_paths_under_the_archive_root(self, processed_root, tmp_path, monkeypatch):
        raw = plant_frames(tmp_path / "raw", UID_PATH, SECOND_UID_PATH)
        monkeypatch.setenv("PANOPTES_ARCHIVE_ROOT", str(raw))

        obs_info = obs_mod.ObservationInfo(sequence_id=SEQUENCE_ID)

        assert obs_info.image_list == [raw / UID_PATH, raw / SECOND_UID_PATH]

    def test_the_raw_archive_is_not_the_processed_tree(self, processed_root, tmp_path, monkeypatch):
        """Two roots, deliberately: outputs regenerate without touching inputs."""
        raw = plant_frames(tmp_path / "raw", UID_PATH, SECOND_UID_PATH)
        monkeypatch.setenv("PANOPTES_ARCHIVE_ROOT", str(raw))

        obs_info = obs_mod.ObservationInfo(sequence_id=SEQUENCE_ID)

        assert processed_root not in obs_info.image_list[0].parents

    def test_bucket_does_not_leak_into_local_paths(self, processed_root, tmp_path, monkeypatch):
        raw = plant_frames(tmp_path / "raw", UID_PATH, SECOND_UID_PATH)
        monkeypatch.setenv("PANOPTES_ARCHIVE_ROOT", str(raw))

        obs_info = obs_mod.ObservationInfo(sequence_id=SEQUENCE_ID)

        assert obs_info.get_image_list(bucket="some-other-bucket")[0] == raw / UID_PATH

    def test_archive_root_argument_overrides_the_setting(
        self, processed_root, tmp_path, monkeypatch
    ):
        configured = plant_frames(tmp_path / "configured", UID_PATH, SECOND_UID_PATH)
        other = plant_frames(tmp_path / "other", UID_PATH, SECOND_UID_PATH)
        monkeypatch.setenv("PANOPTES_ARCHIVE_ROOT", str(configured))

        obs_info = obs_mod.ObservationInfo(sequence_id=SEQUENCE_ID)

        assert obs_info.get_image_list()[0] == configured / UID_PATH
        assert obs_info.get_image_list(archive_root=other)[0] == other / UID_PATH

    def test_missing_frame_raises_naming_the_path(self, processed_root, tmp_path, monkeypatch):
        """A partial archive must not quietly become a shorter observation."""
        raw = plant_frames(tmp_path / "raw", UID_PATH)  # the second was not copied
        monkeypatch.setenv("PANOPTES_ARCHIVE_ROOT", str(raw))

        with pytest.raises(FileNotFoundError) as excinfo:
            obs_mod.ObservationInfo(sequence_id=SEQUENCE_ID)

        assert str(raw / SECOND_UID_PATH) in str(excinfo.value)
        assert SEQUENCE_ID in str(excinfo.value)

    def test_root_that_is_not_a_directory_says_so(self, processed_root, tmp_path, monkeypatch):
        monkeypatch.setenv("PANOPTES_ARCHIVE_ROOT", str(tmp_path / "typo"))

        with pytest.raises(FileNotFoundError, match="is not a directory"):
            obs_mod.ObservationInfo(sequence_id=SEQUENCE_ID)

    def test_extension_is_matched_exactly(self, processed_root, tmp_path, monkeypatch):
        """A decompressed `.fits` does not stand in for the `.fits.fz` the layout names."""
        raw = plant_frames(
            tmp_path / "raw",
            UID_PATH.replace(".fits.fz", ".fits"),
            SECOND_UID_PATH.replace(".fits.fz", ".fits"),
        )
        monkeypatch.setenv("PANOPTES_ARCHIVE_ROOT", str(raw))

        with pytest.raises(FileNotFoundError, match=r"\.fits\.fz"):
            obs_mod.ObservationInfo(sequence_id=SEQUENCE_ID)

        monkeypatch.delenv("PANOPTES_ARCHIVE_ROOT")
        obs_info = obs_mod.ObservationInfo(sequence_id=SEQUENCE_ID)
        assert obs_info.get_image_list(file_ext=".fits", archive_root=raw)[0] == (
            raw / UID_PATH.replace(".fits.fz", ".fits")
        )

    def test_malformed_uid_raises_naming_the_uid_and_sequence(self, tmp_path, monkeypatch):
        broken = frame_document(image_time="20180824T040118")
        broken["image"]["uid"] = "PAN001_abc_foo"
        root = tmp_path / "processed"
        write_sequence(root, [broken])
        monkeypatch.setenv("PANOPTES_PROCESSED_ROOT", str(root))

        with pytest.raises(ValueError, match=f"PAN001_abc_foo.*{SEQUENCE_ID}"):
            obs_mod.ObservationInfo(sequence_id=SEQUENCE_ID)


class TestImageData:
    def test_reads_the_local_frame(self, processed_root, tmp_path, monkeypatch):
        from astropy.io import fits

        raw = tmp_path / "raw"
        for relative_path in (UID_PATH, SECOND_UID_PATH):
            frame = raw / relative_path
            frame.parent.mkdir(parents=True, exist_ok=True)
            fits.CompImageHDU(np.arange(4, dtype=np.float32).reshape(2, 2)).writeto(frame)
        monkeypatch.setenv("PANOPTES_ARCHIVE_ROOT", str(raw))

        ccd = obs_mod.ObservationInfo(sequence_id=SEQUENCE_ID).get_image_data(idx=0)

        assert ccd.data.tolist() == [[0.0, 1.0], [2.0, 3.0]]
        assert str(ccd.unit) == "adu"

    def test_uses_fits_utils(self, processed_root, monkeypatch):
        class FakeCCDData:
            def __init__(self, data, wcs=None, unit=None, meta=None):
                self.data, self.wcs, self.unit, self.meta = data, wcs, unit, meta

        monkeypatch.setattr(obs_mod, "CCDData", FakeCCDData)
        monkeypatch.setattr(
            obs_mod.fits_utils,
            "getdata",
            lambda url, header=False: (np.array([[1, 2], [3, 4]]), {"FAKE": True}),
        )
        monkeypatch.setattr(obs_mod.fits_utils, "getwcs", lambda url: "WCSOBJ")

        ccd = obs_mod.ObservationInfo(sequence_id=SEQUENCE_ID).get_image_data(idx=0)

        assert (ccd.data == np.array([[1, 2], [3, 4]])).all()
        assert ccd.wcs == "WCSOBJ"
        assert ccd.meta.get("FAKE") is True


class TestDownloadImages:
    """Regression for panoptes/panoptes-data#17: nothing serves the frames."""

    def test_raises_and_warns(self, processed_root):
        obs_info = obs_mod.ObservationInfo(sequence_id=SEQUENCE_ID)

        with pytest.warns(DeprecationWarning), pytest.raises(obs_mod.ImagesUnavailableError):
            obs_info.download_images(output_dir="unused", show_progress=False)

    def test_raises_even_when_warn_on_error(self, processed_root):
        """`warn_on_error` used to turn every failed fetch into an empty result."""
        obs_info = obs_mod.ObservationInfo(sequence_id=SEQUENCE_ID)

        with pytest.warns(DeprecationWarning), pytest.raises(obs_mod.ImagesUnavailableError):
            obs_info.download_images(warn_on_error=True, show_progress=False)

    def test_the_message_points_at_the_archive_root(self):
        assert "PANOPTES_ARCHIVE_ROOT" in obs_mod.IMAGES_UNAVAILABLE_MESSAGE


if __name__ == "__main__":
    pytest.main(["-q"])
