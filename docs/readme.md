```{include} ../README.md
:relative-docs: docs/
:relative-images:
```

# Building the documentation

Follow these steps to build the HTML documentation locally.

1. Create and activate a virtual environment (recommended):

```bash
python3 -m venv .venv
. .venv/bin/activate
```

2. Upgrade packaging tools and install the docs requirements:

```bash
python -m pip install --upgrade pip setuptools wheel
pip install -r docs/requirements.txt
```

3. For more complete API docs (fewer autodoc import warnings), install the project runtime dependencies in the same environment. This will allow Sphinx to import modules like `astropy` and `typer` during the build:

```bash
# Install the package and its dependencies (editable install is convenient during development)
pip install -e .
# or install specific packages if you prefer:
# pip install astropy typer
```

4. Build the docs and open them locally:

```bash
cd docs
make html
# open the index in your browser
open _build/html/index.html
```

Notes
- If you prefer not to install heavy runtime packages, `docs/conf.py` includes `autodoc_mock_imports` for some common heavy dependencies so the docs can still build; however installing the real packages gives more complete API documentation.
- If you see autodoc import warnings mentioning other missing packages, install those packages into your `.venv` or add them to `autodoc_mock_imports` in `docs/conf.py`.

# CI / Automated docs build

For CI (for example GitHub Actions) it's convenient to use a full docs requirements file that includes runtime packages needed for autodoc. A sample workflow is provided at `.github/workflows/docs.yml`.

Quick CI steps (what the workflow runs):

```bash
# create and activate a venv (CI runners already provide Python environment)
python3 -m venv .venv
. .venv/bin/activate

# install full docs deps
python -m pip install --upgrade pip setuptools wheel
pip install -r docs/requirements.txt

# install the package (editable) so autodoc can import it
pip install -e .

# build
cd docs
make html
```

The CI example workflow will upload the generated `_build/html` directory as an artifact so you can preview the generated site.

(See `.github/workflows/docs.yml` for the exact GitHub Actions configuration.)
