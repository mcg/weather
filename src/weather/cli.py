"""Command-line entry point; application logic lives in application.py."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path

from .application import run
from .config import load_settings


def setup_logging(log_file_path: str | None = None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file_path:
        Path(log_file_path).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file_path))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=handlers,
    )


def main(argv: Sequence[str] | None = None) -> None:
    settings = load_settings(argv)
    setup_logging(str(settings.log_file) if settings.log_file else None)
    run(settings)
