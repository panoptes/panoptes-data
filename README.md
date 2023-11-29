# PANOPTES Data tools

Tools for working with PANOPTES data.

## Install

Install from pip:

```bash
pip install panoptes-data
```

## Examples

> See example Jupyter Notebooks in the [`notebooks`](notebooks/) folder.

### Finding observations

```py
>>> from panoptes.data.search import search_observations
>>> from panoptes.data.observations import ObservationInfo

>>> # Find some observations
>>> results = search_observations(by_name='M42')

>>> # Use last result entry to create ObservationInfo object.
>>> obs_info = ObservationInfo(meta=results.iloc[0])
>>> obs_info.meta

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

>>> # Create an ObservationInfo object directly from a sequence_id.
>>> obs_info = ObservationInfo('PAN001_14d3bd_20180113T052325')
>>> # But then there is no metadata:
>>> obs_info.meta

{}
```

### Downloading images

The `ObservationInfo` object makes it easy to download the files:

```py
>>> obs_info.download_images()
```

### Command-line tools

There is a simple command line tool that allows for both searching and downloading of images and metadata.

#### Search for observations:

```bash
$ panoptes-data search --name M42 --min-num-images 90

| sequence_id                   | field_name   | unit_id   |   coordinates_mount_ra |   coordinates_mount_dec |   num_images |   exptime |   total_exptime | time                      |
|:------------------------------|:-------------|:----------|-----------------------:|------------------------:|-------------:|----------:|----------------:|:--------------------------|
| PAN022_977c86_20220108T090553 | M42          | PAN022    |                83.8221 |                -5.39111 |           95 |   90      |            8550 | 2022-01-08 09:05:53+00:00 |
| PAN022_538cc6_20220108T090553 | M42          | PAN022    |                83.8221 |                -5.39111 |           95 |   89      |            8455 | 2022-01-08 09:05:53+00:00 |
| PAN019_42433a_20220114T085722 | M42          | PAN019    |                83.8221 |                -5.39111 |           90 |   90      |            8100 | 2022-01-14 08:57:22+00:00 |
| PAN019_c623e9_20220114T085722 | M42          | PAN019    |                83.8221 |                -5.39111 |           90 |   89.0222 |            8012 | 2022-01-14 08:57:22+00:00 |
| PAN019_c623e9_20220115T082108 | M42          | PAN019    |                83.8221 |                -5.39111 |          105 |   89.019  |            9347 | 2022-01-15 08:21:08+00:00 |
| PAN019_42433a_20220115T082108 | M42          | PAN019    |                83.8221 |                -5.39111 |          105 |   90.0095 |            9451 | 2022-01-15 08:21:08+00:00 |
```

#### Downloading all images for an observation:

```bash
panoptes-data download PAN022_977c86_20220108T090553
```

#### Get all metadata for a unit in a given date range:

```bash
panoptes-data get-metadata --unit-id PAN022 --start-date '2022-01-08'
```

See `panoptes-data --help` for more options.
