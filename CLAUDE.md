# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this package is

`panoptes-data` **reads**. It does not produce anything. Its metadata comes
from the documents under the processed tree — written by
[`panoptes-pipeline`][pipeline], plus whatever decorations the uploader of the
products added, which is where the optional `*_url` fields in `public_urls`
come from — and every frame it resolves comes from the raw archive the units
uploaded. Nothing here writes into either tree.

That makes the [data contract][contract] the governing document: field names,
the flattening separator, the dropped blocks, the required columns and the
status vocabulary are declared there rather than inferred from whatever
happened to be in a record. Of those, the ones `schema.json` carries — and so
the ones `contract_for` reads back at runtime — are the separator, the dropped
blocks and the required frame columns. The status vocabulary is not in the
manifest; it is mirrored in `settings.py`. Cite the contract's sections by
number in comments and commit messages ("data contract 3.2"), as the sibling
repositories do.

[pipeline]: https://github.com/panoptes/panoptes-pipeline
[contract]: https://github.com/panoptes/panoptes-pipeline/blob/main/plans/data-contract.md

## Commands

Everything goes through `uv`. `requires-python` is `>=3.12`, and `uv` resolves
a matching interpreter on its own — a bare `pip install -e .` against an older
default `python3` fails before it installs anything.

```bash
uv sync --group dev           # project + test group + ruff
uv run pytest -q              # whole suite; needs no network and no fixture data
uv run pytest tests/test_search.py::TestAddPointing -q
uv run pytest -k pointing -q
uv run pytest --cov=panoptes.data --cov-report=term-missing
uv run ruff check .
uv build                      # sdist + wheel into dist/
uv sync --group docs          # project + documentation toolchain
uv run --group docs zensical serve          # docs with live reload
uv run --group docs zensical build --clean # static site into site/
```

If a sync dies on a download, raise `UV_HTTP_TIMEOUT` (default 30s) rather than
retrying blindly.

`hatchling` is the build backend and `hatch-vcs` derives the version from the
git tag, so the `[build-system]`, `[tool.hatch.version]` and
`[tool.hatch.build*]` blocks are load-bearing. There are no `hatch` *envs*:
local development, the lint job and the test job go through `uv` and
`[dependency-groups]`, as they do everywhere else in the fleet. Don't add a
second way to install the test dependencies — that is what the hatch envs were,
a hand-copy of the `test` group that could drift from it.

The docs build goes through `uv` too: `uv sync --locked --group docs`, then
`zensical build --clean --strict`. The `docs` group holds the documentation
toolchain and nothing else — the runtime dependencies `mkdocstrings` reads come
from the project, which `uv sync` installs with the group. There is no
`docs/requirements.txt`; it hand-copied `[project.dependencies]` and had
already drifted from it. A package the docs need belongs in
`[project.dependencies]`, never in a second list.

The site is `zensical.toml` plus `docs/`, and every page there is either a
snippet line including a root file or a `:::` block naming a module. **Don't
write prose into `docs/`** — it belongs in a docstring or in a root Markdown
file, or it becomes the next thing to keep in step by hand. The one exception
is `docs/building.md`, which is about the site itself.

Docs publish to GitHub Pages only, through the Pages deployment API —
`upload-pages-artifact` then `deploy-pages`, with the repository's Pages source
set to **GitHub Actions**. There is no `gh-pages` branch in the loop and the
workflow needs no write access to the repository. Read the Docs is gone: it was
a second build, from a second config, of the same site.

### Linting

**`uv run ruff check .` passes before anything is committed.** The rule set is
pinned in `[tool.ruff.lint]` — `E`, `F`, `I`, `UP`, matching
`panoptes-pipeline` — precisely so that "clean" means the same thing on every
machine and across ruff releases; ruff's own defaults move, and an unpinned
config makes each upgrade look like a regression. `notebooks/` is excluded:
re-running a notebook re-dirties it, and nobody acts on the churn.

**`uv run ruff format .` passes too.** Double quotes, ruff's default, matching
`panoptes-pipeline`. Let the formatter decide: don't hand-wrap a line shorter
than 100 characters, because it will join it back and the diff is noise. What
it cannot do is split a string, so an `E501` inside a docstring or an f-string
is yours — break it across implicit-concatenated pieces or indent a
continuation line.

**A format-only change goes in its own commit**, never mixed with a real one.
Reviewing a behavior change through a reflow is how things get missed.

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

**`documents.py`** — the contract layer, and the only module that reads the
processed tree or the parquet index. (It is not the only filesystem access in
the package: `ObservationInfo.get_image_list` stats frames under the archive
root, and the CLI writes CSVs.) It carries constants (`SEPARATOR`, `DROPPED`,
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
- **There is no cloud *source* and no download path.** The Firestore-derived
  `observations.csv` and the `get-observation-info` function are gone because
  nothing produces them (panoptes/panoptes-data#15), and archived frames 404
  anonymously in every era of the bucket (panoptes/panoptes-data#17) — so
  `download_images` and the `download` CLI command raise rather than returning
  an empty list. Do not reintroduce either. Archive *URLs* are still produced:
  with no `archive_root` configured, `get_image_list` returns them, because
  naming where a frame lives is useful even when nothing will serve it.
- Parquet over CSV is load-bearing: dtypes live in the file, so a serial like
  `032071000633` comes back a string instead of `3.207100e+10` (panoptes/panoptes-data#13).

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
- **Branch names say what the branch is for**: `type/issue-NNN` plus an
  optional short description — `fix/issue-18`, `docs/issue-24-contract-notes`,
  `cleanup/issue-19-ruff-config`. The type is the kind of work (`fix`,
  `cleanup`, `docs`, `search`, `release`); the issue number is what makes the
  branch findable a year later, when the diff is the only thing left explaining
  itself. Name a batch for its parent issue, not for one of its children, and
  branch from `main` — it is the only long-lived branch.

  A generated name carrying neither — the `claude/adjective-surname-hex` an
  agent session is handed by default — is **not** one. Rename it before the
  first push; renaming after the push means force-pushing or reopening the PR.
  With no issue to point at, still say what the work is: `cleanup/ruff-config`
  beats `claude/amazing-newton-fmbd97`.
- **Pull requests open ready for review, not as drafts.** A draft asks a
  reviewer to guess whether it is finished; if the work is not ready, say what
  is missing in the description instead. Agent sessions often default to
  opening drafts -- this repository does not want that.
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
