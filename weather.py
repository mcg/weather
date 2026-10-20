"""Deprecated launcher for existing ``uv run weather.py`` scheduled jobs."""

from pathlib import Path

# The legacy filename shadows the installed package from the repository root.
# Expose its source package here so both old jobs and ``python -m weather`` work
# without changing sys.path or duplicating application code.
__path__ = [str(Path(__file__).resolve().parent / "src" / "weather")]

if __name__ == "__main__":
    from weather.cli import main

    main()
