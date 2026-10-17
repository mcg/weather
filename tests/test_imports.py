"""Import the installed package in a fresh process, not pytest's module cache."""

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


@pytest.mark.parametrize("entry_point", ["console", "module", "legacy"])
def test_entry_points_work_from_repository_root(entry_point):
    root = Path(__file__).resolve().parents[1]
    if entry_point == "console":
        command = [str(Path(sys.executable).with_name("weather")), "--help"]
    elif entry_point == "module":
        command = [sys.executable, "-B", "-m", "weather", "--help"]
    else:
        command = [sys.executable, "-B", str(root / "weather.py"), "--help"]
    result = subprocess.run(
        command, cwd=root, capture_output=True, text=True, timeout=30, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "--env-file" in result.stdout
    assert result.stderr == ""


def test_imports_have_no_network_cache_logging_or_filesystem_side_effects(tmp_path):
    script = textwrap.dedent("""
        import importlib
        import logging
        from pathlib import Path
        from unittest.mock import patch

        import requests
        import requests_cache
        import dotenv

        root = logging.getLogger()
        before = (list(root.handlers), root.level)
        forbidden = AssertionError('Import must not perform application I/O')
        with (
            patch.object(requests.sessions.Session, 'request', side_effect=forbidden),
            patch.object(requests_cache, 'install_cache', side_effect=forbidden),
            patch.object(requests_cache, 'CachedSession', side_effect=forbidden),
            patch.object(logging, 'basicConfig', side_effect=forbidden),
            patch.object(logging, 'FileHandler', side_effect=forbidden),
            patch.object(dotenv, 'load_dotenv', side_effect=forbidden),
            patch.object(dotenv, 'dotenv_values', side_effect=forbidden),
        ):
            for name in (
                'weather', 'weather.models', 'weather.config', 'weather.feeds',
                'weather.images', 'weather.publishing', 'weather.application',
                'weather.cli', 'weather.__main__',
            ):
                importlib.import_module(name)
        assert (list(root.handlers), root.level) == before
        assert list(Path.cwd().iterdir()) == []
    """)

    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", script],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == ""
    assert result.stderr == ""
    assert list(tmp_path.iterdir()) == []
