from pathlib import Path
from typing import List, Union

import typer

from panoptes.data.observations import ObservationInfo

app = typer.Typer()


@app.command()
def download(sequence_id: Union[str, None] = typer.Option(None, '--sequence-id', '-s',
                                                          help='Sequence ID for the Observation.'),
             output_dir: Path = typer.Option(Path('.'), '--output-dir', '-o',
                                             help='Output directory for images.'),
             ) -> List[str]:
    """Downloads all FITS images for the observation."""
    local_files = list()
    try:
        obs_info = ObservationInfo(sequence_id=sequence_id)
        local_files = obs_info.download_images(output_dir=output_dir)
        typer.secho(f'Downloaded {len(local_files)} images to {output_dir}.')

    except Exception:
        typer.secho(f'Error downloading images for {sequence_id}', fg='red')

    return local_files


@app.command()
def get_metadata(
        sequence_id: Union[str, None] = typer.Option(None, '--sequence-id', '-s',
                                                     help='Sequence ID for the Observation.'),
        output_dir: Path = typer.Option(Path('.'), '--output-dir', '-o',
                                        help='Output directory for metadata file.'),
):
    """Download metadata for the observation as CSV file."""
    output_fn = output_dir / f'{sequence_id}-metadata.csv'
    try:
        obs_info = ObservationInfo(sequence_id=sequence_id)
        obs_info.image_metadata.to_csv(output_fn)
        typer.secho(f'Metadata saved to {output_fn}', fg='green')
    except Exception:
        typer.secho(f'Error downloading metadata for {sequence_id}', fg='red')

    return output_fn


if __name__ == "__main__":
    app()
