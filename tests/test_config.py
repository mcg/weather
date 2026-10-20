"""Exercise public configuration resolution independently of orchestration."""

import argparse
import os
from pathlib import Path

import pytest

from weather.config import DEFAULT_THRESHOLD, Settings, load_settings, parse_threshold


REQUIRED = {
    "rss_file_path": "out/feed.xml",
    "image_file_path": "out/images/",
    "slack_token": "test-token",
    "upload_channel": "test-channel",
    "discord_webhook_url": "https://example.com/webhook",
}


def required_flags(values=None):
    return [
        argument
        for name, value in (REQUIRED if values is None else values).items()
        for argument in ("--" + name.replace("_", "-"), value)
    ]


def test_unused_slack_webhook_is_not_required():
    assert "SLACK_WEBHOOK_URL" not in os.environ

    settings = load_settings(required_flags())

    assert isinstance(settings, Settings)
    assert settings.rss_file_path == Path("out/feed.xml")
    assert settings.image_file_path == Path("out/images")
    assert settings.slack_token == "test-token"
    assert settings.upload_channel == "test-channel"
    assert settings.discord_webhook_url == "https://example.com/webhook"


@pytest.mark.parametrize("missing", REQUIRED)
def test_each_used_configuration_value_is_required(missing, capsys):
    values = {name: value for name, value in REQUIRED.items() if name != missing}

    with pytest.raises(SystemExit) as error:
        load_settings(required_flags(values))

    assert error.value.code == 2
    assert missing in capsys.readouterr().err


@pytest.mark.parametrize("source", ["dotenv", "environment", "cli"])
def test_cli_environment_dotenv_precedence_does_not_mutate_environment(
    source, tmp_path, monkeypatch
):
    dotenv_values = {
        **REQUIRED,
        "rss_file_path": "dotenv/feed.xml",
        "image_file_path": "dotenv/images/",
        "log_file": "dotenv/app.log",
        "threshold": "0.002",
        "pixel_tolerance": "12",
    }
    env_file = tmp_path / "settings.env"
    env_file.write_text(
        "".join(f"{name.upper()}={value}\n" for name, value in dotenv_values.items())
    )
    argv = ["--env-file", str(env_file)]
    expected = dotenv_values
    if source in ("environment", "cli"):
        expected = {
            **dotenv_values,
            "rss_file_path": "env/feed.xml",
            "image_file_path": "env/images/",
            "slack_token": "env-token",
            "upload_channel": "env-channel",
            "discord_webhook_url": "https://example.com/env",
            "log_file": "env/app.log",
            "threshold": "0.005",
            "pixel_tolerance": "8",
        }
        for name, value in expected.items():
            monkeypatch.setenv(name.upper(), value)
    if source == "cli":
        expected = {
            **REQUIRED,
            "log_file": "cli/app.log",
            "threshold": "0.008",
            "pixel_tolerance": "4",
        }
        argv += required_flags(expected)
    before = dict(os.environ)

    settings = load_settings(argv)

    assert settings.rss_file_path == Path(expected["rss_file_path"])
    assert settings.image_file_path == Path(expected["image_file_path"])
    assert settings.slack_token == expected["slack_token"]
    assert settings.upload_channel == expected["upload_channel"]
    assert settings.discord_webhook_url == expected["discord_webhook_url"]
    assert settings.log_file == Path(expected["log_file"])
    assert settings.threshold == float(expected["threshold"])
    assert settings.pixel_tolerance == int(expected["pixel_tolerance"])
    assert dict(os.environ) == before


def test_load_settings_defaults_to_process_arguments(monkeypatch):
    monkeypatch.setattr(
        "sys.argv", ["weather", *required_flags(), "--threshold", "0.5"]
    )

    settings = load_settings()

    assert settings.threshold == 0.5
    assert settings.rss_file_path == Path(REQUIRED["rss_file_path"])


@pytest.mark.parametrize(
    "raw, expected",
    [
        (None, DEFAULT_THRESHOLD),
        ("0", 0.0),
        (0, 0.0),
        ("1", 1.0),
        (1, 1.0),
        ("0.005", 0.005),
        (0.5, 0.5),
    ],
)
def test_parse_threshold_accepts_finite_fraction_boundaries(raw, expected):
    assert parse_threshold(raw, argparse.ArgumentParser()) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "not-a-number",
        "nan",
        "inf",
        "-inf",
        "1e309",
        "-0.001",
        "1.001",
        float("nan"),
        float("inf"),
        float("-inf"),
        -1,
        2,
    ],
)
def test_parse_threshold_rejects_invalid_or_nonfinite_values(raw):
    with pytest.raises(SystemExit) as error:
        parse_threshold(raw, argparse.ArgumentParser())
    assert error.value.code == 2


@pytest.mark.parametrize("source", ["dotenv", "environment", "cli"])
def test_load_settings_rejects_nonfinite_threshold(source, tmp_path, monkeypatch):
    argv = required_flags()
    if source == "dotenv":
        env_file = tmp_path / "invalid.env"
        env_file.write_text("THRESHOLD=nan\n")
        argv += ["--env-file", str(env_file)]
    elif source == "environment":
        monkeypatch.setenv("THRESHOLD", "nan")
    else:
        argv += ["--threshold", "nan"]

    with pytest.raises(SystemExit) as error:
        load_settings(argv)
    assert error.value.code == 2


@pytest.mark.parametrize("kind", ["missing", "directory"])
def test_env_file_must_be_an_existing_file(kind, tmp_path, capsys):
    path = tmp_path / "invalid.env"
    if kind == "directory":
        path.mkdir()
    before = dict(os.environ)

    with pytest.raises(SystemExit) as error:
        load_settings([*required_flags(), "--env-file", str(path)])

    assert error.value.code == 2
    assert str(path) in capsys.readouterr().err
    assert dict(os.environ) == before


def test_incomplete_env_file_reports_missing_configuration(tmp_path, capsys):
    env_file = tmp_path / "incomplete.env"
    env_file.write_text("RSS_FILE_PATH=feed.xml\n")
    before = dict(os.environ)

    with pytest.raises(SystemExit) as error:
        load_settings(["--env-file", str(env_file)])

    assert error.value.code == 2
    assert "Missing required arguments" in capsys.readouterr().err
    assert dict(os.environ) == before
