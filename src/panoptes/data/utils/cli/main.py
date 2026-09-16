from pathlib import Path

import pandas as pd
import typer
from astropy.time import Time
from panoptes.utils.time import current_time, flatten_time
from rich import print
from tqdm import tqdm

from panoptes.data.observations import IMAGES_UNAVAILABLE_MESSAGE, ObservationInfo
from panoptes.data.search import find_simultaneous, search_observations

app = typer.Typer(pretty_exceptions_enable=False)


@app.command(deprecated=True)
def download(
    sequence_id: str | None = typer.Argument(..., help="Sequence ID for the Observation."),
    output_dir: Path = typer.Option(
        None, "--output-dir", "-o", help="Output directory for images, defaults to sequence_id."
    ),
    image_query: str = typer.Option(
        'image_status!="ERROR"',
        "--image-query",
        "-q",
        help="Query for images, default 'image_status!=\"ERROR\"'",
    ),
) -> list[str]:
    """Deprecated: archived frames are not currently available for download.

    See `ObservationInfo.download_images` for why. Exits non-zero.
    """
    print(f"[red]Cannot download images for {sequence_id}: {IMAGES_UNAVAILABLE_MESSAGE}")
    raise typer.Exit(code=1)


@app.command()
def get_metadata(
    sequence_id: str | None = typer.Option(
        None, "--sequence-id", "-i", help="Sequence ID for the Observation."
    ),
    unit_id: str | None = typer.Option(
        None,
        "--unit-id",
        "-u",
        help="Unit ID for the Observation. "
        "Use with a date range to download all metadata for a unit.",
    ),
    start_date: str | None = typer.Option(
        None, "--start-date", "-s", help="Start date for downloading metadata in form YYYY-MM-DD"
    ),
    end_date: str | None = typer.Option(
        None, "--end-date", "-e", help="End date for downloading metadata, defaults to now."
    ),
    output_dir: Path = typer.Option(
        Path("."), "--output-dir", "-o", help="Output directory for metadata file."
    ),
):
    """Download metadata.

    If given a single sequence_id, will download the metadata for that observation.
    If given a unit_id and date range, will download all metadata for that unit.
    """

    if sequence_id is not None:
        output_fn = output_dir / f"{sequence_id}-metadata.csv"
        try:
            obs_info = ObservationInfo(sequence_id=sequence_id)
            obs_info.image_metadata.to_csv(output_fn)
            print(f"[green]Metadata saved to {output_fn}")
        except Exception as e:
            print(f"[red]Error downloading metadata for {sequence_id}: {e}")
            raise typer.Exit(code=1) from e
    else:
        if unit_id is None:
            print("[red]Must provide a unit_id if not providing a sequence_id.")
            return

        if start_date is None:
            print("[red]Must provide a start_date if not providing a sequence_id.")
            return
        else:
            start_date = flatten_time(Time(start_date))[:8]

        if end_date is None:
            end_date = current_time(flatten=True)[:8]
        else:
            end_date = flatten_time(Time(end_date))[:8]

        output_fn = output_dir / f"{unit_id}-{start_date}-{end_date}-metadata.csv"
        try:
            # No position: a unit and a date range is the whole query.
            results_df = search_observations(
                unit_id=unit_id.upper(),
                start_date=start_date,
                end_date=end_date,
            )

            dfs = list()
            failed = list()
            for idx, rec in (pbar := tqdm(results_df.iterrows(), total=len(results_df))):
                sequence_id = rec["sequence_sequence_id"]
                try:
                    pbar.set_description(f"Getting metadata for {sequence_id}")
                    dfs.append(ObservationInfo(meta=rec).image_metadata)
                except Exception as e:
                    pbar.write(f"Error in {idx} {sequence_id}: {e!r}")
                    failed.append(sequence_id)

            # A partial export is indistinguishable from a complete one once it
            # is a file on disk, so it is not written at all. Reporting the
            # count and exiting non-zero is the whole point.
            if failed:
                print(
                    f"[red]{len(failed)} of {len(results_df)} sequences could not be "
                    f"read, so no metadata file was written. First failures: "
                    f"{failed[:5]}"
                )
                raise typer.Exit(code=1)

            pd.concat(dfs).to_csv(output_fn)

        except (ValueError, FileNotFoundError) as e:
            # `DocumentsUnavailableError` is a `FileNotFoundError`: an
            # unconfigured or mistyped root is a configuration mistake and
            # deserves its message, not a traceback.
            print(f"[red]Error downloading metadata for {unit_id}: {e}")
            raise typer.Exit(code=1) from e

    print(f"Metadata saved to [green]{output_fn}")

    return output_fn


@app.command()
def search(
    name: str = typer.Option(None, "--name", "-n", help="Name of object to search for."),
    unit_id: str = typer.Option(None, "--unit-id", "-u", help="Unit ID for the Observation."),
    field_name: str = typer.Option(None, "--field", "-f", help="Field name to search for."),
    camera_id: str = typer.Option(None, "--camera-id", "-c", help="Camera uid to search for."),
    start_date: str = typer.Option(
        None, "--start-date", "-s", help="Start date for the search in form YYYY-MM-DD"
    ),
    end_date: str = typer.Option(
        None, "--end-date", "-e", help="End date for the search, defaults to now."
    ),
    duration: str = typer.Option(
        None,
        "--duration",
        "-D",
        help="Window length instead of an end date, e.g. '90 days', "
        "'6 months', '10 days before and after'. Not with --end-date.",
    ),
    ra: float = typer.Option(None, "--ra", help="RA in degrees for search."),
    dec: float = typer.Option(None, "--dec", "-d", help="Dec in degrees for search."),
    radius: float = typer.Option(10, "--radius", "-r", help="Radius in degrees for search."),
    min_num_frames: int = typer.Option(
        1,
        "--min-num-frames",
        "-m",
        help="Minimum number of frames the observation has a document for.",
    ),
    min_num_usable: int = typer.Option(
        None,
        "--min-num-usable",
        "-U",
        help="Minimum number of frames the pipeline processed cleanly.",
    ),
    min_duration_minutes: float = typer.Option(
        None,
        "--min-duration",
        "-L",
        help="Minimum wall-clock span of the observation, in minutes.",
    ),
    query: str = typer.Option(
        None,
        "--query",
        "-q",
        help="A pandas query over the results, e.g. 'iso == 100 and moonfrac < 0.3'.",
    ),
):
    """Search for observations.

    With no position the search is all-sky, so a unit and a date range, or a
    frame count and a duration, are each a complete query on their own.

    A window can be given as --duration instead of --end-date: "90 days" from
    the start date, or "10 days before and after" it. With no --start-date a
    duration runs backward from now.
    """
    try:
        results = search_observations(
            unit_id=unit_id,
            field_name=field_name,
            camera_id=camera_id,
            start_date=start_date,
            end_date=end_date,
            duration=duration,
            by_name=name,
            ra=ra,
            dec=dec,
            min_num_frames=min_num_frames,
            min_num_usable=min_num_usable,
            min_duration_minutes=min_duration_minutes,
            query=query,
            radius=radius,
        )
    except ValueError as e:
        # A bad duration or a half-given position is a usage mistake, and its
        # message already says what to write instead.
        print(f"[red]{e}")
        raise typer.Exit(code=1) from e

    if len(results) == 0:
        print("[red]No results found.")
        return

    display_cols = [
        "field_name",
        "unit_id",
        "mount_ra",
        "mount_dec",
        "num_frames",
        "num_usable",
        "exptime",
        "total_exptime",
        "duration_minutes",
        "sequence_time",
    ]
    markdown_table = results.set_index("sequence_sequence_id")[display_cols].to_markdown()
    print(markdown_table)
    print(f"Found {len(results)} observations.")


@app.command()
def pairs(
    across: str = typer.Option(
        "camera_id",
        "--across",
        "-a",
        help="What must differ between the two: 'camera_id' or 'unit_id'.",
    ),
    unit_id: str = typer.Option(None, "--unit-id", "-u", help="Unit ID to restrict the search to."),
    start_date: str = typer.Option(
        None, "--start-date", "-s", help="Start date for the search in form YYYY-MM-DD"
    ),
    end_date: str = typer.Option(
        None, "--end-date", "-e", help="End date for the search, defaults to now."
    ),
    min_num_frames: int = typer.Option(
        1, "--min-num-frames", "-m", help="Minimum number of frames each observation has."
    ),
    min_overlap_minutes: float = typer.Option(
        0.0, "--min-overlap", "-o", help="Discard pairs overlapping by less than this."
    ),
    any_field: bool = typer.Option(
        False, "--any-field", help="Do not require the two to have pointed at the same field."
    ),
):
    """Find observations of one field recorded at the same time by different hardware.

    A simultaneous pair is the control the photometry rebuild compares against:
    the sky was the same and the camera was not.
    """
    results = search_observations(
        unit_id=unit_id,
        start_date=start_date,
        end_date=end_date,
        min_num_frames=min_num_frames,
    )

    try:
        found = find_simultaneous(
            results,
            across=across,
            min_overlap_minutes=min_overlap_minutes,
            same_field=not any_field,
        )
    except ValueError as e:
        print(f"[red]{e}")
        raise typer.Exit(code=1) from e

    if len(found) == 0:
        print("[red]No simultaneous observations found.")
        return

    # With `--any-field` the two need not share a field, and the shared
    # `field_name` is null for those rows, so name both instead.
    field_cols = ["field_name_a", "field_name_b"] if any_field else ["field_name"]
    display_cols = [
        *field_cols,
        "sequence_sequence_id_a",
        "sequence_sequence_id_b",
        "num_usable_a",
        "num_usable_b",
        "overlap_minutes",
    ]
    print(found[display_cols].to_markdown(index=False))
    print(f"Found {len(found)} simultaneous pairs.")


if __name__ == "__main__":
    app()
