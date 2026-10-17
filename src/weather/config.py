"""Resolve and validate CLI, process-environment, and dotenv configuration."""

from __future__ import annotations

import argparse
import math
import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

DEFAULT_THRESHOLD = 0.001
PIXEL_TOLERANCE = 16


@dataclass(frozen=True, slots=True)
class Settings:
    rss_file_path: Path
    image_file_path: Path
    slack_token: str = field(repr=False)
    upload_channel: str
    discord_webhook_url: str = field(repr=False)
    log_file: Path | None = None
    threshold: float = DEFAULT_THRESHOLD
    pixel_tolerance: int = PIXEL_TOLERANCE


def parse_threshold(
    raw_threshold: float | str | None, parser: argparse.ArgumentParser
) -> float:
    """Parse a finite changed-pixel fraction between zero and one."""
    try:
        threshold = DEFAULT_THRESHOLD if raw_threshold is None else float(raw_threshold)
    except TypeError, ValueError:
        parser.error(
            f"Invalid THRESHOLD value {raw_threshold!r}. Must be a float from 0 to 1."
        )
    if not math.isfinite(threshold) or not 0 <= threshold <= 1:
        parser.error(
            f"Invalid THRESHOLD value {raw_threshold!r}. Must be a finite float from 0 to 1."
        )
    return threshold


def parse_pixel_tolerance(
    raw_tolerance: int | str | None, parser: argparse.ArgumentParser
) -> int:
    """Parse the per-pixel gray-level tolerance."""
    try:
        tolerance = PIXEL_TOLERANCE if raw_tolerance is None else int(raw_tolerance)
    except TypeError, ValueError:
        parser.error(
            f"Invalid PIXEL_TOLERANCE value {raw_tolerance!r}. Must be an integer from 0 to 255."
        )
    if not 0 <= tolerance <= 255:
        parser.error(
            f"Invalid PIXEL_TOLERANCE value {raw_tolerance!r}. Must be an integer from 0 to 255."
        )
    return tolerance


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch and publish NOAA weather images."
    )
    _ = parser.add_argument(
        "--env-file", type=Path, help="Path to a dotenv configuration file."
    )
    fields = {
        "rss_file_path": "Path to save the RSS feed.",
        "image_file_path": "Directory for image history.",
        "slack_webhook_url": "Deprecated, unused Slack webhook placeholder (legacy positional slot).",
        "slack_token": "Slack API token.",
        "upload_channel": "Slack channel ID.",
        "discord_webhook_url": "Discord webhook URL.",
    }
    for name, help_text in fields.items():
        _ = parser.add_argument(name, nargs="?", help=help_text)
        if name != "slack_webhook_url":
            _ = parser.add_argument(
                "--" + name.replace("_", "-"), dest=name + "_option", help=help_text
            )
    _ = parser.add_argument("--log-file", help="Optional log file.")
    _ = parser.add_argument(
        "--threshold", help=f"Changed-pixel fraction (default: {DEFAULT_THRESHOLD})."
    )
    _ = parser.add_argument(
        "--pixel-tolerance",
        help=f"Ignore gray-level differences up to this value (default: {PIXEL_TOLERANCE}).",
    )
    return parser


def load_settings(argv: Sequence[str] | None = None) -> Settings:
    """CLI wins over process environment, then the explicitly selected dotenv file."""
    parser = build_parser()
    args: dict[str, object] = vars(parser.parse_args(argv))
    env_file = args.get("env_file")
    if env_file is not None and not isinstance(env_file, Path):
        parser.error("Environment file must be a path.")
    if env_file is not None and not env_file.is_file():
        parser.error(f"Environment file does not exist: {env_file}")
    environment = dict(dotenv_values(env_file)) if env_file is not None else {}
    environment.update(os.environ)

    def resolve(name: str) -> str | None:
        value = args.get(name + "_option")
        if value is None:
            value = args.get(name)
        return value if isinstance(value, str) else environment.get(name.upper())

    names = (
        "rss_file_path",
        "image_file_path",
        "slack_token",
        "upload_channel",
        "discord_webhook_url",
    )
    required = {name: resolve(name) for name in names}
    missing = [name for name, value in required.items() if not value]
    if missing:
        parser.error(
            f"Missing required arguments: {', '.join(missing)}. Provide CLI arguments or environment configuration."
        )
    # After validation every required value is a nonempty string.
    values = {name: value for name, value in required.items() if value is not None}
    log_file = resolve("log_file")
    return Settings(
        rss_file_path=Path(values["rss_file_path"]),
        image_file_path=Path(values["image_file_path"]),
        slack_token=values["slack_token"],
        upload_channel=values["upload_channel"],
        discord_webhook_url=values["discord_webhook_url"],
        log_file=Path(log_file) if log_file else None,
        threshold=parse_threshold(resolve("threshold"), parser),
        pixel_tolerance=parse_pixel_tolerance(resolve("pixel_tolerance"), parser),
    )
