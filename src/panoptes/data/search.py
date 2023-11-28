from contextlib import suppress

import logging
import astropy
import pandas as pd
from astropy import units as u
from astropy.coordinates import SkyCoord
from astropy.utils.data import download_file
from dateutil.parser import parse as parse_date

from panoptes.utils.time import current_time
from panoptes.utils.utils import listify

from panoptes.data.settings import CloudSettings

logger = logging.getLogger()


def search_observations(
        by_name=None,
        coords=None,
        unit_id=None,
        start_date=None,
        end_date=None,
        ra=None,
        dec=None,
        radius=10,  # degrees
        status=None,
        min_num_images=1,
        source=None,
        ra_col='coordinates_mount_ra',
        dec_col='coordinates_mount_dec',
):
    """Search PANOPTES observations.

    Either a `coords` or `ra` and `dec` must be specified for search to work.

    >>> from astropy.coordinates import SkyCoord
    >>> from panoptes.data.observations import search_observations
    >>> coords = SkyCoord.from_name('Andromeda Galaxy')
    >>> start_date = '2019-01-01'
    >>> end_date = '2019-12-31'
    >>> search_results = search_observations(coords=coords, min_num_images=10, start_date=start_date, end_date=end_date)
    >>> # The result is a DataFrame you can further work with.
    >>> image_count = search_results.groupby(['unit_id', 'field_name']).num_images.sum()
    >>> image_count
    unit_id  field_name
    PAN001   Andromeda Galaxy     378
             HAT-P-19             148
             TESS_SEC17_CAM02    9949
    PAN012   Andromeda Galaxy      70
             HAT-P-16 b           268
             TESS_SEC17_CAM02    1983
    PAN018   TESS_SEC17_CAM02     244
    Name: num_images, dtype: Int64
    >>> print('Total minutes exposure:', search_results.total_minutes_exptime.sum())
    Total minutes exposure: 20376.83

    Args:
        by_name (str|None): If present, this will use the `SkyCoords.from_name` method
            to do a search for the appropriate coords.
        coords (`astropy.coordinates.SkyCoord`|None): A valid coordinate instance.
        ra (float|None): The RA position in degrees of the center of search.
        dec (float|None): The Dec position in degrees of the center of the search.
        radius (float): The search radius in degrees. Searches are currently done in
            a square box, so this is half the length of the side of the box.
        start_date (str|`datetime.datetime`|None): A valid datetime instance or `None` (default).
            If `None` then the beginning of 2018 is used as a start date.
        end_date (str|`datetime.datetime`|None): A valid datetime instance or `None` (default).
            If `None` then today is used.
        unit_id (str|list|None): A str or list of strs of unit_ids to include.
            Default `None` will include all.
        status (str|list|None): A str or list of observation status to include.
            Defaults to "matched" for observations that have been fully processed. Passing
            `None` will return all status.
        min_num_images (int): Minimum number of images the observation should have, default 1.
        source (`pandas.DataFrame`|None): The dataframe to use or the search.
            If `None` (default) then the `source_url` will be used to look up the file.
        ra_col (str): The column in the `source` table to use for the RA, default
            'coordinates_mount_ra'.
        dec_col (str): The column in the `source` table to use for the Dec, default
            'coordinates_mount_dec'.

    Returns:
        `pandas.DataFrame`: A table with the matching observation results.
    """
    settings = CloudSettings()
    logger.debug(f'Setting up search params')

    if coords is None:
        if by_name is not None:
            coords = SkyCoord.from_name(by_name)
        else:
            try:
                coords = SkyCoord(ra=ra, dec=dec, unit='degree')
            except ValueError:
                raise

            # Setup defaults for search.
    if start_date is None:
        start_date = '2018-01-01'

    if end_date is None:
        end_date = current_time()

    with suppress(TypeError):
        start_date = parse_date(start_date).replace(tzinfo=None)
    with suppress(TypeError):
        end_date = parse_date(end_date).replace(tzinfo=None)

    ra_max = (coords.ra + (radius * u.degree)).value
    ra_min = (coords.ra - (radius * u.degree)).value
    dec_max = (coords.dec + (radius * u.degree)).value
    dec_min = (coords.dec - (radius * u.degree)).value

    logger.debug(f'Getting list of observations')

    # Get the observation list
    obs_df = source
    if obs_df is None:
        local_path = download_file(settings.observations_url.unicode_string(),
                                   cache='update',
                                   show_progress=False,
                                   pkgname='panoptes')
        obs_df = pd.read_csv(local_path)

    logger.info(f'Found {len(obs_df)} total observations')

    # Perform filtering on other fields here.
    logger.debug(f'Filtering observations')
    obs_df.query(
        f'{dec_col} >= {dec_min} and {dec_col} <= {dec_max}'
        ' and '
        f'{ra_col} >= {ra_min} and {ra_col} <= {ra_max}'
        ' and '
        f'time >= "{start_date}"'
        ' and '
        f'time <= "{end_date}"'
        ' and '
        f'num_images >= {min_num_images}',
        inplace=True
    )
    logger.debug(f'Found {len(obs_df)} observations after initial filter')

    unit_ids = listify(unit_id)
    if len(unit_ids) > 0 and unit_ids != 'The Whole World! 🌎':
        obs_df.query(f'unit_id in {listify(unit_ids)}', inplace=True)
    logger.debug(f'Found {len(obs_df)} observations after unit filter')

    with suppress(Exception):
        obs_df.query(f'status in {listify(status)}', inplace=True)
        logger.debug(f'Found {len(obs_df)} observations after status filter')

    logger.debug(f'Found {len(obs_df)} observations after filtering')

    obs_df = obs_df.reindex(sorted(obs_df.columns), axis=1)
    obs_df.sort_values(by=['time'], inplace=True)

    # Make sure we show an average exptime.
    obs_df['exptime'] = obs_df.total_exptime / obs_df.num_images

    # Fix bad names and drop useless columns.
    obs_df = obs_df.rename(columns=dict(camera_camera_id='camera_id'))
    obs_df = obs_df.drop(columns=['received_time', 'urls', 'status'], errors='ignore')

    obs_df.time = pd.to_datetime(obs_df.time)

    # Fix bad field name.
    obs_df.loc[obs_df.field_name.str.endswith('00:00:42+00:00'), 'field_name'] = 'M42'

    logger.debug(f'Returning {len(obs_df)} observations')
    return obs_df
