# Changelog

## UNRELEASED

- Modernize the repo to use `pyproject.toml`.
- Update docs.
- Add basic tests.
- Releases are created directly by GitHub Actions.

## Version 0.2.0 (2024-10-08)

- Print length of observations.
- Only apply status filter if given.

## Version 0.1.9 (2024-10-07)

- Do better path matching for image url for new scheme.

## Version 0.1.8 (2024-10-07)

- Merge branch 'main' of github.com:panoptes/panoptes-data

## Version 0.1.7 (2024-04-18)

- Fix image_list for latest processing.

## Version 0.1.6 (2024-04-18)

- Change default bucket for downloading images and also make it a parameter.

## Version 0.1.5 (2024-02-02)

- Fixing release for pypi api tokens.

## Version 0.1.4 (2024-02-02)

- Add GitHub Actions PyPI release workflow.

## Version 0.1.3

- Cleanup of images and observations.

## Version 0.1.2

- Notebook updates.

## Version 0.1.1

- Notebook and usability improvements.

## Version 0.1.0

- Minor bump to correspond with new GCP project and working library..

## Version 0.0.9

- Fixing the library to work with new GCP project.

## Version 0.0.8

- Import error fix.

## Version 0.0.7

- Update to pydantic v2 including pydantic-settings.
- CLI improvements.

## Version 0.0.6

- Added ability to download metadata for a unit for a range of data via cli.
- Fix ``search_observations`` so it ignores ``status`` column by default.

## Version 0.0.5

- Allow for query of image metadata before download.
- Handle image download exceptions better.

## Version 0.0.4

- Added basic cli interface for download images and metadata for observations.
- Fixed install dependencies.

## Version 0.0.3 (2022-06-24)

- More cleanup of dependencies for release.

## Version 0.0.2

- Observation search available via ``panoptes.data.search.search_observations``.
- ``ObservationInfo`` for working with observation data and metadata.

## Version 0.0.1 (2022-06-23)

- Fixing setup.
