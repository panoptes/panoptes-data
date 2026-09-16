# Building the documentation

Everything goes through `uv`, as it does everywhere else in this repository.
The toolchain is the `docs` dependency group in `pyproject.toml`, and `uv sync`
installs the project alongside it, so `mkdocstrings` reads the real package
rather than working from stubs.

```bash
uv sync --group docs
uv run --group docs zensical serve          # live reload at localhost:8000
uv run --group docs zensical build --clean # static site into site/
```

CI runs `zensical build --clean --strict`. `--strict` turns warnings into
errors: a link to a page that does not exist, or a `:::` block naming a module
that cannot be imported, fails the build rather than publishing a hole.

The site is configured in `zensical.toml` and published to
[GitHub Pages](https://panoptes.github.io/panoptes-data/) by
`.github/workflows/docs.yml` on every push to `main`.

## How the pages are put together

There are no copies of anything.

- `README.md`, `CHANGELOG.md`, `CONTRIBUTING.md`, `AUTHORS.md` and
  `LICENSE.txt` stay at the repository root, where GitHub and PyPI read them.
  The pages under `docs/` pull them in with a snippet line -- `--8<--
  "README.md"` -- rather than holding a second copy to keep in step.
- The API reference is `mkdocstrings` reading the docstrings:
  `::: panoptes.data.search` in `docs/api/search.md` is the entire page. There
  is no generated intermediate, nothing to run before a build, and nothing to
  gitignore.

So a page in `docs/` is either a snippet line or a `:::` block. Prose that
belongs to the package belongs in a docstring or in a root Markdown file; this
directory is the arrangement of those, not a second place to write them.

## Adding to the API reference

Add the module's page and put it in the `nav`:

```bash
printf '# `panoptes.data.thing`\n\n::: panoptes.data.thing\n' > docs/api/thing.md
```

Then add `{ "thing" = "api/thing.md" }` under `API reference` in
`zensical.toml`.

A `mkdocstrings` warning naming some other package means it is missing from
`[project.dependencies]`. Add it there, not to a documentation-only list.
