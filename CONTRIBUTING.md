# Contributing

Thanks for helping with `panoptes-data`.

This package **reads**. Its metadata comes from the documents
[`panoptes-pipeline`][pipeline] writes, and its frames come from the raw
archive the units uploaded; nothing here writes into either tree. The
[data contract][contract] is the governing document for field names, the
flattening separator and the status vocabulary, so a question about what a
column means is usually a question about the contract.

[pipeline]: https://github.com/panoptes/panoptes-pipeline
[contract]: https://github.com/panoptes/panoptes-pipeline/blob/main/plans/data-contract.md

## Issue reports

Please check the [issue tracker] first — including closed issues, since the
answer is often there. When reporting a bug, include the version
(`panoptes-data --help` or `pip show panoptes-data`), your operating system and
Python version, and the steps to reproduce it.

Bugs in how data is *produced* belong in the repository that produces it. POCS
writes the FITS headers, `panoptes-utils` is the shared base,
`panoptes-pipeline` processes and produces, and this repository discovers and
queries. Cross-repository references are written fully qualified —
`panoptes/panoptes-pipeline#42` — because a bare `#42` resolves against
whichever repository the reader is looking at.

## Getting set up

Everything goes through [uv]. `requires-python` is `>=3.12`, and `uv` resolves a
matching interpreter on its own, so there is no virtual environment to create
by hand:

```bash
git clone git@github.com:panoptes/panoptes-data.git
cd panoptes-data
uv sync --group dev
```

That gives you the project, the test dependencies and `ruff`. The suite needs no
network and no fixture data:

```bash
uv run pytest -q
```

If a sync dies on a download, raise `UV_HTTP_TIMEOUT` (it defaults to 30
seconds) rather than retrying blindly.

## Making a change

**Branch from `main`** — it is the only long-lived branch — and name the branch
for the work: `type/issue-NNN` plus an optional short description, as in
`fix/issue-18` or `cleanup/issue-19-ruff-config`. The type is the kind of work
(`fix`, `cleanup`, `docs`, `search`, `release`). With no issue to point at,
still say what the work is: `cleanup/ruff-config`, never a generated name.

**Lint and format before you commit.** The ruff rule set is pinned in
`pyproject.toml` — `E`, `F`, `I`, `UP` — so that "clean" means the same thing on
every machine and across ruff releases:

```bash
uv run ruff check .
uv run ruff format .
```

Both run in CI as their own job. A format-only change goes in its own commit,
never mixed with a behavior change: reviewing a real change through a reflow is
how things get missed.

**Update `CHANGELOG.md` in the same branch**, under `## Unreleased`, using
[Keep a Changelog] headings. No entry for changes nobody outside the branch can
observe.

**Add tests.** `tests/conftest.py` builds a real processed tree and a real
parquet index in `tmp_path`; extend those fixtures when the contract moves
rather than stubbing this package's own reader. A test written for a fixed bug
should be run against the unfixed code first, and the commit should say so.

Note that docstring examples are **not** executed — `pytest-doctestplus` is
installed but nothing enables doctest collection — so check any `>>>` blocks you
touch by hand.

**Never hardcode a version.** `hatch-vcs` derives it from the nearest `v*` tag.

American English throughout: code, comments, docstrings, commits and the
changelog.

## Documentation

The site is built with [Zensical] from `zensical.toml` and the Markdown in
`docs/`:

```bash
uv sync --group docs
uv run --group docs zensical serve
```

Every page in `docs/` is either a snippet line including a file from the
repository root or a `:::` block naming a module for `mkdocstrings`. Prose that
belongs to the package belongs in a docstring or in a root Markdown file — see
`docs/building.md`. Improvements to docstrings are as welcome as improvements to
code, and they are the API reference.

## Submitting your contribution

Push your branch and open a pull request against `main`. Say what changed and
why; the diff shows the what, so the why is the part only you have.

CI runs lint, the test suite and a documentation build. Please get them green —
or say what you are stuck on, which is a perfectly good reason to open the pull
request as a draft.

## Maintainer tasks

### Releases

Releases are cut by tagging, and only when explicitly intended:

1. Make sure CI is green on `main` and `CHANGELOG.md` describes the release.
2. Create an **annotated** tag `vX.Y.Z` on `main`. Its message becomes the
   GitHub release notes, so what the tag says and what the release says cannot
   drift apart.
3. Push the tag.

`create-release.yml` then builds the package, publishes it to PyPI through
Trusted Publishing, and creates the GitHub release from the tag message. Pre-1.0
semver applies.

[uv]: https://docs.astral.sh/uv/
[Zensical]: https://zensical.org/
[Keep a Changelog]: https://keepachangelog.com/en/1.1.0/
[issue tracker]: https://github.com/panoptes/panoptes-data/issues
