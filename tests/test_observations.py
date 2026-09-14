import numpy as np
import pandas as pd
import pytest

from panoptes.data import observations as obs_mod

# A well-formed image uid: unit, camera, sequence start, image start. It is the
# archive path with underscores for separators, which is what makes a frame's
# location derivable from it.
UID = "PAN012_358d0f_20180824T035917_20180824T040118"
UID_PATH = "PAN012/358d0f/20180824T035917/20180824T040118.fits.fz"


class FakeURL:
    def __init__(self, s):
        self._s = s

    def unicode_string(self):
        return self._s


class FakeSettings:
    def __init__(self, meta_url=None, base_url=None, bucket=None, archive_root=None):
        self.img_metadata_url = FakeURL(meta_url or "http://metadata.example/")
        # img_base_url used by get_image_list
        self.img_base_url = FakeURL(base_url or "http://base.example/")
        self.img_bucket = bucket or "bucket"
        self.archive_root = archive_root


def make_meta_df():
    # Records that carry a `public_url` alongside the uid.
    df = pd.DataFrame(
        {
            "time": ["2020-01-02T00:00:00+00:00", "2020-01-01T00:00:00+00:00"],
            "public_url": ["http://example.com/a.fits", "http://example.com/b.fits"],
            "uid": ["PAN001_abc123_20200102T000000_20200102T000100",
                    "PAN002_def456_20200101T000000_20200101T000100"],
        }
    )
    return df


def make_2025_meta_df():
    # Records that carry no `public_url` at all.
    df = pd.DataFrame(
        {
            "time": ["2025-04-07T06:19:56+00:00"],
            "uploaded_public_url": ["http://example.com/incoming/a.fits.fz"],
            "fits_public_url": ["http://example.com/processed/a.fits.fz"],
            "jpg_public_url": ["http://example.com/incoming/a.jpg"],
            "uid": ["PAN007_d37295_20250407T061910_20250407T061956"],
        }
    )
    return df


def make_one_frame_df(uid=UID):
    return pd.DataFrame({"time": ["2018-08-24T04:01:18+00:00"], "uid": [uid]})


def patch_metadata(monkeypatch, df, **settings):
    """Point ObservationInfo at `df` instead of the network."""
    monkeypatch.setattr(obs_mod, "SurveySettings", lambda: FakeSettings(**settings))
    monkeypatch.setattr(obs_mod.pd, "read_csv", lambda url: df.copy())


def plant_frames(root, *relative_paths):
    """Create empty files at `relative_paths` under `root`, as a local archive."""
    for relative_path in relative_paths:
        frame = root / relative_path
        frame.parent.mkdir(parents=True, exist_ok=True)
        frame.touch()
    return root


def test_get_metadata_parses_and_sorts(monkeypatch):
    patch_metadata(monkeypatch, make_meta_df(), meta_url="unused")

    oi = obs_mod.ObservationInfo(sequence_id="SEQ123")

    # get_metadata should return a DataFrame indexed by datetime and sorted
    meta = oi.get_metadata()
    assert isinstance(meta.index, pd.DatetimeIndex)
    # dates should be sorted ascending
    assert list(meta.index) == sorted(list(meta.index))
    # public_url column preserved
    assert "public_url" in meta.columns


def test_sequence_without_public_url_still_works(monkeypatch):
    """Regression for panoptes/panoptes-data#12: some records have no `public_url`."""
    patch_metadata(monkeypatch, make_2025_meta_df(), base_url="http://cdn/", bucket="PANBUCKET")

    oi = obs_mod.ObservationInfo(sequence_id="PAN007_d37295_20250407T061910")

    assert "public_url" not in oi.image_metadata.columns
    assert oi.image_list == [
        "http://cdn/PANBUCKET/PAN007/d37295/20250407T061910/20250407T061956.fits.fz"
    ]


def test_image_list_matches_the_public_url_column(monkeypatch):
    """The uid-derived URL names the same raw frame the `public_url` column does."""
    with_url = pd.DataFrame({
        "time": ["2018-08-24T04:01:18+00:00"],
        "uid": [UID],
        "public_url": [("https://storage.googleapis.com/panoptes-images-incoming/"
                        "PAN012/358d0f/20180824T035917/20180824T040118.fits.fz")],
    })
    patch_metadata(monkeypatch, with_url,
                   base_url="https://storage.googleapis.com/",
                   bucket="panoptes-images-incoming")

    oi = obs_mod.ObservationInfo(sequence_id="PAN012_358d0f_20180824T035917")

    assert oi.image_list == list(oi.image_metadata.public_url.values)


def test_missing_required_column_raises_named_error(monkeypatch):
    """A missing column raises a ValueError naming the sequence."""
    no_uid = make_meta_df().drop(columns=["uid"])
    patch_metadata(monkeypatch, no_uid)

    with pytest.raises(ValueError, match="SEQ123.*uid"):
        obs_mod.ObservationInfo(sequence_id="SEQ123")


def test_public_urls_returns_whatever_is_present(monkeypatch):
    patch_metadata(monkeypatch, make_2025_meta_df())
    oi = obs_mod.ObservationInfo(sequence_id="SEQ")
    assert sorted(oi.public_urls.columns) == [
        "fits_public_url", "jpg_public_url", "uploaded_public_url"
    ]

    patch_metadata(monkeypatch, make_meta_df().drop(columns=["public_url"]))
    oi = obs_mod.ObservationInfo(sequence_id="SEQ")
    assert list(oi.public_urls.columns) == []


def test_get_image_list_builds_expected_urls(monkeypatch):
    patch_metadata(monkeypatch, make_one_frame_df(), base_url="http://cdn/", bucket="PANBUCKET")

    oi = obs_mod.ObservationInfo(sequence_id="SEQ")

    lst = oi.get_image_list()
    assert isinstance(lst, list)
    # uid underscores should be replaced by slashes and have file ext appended
    assert lst == [f"http://cdn/PANBUCKET/{UID_PATH}"]


def test_malformed_uid_raises_naming_the_uid_and_sequence(monkeypatch):
    """A uid that is not an archive path cannot locate a frame, so it raises."""
    patch_metadata(monkeypatch, make_one_frame_df(uid="PAN001_abc_foo"))

    with pytest.raises(ValueError, match="PAN001_abc_foo.*SEQ123"):
        obs_mod.ObservationInfo(sequence_id="SEQ123")


class TestLocalArchive:
    """Resolving a sequence against a local copy of the archive.

    Regression for panoptes/panoptes-data#19.
    """

    def test_image_list_is_local_paths_under_the_root(self, monkeypatch, tmp_path):
        plant_frames(tmp_path, UID_PATH)
        patch_metadata(monkeypatch, make_one_frame_df(), archive_root=tmp_path)

        oi = obs_mod.ObservationInfo(sequence_id="SEQ")

        assert oi.image_list == [tmp_path / UID_PATH]
        assert oi.image_list[0].exists()

    def test_bucket_does_not_leak_into_local_paths(self, monkeypatch, tmp_path):
        """The bucket is a cloud-era detail; the root absorbs any level above the units."""
        plant_frames(tmp_path, UID_PATH)
        patch_metadata(monkeypatch, make_one_frame_df(),
                       archive_root=tmp_path, bucket="panoptes-images-incoming")

        oi = obs_mod.ObservationInfo(sequence_id="SEQ")

        assert oi.get_image_list(bucket="some-other-bucket") == [tmp_path / UID_PATH]

    def test_archive_root_argument_overrides_the_setting(self, monkeypatch, tmp_path):
        configured = plant_frames(tmp_path / "configured", UID_PATH)
        other = plant_frames(tmp_path / "other", UID_PATH)
        patch_metadata(monkeypatch, make_one_frame_df(), archive_root=configured)

        oi = obs_mod.ObservationInfo(sequence_id="SEQ")

        assert oi.get_image_list() == [configured / UID_PATH]
        assert oi.get_image_list(archive_root=other) == [other / UID_PATH]

    def test_urls_when_no_root_is_configured(self, monkeypatch):
        """No root, no local copy: the locations are still URLs, still not fetchable."""
        patch_metadata(monkeypatch, make_one_frame_df(),
                       base_url="http://cdn/", bucket="PANBUCKET", archive_root=None)

        oi = obs_mod.ObservationInfo(sequence_id="SEQ")

        assert oi.image_list == [f"http://cdn/PANBUCKET/{UID_PATH}"]

    def test_missing_frame_raises_naming_the_path(self, monkeypatch, tmp_path):
        """A partial archive must not quietly become a shorter observation."""
        second_uid = "PAN012_358d0f_20180824T035917_20180824T040248"
        second_path = "PAN012/358d0f/20180824T035917/20180824T040248.fits.fz"
        two_frames = pd.DataFrame({
            "time": ["2018-08-24T04:01:18+00:00", "2018-08-24T04:02:48+00:00"],
            "uid": [UID, second_uid],
        })
        # Only the first frame was copied.
        plant_frames(tmp_path, UID_PATH)
        patch_metadata(monkeypatch, two_frames, archive_root=tmp_path)

        with pytest.raises(FileNotFoundError) as excinfo:
            obs_mod.ObservationInfo(sequence_id="PAN012_358d0f_20180824T035917")

        message = str(excinfo.value)
        assert str(tmp_path / second_path) in message
        assert "PAN012_358d0f_20180824T035917" in message

    def test_root_that_is_not_a_directory_says_so(self, monkeypatch, tmp_path):
        """A mistyped root is not a partial copy, and should not be reported as one."""
        patch_metadata(monkeypatch, make_one_frame_df(), archive_root=tmp_path / "typo")

        with pytest.raises(FileNotFoundError, match="is not a directory"):
            obs_mod.ObservationInfo(sequence_id="SEQ")

    def test_extension_is_matched_exactly(self, monkeypatch, tmp_path):
        """A decompressed `.fits` does not stand in for the `.fits.fz` the layout names."""
        plant_frames(tmp_path, UID_PATH.replace(".fits.fz", ".fits"))
        patch_metadata(monkeypatch, make_one_frame_df(), archive_root=tmp_path)

        with pytest.raises(FileNotFoundError, match=r"\.fits\.fz"):
            obs_mod.ObservationInfo(sequence_id="SEQ")

        # Asking for that extension explicitly does find it.
        patch_metadata(monkeypatch, make_one_frame_df(), archive_root=None)
        oi = obs_mod.ObservationInfo(sequence_id="SEQ")
        assert oi.get_image_list(file_ext=".fits", archive_root=tmp_path) == [
            tmp_path / UID_PATH.replace(".fits.fz", ".fits")
        ]

    def test_root_comes_from_the_environment(self, monkeypatch, tmp_path):
        """The real settings class, not the fake: `PANOPTES_ARCHIVE_ROOT` configures it."""
        plant_frames(tmp_path, UID_PATH)
        monkeypatch.setenv("PANOPTES_ARCHIVE_ROOT", str(tmp_path))
        monkeypatch.setattr(obs_mod.pd, "read_csv", lambda url: make_one_frame_df())

        oi = obs_mod.ObservationInfo(sequence_id="SEQ")

        assert oi.image_list == [tmp_path / UID_PATH]

    def test_get_image_data_reads_the_local_frame(self, monkeypatch, tmp_path):
        """The whole point: `get_image_data` works again once a root is configured."""
        from astropy.io import fits

        frame = tmp_path / UID_PATH
        frame.parent.mkdir(parents=True, exist_ok=True)
        # An fpacked frame, as the archive holds them.
        fits.CompImageHDU(np.arange(4, dtype=np.float32).reshape(2, 2)).writeto(frame)

        patch_metadata(monkeypatch, make_one_frame_df(), archive_root=tmp_path)
        oi = obs_mod.ObservationInfo(sequence_id="SEQ")

        ccd = oi.get_image_data(idx=0)

        assert ccd.data.tolist() == [[0.0, 1.0], [2.0, 3.0]]
        assert str(ccd.unit) == "adu"


def test_get_image_data_uses_fits_utils(monkeypatch):
    patch_metadata(monkeypatch, make_one_frame_df())

    # Create a fake CCDData constructor to capture inputs
    class FakeCCDData:
        def __init__(self, data, wcs=None, unit=None, meta=None):
            self.data = data
            self.wcs = wcs
            self.unit = unit
            self.meta = meta

    # Patch CCDData and fits_utils
    monkeypatch.setattr(obs_mod, "CCDData", FakeCCDData)

    def fake_getdata(url, header=False):
        return (np.array([[1, 2], [3, 4]]), {"FAKE": True})

    def fake_getwcs(url):
        return "WCSOBJ"

    monkeypatch.setattr(obs_mod.fits_utils, "getdata", fake_getdata)
    monkeypatch.setattr(obs_mod.fits_utils, "getwcs", fake_getwcs)

    oi = obs_mod.ObservationInfo(sequence_id="SEQ")
    ccd = oi.get_image_data(idx=0)

    assert isinstance(ccd, FakeCCDData)
    assert (ccd.data == np.array([[1, 2], [3, 4]])).all()
    assert ccd.wcs == "WCSOBJ"
    assert ccd.unit == "adu"
    assert ccd.meta.get("FAKE") is True


def test_download_images_raises_and_warns(monkeypatch):
    """Regression for panoptes/panoptes-data#17: nothing serves the frames."""
    patch_metadata(monkeypatch, make_one_frame_df())

    oi = obs_mod.ObservationInfo(sequence_id="SEQ")

    with pytest.warns(DeprecationWarning), pytest.raises(obs_mod.ImagesUnavailableError):
        oi.download_images(output_dir="unused", show_progress=False)


def test_download_images_raises_even_when_warn_on_error(monkeypatch):
    """`warn_on_error` used to turn every failed fetch into an empty result."""
    patch_metadata(monkeypatch, make_one_frame_df())

    oi = obs_mod.ObservationInfo(sequence_id="SEQ")

    with pytest.warns(DeprecationWarning), pytest.raises(obs_mod.ImagesUnavailableError):
        oi.download_images(warn_on_error=True, show_progress=False)


def test_unavailable_message_points_at_the_archive_root(monkeypatch):
    """The deprecation has somewhere to send you now that #19 is resolved."""
    assert "PANOPTES_ARCHIVE_ROOT" in obs_mod.IMAGES_UNAVAILABLE_MESSAGE


if __name__ == "__main__":
    pytest.main(["-q"])
