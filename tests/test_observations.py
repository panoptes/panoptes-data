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
    # Create a simple metadata dataframe with time, public_url and uid columns
    df = pd.DataFrame(
        {
            "time": ["2020-01-02T00:00:00+00:00", "2020-01-01T00:00:00+00:00"],
            "public_url": ["http://example.com/a.fits", "http://example.com/b.fits"],
            "uid": ["PAN001_abc", "PAN002_def"],
        }
    )
    return df


def test_get_metadata_parses_and_sorts(monkeypatch):
    # Patch CloudSettings in the observations module to use our fake
    monkeypatch.setattr(obs_mod, "CloudSettings", lambda: FakeSettings(meta_url="unused"))

    # Patch the pandas read_csv used inside the module to return our test DataFrame
    df = make_meta_df()
    monkeypatch.setattr(obs_mod.pd, "read_csv", lambda url: df.copy())

    oi = obs_mod.ObservationInfo(sequence_id="SEQ123")

    # get_metadata should return a DataFrame indexed by datetime and sorted
    meta = oi.get_metadata()
    assert isinstance(meta.index, pd.DatetimeIndex)
    # dates should be sorted ascending
    assert list(meta.index) == sorted(list(meta.index))
    # public_url column preserved
    assert "public_url" in meta.columns


def test_get_image_list_builds_expected_urls(monkeypatch):
    # Replace the settings and stub get_metadata so __init__ won't attempt network
    monkeypatch.setattr(obs_mod, "CloudSettings", lambda: FakeSettings(base_url="http://cdn/", bucket="PANBUCKET"))

    # Stub get_metadata to return a dataframe with uids and public_url so __init__ sets image_list
    meta_df = pd.DataFrame({
        "time": ["2020-01-01"],
        "uid": ["PAN001_14d3bd_20180113T052325"],
        "public_url": ["http://cdn/PAN001_14d3bd_20180113T052325.fits"],
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
    monkeypatch.setattr(obs_mod.ObservationInfo, "get_metadata", lambda self, query="": pd.DataFrame({"time": ["2020-01-01"], "public_url": ["http://example.com/foo.fits"]}))

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
            "public_url": ["http://example.com/foo.fits", "http://example.com/bad.fits"],
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
