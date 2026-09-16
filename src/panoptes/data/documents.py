"""Read what the pipeline writes: the document tree, and the index over it.

`panoptes-pipeline` processes a frame and writes a JSON document beside the
products, in a tree that mirrors the raw archive layout::

    <processed_root>/PAN012/358d0f/20180824T035917/
        observation.json            # the sequence document
        20180824T040118/
            metadata.json           # {unit, sequence, image}
            image.fits
            sources.parquet

Those files are the source of truth. Walking them produces ``frames.parquet``
(one row per frame) and ``observations.parquet`` (one row per sequence, grouped
from the frames), with ``schema.json`` recording the column contract. That is
the query surface, and it is what replaced the Firestore-derived
``observations.csv`` this package used to read.

Why this module exists rather than a few `json.loads` calls
-----------------------------------------------------------
panoptes/panoptes-data#12 and #13 were the same defect twice: a vocabulary
shared across a repository boundary with nothing declaring it, so the reader
guessed a field name and the guess went stale. The names here come from
`data contract`_ section 3 and are read back from ``schema.json`` where the
producer wrote it down, rather than being inferred from what happened to be in
a record.

The flattening in particular is a contract, not a convenience. A document is a
nested map -- ``{"image": {"camera": {"exptime": 120.0}}}`` -- and a DataFrame
column is a flat name, so the two are joined by a separator. That separator is
``_`` and never ``.``: contract 3.2 calls a dotted name a *view* over a nested
map rather than storage, and the dotted ``camera.serial_number`` in the old
``observations.csv`` is exactly the confusion between "document path" and
"column" being avoided. `read_schema` reads the producer's declared separator
so a change there is visible here instead of silently renaming every column.

.. _data contract:
   https://github.com/panoptes/panoptes-pipeline/blob/main/plans/data-contract.md
"""

import json
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pandas as pd
from panoptes.utils.images.fits import ImagePathInfo
from pyarrow import parquet

#: Separator joining the levels of a flattened document key, matching
#: `panoptes.pipeline.index.SEPARATOR`. Used when no ``schema.json`` declares
#: one -- reading a tree directly does not require an index to exist.
SEPARATOR = '_'

#: The documents, as `panoptes.pipeline.settings.FileSettings` names them.
OBSERVATION_FILENAME = 'observation.json'
METADATA_FILENAME = 'metadata.json'

#: The index, as `panoptes.pipeline.index` names it.
FRAMES_FILENAME = 'frames.parquet'
OBSERVATIONS_FILENAME = 'observations.parquet'
SCHEMA_FILENAME = 'schema.json'

#: The index schema version this package knows how to read. Bumped on the
#: producing side when the column contract changes, which is the whole point:
#: a change there becomes a loud failure here rather than a `KeyError` on a
#: column that quietly stopped existing.
SCHEMA_VERSION = 1

NO_ROOT_MESSAGE = (
    'No processed tree is configured, so there are no documents to read. Set '
    'PANOPTES_PROCESSED_ROOT to the root of a tree written by '
    'panoptes-pipeline -- the directory holding the unit folders, so that '
    '<root>/PAN012/358d0f/20180824T035917/observation.json is a sequence '
    'document. This package no longer reads the Firestore-derived '
    'observations.csv or the get-observation-info cloud function; both were '
    'downstream of a pipeline that stopped producing them.'
)


class DocumentsUnavailableError(FileNotFoundError):
    """A document or index was asked for and the tree cannot supply it."""


class SchemaVersionError(RuntimeError):
    """The index declares a column contract this package does not know."""


def flatten(document: Mapping[str, Any], prefix: str = '',
            separator: str = SEPARATOR) -> dict[str, Any]:
    """Flatten nested maps into single-level keys joined by `separator`.

    ``{"image": {"camera": {"exptime": 120.0}}}`` becomes
    ``{"image_camera_exptime": 120.0}``, which is the name the index carries
    for the same value. Lists are left alone: nothing in a document holds one
    that wants to be a column.

    This mirrors `panoptes.pipeline.index.flatten`, deliberately rather than by
    import -- the reader does not depend on the writer's package, only on the
    contract both follow. Reading a tree with no index built over it still has
    to produce the same names, or a sequence read from documents and the same
    sequence read from the index would disagree about what its columns are
    called.
    """
    flat: dict[str, Any] = {}
    for key, value in document.items():
        name = f'{prefix}{key}'
        if isinstance(value, Mapping):
            flat.update(flatten(value, f'{name}{separator}', separator=separator))
        else:
            flat[name] = value
    return flat


def read_schema(index_root: Path | str) -> dict[str, Any] | None:
    """The column contract the index was written with, or None if absent.

    Returns the manifest the producer wrote, having first checked its version.
    An index built by a pipeline newer than this package is refused rather than
    read hopefully: the manifest exists so that a column contract change is
    visible, and reading one anyway would make it invisible again.

    A tree with no manifest is not an error. Documents can be read without an
    index, and an index built before manifests existed is still parquet.

    Raises:
        SchemaVersionError: if the manifest declares a version this package
            does not know how to read.
    """
    path = Path(index_root) / SCHEMA_FILENAME
    if not path.is_file():
        return None

    try:
        manifest = json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError) as e:
        raise SchemaVersionError(f'The index manifest at {path} could not be read: {e}') from e

    if not isinstance(manifest, Mapping):
        raise SchemaVersionError(f'The index manifest at {path} is not an object.')

    version = manifest.get('version')
    if version != SCHEMA_VERSION:
        raise SchemaVersionError(
            f'The index at {index_root} declares schema version {version!r}, and this '
            f'version of panoptes-data reads version {SCHEMA_VERSION}. The column '
            f'contract changed on the producing side; upgrade panoptes-data rather '
            f'than reading the index against the wrong vocabulary.'
        )

    return dict(manifest)


def separator_for(index_root: Path | str | None) -> str:
    """The separator the index at `index_root` declares, else `SEPARATOR`.

    Contract 3.2 records the separator in the manifest precisely so a consumer
    in another repository does not reverse-engineer it from a parquet footer.
    """
    if index_root is None:
        return SEPARATOR

    manifest = read_schema(index_root)
    if manifest is None:
        return SEPARATOR

    return manifest.get('separator') or SEPARATOR


def sequence_directory(processed_root: Path | str, sequence_id: str) -> Path:
    """The directory holding one sequence's documents.

    ``<processed_root>/{unit_id}/{camera_id}/{sequence_time}``, which is both
    the archive's layout and the document path ``units/{unit_id}/observations/
    {sequence_id}``. That they are the same shape is why a tree of files and a
    document store are the same thing here.

    `ImagePathInfo` parses a *frame* path, so the sequence time stands in for
    the image time to validate the three components and then the frame level is
    dropped. Borrowing the validator this way keeps one definition of a
    well-formed identifier rather than a second regex that can drift from it.

    Raises:
        ValueError: if `sequence_id` is not a well-formed sequence identifier.
    """
    try:
        unit_id, camera_id, sequence_time = str(sequence_id).split('_')
        path_info = ImagePathInfo(path=f'{unit_id}/{camera_id}/{sequence_time}/{sequence_time}')
    except ValueError as e:
        raise ValueError(
            f'{sequence_id!r} is not a well-formed sequence id, so no directory in the '
            f'processed tree corresponds to it. It should look like '
            f'PAN012_358d0f_20180824T035917: {e}'
        ) from e

    return Path(processed_root) / path_info.as_path().parent


def require_root(root: Path | str | None, what: str) -> Path:
    """`root` as an existing directory, or a `DocumentsUnavailableError` saying why not.

    Both failures get their own message. A root that is not configured and a
    root that is mistyped are different mistakes, and reporting the second as
    the first sends you to edit a setting that is already correct.
    """
    if root is None:
        raise DocumentsUnavailableError(NO_ROOT_MESSAGE)

    root = Path(root)
    if not root.is_dir():
        raise DocumentsUnavailableError(
            f'The {what} {root} is not a directory. It should point at the directory '
            f'holding the unit folders, e.g. <root>/PAN012/358d0f/20180824T035917/.'
        )

    return root


def find_frame_documents(processed_root: Path | str, sequence_id: str) -> Iterator[Path]:
    """Every ``metadata.json`` of one sequence, in a stable order.

    Sorted by path, which is sorted by image time, because the directory name
    *is* the flattened image time. Two reads of the same tree therefore produce
    rows in the same order.
    """
    directory = sequence_directory(processed_root, sequence_id)
    return iter(sorted(directory.glob(f'*/{METADATA_FILENAME}')))


def read_document(path: Path | str) -> dict[str, Any] | None:
    """Read one JSON document, or None if it is missing or unusable.

    "Unusable" includes a file that parses as valid JSON but is not an object.
    ``json.loads`` will happily return a list or a number, and every caller
    here expects a mapping, so returning one would move the failure to an
    `AttributeError` somewhere less informative.
    """
    try:
        document = json.loads(Path(path).read_text())
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None

    return document if isinstance(document, dict) else None


def read_observation(processed_root: Path | str, sequence_id: str,
                     separator: str = SEPARATOR) -> dict[str, Any]:
    """The sequence's ``observation.json``, flattened.

    Raises:
        DocumentsUnavailableError: if the sequence has no readable observation
            document under `processed_root`.
    """
    path = sequence_directory(processed_root, sequence_id) / OBSERVATION_FILENAME

    document = read_document(path)
    if document is None:
        raise DocumentsUnavailableError(
            f'No readable observation document for {sequence_id} at {path}. The '
            f'sequence is not in this processed tree, or the pipeline has not '
            f'aggregated it yet.'
        )

    return flatten(document, separator=separator)


def read_frames(processed_root: Path | str, sequence_id: str,
                separator: str = SEPARATOR) -> pd.DataFrame:
    """One row per frame of `sequence_id`, from its ``metadata.json`` documents.

    A document that cannot be read is not skipped. The pipeline's own index
    walk skips them, because one bad file must not cost an index over half a
    million frames -- but here the unit of work is a single observation, and a
    frame silently missing from it reads as an observation with fewer frames.
    That is the same failure as a partial local archive, which
    `ObservationInfo.get_image_list` already refuses to paper over.

    Raises:
        DocumentsUnavailableError: if the sequence has no frame documents, or
            if one of them cannot be read.
    """
    paths = list(find_frame_documents(processed_root, sequence_id))
    if not paths:
        raise DocumentsUnavailableError(
            f'No frame documents for {sequence_id} under '
            f'{sequence_directory(processed_root, sequence_id)}. The sequence is not '
            f'in this processed tree, or none of its frames have been processed.'
        )

    rows = []
    for path in paths:
        document = read_document(path)
        if document is None:
            raise DocumentsUnavailableError(
                f'The frame document {path} could not be read, so the metadata for '
                f'{sequence_id} would be short by one frame rather than wrong in a '
                f'way you could see. Reprocess that frame.'
            )
        rows.append(flatten(document, separator=separator))

    # `pandas` unions the keys, so a field absent from one document arrives as
    # a null in that row rather than failing the read. Documents written by an
    # older pipeline still have to load.
    return pd.DataFrame(rows)


def index_columns(index_root: Path | str | None, filename: str) -> list[str]:
    """The column names one index file carries, read from its footer.

    Cheap: parquet keeps its schema in the file, so this does not read a row.
    Used to ask for columns the index only *may* have -- the producer guarantees
    a small required set and everything else depends on what the documents
    happened to hold.

    Raises:
        DocumentsUnavailableError: if the index file is not there.
        SchemaVersionError: if the index declares a version this package does
            not read.
    """
    index_root = require_root(index_root, 'index root')
    read_schema(index_root)

    path = index_root / filename
    if not path.is_file():
        raise DocumentsUnavailableError(
            f'No {filename} at {index_root}. Build the index over the processed tree '
            f'with panoptes-pipeline before querying it.'
        )

    return list(parquet.read_schema(path).names)


def read_index(index_root: Path | str | None, filename: str,
               columns: list[str] | None = None) -> pd.DataFrame:
    """Read one of the index files, with its dtypes intact.

    `columns` is pushed down into the parquet read, so asking for four columns
    of ``frames.parquet`` does not pay for the other fifty.

    Reading parquet rather than CSV is what fixes panoptes/panoptes-data#13 at
    the root: a serial like ``032071000633`` is a string in the file and comes
    back a string. ``pd.read_csv`` had to infer it, inferred float64, and
    returned ``3.207100e+10`` -- a corrupted value for the one field that links
    a frame to a physical camera body.

    Raises:
        DocumentsUnavailableError: if the index file or a requested column is
            not there.
        SchemaVersionError: if the index declares a schema version this package
            does not read.
    """
    index_root = require_root(index_root, 'index root')
    read_schema(index_root)

    path = index_root / filename
    if not path.is_file():
        raise DocumentsUnavailableError(
            f'No {filename} at {index_root}. Build the index over the processed tree '
            f'with panoptes-pipeline before querying it.'
        )

    try:
        return pd.read_parquet(path, columns=columns)
    except (KeyError, ValueError) as e:
        raise DocumentsUnavailableError(
            f'{path} does not carry the column(s) asked for: {e}'
        ) from e
