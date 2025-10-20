
from pydantic.networks import AnyHttpUrl
from pydantic_settings import BaseSettings


class CloudSettings(BaseSettings):
    img_base_url: AnyHttpUrl = 'https://storage.googleapis.com'
    img_bucket: str = 'panoptes-images-incoming'
    img_metadata_url: AnyHttpUrl = 'https://us-central1-project-panoptes-01.cloudfunctions.net/get-observation-info'
    observations_url: AnyHttpUrl = 'https://storage.googleapis.com/panoptes-assets/observations.csv'
