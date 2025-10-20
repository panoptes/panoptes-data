
import pandas as pd
import pytest
from astropy.coordinates import SkyCoord

from panoptes.data import search as search_mod
from panoptes.data.search import search_observations, get_all_observations, get_metadata


class FakeURL:
    def __init__(self, s):
        self._s = s

    def unicode_string(self):
        return self._s


class FakeSettings:
    def __init__(self, observations_url=None):
        self.observations_url = FakeURL(observations_url or "http://example.com/obs.csv")


def test_get_all_observations_reads_remote_csv(monkeypatch, tmp_path):
    # Create a temporary CSV and ensure download_file returns its path
    csv_path = tmp_path / "obs.csv"
    df = pd.DataFrame({
        "time": ["2020-01-01", "2020-01-02"],
        "unit_id": ["PAN001", "PAN002"],
        "coordinates.mount_ra": [10.0, 11.0],
        "coordinates.mount_dec": [20.0, 21.0],
        "num_images": [5, 10],
        "total_exptime": [600, 1200],
        "field_name": ["FieldA", "FieldB"],
    })
    df.to_csv(csv_path, index=False)

    # Monkeypatch CloudSettings and download_file
    monkeypatch.setattr(search_mod, "CloudSettings", lambda: FakeSettings(observations_url=str(csv_path)))
    monkeypatch.setattr(search_mod, "download_file", lambda url, **kwargs: str(csv_path))

    out = get_all_observations()
    assert isinstance(out, pd.DataFrame)
    assert len(out) == 2
    assert list(out.columns) >= list(df.columns)


def test_get_metadata_concatenates(monkeypatch):
    # Create fake observations DataFrame with two records
    obs = pd.DataFrame({"id": [1, 2]})

    # Fake ObservationInfo to provide image_metadata
    class FakeObsInfo:
        def __init__(self, meta=None):
            self.image_metadata = pd.DataFrame({"a": [1]})

    monkeypatch.setattr(search_mod, "ObservationInfo", FakeObsInfo)

    meta = get_metadata(obs)
    # Since each of two records returns one-row df, concatenation should yield length 2
    assert isinstance(meta, pd.DataFrame)
    assert len(meta) == 2


def test_search_observations_filters_by_coords_and_unit(monkeypatch):
    # Build a source DataFrame with coordinates and other fields
    src = pd.DataFrame(
        {
            "time": ["2023-01-01T00:00:00", "2023-01-02T00:00:00", "2023-06-01T00:00:00"],
            "coordinates.mount_ra": [10.0, 10.5, 100.0],
            "coordinates.mount_dec": [20.0, 20.1, -10.0],
            "num_images": [5, 2, 100],
            "total_exptime": [500, 200, 1000],
            "unit_id": ["PAN001", "PAN001", "PAN002"],
            "field_name": ["A", "B", "C"],
        }
    )

    # Patch get_all_observations to return our src
    monkeypatch.setattr(search_mod, "get_all_observations", lambda: src.copy())

    # Use a SkyCoord centered at (10,20) with small radius to pick first two rows
    coords = SkyCoord(ra=10.0, dec=20.0, unit='deg')

    # Filter with min_num_images so second row (num_images=2) is removed
    res = search_observations(coords=coords, radius=1.0, min_num_images=3, source=None, start_date="2023-01-01", end_date="2023-12-31")

    # Should return only rows meeting criteria (first row)
    assert isinstance(res, pd.DataFrame)
    assert all(res.num_images >= 3)
    # Check exptime column computed as total_exptime / num_images
    assert "exptime" in res.columns
    assert (res.exptime == res.total_exptime / res.num_images).all()


if __name__ == "__main__":
    pytest.main(["-q"])
