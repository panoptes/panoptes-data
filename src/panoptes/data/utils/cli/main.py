from pathlib import Path
from typing import List, Union

import pandas as pd
import typer
from astropy.time import Time
from tqdm import tqdm

from panoptes.data.observations import ObservationInfo
from panoptes.data.search import search_observations
from panoptes.utils.time import current_time, flatten_time

app = typer.Typer()


@app.command()
def download(sequence_id: Union[str, None] = typer.Option(None,
                                                          '--sequence-id', '-s',
                                                          help='Sequence ID for the Observation.'),
             output_dir: Path = typer.Option(Path('.'),
                                             '--output-dir', '-o',
                                             help='Output directory for images.'),
             image_query: str = typer.Option('status!="ERROR"',
                                             '--image-query', '-q',
                                             help='Query for images, default \'status!="ERROR"\''),
             ) -> List[str]:
    """Downloads all FITS images for the observation."""
    local_files = list()
    try:
        obs_info = ObservationInfo(sequence_id=sequence_id, image_query=image_query)
        local_files = obs_info.download_images(output_dir=output_dir)
        typer.secho(f'Downloaded {len(local_files)} images to {output_dir}.')

    except Exception:
        typer.secho(f'Error downloading images for {sequence_id}', fg='red')

    return local_files


@app.command()
def get_metadata(
        sequence_id: Union[str, None] = typer.Option(None, '--sequence-id', '-s',
                                                     help='Sequence ID for the Observation.'),
        unit_id: Union[str, None] = typer.Option(None, '--unit-id', '-u',
                                                 help='Unit ID for the Observation. '
                                                      'Use with a date range to download all '
                                                      'metadata for a unit.'),
        start_date: Union[str, None] = typer.Option(None, '--start-date', '-s',
                                                    help='Start date for downloading metadata in '
                                                         'form YYYY-MM-DD'),
        end_date: Union[str, None] = typer.Option(None, '--end-date', '-s',
                                                  help='End date for downloading metadata, '
                                                       'defaults to now.'),
        output_dir: Path = typer.Option(Path('.'), '--output-dir', '-o',
                                        help='Output directory for metadata file.'),
):
    """Download metadata.

    If given a single sequence_id, will download the metadata for that observation.
    If given a unit_id and date range, will download all metadata for that unit.
    """

    if sequence_id is not None:
        output_fn = output_dir / f'{sequence_id}-metadata.csv'
        try:
            obs_info = ObservationInfo(sequence_id=sequence_id)
            obs_info.image_metadata.to_csv(output_fn)
            typer.secho(f'Metadata saved to {output_fn}', fg='green')
        except Exception:
            typer.secho(f'Error downloading metadata for {sequence_id}', fg='red')
    else:
        if unit_id is None:
            typer.secho('Must provide a unit_id if not providing a sequence_id.', fg='red')
            return

        if start_date is None:
            typer.secho('Must provide a start_date if not providing a sequence_id.', fg='red')
            return
        else:
            start_date = flatten_time(Time(start_date))[:8]

        if end_date is None:
            end_date = current_time(flatten=True)[:8]
        else:
            end_date = flatten_time(Time(end_date))[:8]

        output_fn = output_dir / f'{unit_id}-{start_date}-{end_date}-metadata.csv'
        try:
            results_df = search_observations(unit_id=unit_id.upper(),
                                             start_date=start_date,
                                             by_name='M42',
                                             radius=290)

            dfs = list()
            for idx, rec in tqdm(results_df.iterrows(), total=len(results_df)):
                try:
                    dfs.append(ObservationInfo(meta=rec).image_metadata)
                except Exception as e:
                    tqdm.write(f'Error in {idx} {rec["sequence_id"]}')

            pd.concat(dfs).to_csv(output_fn)

        except ValueError as e:
            typer.secho(f'Error downloading metadata for {unit_id}: {e}', fg='red')

    return output_fn


if __name__ == "__main__":
    app()
