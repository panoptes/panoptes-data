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
