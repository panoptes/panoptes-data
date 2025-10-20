from enum import IntEnum, auto

from pydantic.networks import AnyHttpUrl
from pydantic_settings import BaseSettings


class CloudSettings(BaseSettings):
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
