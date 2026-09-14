[![Documentation Status](https://readthedocs.org/projects/panoptes-data/badge/?version=latest)](https://panoptes-data.readthedocs.io/en/latest/?badge=latest)

# PANOPTES Data tools

Tools for searching PANOPTES observations and reading their frames from a
local copy of the archive.

## Install

Install from pip:

```bash
pip install panoptes-data
```

## Examples

See the example Jupyter Notebooks in the `notebooks/` directory.

### Finding observations

```py
from panoptes.data.search import search_observations
from panoptes.data.observations import ObservationInfo

# Find some observations
results = search_observations(by_name='M42')

# Use last result entry to create ObservationInfo object.
obs_info = ObservationInfo(meta=results.iloc[0])
print(obs_info.meta)

# Create an ObservationInfo object directly from a sequence_id.
obs_info = ObservationInfo('PAN001_14d3bd_20180113T052325')
# But then there is no metadata:
print(obs_info.meta)
```

```text
Sample output (truncated):

camera_id                                           14d3bd
camera_lens_serial_number                        HA0028608
camera_serial_number                           12070048413
coordinates_mount_dec                            -6.229778
coordinates_mount_ra                               76.0815
exptime                                              120.0
field_name                                         Wasp 35
num_images                                            28.0
sequence_id                  PAN001_14d3bd_20180113T052325
software_version                                POCSv0.6.0
time                             2018-01-13 05:23:25+00:00
total_exptime                                       3360.0
unit_id                                             PAN001
Name: 6121, dtype: object
```

### Configuration

Settings are read from the environment with a `PANOPTES_` prefix, and from a
`.env` file in the directory you run from. Copy the shipped example and edit
it:

```bash
cp .env.example .env
```

```ini
# .env
PANOPTES_ARCHIVE_ROOT=/data/panoptes-archive
```

A real environment variable beats the file, and an argument beats both, so a
one-off run needs no edit:

```bash
PANOPTES_ARCHIVE_ROOT=/mnt/other-copy panoptes-data search --name M42
```

The `.env` is read from the current working directory, not from wherever the
package is installed -- so run from the directory holding it, or export the
variable instead. Keys that are not settings of this package are ignored, since
a `.env` is usually shared with other tools; the cost is that a *misspelled*
`PANOPTES_*` key is ignored too, so check for a typo if a setting seems not to
take effect.

| Setting | What it locates |
| --- | --- |
| `PANOPTES_ARCHIVE_ROOT` | A local copy of the archive. No default; without it frames resolve to URLs that cannot be fetched. |
| `PANOPTES_IMG_BASE_URL`, `PANOPTES_IMG_BUCKET` | The cloud archive as it was laid out. |
| `PANOPTES_IMG_METADATA_URL` | Per-sequence image metadata. |
| `PANOPTES_OBSERVATIONS_URL` | The `observations.csv` summary that `search_observations` reads. |

### Reading images

Frames are read from a local copy of the archive. `PANOPTES_ARCHIVE_ROOT`
points at the directory holding the unit folders -- the archive keeps the
bucket's layout, so that is
`<root>/PAN012/358d0f/20180824T035917/20180824T040118.fits.fz` -- and a
sequence resolves to files on disk:

```py
from panoptes.data.observations import ObservationInfo

obs_info = ObservationInfo('PAN012_358d0f_20180824T035917')

# Local paths now, rather than archive URLs.
print(obs_info.image_list[0])
# /data/panoptes-archive/PAN012/358d0f/20180824T035917/20180824T040118.fits.fz

ccd = obs_info.get_image_data(idx=0)
```

Every frame the metadata names has to be present: a sequence whose local copy
is incomplete raises `FileNotFoundError` naming the path it looked for, rather
than returning a shorter list that reads as an observation with fewer frames.

Downloading is not possible and the root has no default. Every frame URL the
archive metadata carries points into a Google Cloud Storage bucket that no
longer serves the object anonymously, and there is no public replacement, so
`ObservationInfo.download_images()` raises rather than returning an empty list
([#17][issue-17]). With no root configured `obs_info.image_list` holds those
URLs, which name where each frame lives but are not fetchable.

[issue-17]: https://github.com/panoptes/panoptes-data/issues/17

### Command-line tools

There is a simple command line tool that allows for both searching of observations and downloading of metadata.

#### Search for observations:

```bash
panoptes-data search --name M42 --min-num-images 90
```

Example table output:

```text
| sequence_id                   | field_name   | unit_id   |   coordinates_mount_ra |   coordinates_mount_dec |   num_images |   exptime |   total_exptime | time                      |
|:------------------------------|:-------------|:----------|-----------------------:|------------------------:|-------------:|----------:|----------------:|:--------------------------|
| PAN022_977c86_20220108T090553 | M42          | PAN022    |                83.8221 |                -5.39111 |           95 |   90      |            8550 | 2022-01-08 09:05:53+00:00 |
| PAN022_538cc6_20220108T090553 | M42          | PAN022    |                83.8221 |                -5.39111 |           95 |   89      |            8455 | 2022-01-08 09:05:53+00:00 |
| PAN019_42433a_20220114T085722 | M42          | PAN019    |                83.8221 |                -5.39111 |           90 |   90      |            8100 | 2022-01-14 08:57:22+00:00 |
| PAN019_c623e9_20220114T085722 | M42          | PAN019    |                83.8221 |                -5.39111 |           90 |   89.0222 |            8012 | 2022-01-14 08:57:22+00:00 |
| PAN019_c623e9_20220115T082108 | M42          | PAN019    |                83.8221 |                -5.39111 |          105 |   89.019  |            9347 | 2022-01-15 08:21:08+00:00 |
| PAN019_42433a_20220115T082108 | M42          | PAN019    |                83.8221 |                -5.39111 |          105 |   90.0095 |            9451 | 2022-01-15 08:21:08+00:00 |
```

#### Get all metadata for a unit in a given date range:

```bash
panoptes-data get-metadata --unit-id PAN022 --start-date '2022-01-08'
```

See `panoptes-data --help` for more options.
