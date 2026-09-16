# Changelog

## Unreleased

### Changed

- Metadata is read from the documents `panoptes-pipeline` writes -- an
  `observation.json` per sequence, a `metadata.json` per frame, and the parquet
  index built by walking them -- rather than from a Firestore-derived
  `observations.csv` and a cloud function. Both were downstream of a pipeline
  that stopped producing them, so both had been describing an archive nothing
  was adding to. See [#15][issue-15] and the pipeline's [data contract][contract].

  This is the durable fix for a recurring class of bug rather than one more
  patch in it. [#12][issue-12] happened because nothing ever *named* the field
  holding an image URL, so the reader guessed and the guess went stale in 2025.
  [#13][issue-13] happened because nothing named the type of a camera serial, so
  `pd.read_csv` inferred float64 and turned `032071000633` into `3.207100e+10`.
  Under the contract the fields are named, the reader reads those names, and a
  change on the producing side is a visible change to a document instead of an
  `AttributeError` two years later.

  There is no fallback to the old path. It is not that the cloud sources are
  deprecated; it is that nothing produces them.

- Columns are the contract's flattened document names -- `image_uid`,
  `image_status`, `image_camera_exptime`, `sequence_coordinates_mount_ra`,
  `num_frames`, `num_usable`, `duration_minutes`, `sequence_sequence_id` --
  which are the names the index carries for the same values. They are joined
  with `_` and never `.`: a dotted name is a *view* over a nested map rather
  than storage, and the dotted `camera.serial_number` in the old summary was
  exactly that confusion.

  This renames every column a caller touches. `uid` is `image_uid`,
  `num_images` is `num_frames`, `coordinates.mount_ra` is `mount_ra`, `time` is
  `sequence_time`.

- `search_observations` reads `observations.parquet`, so it can express what the
  summary could not. `num_usable` counts frames the pipeline processed
  successfully, as distinct from frames that exist; `duration_minutes` comes
  from the frames' own timestamps; and `total_exptime` is a sum over per-frame
  records rather than a number the index alone held, which is why it is no
  longer null for exactly the long sequences anyone wants.

  Its `min_num_images` argument is now `min_num_frames`, matching the column,
  and the `status` argument is gone: the observation index records no
  observation status, and `num_usable` is the better question anyway.

- A search cone is widened, per sequence, by that sequence's own pointing drift.
  A sequence has no single position -- `RA-MNT` and `DEC-MNT` are per-frame
  readings, and older observations wander substantially over a night -- so
  `get_all_observations` attaches the mean of a sequence's frame pointings along
  with the largest deviation from it, and the search adds that deviation to the
  radius. An observation whose mean sits outside the cone but which spent half
  the night inside it is now found.

  RA is averaged as an angle rather than as a number, and compared as one, so a
  target near 0h works. The previous version compared raw degrees, which made
  any cone spanning 0h return nothing at all.

- `search_observations` no longer mutates a caller-supplied `source` DataFrame.
  It ran `query(..., inplace=True)` on whatever it was handed.

- Two hacks that patched the CSV are gone with it: the rename of a
  `camera_camera_id` column that never existed under that name, and the rewrite
  of any `field_name` ending in `00:00:42+00:00` to `M42`. Both were repairs to
  a generated summary, and a reader silently rewriting a field is the thing this
  change exists to stop.

### Added

- `PANOPTES_PROCESSED_ROOT` names the pipeline's document tree, and
  `PANOPTES_INDEX_ROOT` names where the parquet index lives -- defaulting to the
  processed tree, which is where the pipeline builds it. It is a separate
  setting because the index is regenerable, so nothing is lost by keeping it
  beside a read-only or mirrored processed tree rather than in it.

  This is a third root, and it is deliberately not `PANOPTES_ARCHIVE_ROOT`. That
  one still points at the **raw** frames, which are genuinely upstream of the
  pipeline: outputs should be regenerable without touching the inputs.

- `panoptes.data.documents` reads the tree and the index: `read_observation`,
  `read_frames`, `read_index`, and the `flatten` that turns a nested document
  into the index's column names. It also reads `schema.json` back, so the
  separator and the schema version come from what the producer declared rather
  than from what this package assumed. An index built by a newer pipeline is
  refused with a message saying so, rather than read against the wrong
  vocabulary.

- `ObservationInfo` exposes the sequence's `observation.json` as `.observation`,
  with `.status` and `.params_fingerprint` on top of it. The fingerprint is the
  pipeline's cache key: two observations with different fingerprints were not
  made by the same code with the same parameters.

- Building `ObservationInfo` from a sequence id alone now has metadata. `.meta`
  used to be an empty dict unless a search result was passed in, which made
  "construct from an id" the second-class way of using the class; the
  observation document is the metadata, so now it is not.

- `ObservationInfo` takes a `processed_root` argument, overriding the setting
  for one instance, as `get_image_list` already took `archive_root`.

- `observations.USABLE_QUERY` is the `image_query` that keeps only the frames
  the pipeline processed cleanly, so the predicate has a name rather than being
  a string literal at each call site. It is equality on `MATCHED`, which is
  exactly how the index computes `num_usable`, and a test asserts the two agree
  -- `ImageStatus` is ordered and `EXTRACTED` sorts above `MATCHED`, so this is
  the one place to change if either definition moves.

  This is the per-frame filter the removed observation-level `status` argument
  was reaching for and was not: an observation marked `MATCHED` could still
  carry `ERROR` frames, so filtering on the observation returned all of them.

  It is the **default** for `image_query`, since anything that reads pixels
  wants the frames that processed cleanly. `image_query=''` gives every frame.
  A default that hides frames has to say so, so `ObservationInfo.num_frames`
  reports what the sequence holds against what the query kept, and the repr
  shows both when they differ -- a filtered observation is not mistakable for a
  smaller one.

### Fixed

- `read_frames` applies the contract's dropped blocks and reindexes to its
  required columns, so reading documents directly and reading `frames.parquet`
  produce the same column names. Without the first, `image.params` -- a whole
  settings dump per frame -- became columns the index does not have; without
  the second, a field absent from every document of an older sequence produced
  no column at all where the index holds a null one. Both are the same defect
  as the one this release exists to fix, one level down: two readers of the
  same values disagreeing about what they are called.

- A frame the observation document counts but whose `metadata.json` is missing
  raises. A glob sees only the files that exist, so nine documents under an
  observation claiming ten read as a smaller observation rather than an
  incomplete one.

- The `get-metadata` CLI no longer writes a partial export. Per-sequence
  failures were logged and the successful subset written anyway, which is a
  file nothing downstream can distinguish from a complete one; it now reports
  the count and exits non-zero without writing. It also catches
  `DocumentsUnavailableError`, so an unconfigured root prints its message
  instead of a traceback.

- A frame document that cannot be read raises rather than being skipped. The
  pipeline's index walk skips them, correctly -- one bad file must not cost an
  index over half a million frames -- but the unit of work here is a single
  observation, and a frame silently missing from one reads as an observation
  with fewer frames. That is the same failure a partial local archive already
  refused to paper over.

### Removed

- `SurveySettings.img_metadata_url` and `SurveySettings.observations_url`, and
  with them the last two things this package fetched over the network. Nothing
  serves either one with current data.

[issue-12]: https://github.com/panoptes/panoptes-data/issues/12
[issue-13]: https://github.com/panoptes/panoptes-data/issues/13
[issue-15]: https://github.com/panoptes/panoptes-data/issues/15
[contract]: https://github.com/panoptes/panoptes-pipeline/blob/main/plans/data-contract.md

### Release tooling

- Releases are published to PyPI with Trusted Publishing rather than a
  long-lived API token, so they now carry build attestations that anyone can
  verify. The publish action produces those by default, but an explicit
  password silently disabled both it and them.

- The release workflow creates the GitHub release as well, using the annotated
  tag message as the notes, so the tag and the release cannot say different
  things. It had only ever published to PyPI, despite the name.

- The tag pattern that triggers a release accepts a multi-digit major version.
  `v[0-9].` would have stopped matching at `v10.0.0`, releasing nothing and
  saying nothing.

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
