# Changelog

## Unreleased

### Fixed

- `ObservationInfo` no longer requires a URL column in the image metadata, so
  it works on every sequence again. It had read `public_url`, which records
  written from 2025 onward do not carry, and raised an `AttributeError` on all
  of them. Image URLs are now derived from the `uid`, which is present in every
  era and is the archive path with underscores for separators.
- A missing required metadata column now raises a `ValueError` naming the
  sequence and listing the columns that did arrive, instead of an
  `AttributeError` from pandas.
- The `download` and `get-metadata` CLI commands exit non-zero when they fail,
  instead of catching every exception and printing it in red.

### Added

- `ObservationInfo.public_urls`, the `*_url` columns of the metadata, for
  display and lookup. Which ones exist depends on when the record was written;
  nothing in the package requires any of them.

## 0.2.3 (2025-10-20)

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
