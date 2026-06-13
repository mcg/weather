# AGENTS.md

This file provides project-specific instructions for the Zed agent.

## Python workflow

For this repository, always use `uv` when interacting with the Python project.

1. Install or sync dependencies with `uv sync`.
2. Run commands and scripts with `uv run ...` (for example, `uv run pytest`).
3. Add or update dependencies with `uv add ...` / `uv remove ...`.
4. Do not use `pip`, `pip3`, `python -m pip`, or manually managed virtualenv workflows unless explicitly requested.
