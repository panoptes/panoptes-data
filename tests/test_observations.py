import os
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from panoptes.data import observations as obs_mod


class FakeURL:
    def __init__(self, s):
        self._s = s

    def unicode_string(self):
        return self._s


class FakeSettings:
    def __init__(self, meta_url=None, base_url=None, bucket=None):
        self.img_metadata_url = FakeURL(meta_url or "http://metadata.example/")
        # img_base_url used by get_image_list
        self.img_base_url = FakeURL(base_url or "http://base.example/")
        self.img_bucket = bucket or "bucket"


def make_meta_df():
    # Records that carry a `public_url` alongside the uid.
    df = pd.DataFrame(
        {
            "time": ["2020-01-02T00:00:00+00:00", "2020-01-01T00:00:00+00:00"],
            "public_url": ["http://example.com/a.fits", "http://example.com/b.fits"],
            "uid": ["PAN001_abc_20200102T000000_20200102T000100",
                    "PAN002_def_20200101T000000_20200101T000100"],
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


def patch_metadata(monkeypatch, df, **settings):
    """Point ObservationInfo at `df` instead of the network."""
    monkeypatch.setattr(obs_mod, "CloudSettings", lambda: FakeSettings(**settings))
    monkeypatch.setattr(obs_mod.pd, "read_csv", lambda url: df.copy())


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
        "uid": ["PAN012_358d0f_20180824T035917_20180824T040118"],
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
    # Replace the settings and stub get_metadata so __init__ won't attempt network
    monkeypatch.setattr(
        obs_mod, "CloudSettings",
        lambda: FakeSettings(base_url="http://cdn/", bucket="PANBUCKET")
    )

    # Stub get_metadata to return a dataframe with uids so __init__ sets image_list
    meta_df = pd.DataFrame({
        "time": ["2020-01-01"],
        "uid": ["PAN001_14d3bd_20180113T052325"],
    })
    monkeypatch.setattr(obs_mod.ObservationInfo, "get_metadata", lambda self, query="": meta_df)

    oi = obs_mod.ObservationInfo(sequence_id="SEQ")

    lst = oi.get_image_list()
    assert isinstance(lst, list)
    # uid underscores should be replaced by slashes and have file ext appended
    expected_suffix = "PAN001/14d3bd/20180113T052325.fits.fz"
    assert any(s.endswith(expected_suffix) for s in lst)


def test_get_image_data_uses_fits_utils(monkeypatch):
    # Stub get_metadata to avoid network calls
    monkeypatch.setattr(
        obs_mod.ObservationInfo, "get_metadata",
        lambda self, query="": pd.DataFrame({"time": ["2020-01-01"], "uid": ["PAN001_abc_foo"]})
    )

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


def test_download_images_saves_files_and_handles_errors(monkeypatch, tmp_path):
    # Patch get_metadata and ensure image_list is set
    monkeypatch.setattr(
        obs_mod.ObservationInfo,
        "get_metadata",
        lambda self, query="": pd.DataFrame({
            "time": ["2020-01-01", "2020-01-02"],
            "uid": ["PAN001_abc_good", "PAN001_abc_bad"],
        }),
    )

    oi = obs_mod.ObservationInfo(sequence_id="SEQ")

    # Fake download_file: create a temp file and return its path. For the bad file, raise Exception
    def fake_download_file(url, show_progress=False):
        if "bad" in url:
            raise RuntimeError("download failed")
        tf = tempfile.NamedTemporaryFile(delete=False)
        tf.write(b"data")
        tf.flush()
        tf.close()
        return tf.name

    monkeypatch.setattr(obs_mod, "download_file", fake_download_file)

    # Run download_images and expect it to warn but continue
    outdir = tmp_path / "out"
    paths = oi.download_images(output_dir=str(outdir), show_progress=False, warn_on_error=True)

    # Only the successful file should be returned
    assert len(paths) == 1
    assert Path(paths[0]).exists()
    # returned path should be within the provided output directory
    assert str(outdir) in paths[0]

    # Clean up created temp files
    for p in paths:
        try:
            os.remove(p)
        except Exception:
            pass


if __name__ == "__main__":
    pytest.main(["-q"])
