from pathlib import Path
from typing import List, Union

import pandas as pd
import typer
from astropy.time import Time
from panoptes.utils.time import current_time, flatten_time
from rich import print
from tqdm import tqdm

from panoptes.data.observations import ObservationInfo
from panoptes.data.search import search_observations

app = typer.Typer()


@app.command()
def download(sequence_id: Union[str, None] = typer.Argument(..., help='Sequence ID for the Observation.'),
             output_dir: Path = typer.Option(None,
                                             '--output-dir', '-o',
                                             help='Output directory for images, defaults to sequence_id.'),
             image_query: str = typer.Option('status!="ERROR"',
                                             '--image-query', '-q',
                                             help='Query for images, default \'status!="ERROR"\''),
             ) -> List[str]:
    """Downloads all FITS images for the observation."""
    local_files = list()

    if output_dir is None:
        output_dir = Path(sequence_id)

    print(f'Downloading images for {sequence_id} to {output_dir}.')

    try:
        obs_info = ObservationInfo(sequence_id=sequence_id, image_query=image_query)
        print(f'Found {len(obs_info.image_metadata)} images for {sequence_id}.')
        local_files = obs_info.download_images(output_dir=output_dir)
        print(f'Downloaded {len(local_files)} images to {output_dir}.')

    except Exception as e:
        print(f'[red]Error downloading images for {sequence_id}: {e}')

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
            print(f'[green]Metadata saved to {output_fn}')
        except Exception as e:
            print(f'[red]Error downloading metadata for {sequence_id}: {e}')
    else:
        if unit_id is None:
            print('[red]Must provide a unit_id if not providing a sequence_id.')
            return

        if start_date is None:
            print('[red]Must provide a start_date if not providing a sequence_id.')
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
            for idx, rec in (pbar := tqdm(results_df.iterrows(), total=len(results_df))):
                try:
                    pbar.set_description(f'Getting metadata for {rec["sequence_id"]}')
                    dfs.append(ObservationInfo(meta=rec).image_metadata)
                except Exception as e:
                    pbar.write(f'Error in {idx} {rec["sequence_id"]}')

            pd.concat(dfs).to_csv(output_fn)

        except ValueError as e:
            print(f'Error downloading metadata for {unit_id}: {e}', fg='red')

    print(f'Metadata saved to [green]{output_fn}')

    return output_fn


@app.command()
def search(
        name: str = typer.Option(None, '--name', '-n',
                                 help='Name of object to search for.'),
        unit_id: str = typer.Option(None, '--unit-id', '-u',
                                    help='Unit ID for the Observation.'),
        start_date: str = typer.Option(None, '--start-date', '-s',
                                       help='Start date for downloading metadata in '
                                            'form YYYY-MM-DD'),
        end_date: str = typer.Option(None, '--end-date', '-s',
                                     help='End date for downloading metadata, '
                                          'defaults to now.'),
        ra: float = typer.Option(None, '--ra', '-r',
                                 help='RA in degrees for search.'),
        dec: float = typer.Option(None, '--dec', '-d',
                                  help='Dec in degrees for search.'),
        radius: int = typer.Option(10, '--radius', '-r',
                                   help='Radius in degrees for search.'),
        min_num_images: int = typer.Option(1, '--min-num-images', '-m',
                                           help='Minimum number of images.'),

):
    """Search for observations."""
    results = search_observations(unit_id=unit_id,
                                  start_date=start_date,
                                  end_date=end_date,
                                  by_name=name,
                                  ra=ra,
                                  dec=dec,
                                  min_num_images=min_num_images,
                                  radius=radius)

    if len(results) == 0:
        print('[red]No results found.')
        return

    display_cols = ['field_name', 'unit_id', 'coordinates_mount_ra', 'coordinates_mount_dec', 'num_images', 'exptime',
                    'total_exptime', 'time']
    markdown_table = results.set_index('sequence_id')[display_cols].to_markdown()
    print(markdown_table)


if __name__ == "__main__":
    app()
