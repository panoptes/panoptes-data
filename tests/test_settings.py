"""How `SurveySettings` is configured: defaults, the environment, and `.env`."""

from pathlib import Path

import pytest

from panoptes.data.settings import SurveySettings

PANOPTES_KEYS = (
    "PANOPTES_ARCHIVE_ROOT",
    "PANOPTES_PROCESSED_ROOT",
    "PANOPTES_INDEX_ROOT",
    "PANOPTES_IMG_BASE_URL",
    "PANOPTES_IMG_BUCKET",
)


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    """An empty directory to run in, with no PANOPTES_* set by the real environment."""
    for key in PANOPTES_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_defaults_with_no_configuration(clean_env):
    settings = SurveySettings()

    assert settings.archive_root is None
    assert settings.processed_root is None
    assert settings.index_root is None
    assert settings.img_bucket == "panoptes-images-incoming"


def test_the_index_defaults_to_sitting_in_the_processed_tree(clean_env):
    """Where `panoptes.pipeline.index.build` puts it, so one setting usually does."""
    settings = SurveySettings(processed_root="/data/panoptes-processed")

    assert str(settings.resolved_index_root) == "/data/panoptes-processed"


def test_an_index_root_can_sit_apart_from_the_processed_tree(clean_env):
    """The index is regenerable, so a read-only processed tree need not hold it."""
    settings = SurveySettings(
        processed_root="/data/panoptes-processed", index_root="/data/panoptes-index"
    )

    assert str(settings.resolved_index_root) == "/data/panoptes-index"


def test_reads_a_dotenv_in_the_working_directory(clean_env):
    (clean_env / ".env").write_text(
        "PANOPTES_ARCHIVE_ROOT=/data/panoptes-archive\n"
        "PANOPTES_IMG_BUCKET=some-other-bucket\n"
    )

    settings = SurveySettings()

    assert str(settings.archive_root) == "/data/panoptes-archive"
    assert settings.img_bucket == "some-other-bucket"


def test_environment_wins_over_the_dotenv(clean_env, monkeypatch):
    (clean_env / ".env").write_text("PANOPTES_ARCHIVE_ROOT=/from/the/file\n")
    monkeypatch.setenv("PANOPTES_ARCHIVE_ROOT", "/from/the/environment")

    assert str(SurveySettings().archive_root) == "/from/the/environment"


def test_an_argument_wins_over_the_dotenv(clean_env):
    (clean_env / ".env").write_text("PANOPTES_ARCHIVE_ROOT=/from/the/file\n")

    assert str(SurveySettings(archive_root="/from/the/argument").archive_root) == (
        "/from/the/argument"
    )


def test_unrelated_keys_in_a_shared_dotenv_are_ignored(clean_env):
    """A `.env` is shared with other tools; a DATABASE_URL in it must not break us."""
    (clean_env / ".env").write_text(
        "PANOPTES_ARCHIVE_ROOT=/data/panoptes-archive\n"
        "DATABASE_URL=postgres://localhost/something\n"
        "AWS_PROFILE=default\n"
        "SOME_OTHER_TOOL_KEY=abc\n"
    )

    settings = SurveySettings()

    assert str(settings.archive_root) == "/data/panoptes-archive"


def test_the_shipped_example_is_a_valid_dotenv(clean_env):
    """`.env.example` is what people copy, so it has to parse into real settings."""
    example = Path(__file__).parent.parent / ".env.example"
    (clean_env / ".env").write_text(example.read_text())

    settings = SurveySettings()

    assert str(settings.archive_root) == "/data/panoptes-archive"
    assert str(settings.processed_root) == "/data/panoptes-processed"
    # The commented-out lines stay commented: they are the defaults.
    assert settings.index_root is None
    assert settings.img_bucket == "panoptes-images-incoming"
