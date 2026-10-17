# Weather

Fetch NOAA weather feeds and images, publish updates to Slack and Discord, and
write an RSS feed. Requires Python 3.15 and [uv](https://docs.astral.sh/uv/).
Only Python 3.15 is supported.

## Setup and run

On Debian/Ubuntu, install the native prerequisites before syncing. `lxml` may
need to build from source when a Python 3.15 wheel is unavailable:

```sh
sudo apt-get update
sudo apt-get install --yes --no-install-recommends build-essential libxml2-dev libxslt1-dev
```

```sh
uv sync
cp .env.example .env
```

Fill in `.env` with your paths and credentials, then run the installed command:

```sh
uv run weather --env-file .env
```

The package module and legacy root launcher are equivalent entry points:

```sh
uv run python -m weather --env-file .env
uv run weather.py --env-file .env
```

For the full option list, use `uv run weather --help`.

The legacy positional form is still accepted:

```sh
uv run weather.py ./weather_feed.xml ./img UNUSED SLACK_TOKEN SLACK_CHANNEL_ID DISCORD_WEBHOOK_URL
```

The third positional slot is the deprecated, unused Slack webhook URL; keep a
placeholder there when passing later positional arguments. `SLACK_WEBHOOK_URL` is
no longer required. Slack uploads use `SLACK_TOKEN` and `UPLOAD_CHANNEL` instead.
The app still requires **both Slack and Discord destinations**; it does not run
with just one configured destination. Prefer environment configuration to avoid
putting credentials into shell history.

## Configuration

Precedence is **CLI arguments > process environment > selected environment file
> defaults**. Load a file explicitly with `--env-file`. Configuration is read
without changing the process environment; file values do not replace existing
process variables. Named options such as `--rss-file-path`, `--image-file-path`,
`--slack-token`, `--upload-channel`, and `--discord-webhook-url` are also supported.

| Environment variable | Purpose | Default / requirement |
| --- | --- | --- |
| `RSS_FILE_PATH` | Output RSS file path | Required |
| `IMAGE_FILE_PATH` | Directory for downloaded images | Required |
| `SLACK_TOKEN` | Slack API token | Required |
| `UPLOAD_CHANNEL` | Slack channel ID for file uploads | Required |
| `DISCORD_WEBHOOK_URL` | Discord webhook URL | Required |
| `LOG_FILE` | Log file path (`--log-file`) | Optional |
| `THRESHOLD` | Fraction of changed pixels (`--threshold`) | `0.001`; finite number from `0` to `1`, inclusive |
| `PIXEL_TOLERANCE` | Gray-level difference ignored (`--pixel-tolerance`) | `16`; integer from `0` to `255`, inclusive |
| `SLACK_WEBHOOK_URL` | Deprecated and unused | Not required |

`THRESHOLD` and `PIXEL_TOLERANCE` work together. NOAA can re-render images with
faint pixel differences even when nothing meaningful changes. Pixel tolerance
ignores those differences so the threshold can remain sensitive to real changes.
Set `PIXEL_TOLERANCE=0` to count every differing pixel. Comparison logs report the
changed percentage and configured tolerance to help tune these settings.

The app creates output directories as needed. The HTTP cache is
`weather_cache.sqlite` in the **current working directory**, explicitly created
only when the app runs—not on package import. Running from another directory
therefore uses a different cache. Existing local runtime state (cached requests,
downloaded images, feed files, and logs) is not migrated by the package refactor.
Choose output paths and your working directory accordingly. `.env` files and
standard root runtime outputs are ignored by Git; keep credentials private and
review custom output paths before committing.

## Lifecycle and retry policy

Each invocation performs one update and exits; use an external scheduler for
periodic runs. Active storms trigger outlook, cone, and available model image
processing. With formation chances but no named storms, only the outlook is
published and old storm images are removed. When both storms and formation
chances disappear, a final changed outlook is published before all PNG/GIF
images are cleared. If no images remain, subsequent quiet runs do nothing.
Use a dedicated image directory: lifecycle cleanup deletes PNG/GIF files there.

Downloaded images are compared with the local copies even on HTTP cache hits.
Only changed images are uploaded. GIF history retains up to ten frames; a
single-frame GIF is not uploaded alongside its identical PNG. RSS is updated
from the static outlook.

Slack and Discord uploads are attempted independently. If **both fail**, local
image changes are rolled back so the next invocation retries them. If **one
succeeds**, local changes are committed and the failure is still raised; the
failed destination is not automatically retried on the next unchanged run,
avoiding duplicate uploads to the destination that succeeded. There is no
persistent per-destination delivery queue. A cone/model download failure is
logged without blocking other available images. Retries described here happen
on a later invocation, not in an application-level retry loop.

## Development

Development dependencies are in `dependency-groups.dev` and installed by default:

```sh
uv sync --locked --dev
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run basedpyright
uv build
```

Pytest discovers the complete suite under `tests/` and reports package coverage
with missing lines in the terminal. HTML coverage is opt-in:

```sh
uv run pytest --cov-report=html
```

Basedpyright checks `src/weather` only; tests are not yet part of its
configured scope. Ruff retains the existing `E4`, `E7`, `E9`, and `F` lint rules.
CI tests Python 3.15 with `uv sync --locked --dev`, checks lint,
formatting, and types, and builds wheel and source distributions.

Application code lives in `src/weather`; the console entry point is
`weather.cli:main`, and `weather/__main__.py` supports `python -m weather`.
The root `weather.py` remains a compatibility launcher. Use `uv run pytest`
instead of the removed `run_tests.py` helper.
