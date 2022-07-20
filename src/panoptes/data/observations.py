import shutil
import warnings
from pathlib import Path

import pandas as pd
from astropy.nddata import Cutout2D, CCDData
from astropy.utils.data import download_file
from astropy.wcs import FITSFixedWarning
from tqdm.auto import tqdm

from panoptes.utils.images import fits as fits_utils
from panoptes.data.settings import CloudSettings

warnings.filterwarnings('ignore', category=FITSFixedWarning)


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
            >>> len(obs_info.raw_images)
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
        self.raw_images = self.get_image_list()
        self.processed_images = self.get_image_list(raw=False)

    def get_image_cutout(self, data=None, coords=None, box_size=None, *args, **kwargs):
        """Gets a Cutout2D object for the given coords and box_size."""
        ccd0 = data or self.get_image_data(*args, **kwargs)
        return Cutout2D(ccd0, coords, box_size, copy=True)

    def get_image_data(self, idx=0, use_raw=True):
        """Downloads the image data for the given index."""
        if use_raw:
            image_list = self.raw_images
        else:
            image_list = self.processed_images

        data_img = image_list[idx]
        wcs_img = self.processed_images[idx]

        data0, header0 = fits_utils.getdata(data_img, header=True)
        wcs0 = fits_utils.getwcs(wcs_img)
        ccd0 = CCDData(data0, wcs=wcs0, unit='adu', meta=header0)

        return ccd0

    def get_metadata(self, query=''):
        """Download the image metadata associated with the observation."""
        images_df = pd.read_csv(f'{self._settings.img_metadata_url}?sequence_id={self.sequence_id}')

        # Set a time index.
        images_df.time = pd.to_datetime(images_df.time)
        images_df = images_df.set_index(['time']).sort_index()

        if query > '':
            images_df = images_df.query(query)

        return images_df

    def get_image_list(self, raw=True):
        """Get the images for the observation."""
        if raw:
            bucket = 'panoptes-images-raw'
            file_ext = '.fits.fz'
        else:
            bucket = 'panoptes-images-processed'
            file_ext = '-reduced.fits.fz'

        # Build up the image list from the metadata.
        image_list = [self._settings.img_base_url + '/'
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

        image_list = image_list or self.raw_images

        if show_progress:
            img_iter = tqdm(image_list)
        else:
            img_iter = image_list

        img_paths = list()
        for img in img_iter:
            try:
                fn = Path(download_file(img, show_progress=False))
                new_fn = output_dir / Path(img).name
                shutil.move(fn, new_fn)
                img_paths.append(str(new_fn))
            except Exception as e:
                if warn_on_error:
                    print(f'Failed to download {img}: {e}')
                else:
                    raise e

        return img_paths

    def __str__(self):
        return f'Obs: seq_id={self.sequence_id} num_images={len(self.raw_images)}'

    def __repr__(self):
        return str(self)
