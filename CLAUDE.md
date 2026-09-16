# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this package is

`panoptes-data` **reads**. It does not produce anything. Every piece of metadata
it serves comes from documents [`panoptes-pipeline`][pipeline] wrote, and every
frame it resolves comes from the raw archive the units uploaded. Nothing here
writes into either tree.

That makes the [data contract][contract] the governing document. Field names,
the flattening separator, the dropped blocks, the required columns and the
status vocabulary are all declared there and read back from `schema.json`;
they are not inferred from whatever happened to be in a record. Cite its
sections by number in comments and commit messages ("data contract 3.2"), as
the sibling repositories do.

[pipeline]: https://github.com/panoptes/panoptes-pipeline
[contract]: https://github.com/panoptes/panoptes-pipeline/blob/main/plans/data-contract.md

## Commands

Everything goes through `uv`. It picks a 3.12 interpreter on its own; the
system `python3` here is 3.11, so a bare `pip install -e .` fails on
`requires-python`.

```bash
uv sync --group test          # project + pytest, coverage, doctestplus
uv run pytest -q              # 113 tests, ~6s, no network or fixture data needed
uv run pytest tests/test_search.py::TestAddPointing -q
uv run pytest -k pointing -q
uv run pytest --cov=panoptes.data --cov-report=term-missing
```

If a sync dies on a download, raise `UV_HTTP_TIMEOUT` (default 30s) rather than
retrying blindly.

`pyproject.toml` also declares `hatch` envs and scripts. They work, but `uv` is
the path everything else in the fleet uses; prefer it.

### Linting, and why it is not clean

```bash
uv run --with 'ruff>=0.5.0' ruff check src/panoptes/data/<file you touched>.py
```

`ruff check .` currently reports ~20 findings across `src/`, `docs/conf.py`
and `notebooks/` — unsorted imports, `BLE001`, `C408`, a `UP036` version block
in `__init__.py`. Unlike `panoptes-pipeline`, this repo is **not** clean, so an
error you see is probably not one you introduced. Lint the files you touched
and leave the rest; a repo-wide cleanup is its own PR.

**Do not run `ruff format .`** casually. `pyproject.toml` sets no
`quote-style`, the codebase is single-quoted throughout, and the formatter's
default would rewrite 13 files. That is a decision, not a chore.

## Architecture

Four modules, layered, each depending only on the one below it.

**`settings.py`** — `SurveySettings` (pydantic-settings, `PANOPTES_` prefix,
reads `.env` from the *working directory*) names three separate locations, and
conflating them is the most common mistake here:

| Setting | Tree |
| --- | --- |
| `processed_root` | The pipeline's documents. **All metadata.** No default. |
| `index_root` | The parquet index. Defaults to `processed_root`, where the pipeline builds it. |
| `archive_root` | A local copy of the **raw** FITS archive. Where frames are read from. No default. |

`ImageStatus` / `ObservationStatus` are kept here because a reader comparing
`image_status` against them should not have to install the producer — the
pipeline owns the definitions (contract 5.2).

**`documents.py`** — the contract layer, and the only module that touches the
filesystem or parquet. It carries constants (`SEPARATOR`, `DROPPED`,
`REQUIRED_FRAME_COLUMNS`, `SCHEMA_VERSION`) that **deliberately duplicate**
`panoptes.pipeline.index` rather than importing it. This package does not
depend on the producer; it depends on the contract both follow. Do not
"fix" that by adding a dependency.

`flatten()` joins nested document keys with `_` — `{"image": {"camera":
{"exptime": ...}}}` becomes `image_camera_exptime`, which is exactly what
`frames.parquet` calls the same value. Never `.`: a dotted name is a *view*
over a nested map, never storage, and the dotted `camera.serial_number` in the
retired CSV is the confusion being avoided (contract 3.2). `contract_for()`
reads the producer's declared separator and dropped blocks out of
`schema.json`, so a change there surfaces here instead of silently renaming
every column. A `schema.json` declaring a version other than `SCHEMA_VERSION`
is refused, not read hopefully.

**`observations.py`** — `ObservationInfo`, one sequence. Reads
`observation.json` plus every `metadata.json`, and resolves frames to disk.
Frame paths are derived from `image_uid` via `ImagePathInfo` (contract 3.1),
not from any URL field.

**`search.py`** — the whole-archive query surface, over
`observations.parquet` + `frames.parquet`.

### Invariants worth knowing before you change anything

- **Partial data raises; it never silently shortens.** `read_frames` refuses a
  sequence whose observation document counts more frames than the tree holds,
  and refuses an unreadable document; `get_image_list` refuses an incomplete
  local archive, naming the path. The pipeline's own index walk *skips* bad
  files — one bad file must not cost an index over half a million frames — but
  here the unit of work is one observation, and a short table reads as a
  smaller observation rather than an incomplete one. Keep both behaviors as
  they are.
- **`USABLE_QUERY` is `image_status == "MATCHED"` because that is exactly how
  the index computes `num_usable`.** Equality, not `>= MATCHED`: `ImageStatus`
  is an `IntEnum` whose order is load-bearing in the pipeline, and
  `EXTRACTING`/`EXTRACTED` sort *above* `MATCHED`.
  `test_usable_query_agrees_with_num_usable` is the tripwire if that ever
  diverges.
- **Filtered frames are reported, never merely absent.** `num_frames` is what
  the sequence holds, `len(image_metadata)` is what the query kept, and
  `__str__` shows both when they differ.
- **`mount_ra`, `mount_dec` and the two `*_drift` columns are derived in
  `add_pointing`, not contract columns.** `observations.parquet` carries no
  coordinates: `RA-MNT`/`DEC-MNT` are per-frame readings, so a sequence has no
  single position. Each sequence gets its frames' mean pointing *and* its
  largest deviation from that mean, and `search_observations` widens the cone
  per sequence by that sequence's own drift. Unknown drift is treated as zero,
  never infinite.
- **RA is angular everywhere.** Averaged via the mean unit vector, compared
  through `wrap_degrees`. Comparing raw degrees made every cone spanning 0h
  return nothing.
- **There is no cloud path and no fallback.** The Firestore-derived
  `observations.csv` and the `get-observation-info` function are gone because
  nothing produces them (#15), and archived frames 404 anonymously in every
  era of the bucket (#17) — so `download_images` and the `download` CLI command
  raise rather than returning an empty list. Do not reintroduce either.
- Parquet over CSV is load-bearing: dtypes live in the file, so a serial like
  `032071000633` comes back a string instead of `3.207100e+10` (#13).

## Tests

`tests/conftest.py` builds a real processed tree and a real parquet index in
`tmp_path` — documents nested as `extract_metadata` returns them, files where
`products.write_frame` puts them, `build_index` re-implementing the producer's
grouping. It deliberately does **not** import `panoptes-pipeline`: a test that
imported the writer to check the reader would agree with itself no matter what
either did. Extend the fixtures when the contract moves; don't stub this
package's own reader.

Note that docstring examples are **not** executed — `pytest-doctestplus` is
installed but nothing enables doctest collection, so the `>>>` blocks in
`ObservationInfo` and `search_observations` can drift. Check them by hand if
you touch them.

A test written for a fixed bug should be run against the unfixed code first
(`git stash push <file>`, run, restore), and the commit should say so.

## Conventions

- **American English** in code, comments, docstrings, commits and the
  changelog.
- **`CHANGELOG.md` is updated in the branch that makes the change**, under
  `## Unreleased`, with [Keep a Changelog] headings. No entry for changes
  nobody outside the branch can observe.
- Pre-1.0 semver, `hatch-vcs` derives the version from the nearest `v*` tag, so
  **never hardcode a version**. Tagging pushes to PyPI via Trusted Publishing
  and cuts the GitHub release from the annotated tag's message — so only ever
  tag when explicitly asked to cut a release.
- **Reference issues across repositories fully qualified**: `panoptes/POCS#1410`,
  `panoptes/panoptes-pipeline#42`. A bare `#42` resolves against whichever repo
  the reader is looking at. This holds in chat as much as in commits.
- Work spanning the fleet is filed where the code lives — POCS writes the FITS
  headers, `panoptes-utils` is the shared base, `panoptes-pipeline` processes
  and produces, this repo discovers and queries (contract 8). The "Photometry
  rebuild" project board is the single view across all four.

[Keep a Changelog]: https://keepachangelog.com/en/1.1.0/
