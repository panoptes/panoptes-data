from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Union

from astropy.time import Time
from dateutil.parser import parse as parse_date

from panoptes.utils.time import flatten_time
from panoptes.utils.images import fits as fits_utils

from panoptes.data.settings import PATH_MATCHER


@dataclass
class ImagePathInfo:
    """Parse the location path for an image.

    This is a small dataclass that offers some convenience methods for dealing
    with a path based on the image id.

    This would usually be instantiated via `path`:

    ..doctest::

        >>> from panoptes.data.images import ImagePathInfo
        >>> bucket_path = 'gs://panoptes-images-background/PAN012/Hd189733/358d0f/20180824T035917/20180824T040118.fits'
        >>> path_info = ImagePathInfo(path=bucket_path)

        >>> path_info.id
        'PAN012_358d0f_20180824T035917_20180824T040118'

        >>> path_info.unit_id
        'PAN012'

        >>> path_info.sequence_id
        'PAN012_358d0f_20180824T035917'

        >>> path_info.sequence_time
        <Time object: scale='utc' format='isot' value='2018-08-24T03:59:17.000'>

        >>> path_info.image_id
        'PAN012_358d0f_20180824T040118'

        >>> path_info.image_time
        <Time object: scale='utc' format='isot' value='2018-08-24T04:01:18.000'>

        >>> path_info.as_path(base='/tmp', ext='.jpg')
        '/tmp/PAN012/358d0f/20180824T035917/20180824T040118.jpg'

        >>> ImagePathInfo(path='foobar')
        Traceback (most recent call last):
          ...
        ValueError: Invalid path received: self.path='foobar'


    """
    unit_id: str = None
    camera_id: str = None
    field_name: str = None
    sequence_time: Union[str, datetime, Time] = None
    image_time: Union[str, datetime, Time] = None
    path: Union[str, Path] = None

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
