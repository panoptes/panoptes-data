import logging
import re
from datetime import datetime as dt
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from astropy.coordinates import SkyCoord
from dateutil.parser import parse as parse_date
from dateutil.relativedelta import relativedelta
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

#: Header facts that live only in the per-frame records, and the sequence-level
#: column each becomes.
#:
#: Data contract 9 names the cuts benchmark selection makes *before* anything is
#: downloaded -- frame count, duration, unit, camera uid, ISO, exposure, moon,
#: airmass, field -- and deliberately keeps them to header facts, because
#: ranking candidates on pipeline-derived quantities to build the substrate for
#: judging a new pipeline is circular. Of that list, these four are in no
#: column of ``observations.parquet``: each is a per-frame reading, so the index
#: groups frames without them the same way it groups without a coordinate.
#:
#: Each is reduced to its sequence mean, for the reason `add_pointing` reports
#: one: a sequence has no single value. A sequence whose airmass ran from 1.1 to
#: 2.4 has a mean that is no frame's reading, which is what cutting on a whole
#: sequence means. Unlike pointing, no spread is reported alongside: a cone is a
#: membership test that drift can move a sequence into, whereas "ISO 100" is a
#: description, and widening it by its own variation would make every threshold
#: mean something different per row.
FRAME_FACT_COLUMNS = {
    "image_camera_iso": "iso",
    "sequence_coordinates_airmass": "airmass",
    "image_environment_moonfrac": "moonfrac",
    "image_environment_moonsep": "moonsep",
}

#: Columns naming one sequence of a simultaneous pair, suffixed ``_a``/``_b`` by
#: `find_simultaneous`.
SIMULTANEOUS_COLUMNS = (
    "sequence_sequence_id",
    "unit_id",
    "camera_id",
    "field_name",
    "start_time",
    "end_time",
    "num_frames",
    "num_usable",
)


#: A `duration`: how many of which unit, and which side of the anchor date the
#: window falls on. Months and years are calendar spans rather than fixed
#: multiples of a day, so they are applied with `relativedelta` -- "6 months
#: after 2024-03-12" is 2024-09-12, not 2024-03-12 plus 182.6 days.
DURATION_PATTERN = re.compile(
    r"""^\s*
        (?P<count>\d+)\s*
        (?P<unit>hour|day|week|month|year)s?
        (?:\s+(?P<direction>
            before\s+and\s+after | either\s+side\s+of | either\s+side
            | before | ago | after | ahead | forwards? | backwards?
        ))?
    \s*$""",
    re.IGNORECASE | re.VERBOSE,
)

#: What each direction word does to the anchor. `None` means "read it off the
#: anchor", which `duration_window` resolves.
DURATION_DIRECTIONS = {
    "before and after": "both",
    "either side": "both",
    "either side of": "both",
    "before": "backward",
    "ago": "backward",
    "backward": "backward",
    "backwards": "backward",
    "after": "forward",
    "ahead": "forward",
    "forward": "forward",
    "forwards": "forward",
}


def parse_duration(duration: str | timedelta) -> tuple[relativedelta, str | None]:
    """A `duration` as a span and the direction it runs, if it says.

    Accepts ``"90 days"``, ``"6 months before"``, ``"10 days before and after"``
    and a `datetime.timedelta`. The direction is `None` when the string does not
    give one, which leaves the choice to `duration_window`; a negative
    `timedelta` names one, since a window cannot run backward from its start.

    Raises:
        ValueError: if the string is not a duration this understands. The
            message lists the forms that work, because a duration that silently
            parsed as something else would move the search window without
            saying so.
    """
    if isinstance(duration, timedelta):
        # A negative timedelta is a window running backward, not one running
        # from the anchor to before the anchor: an inverted window matches
        # nothing, and would report an empty archive rather than a mistake.
        seconds = duration.total_seconds()
        if seconds < 0:
            return relativedelta(seconds=-seconds), "backward"
        return relativedelta(seconds=seconds), None

    match = DURATION_PATTERN.match(str(duration))
    if match is None:
        raise ValueError(
            f"{duration!r} is not a duration this understands. Write a whole "
            f"number of hours, days, weeks, months or years, optionally saying "
            f"which way it runs: '90 days', '6 months before', "
            f"'10 days before and after', '3 weeks after'."
        )

    count = int(match["count"])
    unit = match["unit"].lower()
    written = match["direction"]
    direction = DURATION_DIRECTIONS[" ".join(written.lower().split())] if written else None

    return relativedelta(**{f"{unit}s": count}), direction


def duration_window(
    duration: str | timedelta, anchor: pd.Timestamp, default: str
) -> tuple[pd.Timestamp, pd.Timestamp]:
    """The ``(start, end)`` a `duration` marks out around `anchor`.

    `default` is the direction to use when the duration does not name one. A
    duration anchored on a `start_date` runs forward from it; one with no
    `start_date` is anchored on now and runs backward, because a window in the
    future holds no observations.

    ``"10 days before and after"`` is why this returns a pair rather than an end
    date: a window can sit on both sides of its anchor, and no single parsed
    datetime can say so.
    """
    span, direction = parse_duration(duration)
    direction = direction or default

    if direction == "both":
        return anchor - span, anchor + span
    if direction == "backward":
        return anchor - span, anchor
    return anchor, anchor + span


def as_utc(value) -> pd.Timestamp:
    """Any date this module accepts, as a UTC timestamp."""
    return pd.to_datetime(parse_date(str(value)), utc=True)


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


def add_frame_facts(observations: pd.DataFrame, frames: pd.DataFrame) -> pd.DataFrame:
    """Attach the per-frame header facts a sequence can be cut on.

    ``observations.parquet`` groups frames into sequences and keeps what is
    meaningful for a sequence, so ISO, airmass and the moon columns are not in
    it -- each is a reading taken per frame. They are nonetheless four of the
    nine cuts data contract 9 says benchmark selection has to make, and
    answering "which sequences were shot at ISO 100 with the moon down" one
    sequence at a time is the 12,439 document reads that issue is about. So they
    are reduced here, in the same pass over ``frames.parquet`` that already
    derives pointing.

    A column no document supplied arrives as null rather than as a missing
    column, so a cut on it selects nothing instead of raising. That matches
    `add_pointing`: an index built over documents that never carried the field
    is a valid index, and a query against it should come back empty rather than
    refuse to run.

    Returns:
        `observations` with the values of `FRAME_FACT_COLUMNS` added as columns.
    """
    available = {
        source: name for source, name in FRAME_FACT_COLUMNS.items() if source in frames.columns
    }
    missing = [name for source, name in FRAME_FACT_COLUMNS.items() if source not in available]
    if missing:
        logger.debug(f"The frames index carries no {missing}; no sequence has them.")
        observations = observations.assign(**{name: np.nan for name in missing})

    if not available:
        return observations

    # Coerced rather than assumed numeric: `iso` is an unparsed header value and
    # arrives as a string from some eras. A reading that will not parse becomes
    # null, which excludes it from the mean rather than poisoning it.
    facts = frames[["sequence_sequence_id", *available]].copy()
    for source in available:
        facts[source] = pd.to_numeric(facts[source], errors="coerce")

    means = (
        facts.groupby("sequence_sequence_id", observed=True)
        .mean()
        .rename(columns=available)
        .reindex(columns=list(available.values()))
    )
    return observations.join(means, on="sequence_sequence_id")


def find_simultaneous(
    observations: pd.DataFrame,
    across: str = "camera_id",
    min_overlap_minutes: float = 0.0,
    same_field: bool = True,
) -> pd.DataFrame:
    """Sequences of the same field shot at the same time by different bodies.

    ``PAN007_d37295_20250407T061910`` and ``PAN007_f6eb3d_20250407T061910`` are
    372 frames each, the same night, the same field, two different cameras on
    one unit. A pair observed simultaneously is the control the photometry
    rebuild compares against, because the sky was the same and the hardware was
    not (panoptes/panoptes-data#14).

    **Overlap in time, not "the same night".** The units are spread across
    longitudes, so a calendar date is a different slice of an observing night
    for each of them, and any UTC-based night key is wrong for somebody.
    ``start_time`` and ``end_time`` are contract columns, so asking whether two
    sequences were *running at once* needs no such convention and admits no
    argument. Sequences whose extent the index cannot report are dropped, since
    an unknown interval overlaps nothing that can be checked.

    Args:
        observations: Sequences to pair, as `get_all_observations` or
            `search_observations` returns them. Never modified in place. A
            sequence with no `across` value, or with no extent, is dropped: it
            cannot be shown to be different hardware or to have overlapped.
        across: The column that must *differ* between the two sequences of a
            pair -- ``'camera_id'`` for two bodies, which includes two cameras
            on one unit, or ``'unit_id'`` for two units.
        min_overlap_minutes: Discard pairs overlapping by less than this. The
            default keeps any overlap at all, including two sequences that
            merely touch.
        same_field: Require both sequences to carry the same ``field_name``.
            Pass `False` to find whatever ran concurrently regardless of where
            it pointed.

    Returns:
        One row per pair, the columns of `SIMULTANEOUS_COLUMNS` suffixed ``_a``
        and ``_b`` plus ``overlap_minutes``, earliest first. ``field_name`` is
        the field the pair shares, and is null when the two sequences were on
        different fields -- ``field_name_a`` and ``field_name_b`` always carry
        both. A table with no pairs still has those columns.

    Raises:
        ValueError: if `across` is not a column to pair across, or if
            `observations` lacks the columns a pairing needs.
    """
    if across not in ("camera_id", "unit_id"):
        raise ValueError(
            f"Pairs are found across 'camera_id' or 'unit_id', not {across!r}. Those are "
            f"the two ways the same sky is recorded by different hardware."
        )

    needed = ["sequence_sequence_id", "start_time", "end_time", across]
    if same_field:
        needed.append("field_name")
    absent = [name for name in needed if name not in observations.columns]
    if absent:
        raise ValueError(
            f"Pairing sequences by overlap needs the column(s) {absent}, which this "
            f"table does not carry; got {sorted(observations.columns)}. They are "
            f"contract columns of observations.parquet, so a table without them is "
            f"probably not the observation index."
        )

    result_columns = [
        *(f"{name}_a" for name in SIMULTANEOUS_COLUMNS),
        *(f"{name}_b" for name in SIMULTANEOUS_COLUMNS),
        "field_name",
        "overlap_minutes",
    ]

    table = observations.copy()
    for name in ("start_time", "end_time"):
        table[name] = pd.to_datetime(table[name], format="mixed", utc=True, errors="coerce")
    # A null `across` value is dropped rather than compared. Two unknown camera
    # ids are not known to be *different* hardware, so pairing them overclaims
    # exactly as pairing two unrecorded fields would -- and comparing them is
    # not even consistent: `nan != nan` invents a pair, while `pd.NA == pd.NA`
    # raises when a branch tests it.
    table = table.dropna(subset=["start_time", "end_time", across])
    # Carried into the pair rows even when the index did not supply them, so the
    # result's columns do not depend on which fields the archive happened to hold.
    for name in SIMULTANEOUS_COLUMNS:
        if name not in table.columns:
            table[name] = pd.NA

    # `dropna` drops the sequences with no recorded field rather than grouping
    # them together: two sequences that never said where they pointed are not
    # known to have pointed at the same place, and reporting them as a matched
    # pair is the overclaim `same_field` exists to prevent. Pass
    # `same_field=False` to pair them anyway.
    groups = (
        table.groupby("field_name", observed=True, dropna=True) if same_field else [(None, table)]
    )

    rows = []
    for _, group in groups:
        # A sweep rather than every pair: the archive is over twelve thousand
        # sequences and comparing each to each is a hundred and fifty million
        # comparisons to find a few hundred pairs. Sorted by start, a sequence
        # can only overlap one still running when it begins.
        running: list = []
        for sequence in group.sort_values("start_time").itertuples(index=False):
            running = [other for other in running if other.end_time >= sequence.start_time]
            for other in running:
                if getattr(other, across) == getattr(sequence, across):
                    continue
                overlap = (
                    min(other.end_time, sequence.end_time) - sequence.start_time
                ).total_seconds() / 60.0
                if overlap < min_overlap_minutes:
                    continue
                # The unsuffixed `field_name` is the field *the pair* was on,
                # so it is set only when both sequences agree. With
                # `same_field=False` they need not, and naming one of the two
                # would describe the pair by half of it.
                field_a = getattr(other, "field_name", None)
                field_b = getattr(sequence, "field_name", None)
                rows.append(
                    {
                        **{f"{name}_a": getattr(other, name) for name in SIMULTANEOUS_COLUMNS},
                        **{f"{name}_b": getattr(sequence, name) for name in SIMULTANEOUS_COLUMNS},
                        "field_name": field_a if field_a == field_b else pd.NA,
                        "overlap_minutes": overlap,
                    }
                )
            running.append(sequence)

    pairs = pd.DataFrame(rows, columns=result_columns)
    return pairs.sort_values("start_time_a").reset_index(drop=True)


def search_observations(
    *,
    by_name=None,
    coords=None,
    unit_id=None,
    start_date=None,
    end_date=None,
    duration=None,
    ra=None,
    dec=None,
    radius=10,  # degrees
    min_num_frames=1,
    min_num_usable=None,
    min_duration_minutes=None,
    field_name=None,
    camera_id=None,
    query=None,
    source=None,
    ra_col="mount_ra",
    dec_col="mount_dec",
) -> pd.DataFrame:
    """Search PANOPTES observations.

    The columns are the ones `panoptes-pipeline` declares -- ``num_frames``,
    ``num_usable``, ``duration_minutes``, ``sequence_sequence_id`` -- rather
    than the Firestore summary's, because the summary is not produced any more.
    See `panoptes.data.documents`.

    **A position is optional.** With no `coords`, `by_name`, or `ra`/`dec`, the
    search is all-sky and every other filter still applies
    (panoptes/panoptes-data#14).

    >>> from astropy.coordinates import SkyCoord
    >>> from panoptes.data.search import search_observations
    >>> coords = SkyCoord.from_name('Andromeda Galaxy')
    >>> start_date = '2019-01-01'
    >>> end_date = '2019-12-31'
    >>> search_results = search_observations(coords=coords, min_num_frames=10,
    ...                                      start_date=start_date, end_date=end_date)
    >>> # The result is a DataFrame you can further work with.
    >>> search_results.groupby(['unit_id', 'field_name']).num_usable.sum()

    The benchmark criterion of data contract 9 -- 300 usable frames over three
    hours, anywhere in the sky -- is the whole surface at once:

    >>> benchmarks = search_observations(start_date='2024-01-01',
    ...                                  min_num_usable=300,
    ...                                  min_duration_minutes=180,
    ...                                  query='moonfrac < 0.25 and airmass < 1.5')

    Args:
        by_name (str|None): If present, this will use the `SkyCoords.from_name` method
            to do a search for the appropriate coords.
        coords (`astropy.coordinates.SkyCoord`|None): A valid coordinate instance.
        ra (float|None): The RA position in degrees of the center of search.
            Requires `dec`; giving one without the other is an error rather
            than a silent all-sky search.
        dec (float|None): The Dec position in degrees of the center of the search.
        radius (float): The search radius in degrees. Searches are done in a
            square box, so this is half the length of the side of the box. Each
            sequence's box is widened by that sequence's own pointing drift, so
            an observation that wandered into the box is found; see
            `add_pointing`. Ignored when no position is given.
        start_date (str|`datetime.datetime`|None): A valid datetime instance or `None` (default).
            If `None` then the beginning of the current year is used as a start date.
        end_date (str|`datetime.datetime`|None): A valid datetime instance or `None` (default).
            If `None` then today is used. Mutually exclusive with `duration`.
        duration (str|`datetime.timedelta`|None): The length of the window,
            instead of naming its far end: ``'90 days'``, ``'6 months'``,
            ``'3 weeks after'``, ``'10 days before and after'``.

            The window is anchored on `start_date` and runs forward from it. With
            no `start_date` it is anchored on now and runs backward, because a
            window in the future holds no observations -- so ``duration='90
            days'`` on its own is the last 90 days. A duration that says which
            way it runs overrides both.

            ``'before and after'`` puts the window on both sides of the anchor,
            which is why this is a duration rather than a parsed end date: one
            datetime cannot express a two-sided window. Months and years are
            calendar spans, so ``'6 months'`` from March 12 ends September 12.
        unit_id (str|list|None): A str or list of strs of unit_ids to include.
            Default `None` will include all.
        min_num_frames (int): Minimum number of frames the observation should
            have, default 1. This counts frames the pipeline has a document for;
            ``num_usable`` counts the ones that were processed successfully,
            which is not the same number -- see `min_num_usable`.
        min_num_usable (int|None): Minimum number of frames the pipeline
            processed cleanly. This is the count a benchmark criterion means:
            one 372-frame sequence in the archive has 310 usable frames and 62
            errors, and only this filter can tell them apart.
        min_duration_minutes (float|None): Minimum wall-clock span from the
            first frame to the last, which is what "3+ hours" asks for. A
            sequence whose span the index could not compute is excluded rather
            than assumed long enough.
        field_name (str|list|None): A str or list of strs of field names to
            include. Default `None` will include all.
        camera_id (str|list|None): A str or list of strs of camera uids to
            include. Default `None` will include all.
        query (str|None): A `pandas.DataFrame.query` string applied last, so it
            can mention every column of the result -- including `exptime` and
            the per-frame header facts `add_frame_facts` attaches, e.g.
            ``'iso == 100 and moonfrac < 0.3 and airmass < 2'``. Named filters
            exist for the cuts with a sequence-level column of their own; this
            is how the rest are reached without one keyword per column.
        source (`pandas.DataFrame`|None): The table to search. If `None`
            (default) the observation index is read. Never modified in place.
        ra_col (str): The column to use for the RA, default 'mount_ra'.
        dec_col (str): The column to use for the Dec, default 'mount_dec'.

    Returns:
        `pandas.DataFrame`: A table with the matching observation results.

    Raises:
        ValueError: if exactly one of `ra` and `dec` is given, if both
            `end_date` and `duration` are given, if `duration` is not a
            duration this understands, or if `query` is not a valid query over
            the result's columns.
    """
    logger.debug("Setting up search params")

    if coords is None:
        if by_name is not None:
            coords = SkyCoord.from_name(by_name)
            print(f"Found coords for {by_name}: {coords}")
        elif (ra is None) != (dec is None):
            # Half a position is a mistake, not a request for everything. An
            # all-sky search is what you get by naming no position at all.
            raise ValueError(
                f"A cone needs both `ra` and `dec`; got ra={ra!r}, dec={dec!r}. Omit "
                f"both to search the whole sky."
            )
        elif ra is not None:
            coords = SkyCoord(ra=ra, dec=dec, unit="degree")

    if duration is not None:
        if end_date is not None:
            # Both would describe the same edge of the window, and nothing says
            # which one wins. Naming the far end and naming the length are two
            # ways to ask the same question, so take one.
            raise ValueError(
                f"`end_date` and `duration` both set the end of the window; got "
                f"end_date={end_date!r}, duration={duration!r}. Give one."
            )
        # No `start_date` means the anchor is now, and the window runs backward:
        # forward from now there is nothing to find.
        anchor = as_utc(start_date) if start_date is not None else as_utc(current_time())
        start_date, end_date = duration_window(
            duration, anchor, "forward" if start_date is not None else "backward"
        )
    else:
        start_date = as_utc(start_date if start_date is not None else f"{dt.today().year}-01-01")
        end_date = as_utc(end_date if end_date is not None else current_time())

    # Never mutate the caller's table: the previous version ran
    # `query(..., inplace=True)` on whatever `source` was handed in.
    obs_df = source.copy() if source is not None else get_all_observations()
    print(f"Searching {len(obs_df)} observations")

    sequence_time = pd.to_datetime(obs_df.sequence_time, format="mixed", utc=True)

    matches = (
        (sequence_time >= start_date)
        & (sequence_time <= end_date)
        & (obs_df.num_frames >= min_num_frames)
    )

    if coords is not None:
        # Widen each sequence's box by its own drift. A drift the index cannot
        # report is treated as zero rather than as infinite: unknown drift should
        # not pull in every sequence in the archive.
        ra_pad = radius + obs_df.get(f"{ra_col}_drift", pd.Series(0.0, index=obs_df.index)).fillna(
            0.0
        )
        dec_pad = radius + obs_df.get(
            f"{dec_col}_drift", pd.Series(0.0, index=obs_df.index)
        ).fillna(0.0)
        matches &= (
            np.abs(wrap_degrees(obs_df[ra_col].astype(float) - coords.ra.deg)) <= ra_pad
        ) & (np.abs(obs_df[dec_col].astype(float) - coords.dec.deg) <= dec_pad)
    else:
        print("No position given; searching the whole sky")

    # A null never satisfies a threshold, so a sequence the index could not
    # measure is excluded rather than assumed to pass. `fillna(False)` below is
    # what makes that true of every comparison here at once.
    if min_num_usable is not None:
        matches &= obs_df.num_usable >= min_num_usable
    if min_duration_minutes is not None:
        matches &= obs_df.duration_minutes >= min_duration_minutes

    obs_df = obs_df[matches.fillna(False)]
    print(f"Found {len(obs_df)} observations after initial filter")

    for column, wanted in (
        ("unit_id", unit_id),
        ("field_name", field_name),
        ("camera_id", camera_id),
    ):
        values = listify(wanted)
        if len(values) > 0:
            obs_df = obs_df[obs_df[column].isin(values)]
            print(f"Found {len(obs_df)} observations after {column} filter")

    obs_df = obs_df.reindex(sorted(obs_df.columns), axis=1)
    obs_df = obs_df.sort_values(by=["sequence_time"])

    # Mean exposure per frame. `total_exptime` is summed over the frame
    # documents rather than read from a column only the index held, which is
    # why it is no longer null for exactly the long sequences anyone wants.
    obs_df["exptime"] = obs_df.total_exptime / obs_df.num_frames

    if query:
        # Last, so it can mention `exptime` and the frame facts. A query naming
        # a column that is not there is the caller's mistake and says so, rather
        # than returning everything or nothing.
        try:
            obs_df = obs_df.query(query)
        except Exception as e:
            raise ValueError(
                f"The query {query!r} could not be applied to the search results: {e}. "
                f"Available columns are {sorted(obs_df.columns)}."
            ) from e
        print(f"Found {len(obs_df)} observations after query")

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
        pd.DataFrame: One row per sequence, with `POINTING_RESULT_COLUMNS` and
            the values of `FRAME_FACT_COLUMNS` -- ``iso``, ``airmass``,
            ``moonfrac``, ``moonsep`` -- added.

    Raises:
        DocumentsUnavailableError: if no index is configured or none is there.
    """
    settings = settings or SurveySettings()
    index_root = index_root if index_root is not None else settings.resolved_index_root

    print(f"Getting list of observations from the index at {index_root}")
    obs_df = documents.read_index(index_root, documents.OBSERVATIONS_FILENAME)

    # Ask only for the columns the index actually has: none of these is in the
    # set the producer guarantees, so an index without them is still valid. One
    # read serves both derivations -- the expensive part is touching
    # `frames.parquet` at all, and half a million rows should be touched once.
    available = documents.index_columns(index_root, documents.FRAMES_FILENAME)
    wanted = [*POINTING_COLUMNS, *FRAME_FACT_COLUMNS]
    frames = documents.read_index(
        index_root,
        documents.FRAMES_FILENAME,
        columns=[name for name in wanted if name in available],
    )

    logger.info(f"Found {len(obs_df)} total observations")
    return add_frame_facts(add_pointing(obs_df, frames), frames)


def get_metadata(observations: pd.DataFrame, errors: str = "raise") -> pd.DataFrame:
    """Read the per-frame documents of many sequences into one table.

    Failing is the default, and a caller who wants only what could be read has
    to say so. Swallowing a per-sequence failure makes a run in which half the
    sequences failed indistinguishable from one that read the whole archive
    (panoptes/panoptes-data#13), and the rest of the package follows the same
    rule: partial data raises, it never silently shortens.

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
