[![Tests](https://github.com/panoptes/panoptes-data/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/panoptes/panoptes-data/actions/workflows/tests.yml)
[![Documentation](https://github.com/panoptes/panoptes-data/actions/workflows/docs.yml/badge.svg?branch=main)](https://panoptes.github.io/panoptes-data/)
[![Coverage](https://codecov.io/gh/panoptes/panoptes-data/branch/main/graph/badge.svg)](https://codecov.io/gh/panoptes/panoptes-data)
[![PyPI](https://img.shields.io/pypi/v/panoptes-data)](https://pypi.org/project/panoptes-data/)
[![Python](https://img.shields.io/pypi/pyversions/panoptes-data)](https://pypi.org/project/panoptes-data/)
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE.txt)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

# PANOPTES Data tools

Tools for searching PANOPTES observations and reading their frames from a
local copy of the archive.

Metadata comes from the documents [panoptes-pipeline][pipeline] writes -- an
`observation.json` per sequence, a `metadata.json` per frame, and a parquet
index built by walking them. It used to come from a Firestore-derived
`observations.csv` and a cloud function, both downstream of a pipeline that
stopped producing them; see [#15][issue-15] and the pipeline's
[data contract][contract].

[pipeline]: https://github.com/panoptes/panoptes-pipeline
[contract]: https://github.com/panoptes/panoptes-pipeline/blob/main/plans/data-contract.md
[issue-15]: https://github.com/panoptes/panoptes-data/issues/15

## Install

Install from pip:

```bash
pip install panoptes-data
```

## Examples

### Finding observations

```py
from panoptes.data.search import search_observations
from panoptes.data.observations import ObservationInfo

# Find some observations
results = search_observations(by_name="M42")

# Use a result entry to create an ObservationInfo object.
obs_info = ObservationInfo(meta=results.iloc[0])

# Or go straight to a sequence id -- the observation document is read either
# way, so this is no longer the lesser way in.
obs_info = ObservationInfo("PAN001_14d3bd_20180113T052325")
print(obs_info.meta)
```

```text
Sample output (truncated), from the sequence's observation.json:

sequence_id                       PAN001_14d3bd_20180113T052325
unit_id                                                  PAN001
camera_id                                                14d3bd
sequence_time                         2018-01-13T05:23:25+00:00
num_frames                                                   28
num_processed                                                28
status                                                  MATCHED
params_fingerprint                                 a1b2c3d4e5f6
sequence_field_name                                     Wasp 35
sequence_camera_camera_id                                14d3bd
sequence_camera_serial_number                       12070048413
sequence_camera_lens_serial_number                    HA0028608
sequence_coordinates_mount_ra                           76.0815
sequence_coordinates_mount_dec                        -6.229778
sequence_software_version                            POCSv0.6.0
```

The names are the document's nested keys joined with `_`, which is exactly what
the index calls the same values. A dotted name like `camera.serial_number` is a
*view* over a nested map, never storage -- which is the confusion the old CSV
built in.

`search_observations` reads `observations.parquet`, which groups the frame
records by sequence, so it can now express things the summary could not:
`num_usable`, `duration_minutes`, and a `total_exptime` that is populated for
the long sequences where the CSV had null and nowhere to recover it from.

A sequence has no single position -- `RA-MNT` and `DEC-MNT` are per-frame
readings, and older observations drift substantially over a night. So each
sequence gets the mean of its frames' pointings *and* the largest deviation
from that mean, and a search cone is widened per sequence by that sequence's own
drift. An observation whose mean sits just outside the cone, but which spent
half the night inside it, is found.

### Selecting observations to work on

A position is optional, so a search can be all-sky, and the cuts data contract 9
names for benchmark selection are each expressible:

```py
# 300 usable frames over three hours, anywhere in the sky, with the moon down.
benchmarks = search_observations(
    start_date="2024-01-01",
    min_num_usable=300,
    min_duration_minutes=180,
    query="moonfrac < 0.25 and airmass < 1.5",
)
```

`min_num_usable` is not `min_num_frames`: one 372-frame sequence in the archive
has 310 usable frames and 62 errors, and only the first filter tells them apart.
`iso`, `airmass`, `moonfrac` and `moonsep` are per-frame header readings that
`observations.parquet` carries no column for, so they are reduced to a sequence
mean and attached alongside the pointing -- which is why `query` reaches them.

Sequences recorded at the same time by different hardware are the control the
photometry rebuild compares against, since the sky was the same and the camera
was not:

```py
from panoptes.data.search import find_simultaneous, get_all_observations

pairs = find_simultaneous(get_all_observations(), across="camera_id")
```

Pairing is on overlap between each sequence's `start_time` and `end_time`, not
on a calendar date: the units sit at different longitudes, so any UTC-based
"night" is the wrong slice of the night for somebody.

### Working with the results

The result is a DataFrame, so the survey-wide questions are ordinary pandas.
Reading the index is the expensive part, so read it once and pass it back in as
`source` rather than searching from scratch:

```py
from panoptes.data.search import get_all_observations, search_observations

observations = get_all_observations()

# Which fields have the most frames, and roughly where are they?
totals = (
    observations.groupby("field_name")
    .agg(
        num_frames=("num_frames", "sum"),
        num_usable=("num_usable", "sum"),
        total_exptime=("total_exptime", "sum"),
        mount_ra=("mount_ra", "median"),
        mount_dec=("mount_dec", "median"),
    )
    .sort_values("num_frames", ascending=False)
)

# Everything around the busiest field, whatever each observation called it.
top = totals.iloc[0]
nearby = search_observations(
    ra=top.mount_ra, dec=top.mount_dec, min_num_frames=10, source=observations
)
```

`source` is never modified in place.

### Reading one observation's frames

```py
from panoptes.data.observations import ObservationInfo

obs_info = ObservationInfo(meta=nearby.iloc[0])

# One row per frame, indexed on image time. Defaults to the frames the
# pipeline processed cleanly; pass image_query='' for every frame it has.
obs_info.image_metadata.to_csv(f"{obs_info.sequence_id}-image-metadata.csv")

# Where each frame is. Paths under PANOPTES_ARCHIVE_ROOT when one is set,
# archive URLs otherwise -- which name where a frame lives, but nothing
# serves them.
obs_info.image_list
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
PANOPTES_PROCESSED_ROOT=/data/panoptes-processed
PANOPTES_ARCHIVE_ROOT=/data/panoptes-archive
```

A real environment variable beats the file, and an argument beats both, so a
one-off run needs no edit:

```bash
PANOPTES_PROCESSED_ROOT=/mnt/other-copy panoptes-data search --name M42
```

The `.env` is read from the current working directory, not from wherever the
package is installed -- so run from the directory holding it, or export the
variable instead. Keys that are not settings of this package are ignored, since
a `.env` is usually shared with other tools; the cost is that a *misspelled*
`PANOPTES_*` key is ignored too, so check for a typo if a setting seems not to
take effect.

| Setting | What it locates |
| --- | --- |
| `PANOPTES_PROCESSED_ROOT` | The pipeline's document tree. No default; without it there is no metadata to read. |
| `PANOPTES_INDEX_ROOT` | The parquet index. Defaults to the processed tree, where the pipeline builds it. |
| `PANOPTES_ARCHIVE_ROOT` | A local copy of the *raw* archive. No default; without it frames resolve to URLs that cannot be fetched. |
| `PANOPTES_IMG_BASE_URL`, `PANOPTES_IMG_BUCKET` | The cloud archive as it was laid out. |

The processed tree and the raw archive are two different trees, on purpose:
outputs should be regenerable without touching the inputs, and the raw copy may
be read-only or mirrored.

### Reading per-frame metadata

`ObservationInfo.image_metadata` is one row per `metadata.json`, with the
document's nested maps flattened into the same column names the index carries:

```py
from panoptes.data.observations import USABLE_QUERY, ObservationInfo

obs_info = ObservationInfo("PAN012_358d0f_20180824T035917")

obs_info.image_metadata[["image_uid", "image_status", "image_camera_exptime"]]

# Every frame the sequence has, including the ones that failed processing.
everything = ObservationInfo("PAN012_358d0f_20180824T035917", image_query="")
```

**The default is `USABLE_QUERY`**, which is `image_status == "MATCHED"` -- the
frames the pipeline processed cleanly, and exactly how the index computes
`num_usable`, so what you get is what the index counted. `image_list` follows
the query, so a failed frame is not read either.

Excluded frames are reported rather than merely absent. `num_frames` is what
the sequence holds, `len(image_metadata)` is what the query kept, and the repr
shows both when they differ, so a filtered observation cannot be mistaken for a
smaller one:

```text
>>> ObservationInfo('PAN012_358d0f_20180824T035917')
Obs: seq_id=PAN012_358d0f_20180824T035917 num_frames=310 of 372
```

This is a *per-frame* filter, and the old observation-level `status` argument
was not. An observation marked `MATCHED` could still contain `ERROR` frames --
one 372-frame sequence in the archive has metadata for 310 -- so filtering on
the observation handed you all of them.

Every frame of the sequence has to have a readable document. One that does not
raises, rather than quietly producing an observation with fewer frames -- the
same rule the local archive follows below.

Browsable URLs are decorations added by whatever uploads the products, not part
of the document, so most sequences carry none. `obs_info.public_urls` returns
whichever `*_url` fields are present, which may be no columns at all. That is
the normal case, not a broken one: frame locations come from `image_list`.

### Reading images

Frames are read from a local copy of the **raw** archive, which is a different
tree from the processed one the metadata came from. `PANOPTES_ARCHIVE_ROOT`
points at the directory holding the unit folders -- the archive keeps the
bucket's layout, so that is
`<root>/PAN012/358d0f/20180824T035917/20180824T040118.fits.fz` -- and a
sequence resolves to files on disk:

```py
from panoptes.data.observations import ObservationInfo

obs_info = ObservationInfo("PAN012_358d0f_20180824T035917")

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
panoptes-data search --name M42 --min-num-frames 90
```

Example table output:

```text
| sequence_sequence_id          | field_name   | unit_id   |   mount_ra |   mount_dec |   num_frames |   num_usable |   exptime |   total_exptime |   duration_minutes | sequence_time             |
|:------------------------------|:-------------|:----------|-----------:|------------:|-------------:|-------------:|----------:|----------------:|-------------------:|:--------------------------|
| PAN022_977c86_20220108T090553 | M42          | PAN022    |    83.8221 |    -5.39111 |           95 |           95 |   90      |            8550 |              196.4 | 2022-01-08 09:05:53+00:00 |
| PAN022_538cc6_20220108T090553 | M42          | PAN022    |    83.8221 |    -5.39111 |           95 |           93 |   89      |            8455 |              196.1 | 2022-01-08 09:05:53+00:00 |
| PAN019_42433a_20220114T085722 | M42          | PAN019    |    83.8232 |    -5.39004 |           90 |           90 |   90      |            8100 |              188.7 | 2022-01-14 08:57:22+00:00 |
| PAN019_c623e9_20220114T085722 | M42          | PAN019    |    83.8232 |    -5.39004 |           90 |           88 |   89.0222 |            8012 |              188.7 | 2022-01-14 08:57:22+00:00 |
```

`num_frames` counts frames the pipeline has a document for; `num_usable` counts
the ones it processed successfully. They are not the same number -- one
372-frame sequence in the archive has metadata for 310 -- and the old summary
could only express the first.

#### Get all metadata for a unit in a given date range:

```bash
panoptes-data get-metadata --unit-id PAN022 --start-date '2022-01-08'
```

See `panoptes-data --help` for more options.
