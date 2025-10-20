from enum import IntEnum, auto


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
