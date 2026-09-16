import warnings
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pandas as pd
from astropy.nddata import CCDData, Cutout2D
from astropy.wcs import FITSFixedWarning
from panoptes.utils.images import fits as fits_utils
from panoptes.utils.images.fits import ImagePathInfo

from panoptes.data import documents
from panoptes.data.settings import ImageStatus, SurveySettings

warnings.filterwarnings("ignore", category=FITSFixedWarning)

# The flattened document fields this class actually needs, named by the data
# contract rather than guessed at. Everything else, including every URL field,
# is optional -- see `ObservationInfo.public_urls`.
REQUIRED_FRAME_FIELDS = ("image_uid", "image_image_time")

#: An `image_query` keeping only the frames the pipeline processed cleanly.
#:
#: The predicate is equality on ``MATCHED`` because that is exactly how the
#: index computes ``num_usable``, so a sequence filtered with this has as many
#: frames as the index said were usable -- the two cannot drift into disagreeing
#: about what "usable" counts.
#:
#: It is named here rather than spelled out at each call site because the
#: definition is upstream's to move. `ImageStatus` is an `IntEnum` whose *order*
#: is load-bearing in the pipeline -- its idempotency rule is "at or past
#: ``PROCESSING``" -- and ``EXTRACTING``/``EXTRACTED`` sort **above**
#: ``MATCHED``. Today the pipeline writes only ``MATCHED`` or ``ERROR``, so
#: equality and "at or past" select the same frames; if that changes, this is
#: the one line to change, and `test_usable_query_agrees_with_num_usable` is
#: what notices.
USABLE_QUERY = f'image_status == "{ImageStatus.MATCHED.name}"'

# Where a sequence id is found on a metadata record. The observation index
# groups on `sequence_sequence_id`, so that is what a row of search results
# carries; a flattened `observation.json` carries the top-level `sequence_id`.
SEQUENCE_ID_FIELDS = ("sequence_sequence_id", "sequence_id")

IMAGES_UNAVAILABLE_MESSAGE = (
    "Archived frames cannot be downloaded. Every URL the metadata carries "
    "points into a Google Cloud Storage bucket that does not serve the "
    "object anonymously, in any era of the archive, and there is no public "
    "replacement -- see panoptes/panoptes-data#17. Frames are read from a "
    "local copy of the archive instead: set PANOPTES_ARCHIVE_ROOT to the "
    "directory holding the unit folders and `get_image_list` resolves the "
    "sequence to files on disk."
)


class ImagesUnavailableError(RuntimeError):
    """Raised when a frame is asked for and no fetchable source exists."""


def sequence_id_of(meta) -> str:
    """The sequence id carried by a metadata record.

    Accepts a `pandas.Series` from search results, a flattened document, or a
    row object with attributes, and looks only at the names the contract
    declares. Guessing which field holds an identifier is what
    panoptes/panoptes-data#12 and #13 both were, so an unrecognized record says
    which names it looked for rather than reaching for the first thing that
    looks like an id.

    Raises:
        ValueError: if none of `SEQUENCE_ID_FIELDS` is present.
    """
    if isinstance(meta, Mapping | pd.Series):
        keys = list(meta.keys())
        for field in SEQUENCE_ID_FIELDS:
            if field in keys:
                return str(meta[field])
    else:
        keys = [name for name in dir(meta) if not name.startswith("_")]
        for field in SEQUENCE_ID_FIELDS:
            if hasattr(meta, field):
                return str(getattr(meta, field))

    raise ValueError(
        f"The record given as `meta` carries no sequence id: looked for "
        f"{list(SEQUENCE_ID_FIELDS)} and found {sorted(keys)}."
    )


class ObservationInfo:
    """A container class for information about an Observation."""

    def __init__(
        self,
        sequence_id: str | None = None,
        meta: Any = None,
        image_query: str = USABLE_QUERY,
        processed_root: Path | str | None = None,
    ):
        """Initialize the observation info with a sequence_id.

        The metadata comes from the documents `panoptes-pipeline` wrote: the
        sequence's ``observation.json`` and one ``metadata.json`` per frame,
        under ``PANOPTES_PROCESSED_ROOT`` (panoptes/panoptes-data#15).

        Where the *frames* resolve to is a separate question with a separate
        setting -- see `get_image_list`. Raw-frame discovery is genuinely
        upstream of the pipeline and is unchanged. With an archive root
        configured, constructing this class also asserts that every frame of the
        sequence is present in that local copy.

        Example:

            >>> from panoptes.data.observations import ObservationInfo
            >>> obs_info = ObservationInfo(sequence_id='PAN012_358d0f_20180824T035917')
            >>> obs_info.sequence_id
            'PAN012_358d0f_20180824T035917'
            >>> len(obs_info.image_list)
            124


        Args:
            sequence_id: The sequence id of the observation.
            meta: A record carrying a sequence id, such as a row of
                `search_observations` results. Supplying it saves nothing --
                the observation document is read either way -- but it keeps the
                search result and the observation together.
            image_query: A query string to use when querying for images. The
                field names are the contract's, so this is `image_status` and
                not `status`. Defaults to `USABLE_QUERY`, so the frames you get
                are the ones the pipeline processed cleanly -- which is what
                you want for anything that reads pixels. Pass ``''`` for every
                frame the sequence has, including the failed ones.

                Excluded frames are never merely absent: `num_frames` reports
                what the sequence holds against what the query kept, and `repr`
                shows both when they differ. A filtered observation should not
                be mistakable for a smaller one.
            processed_root: The tree of pipeline documents, overriding the
                ``processed_root`` setting for this instance.
        """
        self._settings = SurveySettings()
        self._processed_root = documents.require_root(
            processed_root if processed_root is not None else self._settings.processed_root,
            "processed root",
        )
        # The contract belongs to the tree being read. An explicit `index_root`
        # setting still wins -- that is what it is for -- but otherwise the
        # manifest to trust is the one in the tree this instance was pointed
        # at, not the one in whichever tree the settings happen to name.
        self._contract = documents.contract_for(self._settings.index_root or self._processed_root)

        self.sequence_id = sequence_id_of(meta) if meta is not None else sequence_id
        if self.sequence_id is None:
            raise ValueError("One of `sequence_id` or `meta` is required.")

        self.observation = documents.read_observation(
            self._processed_root, self.sequence_id, contract=self._contract
        )
        # The observation document *is* the metadata, so building from a
        # sequence id is not the lesser way in: `meta` is populated either way.
        self.meta = meta if meta is not None else self.observation

        self.image_metadata = self.get_metadata(query=image_query)
        self.image_list = self.get_image_list()

    @property
    def status(self):
        """The `ObservationStatus` name the pipeline last recorded, or None."""
        return self.observation.get("status")

    @property
    def params_fingerprint(self):
        """The digest of the settings this observation was produced with.

        It is the pipeline's cache key (data contract 3.4): two observations
        with different fingerprints were not made by the same code with the
        same parameters, whatever else they have in common.
        """
        return self.observation.get("params_fingerprint")

    @property
    def public_urls(self) -> pd.DataFrame:
        """The browsable URLs carried by the metadata, if any.

        Under the contract these are decorations added by whatever uploads the
        products, not part of the document the pipeline writes. So a document
        with no URL field at all is the normal case, not the broken one, and
        the raw frame locations come from `get_image_list` regardless.

        Returns:
            A DataFrame of whichever ``*_url`` columns are present, which may
                have no columns.
        """
        return self.image_metadata.filter(regex=r"_url$")

    def get_image_cutout(self, data=None, coords=None, box_size=None, *args, **kwargs):
        """Gets a Cutout2D object for the given coords and box_size."""
        ccd0 = data or self.get_image_data(*args, **kwargs)
        return Cutout2D(ccd0, coords, box_size, copy=True)

    def get_image_data(self, idx=0, use_raw=True):
        """Reads the image data for the given index.

        This reads whatever is in `image_list`. With an archive root
        configured those are local files and this works; without one they are
        archive URLs that nothing serves anonymously (`download_images`) and
        the read fails. See `get_image_list`.
        """
        data_img = self.image_list[idx]
        wcs_img = self.image_list[idx]

        data0, header0 = fits_utils.getdata(data_img, header=True)
        wcs0 = fits_utils.getwcs(wcs_img)
        ccd0 = CCDData(data0, wcs=wcs0, unit="adu", meta=header0)

        return ccd0

    def get_metadata(self, query: str = "") -> pd.DataFrame:
        """Read the per-frame documents of this observation.

        One row per ``metadata.json``, with the document's nested maps
        flattened into column names -- ``image_camera_exptime``,
        ``sequence_coordinates_mount_ra``, ``image_status`` -- which are the
        same names the pipeline's ``frames.parquet`` carries for the same
        values. `panoptes.data.documents` says why that mapping is a contract
        rather than a convenience.

        The rows are indexed on ``image_image_time`` and sorted, as they were
        on ``time`` before. The column keeps its contract name as well as being
        the index, so a query can still reach it.

        Args:
            query: A `pandas.DataFrame.query` string, applied after the index
                is set, e.g. ``'image_status == "MATCHED"'``.

        Raises:
            DocumentsUnavailableError: if the sequence is not in the processed
                tree, or one of its documents cannot be read.
            ValueError: if a document is missing a field the contract requires.
        """
        images_df = documents.read_frames(
            self._processed_root, self.sequence_id, contract=self._contract
        )

        # Absent *or* entirely null: the contract's required columns are
        # reindexed into existence, so a field no document supplied is a column
        # of nulls rather than a missing one, and a null uid locates no frame.
        missing = [
            field
            for field in REQUIRED_FRAME_FIELDS
            if field not in images_df.columns or images_df[field].isna().all()
        ]
        if missing:
            raise ValueError(
                f"The frame documents for {self.sequence_id} are missing required "
                f"field(s) {missing}; got {sorted(images_df.columns)}"
            )

        # Set a time index, keeping the column: it is named by the contract and
        # dropping it would make `query` unable to mention it.
        images_df["image_image_time"] = pd.to_datetime(
            images_df.image_image_time, format="mixed", utc=True
        )
        images_df = images_df.set_index("image_image_time", drop=False).sort_index()

        self.num_frames = len(images_df)
        if query > "":
            images_df = images_df.query(query)

        return images_df

    def get_image_list(
        self,
        bucket: str | None = None,
        file_ext: str = ".fits.fz",
        archive_root: Path | str | None = None,
    ) -> list[Path] | list[str]:
        """Resolve the observation's raw frames to where they can be read.

        Each frame's location is derived from its ``image_uid``, which the
        contract names (data contract 3.1) rather than leaving to be inferred
        from whichever URL field a given era of record happened to carry. The
        uid is the archive path with underscores for separators, so
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

        Note that this is the *raw* archive, not the processed tree the
        metadata came from. The two are deliberately separate: outputs should
        be regenerable without touching the inputs, and the raw copy may be
        read-only or mirrored (data contract 5.3).

        Every frame named by the documents must exist under the root. A partial
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
            archive_root: A local copy of the raw archive, overriding the
                ``archive_root`` setting for this call.

        Returns:
            A list of `Path` under an archive root, or of URL strings without
                one, in metadata order and one per image.

        Raises:
            ValueError: if an ``image_uid`` is not a well-formed archive path.
            FileNotFoundError: if a frame is missing from the local archive.
        """
        ext = file_ext.lstrip(".")
        relative_paths = [
            self._frame_path(uid, ext) for uid in self.image_metadata.image_uid.values
        ]

        archive_root = archive_root or self._settings.archive_root
        if archive_root is None:
            url_base = self._settings.img_base_url.unicode_string()
            bucket = bucket or self._settings.img_bucket
            return [f"{url_base}{bucket}/{path}" for path in relative_paths]

        archive_root = Path(archive_root)
        if not archive_root.is_dir():
            raise FileNotFoundError(
                f"The archive root {archive_root} is not a directory, so no "
                f"frame of {self.sequence_id} can be located. It should point "
                f"at the directory holding the unit folders, e.g. "
                f"<root>/PAN012/358d0f/20180824T035917/."
            )

        image_list = [archive_root / path for path in relative_paths]

        for image_path in image_list:
            if not image_path.exists():
                raise FileNotFoundError(
                    f"Frame {image_path} is named by the metadata for "
                    f"{self.sequence_id} but is not in the local archive at "
                    f"{archive_root}. The copy is incomplete for this sequence."
                )

        return image_list

    def _frame_path(self, uid, ext: str) -> Path:
        """The archive-relative path of one frame, from its ``image_uid``.

        The uid is the archive path with underscores for separators, so
        `ImagePathInfo` both validates it and rebuilds the path.
        """
        try:
            path_info = ImagePathInfo(path=str(uid).replace("_", "/"))
        except ValueError as e:
            raise ValueError(
                f"Image uid {uid!r} in the metadata for {self.sequence_id} is "
                f"not a well-formed archive path, so the frame cannot be "
                f"located: {e}"
            ) from e

        return path_info.as_path(ext=ext)

    def download_images(
        self, image_list=None, output_dir=None, show_progress=True, warn_on_error=True
    ):
        """Deprecated: archived frames are not currently available for download.

        The URLs this class builds are the right archive locations, and
        `get_image_list` returns them, but nothing serves them: they 404 for an
        anonymous caller in both the 2018 and the 2025 layout. Raising is what
        tells a caller no frame was fetched, at the point the fetch was
        attempted, rather than handing back an empty list.

        Raises:
            ImagesUnavailableError: always.
        """
        warnings.warn(IMAGES_UNAVAILABLE_MESSAGE, DeprecationWarning, stacklevel=2)
        raise ImagesUnavailableError(IMAGES_UNAVAILABLE_MESSAGE)

    def __str__(self):
        kept = len(self.image_list)
        # Say so when the query dropped frames, so a filtered observation is
        # not mistakable for one that never had them.
        of_total = "" if kept == self.num_frames else f" of {self.num_frames}"
        return f"Obs: seq_id={self.sequence_id} num_frames={kept}{of_total}"

    def __repr__(self):
        return str(self)
