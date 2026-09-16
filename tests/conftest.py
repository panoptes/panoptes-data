"""A processed tree and an index over it, in the shape `panoptes-pipeline` writes.

The point of these fixtures is that nothing here is a stub of this package's own
reader. Documents are nested exactly as `extract_metadata` returns them, the
files sit where `products.write_frame` and `write_observation` put them, and the
index carries the columns `panoptes.pipeline.index` declares. A test that passes
against these is a test against the contract rather than against our reading of
it.

`build_index` deliberately re-implements the producer's grouping rather than
importing it: this package does not depend on `panoptes-pipeline`, and a test
that imported the writer to check the reader would agree with itself no matter
what either of them did.
"""

import json
from pathlib import Path

import pandas as pd
import pytest

# One sequence, two frames, matching the example throughout the docs.
UNIT_ID = "PAN012"
CAMERA_ID = "358d0f"
SEQUENCE_TIME = "20180824T035917"
SEQUENCE_ID = f"{UNIT_ID}_{CAMERA_ID}_{SEQUENCE_TIME}"
IMAGE_TIMES = ("20180824T040118", "20180824T040248")

# A serial with a leading zero, which is the whole of panoptes/panoptes-data#13:
# `pd.read_csv` inferred float64 and returned 3.207100e+10.
CAMERA_SERIAL = "032071000633"

FINGERPRINT = "a1b2c3d4e5f6"

# Columns `panoptes.pipeline.index.REQUIRED_FRAME_COLUMNS` guarantees.
REQUIRED_FRAME_COLUMNS = (
    "unit_unit_id",
    "sequence_sequence_id",
    "sequence_sequence_time",
    "sequence_field_name",
    "sequence_camera_camera_id",
    "sequence_camera_serial_number",
    "image_uid",
    "image_image_time",
    "image_status",
    "image_camera_exptime",
    "image_params_fingerprint",
)

# Columns `panoptes.pipeline.index.OBSERVATION_COLUMNS` declares, in order.
OBSERVATION_COLUMNS = (
    "sequence_sequence_id",
    "unit_id",
    "camera_id",
    "field_name",
    "sequence_time",
    "num_frames",
    "num_usable",
    "start_time",
    "end_time",
    "duration_minutes",
    "total_exptime",
    "num_serials",
    "camera_num_serials",
)


def as_iso(flattened_time: str) -> str:
    """``20180824T040118`` as the ISO 8601 string a document carries."""
    return f"{pd.to_datetime(flattened_time).isoformat()}+00:00"


def frame_document(
    unit_id=UNIT_ID,
    camera_id=CAMERA_ID,
    sequence_time=SEQUENCE_TIME,
    image_time=IMAGE_TIMES[0],
    *,
    mount_ra=83.8221,
    mount_dec=-5.39111,
    exptime=120.0,
    status="MATCHED",
    field_name="M42",
    serial_number=CAMERA_SERIAL,
):
    """One ``metadata.json``, nested as `extract_metadata` returns it."""
    sequence_id = f"{unit_id}_{camera_id}_{sequence_time}"
    return {
        "unit": {
            "unit_id": unit_id,
            "latitude": 19.54,
            "longitude": -155.58,
            "elevation": 3400.0,
            "name": "PAN012",
        },
        "sequence": {
            "sequence_id": sequence_id,
            "sequence_time": as_iso(sequence_time),
            "coordinates": {
                "airmass": 1.2,
                "mount_dec": mount_dec,
                "mount_ra": mount_ra,
                "mount_ha": 0.5,
            },
            "camera": {
                "camera_id": camera_id,
                "lens_serial_number": "HA0028608",
                "serial_number": serial_number,
            },
            "imagew": 6000,
            "imageh": 4000,
            "field_name": field_name,
            "software_version": "POCSv0.6.0",
        },
        "image": {
            "uid": f"{sequence_id}_{image_time}",
            "camera": {
                "camera_id": camera_id,
                "serial_number": serial_number,
                "exptime": exptime,
                "iso": 100,
                "temperature": 20.0,
                "white_lvln": 11765,
            },
            "environment": {"moonfrac": 0.3, "moonsep": 60.0},
            "calibration": {
                "saturation": {"value": 11765.0, "provenance": "HEADER"},
            },
            "file_creation_date": as_iso(image_time),
            "image_time": as_iso(image_time),
            # The settings dump the pipeline stamps into every frame. It is
            # present here on purpose: the index drops it, so a reader that
            # does not produces columns `frames.parquet` lacks, and a fixture
            # without it lets that bug pass unnoticed.
            "params": {
                "camera": {"saturation": 16384.0, "effective_gain": 1.5},
                "background": {"box_size": 79},
            },
            "params_fingerprint": FINGERPRINT,
            "status": status,
        },
    }


def observation_document(frames):
    """The ``observation.json`` `write_observation` aggregates from `frames`."""
    first = frames[0]
    processed = [f for f in frames if f["image"]["status"] == "MATCHED"]
    return {
        "sequence_id": first["sequence"]["sequence_id"],
        "unit_id": first["unit"]["unit_id"],
        "camera_id": first["sequence"]["camera"]["camera_id"],
        "sequence_time": first["sequence"]["sequence_time"],
        "num_frames": len(frames),
        "num_processed": len(processed),
        "params_fingerprint": FINGERPRINT,
        "status": "MATCHED" if len(processed) == len(frames) else "PROCESSING",
        "sequence": first["sequence"],
    }


def write_sequence(processed_root: Path, frames, write_observation=True) -> Path:
    """Write one sequence's documents into the tree, returning its directory."""
    sequence = frames[0]["sequence"]
    unit_id = frames[0]["unit"]["unit_id"]
    camera_id = sequence["camera"]["camera_id"]
    sequence_time = sequence["sequence_id"].split("_")[-1]
    directory = Path(processed_root) / unit_id / camera_id / sequence_time

    for frame in frames:
        # From the document's own image time, not its uid: a test that removes
        # the uid still needs the frame to land in the right directory.
        image_time = pd.to_datetime(frame["image"]["image_time"]).strftime("%Y%m%dT%H%M%S")
        frame_directory = directory / image_time
        frame_directory.mkdir(parents=True, exist_ok=True)
        (frame_directory / "metadata.json").write_text(json.dumps(frame, indent=2))

    if write_observation:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "observation.json").write_text(
            json.dumps(observation_document(frames), indent=2)
        )

    return directory


def flatten(document, prefix=""):
    flat = {}
    for key, value in document.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(flatten(value, f"{name}_"))
        else:
            flat[name] = value
    return flat


def build_index(processed_root: Path, index_root: Path | None = None, version: int = 1) -> Path:
    """Write ``frames.parquet``, ``observations.parquet`` and ``schema.json``.

    The observation table is grouped from the frame table and never written
    directly from the tree, which is the producer's own rule: two tables built
    separately from the same documents could disagree and nothing would notice.
    """
    index_root = Path(index_root if index_root is not None else processed_root)
    index_root.mkdir(parents=True, exist_ok=True)

    rows = []
    for path in sorted(Path(processed_root).rglob("metadata.json")):
        document = json.loads(path.read_text())
        # The producer's DROPPED step, which `schema.json` declares below.
        document.get("image", {}).pop("params", None)
        rows.append(flatten(document))
    frames = pd.DataFrame(rows)
    frames = frames.reindex(columns=list(dict.fromkeys([*REQUIRED_FRAME_COLUMNS, *frames.columns])))

    if frames.empty:
        observations = pd.DataFrame(columns=list(OBSERVATION_COLUMNS))
    else:
        table = frames.copy()
        table["image_time"] = pd.to_datetime(
            table.image_image_time, format="mixed", utc=True, errors="coerce"
        )
        serials_per_camera = table.groupby("sequence_camera_camera_id")[
            "sequence_camera_serial_number"
        ].nunique()
        grouped = table.groupby("sequence_sequence_id")
        observations = grouped.agg(
            unit_id=("unit_unit_id", "first"),
            camera_id=("sequence_camera_camera_id", "first"),
            field_name=("sequence_field_name", "first"),
            sequence_time=("sequence_sequence_time", "first"),
            num_frames=("image_uid", "size"),
            start_time=("image_time", "min"),
            end_time=("image_time", "max"),
            total_exptime=("image_camera_exptime", lambda v: v.sum(min_count=1)),
            num_serials=("sequence_camera_serial_number", "nunique"),
        )
        observations["num_usable"] = grouped["image_status"].apply(
            lambda status: (status == "MATCHED").sum()
        )
        observations["duration_minutes"] = (
            observations.end_time - observations.start_time
        ).dt.total_seconds() / 60
        observations["camera_num_serials"] = observations.camera_id.map(serials_per_camera)
        observations = observations.reset_index()[list(OBSERVATION_COLUMNS)]

    frames.to_parquet(index_root / "frames.parquet", index=False)
    observations.to_parquet(index_root / "observations.parquet", index=False)
    (index_root / "schema.json").write_text(
        json.dumps(
            {
                "version": version,
                "separator": "_",
                "dropped": [["image", "params"]],
                "required_frame_columns": ["image_uid", "image_image_time"],
                "files": {
                    "frames.parquet": {"row": "frame"},
                    "observations.parquet": {
                        "row": "sequence",
                        "derived_from": "frames.parquet",
                    },
                },
            },
            indent=2,
        )
    )
    return index_root


@pytest.fixture
def frames():
    """Two frames of one sequence, ninety seconds apart."""
    return [frame_document(image_time=image_time) for image_time in IMAGE_TIMES]


@pytest.fixture
def processed_root(tmp_path, frames, monkeypatch):
    """A processed tree holding one sequence, configured via the environment."""
    root = tmp_path / "processed"
    write_sequence(root, frames)
    monkeypatch.setenv("PANOPTES_PROCESSED_ROOT", str(root))
    monkeypatch.delenv("PANOPTES_ARCHIVE_ROOT", raising=False)
    monkeypatch.delenv("PANOPTES_INDEX_ROOT", raising=False)
    return root


@pytest.fixture
def indexed_root(processed_root):
    """`processed_root`, with the index built over it."""
    build_index(processed_root)
    return processed_root
