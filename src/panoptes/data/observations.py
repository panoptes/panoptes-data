import shutil
import warnings
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Union

import pandas as pd
from astropy.nddata import Cutout2D, CCDData
from astropy.time import Time
from astropy.utils.data import download_file
from astropy.wcs import FITSFixedWarning
from dateutil.parser import parse as parse_date
from tqdm.auto import tqdm

from panoptes.utils.images import fits as fits_utils
from panoptes.utils.time import flatten_time
from panoptes.data.settings import PATH_MATCHER, CloudSettings

warnings.filterwarnings('ignore', category=FITSFixedWarning)


@dataclass
class ObservationPathInfo:
    """Parse the location path for an image.

    This is a small dataclass that offers some convenience methods for dealing
    with a path based on the image id.

    This would usually be instantiated via `path`:

    ..doctest::

        >>> from panoptes.data.observations import ObservationPathInfo
        >>> bucket_path = 'gs://panoptes-images-background/PAN012/Hd189733/358d0f/20180824T035917/20180824T040118.fits'
        >>> path_info = ObservationPathInfo(path=bucket_path)

        >>> path_info.id
        'PAN012_358d0f_20180824T035917_20180824T040118'

        >>> path_info.unit_id
        'PAN012'

        >>> path_info.sequence_id
        'PAN012_358d0f_20180824T035917'

        >>> path_info.image_id
        'PAN012_358d0f_20180824T040118'

        >>> path_info.as_path(base='/tmp', ext='.jpg')
        '/tmp/PAN012/358d0f/20180824T035917/20180824T040118.jpg'

        >>> ObservationPathInfo(path='foobar')
        Traceback (most recent call last):
          ...
        ValueError: Invalid path received: self.path='foobar'


    """
    path: Union[str, Path] = None
    unit_id: str = None
    camera_id: str = None
    field_name: str = None
    sequence_time: Union[str, datetime, Time] = None
    image_time: Union[str, datetime, Time] = None

    def __post_init__(self):
        """Parse the path when provided upon initialization."""
        if self.path is not None:
            path_match = PATH_MATCHER.match(self.path)
            if path_match is None:
                raise ValueError(f'Invalid path received: {self.path}')

            self.unit_id = path_match.group('unit_id')
            self.camera_id = path_match.group('camera_id')
            self.field_name = path_match.group('field_name')
            self.sequence_time = Time(parse_date(path_match.group('sequence_time')))
            self.image_time = Time(parse_date(path_match.group('image_time')))

    @property
    def id(self):
        """Full path info joined with underscores"""
        return self.get_full_id()

    @property
    def sequence_id(self) -> str:
        """The sequence id."""
        return f'{self.unit_id}_{self.camera_id}_{flatten_time(self.sequence_time)}'

    @property
    def image_id(self) -> str:
        """The matched image id."""
        return f'{self.unit_id}_{self.camera_id}_{flatten_time(self.image_time)}'

    def as_path(self, base: Union[Path, str] = None, ext: str = None) -> Path:
        """Return a Path object."""
        image_str = flatten_time(self.image_time)
        if ext is not None:
            image_str = f'{image_str}.{ext}'

        full_path = Path(self.unit_id, self.camera_id, flatten_time(self.sequence_time), image_str)

        if base is not None:
            full_path = base / full_path

        return full_path

    def get_full_id(self, sep='_') -> str:
        """Returns the full path id with the given separator."""
        return f'{sep}'.join([
            self.unit_id,
            self.camera_id,
            flatten_time(self.sequence_time),
            flatten_time(self.image_time)
        ])

    @classmethod
    def from_fits(cls, fits_file):
        header = fits_utils.getheader(fits_file)
        return cls.from_fits_header(header)

    @classmethod
    def from_fits_header(cls, header):
        try:
            new_instance = cls(path=header['FILENAME'])
        except ValueError:
            sequence_id = header['SEQID']
            image_id = header['IMAGEID']
            unit_id, camera_id, sequence_time = sequence_id.split('_')
            _, _, image_time = image_id.split('_')

            new_instance = cls(unit_id=unit_id,
                               camera_id=camera_id,
                               sequence_time=Time(parse_date(sequence_time)),
                               image_time=Time(parse_date(image_time)))

        return new_instance


class ObservationInfo:
    """A container class for information about an Observation."""

    def __init__(self, sequence_id=None, meta=None, image_query=''):
        """Initialize the observation info with a sequence_id.

        This object will be populated with information about the observation, including
        image metadata and links to raw and processed images. It is mostly a convenience
        class for accessing information about an observation.

        Example:

            >>> from panoptes.data.observations import ObservationInfo
            >>> obs_info = ObservationInfo(sequence_id='PAN012_358d0f_20180824T035917')
            >>> obs_info.sequence_id
            'PAN012_358d0f_20180824T035917'
            >>> len(obs_info.image_list)
            124


        Args:
            sequence_id: The sequence id of the observation.
            meta: A dictionary of metadata for the observation.
            image_query: A query string to use when querying for images, e.g. 'status != "ERROR"'
        """
        self._settings = CloudSettings()

        if meta is not None:
            self.sequence_id = meta.sequence_id
            self.meta = meta
        else:
            self.sequence_id = sequence_id
            self.meta = dict()

        self.image_metadata = self.get_metadata(query=image_query)
        self.image_list = self.get_image_list()

    def get_image_cutout(self, data=None, coords=None, box_size=None, *args, **kwargs):
        """Gets a Cutout2D object for the given coords and box_size."""
        ccd0 = data or self.get_image_data(*args, **kwargs)
        return Cutout2D(ccd0, coords, box_size, copy=True)

    def get_image_data(self, idx=0, use_raw=True):
        """Downloads the image data for the given index."""
        data_img = self.image_list[idx]
        wcs_img = self.image_list[idx]

        data0, header0 = fits_utils.getdata(data_img, header=True)
        wcs0 = fits_utils.getwcs(wcs_img)
        ccd0 = CCDData(data0, wcs=wcs0, unit='adu', meta=header0)

        return ccd0

    def get_metadata(self, query=''):
        """Download the image metadata associated with the observation."""
        metadata_url = f'{self._settings.img_metadata_url.unicode_string()}?sequence_id={self.sequence_id}'
        images_df = pd.read_csv(metadata_url)

        # Set a time index.
        images_df.time = pd.to_datetime(images_df.time)
        images_df = images_df.set_index(['time']).sort_index()

        if query > '':
            images_df = images_df.query(query)

        return images_df

    def get_image_list(self):
        """Get the images for the observation."""
        bucket = 'panoptes-images'
        file_ext = '.fits.fz'

        # Build up the image list from the metadata.
        image_list = [self._settings.img_base_url.unicode_string()
                      + bucket + '/'
                      + str(s).replace("_", "/")
                      + file_ext for s in
                      self.image_metadata.uid.values]

        return image_list

    def download_images(self, image_list=None, output_dir=None, show_progress=True,
                        warn_on_error=True):
        """Download the images to the output directory (by default named after the sequence_id).

        Args:
            image_list: A list of images to download.
            output_dir: The directory to download the images to.
            show_progress: Whether to show a progress bar.
            warn_on_error: If True (default) issue a warning if an image fails to download,
                otherwise raise an exception.
        """
        output_dir = Path(output_dir or self.sequence_id)
        output_dir.mkdir(parents=True, exist_ok=True)

        image_list = image_list or self.image_list
        print(f'Downloading {len(image_list)} images to {output_dir}')

        if show_progress:
            img_iter = tqdm(image_list)
        else:
            img_iter = image_list

        img_paths = list()
        for img in img_iter:
            if show_progress:
                img_iter.set_description(f'Downloading {img}')
            else:
                print(f'Downloading {img}')

            try:
                fn = Path(download_file(img, show_progress=False))
                new_fn = output_dir / Path(img).name
                shutil.move(fn, new_fn)
                img_paths.append(str(new_fn))
            except Exception as e:
                if warn_on_error:
                    warnings.warn(f'Failed to download {img}: {e}')
                else:
                    raise e

        return img_paths

    def __str__(self):
        return f'Obs: seq_id={self.sequence_id} num_images={len(self.image_list)}'

    def __repr__(self):
        return str(self)
