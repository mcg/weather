# Weather Application - AI Coding Agent Instructions

## Project Overview

This is a hurricane/tropical weather monitoring application that:
1. Fetches NOAA National Hurricane Center XML feeds (`index-at.xml`)
2. Downloads weather images (static outlooks, storm cones, hurricane model data)
3. Detects image changes using pixel-level comparison with configurable thresholds
4. Creates animated GIFs from image sequences (max 10 frames)
5. Uploads new/changed content to Slack and Discord
6. Generates RSS feeds for the static seven-day outlook

## Architecture & Data Flow

**Core Processing Pipeline:**
- `fetch_xml_feed()` → `find_cyclones_in_feed()` → `fetch_all_weather_images()` → upload services
- Three image types: `'static'` (7-day outlook), `'cone'` (storm tracking), `'speg'` (hurricane models)
- Uses `WeatherImage` dataclass to track metadata: paths, URLs, change status, image type

**Key External Dependencies:**
- NOAA NHC XML feed: `https://www.nhc.noaa.gov/index-at.xml`
- Static image: `https://www.nhc.noaa.gov/xgtwo/two_atl_7d0.png`
- Hurricane models: `https://web.uwm.edu/hurricane-models/models/{speg_model}.png`

**Image Processing Logic:**
- `images_are_different()` uses PIL ImageChops with configurable threshold (default: 0.001)
- `update_gif()` maintains rolling 10-frame animations
- Caching via `requests-cache` with 1-hour TTL for hurricane model URLs

## Development Workflow

**Environment Setup:**
```bash
# Project uses uv for dependency management
uv run weather.py --env-file .env

# Testing (multiple approaches available)
uv run run_tests.py           # Smart runner (pytest or unittest)
uv run pytest -v --cov=weather
uv run python -m unittest discover -v
```

**Configuration Patterns:**
- Hybrid arg/env approach: CLI args override env variables
- See `.env.example` for all supported variables
- Required: RSS path, image path, Slack token/channel, Discord webhook

## Testing Architecture

**Key Testing Patterns:**
- `conftest.py` provides shared fixtures (`temp_dir`, `test_image_path`)
- Heavy use of `unittest.mock` for external service calls
- Integration tests in `test_integration.py` test full main() workflow
- Separate files: `test_env_support.py` (config), `test_error_handling.py` (failures)

**Mock Strategies:**
- Mock `requests.get` for HTTP calls
- Mock `WeatherImage` objects for pipeline testing
- Use temporary directories for file operations
- Mock Slack/Discord clients to avoid external calls

## Critical Code Patterns

**XML Parsing Logic:**
```python
# Storm detection uses regex patterns on <title> elements
STORM_PATTERN = re.compile(r'.*(Tropical Storm|Tropical Depression|Hurricane).*Graphics.*')
# SPEG model matching requires correlation between storm names and ATCF codes
```

**Error Handling Approach:**
- Graceful degradation: missing hurricane models don't fail the pipeline
- Image comparison catches PIL exceptions and defaults to "different"
- Slack/Discord upload failures are logged but don't halt processing

**File Management:**
- `delete_storm_images()` preserves static images during cleanup
- Temporary files (`.tmp` suffix) used for atomic image updates
- GIF updates use in-memory frame manipulation

## Project-Specific Conventions

- **Image naming**: `{storm_name}_{type}` (e.g., `Chantal_5day_cone_with_line_and_wind.png`)
- **Logging**: Structured with timestamps, uses module-level logger
- **Type hints**: Uses modern Python syntax (`dict[str, str]`, `list[WeatherImage]`)
- **State management**: No persistent state beyond cached images and requests

## Integration Points

**Slack Integration:**
- Uses `files_upload_v2` for bulk uploads with structured metadata
- Requires both bot token and channel ID

**Discord Integration:**
- Webhook-based, sends separate messages per image type
- Formats content with markdown for storm names

**RSS Generation:**
- Only for static seven-day outlook image
- Uses timestamp-based cache busting in image URLs