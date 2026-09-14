import warnings
from pathlib import Path

import pandas as pd
from astropy.nddata import CCDData, Cutout2D
from astropy.wcs import FITSFixedWarning

from panoptes.data.settings import SurveySettings
from panoptes.utils.images import fits as fits_utils
from panoptes.utils.images.fits import ImagePathInfo

warnings.filterwarnings('ignore', category=FITSFixedWarning)

# The metadata columns this class actually needs. Everything else, including
# every URL column, is optional -- see `ObservationInfo.public_urls`.
REQUIRED_COLUMNS = ('time', 'uid')

IMAGES_UNAVAILABLE_MESSAGE = (
    'Archived frames cannot be downloaded. Every URL the metadata carries '
    'points into a Google Cloud Storage bucket that no longer serves the '
    'object anonymously, in every era of the archive, and there is no public '
    'replacement -- see panoptes/panoptes-data#17. Frames are read from a '
    'local copy of the archive instead: set PANOPTES_ARCHIVE_ROOT to the '
    'directory holding the unit folders and `get_image_list` resolves the '
    'sequence to files on disk.'
)


class ImagesUnavailableError(RuntimeError):
    """Raised when a frame is asked for and no fetchable source exists."""


class ObservationInfo:
    """A container class for information about an Observation."""

    def __init__(self, sequence_id=None, meta=None, image_query=''):
        """Initialize the observation info with a sequence_id.

        This object will be populated with information about the observation, including
        image metadata and the location of each raw frame. It is mostly a convenience
        class for accessing information about an observation.

        Where the frames resolve to depends on whether an archive root is
        configured -- see `get_image_list`. With one, constructing this class
        also asserts that every frame of the sequence is present in that local
        copy.

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
        self._settings = SurveySettings()

        if meta is not None:
            self.sequence_id = meta.sequence_id
            self.meta = meta
        else:
            self.sequence_id = sequence_id
            self.meta = dict()

        self.image_metadata = self.get_metadata(query=image_query)
        self.image_list = self.get_image_list()

    @property
    def public_urls(self):
        """The browsable URLs carried by the metadata, if any.

        Which ``*_url`` columns exist varies between records: ``public_url``
        and ``raw_url`` on some, ``uploaded_public_url``, ``fits_public_url``
        and ``jpg_public_url`` on others. They are for display and lookup only
        -- the raw frame locations come from `get_image_list` -- so a record
        with no URL column at all still works.

        Returns:
            A DataFrame of whichever ``*_url`` columns are present, which may
            have no columns.
        """
        return self.image_metadata.filter(regex=r'_url$')

    def get_image_cutout(self, data=None, coords=None, box_size=None, *args, **kwargs):
        """Gets a Cutout2D object for the given coords and box_size."""
        ccd0 = data or self.get_image_data(*args, **kwargs)
        return Cutout2D(ccd0, coords, box_size, copy=True)

    def get_image_data(self, idx=0, use_raw=True):
        """Reads the image data for the given index.

        This reads whatever is in `image_list`. With an archive root
        configured those are local files and this works; without one they are
        archive URLs that no longer serve anonymously (`download_images`) and
        the read fails. See `get_image_list`.
        """
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

        missing = [col for col in REQUIRED_COLUMNS if col not in images_df.columns]
        if missing:
            raise ValueError(
                f'Metadata for {self.sequence_id} is missing required '
                f'column(s) {missing}; got {sorted(images_df.columns)}'
            )

        # Set a time index.
        images_df.time = pd.to_datetime(images_df.time)
        images_df = images_df.set_index(['time']).sort_index()

        if query > '':
            images_df = images_df.query(query)

        return images_df

    def get_image_list(self, bucket: str | None = None, file_ext: str = '.fits.fz',
                       archive_root: Path | str | None = None):
        """Resolve the observation's raw frames to where they can be read.

        Each frame's location is derived from its ``uid`` rather than read
        from a URL column, which is not dependable (`public_urls`). The
        ``uid`` is the archive path with underscores for separators, so
        `ImagePathInfo` parses it and rebuilds the path below the bucket:
        ``PAN012/358d0f/20180824T035917/20180824T040118.fits.fz``.

        That path is the same in a local copy of the archive, which is what
        makes both cases one derivation:

        * **With an archive root** -- configured by ``PANOPTES_ARCHIVE_ROOT``
          or passed here -- the list holds `Path` objects under that root, and
          `get_image_data` can read them. The root points at the directory
          holding the unit folders, so ``<root>/PAN012/358d0f/...``; the
          bucket name is a cloud-era detail and does not appear in it.
        * **Without one**, the list holds archive URLs. They name where each
          frame lives but nothing serves them (`download_images`), so this is
          a description of the archive, not a way into it.

        Every frame named by the metadata must exist under the root. A partial
        archive raises rather than returning a shorter list, because a
        silently shorter list reads as an observation with fewer frames rather
        than as an incomplete copy. Pass no root -- or use `get_metadata`
        directly -- to inspect a sequence whose frames are not all on hand.

        Args:
             bucket: The bucket the URLs are built against. Ignored when an
                archive root is in play, which is what keeps the bucket name
                out of local paths.
             file_ext: The file extension of the images to retrieve. Matched
                exactly against the local archive: the layout there is the
                bucket's layout, and the extension is part of it.
             archive_root: A local copy of the archive, overriding the
                ``archive_root`` setting for this call.
        Returns:
            A list of `Path` under an archive root, or of URL strings without
            one, in metadata order and one per image.

        Raises:
            ValueError: if a ``uid`` is not a well-formed archive path.
            FileNotFoundError: if a frame is missing from the local archive.
        """
        ext = file_ext.lstrip('.')
        relative_paths = [self._frame_path(uid, ext) for uid in self.image_metadata.uid.values]

        archive_root = archive_root or self._settings.archive_root
        if archive_root is None:
            url_base = self._settings.img_base_url.unicode_string()
            bucket = bucket or self._settings.img_bucket
            return [f'{url_base}{bucket}/{path}' for path in relative_paths]

        archive_root = Path(archive_root)
        if not archive_root.is_dir():
            raise FileNotFoundError(
                f'The archive root {archive_root} is not a directory, so no '
                f'frame of {self.sequence_id} can be located. It should point '
                f'at the directory holding the unit folders, e.g. '
                f'<root>/PAN012/358d0f/20180824T035917/.'
            )

        image_list = [archive_root / path for path in relative_paths]

        for image_path in image_list:
            if not image_path.exists():
                raise FileNotFoundError(
                    f'Frame {image_path} is named by the metadata for '
                    f'{self.sequence_id} but is not in the local archive at '
                    f'{archive_root}. The copy is incomplete for this sequence.'
                )

        return image_list

    def _frame_path(self, uid, ext: str) -> Path:
        """The archive-relative path of one frame, from its ``uid``.

        The ``uid`` is the archive path with underscores for separators, so
        `ImagePathInfo` both validates it and rebuilds the path.
        """
        try:
            path_info = ImagePathInfo(path=str(uid).replace('_', '/'))
        except ValueError as e:
            raise ValueError(
                f'Image uid {uid!r} in the metadata for {self.sequence_id} is '
                f'not a well-formed archive path, so the frame cannot be '
                f'located: {e}'
            ) from e

        return path_info.as_path(ext=ext)

    def download_images(self, image_list=None, output_dir=None, show_progress=True,
                        warn_on_error=True
                        ):
        """Deprecated: archived frames are not currently available for download.

        The URLs this class builds are still the right archive locations, and
        `get_image_list` still returns them, but nothing serves them: they 404
        for an anonymous caller in both the 2018 and the 2025 layout. This
        method used to construct those URLs, fail on every one of them, and --
        with the default ``warn_on_error`` -- return an empty list as though
        the observation simply had no images.

        It now raises instead, so a caller learns that no frame was fetched at
        the point where the fetch was attempted.

        Raises:
            ImagesUnavailableError: always.
        """
        warnings.warn(IMAGES_UNAVAILABLE_MESSAGE, DeprecationWarning, stacklevel=2)
        raise ImagesUnavailableError(IMAGES_UNAVAILABLE_MESSAGE)

    def __str__(self):
        return f'Obs: seq_id={self.sequence_id} num_images={len(self.image_list)}'

    def __repr__(self):
        return str(self)
