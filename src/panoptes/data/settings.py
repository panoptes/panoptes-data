from enum import IntEnum, auto
from pathlib import Path

from pydantic.networks import AnyHttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict


class SurveySettings(BaseSettings):
    """Where the survey's data lives.

    Every field names a *location*, and each is configured from the
    environment with a ``PANOPTES_`` prefix, e.g. ``PANOPTES_ARCHIVE_ROOT``.

    The URL fields describe the cloud archive as it was laid out. Nothing
    serves those objects anonymously any more
    (`ObservationInfo.download_images`), so they name where a frame lives
    rather than somewhere to fetch it from. `archive_root` is how a frame is
    actually read: point it at a local copy of the archive and
    `ObservationInfo.get_image_list` resolves a sequence to files on disk.
    """

    model_config = SettingsConfigDict(env_prefix='panoptes_')

    archive_root: Path | None = None
    img_base_url: AnyHttpUrl = 'https://storage.googleapis.com'
    img_bucket: str = 'panoptes-images-incoming'
    img_metadata_url: AnyHttpUrl = 'https://us-central1-project-panoptes-01.cloudfunctions.net/get-observation-info'
    observations_url: AnyHttpUrl = 'https://storage.googleapis.com/panoptes-assets/observations.csv'


class ImageStatus(IntEnum):
    """The status of an image."""
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
