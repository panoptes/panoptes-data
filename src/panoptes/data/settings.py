from enum import IntEnum, auto
from pathlib import Path

from pydantic.networks import AnyHttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict


class SurveySettings(BaseSettings):
    """Where the survey's data lives.

    Every field names a *location*, and each is configured from the
    environment with a ``PANOPTES_`` prefix, e.g. ``PANOPTES_ARCHIVE_ROOT``.

    There are two trees, and they are not the same tree -- plus the index
    built over the second one:

    `archive_root`
        The **raw** frames, as the units uploaded them. This is what
        `ObservationInfo.get_image_list` resolves a sequence against, and it is
        genuinely upstream of the pipeline.
    `processed_root`
        What `panoptes-pipeline` wrote: ``observation.json`` per sequence and
        ``metadata.json`` per frame. This is where every piece of metadata now
        comes from.
    `index_root`
        The parquet index built by walking the processed tree, which defaults
        to sitting in it. `search_observations` reads that index.

    The URL fields describe the cloud archive as it was laid out. Nothing
    serves those objects anonymously any more
    (`ObservationInfo.download_images`), so they name where a frame lives
    rather than somewhere to fetch it from.

    A ``.env`` file in the working directory is read as well, so the roots can
    be checked out beside a project rather than exported in every shell. A real
    environment variable wins over the file, and an explicit argument wins over
    both.

    Keys that are not settings of this class are ignored rather than rejected,
    because a ``.env`` is usually shared with other tools and a
    ``DATABASE_URL`` in it must not stop a frame being read. The cost is that a
    misspelled ``PANOPTES_*`` key is ignored too, so a setting that appears not
    to take effect is worth checking for a typo.
    """

    model_config = SettingsConfigDict(
        env_prefix="panoptes_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    archive_root: Path | None = None
    processed_root: Path | None = None
    #: Where the index files sit. `None` means "in the processed tree", which
    #: is where `panoptes.pipeline.index.build` puts them by default. It is a
    #: separate setting because the index is regenerable and nothing breaks by
    #: keeping it beside a read-only or mirrored processed tree rather than in
    #: it.
    index_root: Path | None = None
    img_base_url: AnyHttpUrl = "https://storage.googleapis.com"
    img_bucket: str = "panoptes-images-incoming"

    @property
    def resolved_index_root(self) -> Path | None:
        """`index_root` if set, else the processed tree, else `None`."""
        return self.index_root or self.processed_root


class ImageStatus(IntEnum):
    """The status of an image.

    These describe stages of the *pipeline's* work, so `panoptes-pipeline` owns
    them now (`panoptes.pipeline.status`, data contract 5.2). They are kept
    here because they are part of this package's published surface, and because
    a document carries ``image_status`` as one of these names, which a reader
    comparing against them should not have to install the producer to do.
    """

    ERROR = auto()
    MASKED = auto()
    UNKNOWN = auto()
    RECEIVING = auto()
    RECEIVED = auto()
    UNSOLVED = auto()
    PROCESSING = auto()
    CALIBRATING = auto()
    CALIBRATED = auto()
    SOLVING = auto()
    SOLVED = auto()
    MATCHING = auto()
    MATCHED = auto()
    EXTRACTING = auto()
    EXTRACTED = auto()


class ObservationStatus(IntEnum):
    """The status of an observation."""

    ERROR = auto()
    NOT_ENOUGH_FRAMES = auto()
    UNKNOWN = auto()
    CREATED = auto()
    RECEIVING = auto()
    RECEIVED = auto()
    PROCESSING = auto()
    CALIBRATING = auto()
    CALIBRATED = auto()
    MATCHING = auto()
    MATCHED = auto()
