import re
from typing import Pattern

from pydantic_settings import BaseSettings
from pydantic.networks import AnyHttpUrl


class CloudSettings(BaseSettings):
    img_base_url: AnyHttpUrl = 'https://storage.googleapis.com'
    img_metadata_url: AnyHttpUrl = 'https://us-central1-project-panoptes-01.cloudfunctions.net/get-observation-info'
    observations_url: AnyHttpUrl = 'https://storage.googleapis.com/panoptes-assets/observations.csv'


# This is a regular expression that will match the default file layout for images taken
# with a PANOPTES unit, including the optional "field name".
PATH_MATCHER: Pattern[str] = re.compile(r"""^
                                (?P<pre_info>.*)?                       # Anything before unit_id
                                (?P<unit_id>PAN\d{3})                   # unit_id   - PAN + 3 digits
                                /?(?P<field_name>.*)?                   # Legacy field name - any                                
                                /(?P<camera_id>[a-gA-G0-9]{6})          # camera_id - 6 digits
                                /(?P<sequence_time>[0-9]{8}T[0-9]{6})   # Observation start time
                                /(?P<image_time>[0-9]{8}T[0-9]{6})      # Image start time
                                (?P<post_info>.*)?                      # Anything after (file ext)
                                $""",
                                        re.VERBOSE)
