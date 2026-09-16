```{include} ../README.md
:relative-docs: docs/
:relative-images:
```

# Building the documentation

Everything goes through `uv`, as it does everywhere else in this repository.
The Sphinx toolchain is the `docs` dependency group in `pyproject.toml`, and
`uv sync` installs the project alongside it, so autodoc imports the real
`astropy`, `pandas` and `typer` rather than working from stubs.

```bash
uv sync --group docs
cd docs
uv run make html
open _build/html/index.html
```

Or, without the `Makefile`:

```bash
uv run --group docs sphinx-build -b html docs docs/_build/html
```

There is no `docs/requirements.txt`. There used to be, and it hand-copied the
runtime dependencies next to the Sphinx packages, so `[project.dependencies]`
and the docs build could disagree -- and did, carrying a `photutils` that
nothing in this package imports. The dependencies are declared once now.

Notes

- `docs/conf.py` sets `autodoc_mock_imports` for a few heavy dependencies, so
  the build survives an environment missing them. Installing the real packages
  -- which `uv sync --group docs` does -- gives more complete API docs.
- An autodoc import warning naming some other package means it is missing from
  `[project.dependencies]`. Add it there, not to a requirements file.

# Automated builds

Both automated builds install from `pyproject.toml` and the lockfile, so they
build what you build:

- `.github/workflows/docs.yml` runs `uv sync --locked --group docs` and
  `uv run make html`, uploads `_build/html` as an artifact, and publishes to
  GitHub Pages on a push to `main`.
- `.readthedocs.yaml` runs the same sync and `sphinx-build`.

`--locked` fails rather than quietly re-resolving, so a dependency edit that
skipped `uv lock` is caught in CI instead of producing docs built against
something other than what the lockfile describes.
