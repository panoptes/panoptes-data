# Changelog

## 0.4.0 (2026-09-13)

### Added

- `SurveySettings.archive_root`, set from `PANOPTES_ARCHIVE_ROOT`, names a
  local copy of the archive. It points at the directory holding the unit
  folders, so `<root>/PAN012/358d0f/20180824T035917/...` -- the archive keeps
  the bucket's layout, and the bucket name is absorbed by how the root is set
  rather than leaking into local paths. It has no default; without one nothing
  changes.

- `get_image_list` accepts `archive_root` per call, overriding the setting.

- Settings are read from a `.env` file in the working directory as well as from
  the environment, so a machine-specific archive root can sit beside a project
  rather than being exported in every shell. A real environment variable beats
  the file and an argument beats both. `.env.example` documents every setting.

  Keys that are not settings of this package are ignored rather than rejected:
  a `.env` is usually shared with other tools, and a `DATABASE_URL` in it must
  not stop a frame being read. A misspelled `PANOPTES_*` key is therefore
  ignored too.

### Changed

- `get_image_list` returns local `Path` objects when an archive root is
  configured, and archive URLs when it is not. `get_image_data` therefore works
  again: it reads whatever that list holds, and the URLs it used to hold are
  not fetchable by anyone (panoptes/panoptes-data#17).

  A frame the metadata names but the local archive does not hold raises
  `FileNotFoundError` naming the path it looked for. A partial archive that
  silently returned fewer frames would read as an observation with fewer
  frames rather than as an incomplete copy. The extension is matched exactly,
  since the layout on disk is the bucket's layout. A root that is not a
  directory at all raises separately, so a mistyped root is not reported as an
  incomplete copy.

- **Breaking:** `CloudSettings` is now `SurveySettings`. Every field on it
  names a location of survey data, and with a local archive root among them the
  cloud name described the transport of some of them rather than what the class
  is for.

- **Breaking:** settings take a `PANOPTES_` environment prefix, so
  `IMG_BUCKET` is now `PANOPTES_IMG_BUCKET`, and likewise for
  `PANOPTES_IMG_BASE_URL`, `PANOPTES_IMG_METADATA_URL` and
  `PANOPTES_OBSERVATIONS_URL`. A package should not claim bare names like
  `ARCHIVE_ROOT` in a shared environment, and the same names now have to work
  inside a `.env` shared with other tools.

- `python-dotenv` is a declared dependency, for the `.env` support above.

- A malformed `uid` raises a `ValueError` naming the uid and the sequence.
  Frame paths are now built by `ImagePathInfo` from `panoptes-utils`, which
  parses the archive path convention and so validates the uid on the way past,
  replacing a hand-rolled `str(uid).replace("_", "/")` that accepted anything.

### Deprecated

- `ObservationInfo.download_images` and the `panoptes-data download` CLI
  command. Archived frames are not downloadable: every URL the metadata carries
  points into a Google Cloud Storage bucket that no longer serves the object
  anonymously, in both the 2018 and the 2025 layout, and there is no public
  replacement. Both now raise (`ImagesUnavailableError`, and exit code 1
  respectively) with a message saying so, instead of constructing correct URLs
  and failing on every fetch. Under the default `warn_on_error=True` that
  failure used to be swallowed into an empty list, which reads as an
  observation with no images rather than as a broken fetch.

  The message now says to set `PANOPTES_ARCHIVE_ROOT` and read the frames
  locally, which is the only way to read them.

## 0.3.0 (2026-09-13)

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
