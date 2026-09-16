import logging
from datetime import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd
from astropy.coordinates import SkyCoord
from dateutil.parser import parse as parse_date
from panoptes.utils.time import current_time
from panoptes.utils.utils import listify

from panoptes.data import documents
from panoptes.data.observations import ObservationInfo, sequence_id_of
from panoptes.data.settings import SurveySettings

logger = logging.getLogger()


class MetadataUnavailableError(RuntimeError):
    """Some sequences' metadata could not be read, so the table is incomplete.

    Carries what it knows rather than only that it failed: `failures` maps each
    sequence id to the exception it raised, and `partial` is the table of the
    sequences that did read. A caller who genuinely wants an incomplete result
    takes it from here, which is the difference between choosing a partial
    answer and being handed one.
    """

    def __init__(self, message: str, failures: dict, partial):
        super().__init__(message)
        self.failures = failures
        self.partial = partial


# The frames-index columns a sequence's pointing is derived from. Read on their
# own, so asking four columns of `frames.parquet` does not pay for the fifty
# the file carries.
POINTING_COLUMNS = (
    "sequence_sequence_id",
    "sequence_coordinates_mount_ra",
    "sequence_coordinates_mount_dec",
)

# What `add_pointing` adds to the observation index. These are *derived here*,
# not columns the contract declares: `observations.parquet` groups on sequence
# and carries no coordinates, because a mount position is a per-frame reading.
# See `add_pointing` for why the drift columns come with them.
POINTING_RESULT_COLUMNS = ("mount_ra", "mount_dec", "mount_ra_drift", "mount_dec_drift")


def wrap_degrees(angle):
    """Signed separation in degrees, folded onto ``(-180, 180]``.

    A mount at 359.9 degrees and a target at 0.1 are 0.2 degrees apart, not
    359.8. The old CSV-era search compared raw degrees, so every cone spanning
    0h RA silently returned nothing.
    """
    return (np.asarray(angle, dtype=float) + 180.0) % 360.0 - 180.0


def add_pointing(observations: pd.DataFrame, frames: pd.DataFrame) -> pd.DataFrame:
    """Attach each sequence's mean pointing, and how far it wandered from it.

    ``observations.parquet`` carries no coordinates: the index groups frames
    into sequences, and ``RA-MNT``/``DEC-MNT`` are per-frame header readings, so
    there is no single sequence coordinate to store. This derives one.

    **The mean is not enough on its own.** Older observations drift
    substantially over a night -- that is the point of measuring drift at all
    (panoptes-pipeline improvement plan 3.2) -- so a sequence whose mean sits
    just outside a search cone may still have spent half the night inside it.
    Each sequence therefore also gets the largest deviation of any of its frames
    from its own mean, and `search_observations` widens the cone by that amount
    per sequence rather than by one fleet-wide fudge factor.

    RA is averaged as an angle, via the mean unit vector, so a sequence
    straddling 0h averages to 0 rather than to 180.

    Returns:
        `observations` with `POINTING_RESULT_COLUMNS` added. Sequences with no
        frame coordinates get nulls, and a null never matches a cone.
    """
    missing = [name for name in POINTING_COLUMNS if name not in frames.columns]
    if missing:
        # The producer guarantees a small set of frame columns and these are not
        # in it, so an index over documents that never carried a mount position
        # is valid. Searching it by coordinate finds nothing, which is true;
        # refusing to read it at all would not be.
        logger.warning(f"The frames index carries no {missing}; no sequence has a pointing.")
        return observations.assign(**{name: np.nan for name in POINTING_RESULT_COLUMNS})

    pointing = frames.dropna(
        subset=["sequence_coordinates_mount_ra", "sequence_coordinates_mount_dec"]
    )
    if pointing.empty:
        return observations.assign(**{name: np.nan for name in POINTING_RESULT_COLUMNS})

    ra = np.radians(pointing.sequence_coordinates_mount_ra.astype(float))
    pointing = pointing.assign(_ra_sin=np.sin(ra), _ra_cos=np.cos(ra))

    grouped = pointing.groupby("sequence_sequence_id", observed=True)
    means = grouped.agg(
        _ra_sin=("_ra_sin", "mean"),
        _ra_cos=("_ra_cos", "mean"),
        mount_dec=("sequence_coordinates_mount_dec", "mean"),
    )
    mean_ra = np.degrees(np.arctan2(means._ra_sin, means._ra_cos)) % 360.0
    # A mean that lands a hair below zero rounds to exactly 360 under the
    # modulo, which is the same direction reported as the wrong number.
    means["mount_ra"] = mean_ra.where(mean_ra < 360.0, 0.0)

    # Drift is measured against each sequence's own mean, so it has to be
    # joined back onto the frames before it can be reduced again.
    per_frame = pointing.join(means[["mount_ra", "mount_dec"]], on="sequence_sequence_id")
    per_frame["_ra_offset"] = np.abs(
        wrap_degrees(per_frame.sequence_coordinates_mount_ra.astype(float) - per_frame.mount_ra)
    )
    per_frame["_dec_offset"] = np.abs(
        per_frame.sequence_coordinates_mount_dec.astype(float) - per_frame.mount_dec
    )
    drift = per_frame.groupby("sequence_sequence_id", observed=True).agg(
        mount_ra_drift=("_ra_offset", "max"),
        mount_dec_drift=("_dec_offset", "max"),
    )

    return observations.join(
        means[["mount_ra", "mount_dec"]].join(drift), on="sequence_sequence_id"
    )


def search_observations(
    by_name=None,
    coords=None,
    unit_id=None,
    start_date=None,
    end_date=None,
    ra=None,
    dec=None,
    radius=10,  # degrees
    min_num_frames=1,
    source=None,
    ra_col="mount_ra",
    dec_col="mount_dec",
) -> pd.DataFrame:
    """Search PANOPTES observations.

    Either a `coords` or `ra` and `dec` must be specified for search to work.

    The columns are the ones `panoptes-pipeline` declares -- ``num_frames``,
    ``num_usable``, ``duration_minutes``, ``sequence_sequence_id`` -- rather
    than the Firestore summary's, because the summary is not produced any more.
    See `panoptes.data.documents`.

    >>> from astropy.coordinates import SkyCoord
    >>> from panoptes.data.search import search_observations
    >>> coords = SkyCoord.from_name('Andromeda Galaxy')
    >>> start_date = '2019-01-01'
    >>> end_date = '2019-12-31'
    >>> search_results = search_observations(coords=coords, min_num_frames=10,
    ...                                      start_date=start_date, end_date=end_date)
    >>> # The result is a DataFrame you can further work with.
    >>> search_results.groupby(['unit_id', 'field_name']).num_usable.sum()

    Args:
        by_name (str|None): If present, this will use the `SkyCoords.from_name` method
            to do a search for the appropriate coords.
        coords (`astropy.coordinates.SkyCoord`|None): A valid coordinate instance.
        ra (float|None): The RA position in degrees of the center of search.
        dec (float|None): The Dec position in degrees of the center of the search.
        radius (float): The search radius in degrees. Searches are done in a
            square box, so this is half the length of the side of the box. Each
            sequence's box is widened by that sequence's own pointing drift, so
            an observation that wandered into the box is found; see
            `add_pointing`.
        start_date (str|`datetime.datetime`|None): A valid datetime instance or `None` (default).
            If `None` then the beginning of the current year is used as a start date.
        end_date (str|`datetime.datetime`|None): A valid datetime instance or `None` (default).
            If `None` then today is used.
        unit_id (str|list|None): A str or list of strs of unit_ids to include.
            Default `None` will include all.
        min_num_frames (int): Minimum number of frames the observation should
            have, default 1. This counts frames the pipeline has a document for;
            ``num_usable`` in the result counts the ones that were processed
            successfully, which is not the same number.
        source (`pandas.DataFrame`|None): The table to search. If `None`
            (default) the observation index is read. Never modified in place.
        ra_col (str): The column to use for the RA, default 'mount_ra'.
        dec_col (str): The column to use for the Dec, default 'mount_dec'.

    Returns:
        `pandas.DataFrame`: A table with the matching observation results.
    """
    logger.debug("Setting up search params")

    if coords is None:
        if by_name is not None:
            coords = SkyCoord.from_name(by_name)
            print(f"Found coords for {by_name}: {coords}")
        else:
            coords = SkyCoord(ra=ra, dec=dec, unit="degree")

    if start_date is None:
        start_date = f"{dt.today().year}-01-01"

    if end_date is None:
        end_date = current_time()

    start_date = pd.to_datetime(parse_date(str(start_date)), utc=True)
    end_date = pd.to_datetime(parse_date(str(end_date)), utc=True)

    # Never mutate the caller's table: the previous version ran
    # `query(..., inplace=True)` on whatever `source` was handed in.
    obs_df = source.copy() if source is not None else get_all_observations()
    print(f"Searching {len(obs_df)} observations")

    # Widen each sequence's box by its own drift. A drift the index cannot
    # report is treated as zero rather than as infinite: unknown drift should
    # not pull in every sequence in the archive.
    ra_pad = radius + obs_df.get(f"{ra_col}_drift", pd.Series(0.0, index=obs_df.index)).fillna(0.0)
    dec_pad = radius + obs_df.get(f"{dec_col}_drift", pd.Series(0.0, index=obs_df.index)).fillna(
        0.0
    )

    sequence_time = pd.to_datetime(obs_df.sequence_time, format="mixed", utc=True)

    matches = (
        (np.abs(wrap_degrees(obs_df[ra_col].astype(float) - coords.ra.deg)) <= ra_pad)
        & (np.abs(obs_df[dec_col].astype(float) - coords.dec.deg) <= dec_pad)
        & (sequence_time >= start_date)
        & (sequence_time <= end_date)
        & (obs_df.num_frames >= min_num_frames)
    )
    obs_df = obs_df[matches.fillna(False)]
    print(f"Found {len(obs_df)} observations after initial filter")

    unit_ids = listify(unit_id)
    if len(unit_ids) > 0:
        obs_df = obs_df[obs_df.unit_id.isin(unit_ids)]
        print(f"Found {len(obs_df)} observations after unit filter")

    obs_df = obs_df.reindex(sorted(obs_df.columns), axis=1)
    obs_df = obs_df.sort_values(by=["sequence_time"])

    # Mean exposure per frame. `total_exptime` is summed over the frame
    # documents rather than read from a column only the index held, which is
    # why it is no longer null for exactly the long sequences anyone wants.
    obs_df["exptime"] = obs_df.total_exptime / obs_df.num_frames

    print(f"Returning {len(obs_df)} observations")
    return obs_df


def get_all_observations(
    settings: SurveySettings | None = None,
    index_root: Path | str | None = None,
) -> pd.DataFrame:
    """Every sequence in the index, with its pointing attached.

    Reads ``observations.parquet``, the query surface `panoptes-pipeline`
    builds by walking the documents it wrote. That replaced the
    Firestore-generated ``observations.csv``, which nothing produces any more.

    Two things come for free with parquet that the CSV could not give. Dtypes
    are in the file, so ``camera_serial_number`` comes back as the string
    ``032071000633`` instead of being inferred into ``3.207100e+10``
    (panoptes/panoptes-data#13). And ``total_exptime`` is a sum over per-frame
    records rather than a number the index alone held, so it is populated for
    the long sequences where the CSV had null and nowhere to recover it from.

    Args:
        settings: The settings to use. Defaults to reading the environment.
        index_root: The directory holding the index files, overriding the
            settings for this call.

    Returns:
        pd.DataFrame: One row per sequence, with `POINTING_RESULT_COLUMNS`
        added.

    Raises:
        DocumentsUnavailableError: if no index is configured or none is there.
    """
    settings = settings or SurveySettings()
    index_root = index_root if index_root is not None else settings.resolved_index_root

    print(f"Getting list of observations from the index at {index_root}")
    obs_df = documents.read_index(index_root, documents.OBSERVATIONS_FILENAME)

    # Ask only for pointing columns the index actually has: they are not in the
    # set the producer guarantees, so an index without them is still valid.
    available = documents.index_columns(index_root, documents.FRAMES_FILENAME)
    frames = documents.read_index(
        index_root,
        documents.FRAMES_FILENAME,
        columns=[name for name in POINTING_COLUMNS if name in available],
    )

    logger.info(f"Found {len(obs_df)} total observations")
    return add_pointing(obs_df, frames)


def get_metadata(observations: pd.DataFrame, errors: str = "raise") -> pd.DataFrame:
    """Read the per-frame documents of many sequences into one table.

    This used to wrap the read in ``except Exception: pass``, so a run in which
    every sequence failed returned the same empty table as a run with nothing to
    read, and a run in which half failed returned half the archive with no sign
    that it was half (panoptes/panoptes-data#13). Failing is now the default,
    and a caller who wants what did read has to say so -- which is the same rule
    the rest of the package follows: partial data raises, it never silently
    shortens.

    Args:
        observations: Rows carrying a sequence id, as `search_observations`
            returns them.
        errors: ``'raise'`` (default) to refuse a partial result, or
            ``'warn'`` to log which sequences failed and return the rest. A
            `MetadataUnavailableError` carries both the failures and the partial
            table, so ``'raise'`` loses nothing that ``'warn'`` would have kept.

    Returns:
        pd.DataFrame: The frames of every sequence, concatenated. Empty input
        gives an empty table rather than a `ValueError` from `concat`.

    Raises:
        MetadataUnavailableError: with ``errors='raise'``, if any sequence could
            not be read.
        ValueError: if `errors` is neither ``'raise'`` nor ``'warn'``.
    """
    if errors not in ("raise", "warn"):
        raise ValueError(f"`errors` is 'raise' or 'warn', not {errors!r}.")

    dfs = []
    failures: dict[str, Exception] = {}
    for idx, rec in observations.iterrows():
        # The id is read for the error message, not for the lookup: a record
        # `ObservationInfo` can use but this cannot name is still worth trying.
        try:
            name = sequence_id_of(rec)
        except ValueError:
            name = str(idx)

        try:
            dfs.append(ObservationInfo(meta=rec).image_metadata)
        except Exception as e:  # noqa: BLE001 -- recorded and re-raised below.
            failures[name] = e

    partial = pd.concat(dfs) if dfs else pd.DataFrame()

    if failures:
        summary = ", ".join(f"{name} ({e!r})" for name, e in list(failures.items())[:5])
        message = (
            f"{len(failures)} of {len(observations)} sequence(s) could not be read, so "
            f"this table holds {len(partial)} frame(s) from {len(dfs)} sequence(s) "
            f"rather than all of them. First failures: {summary}"
        )
        if errors == "raise":
            raise MetadataUnavailableError(message, failures=failures, partial=partial)
        logger.warning(message)

    return partial
