# Basics

This project uses [uv](https://github.com/astral-sh/uv)

After `uv` setup it can be run as:

```sh
uv run weather.py ./FEED_FILE_PATH ./IMAGE_STORAGE_PATH SLACK_WEBHOOK SLACK_API_KEY SLACK_CHANNEL DISCORD_WEBHOOK
```

## Environment Variable Support

You can also use a `.env` file to store your configuration instead of passing all arguments on the command line:

1. Copy the example environment file:

   ```sh
   cp .env.example .env
   ```

2. Edit `.env` with your actual values

3. Run with the env file:

   ```sh
   uv run weather.py --env-file .env
   ```

### Environment Variables

The following environment variables are supported:

- `RSS_FILE_PATH` - Path to save the RSS feed file
- `IMAGE_FILE_PATH` - Path to save image files  
- `SLACK_WEBHOOK_URL` - Slack webhook URL
- `SLACK_TOKEN` - Slack API token
- `UPLOAD_CHANNEL` - Slack channel ID for uploading files
- `DISCORD_WEBHOOK_URL` - Discord webhook URL
- `LOG_FILE` - Path to log file (optional)
- `THRESHOLD` - Fraction of pixels that must change before an image counts as new (optional, default `0.001`, i.e. 0.1%). Also available as `--threshold`
- `PIXEL_TOLERANCE` - A pixel only counts as changed if it differs by more than this many gray levels out of 255 (optional, integer 0-255, default `16`). Also available as `--pixel-tolerance`

`THRESHOLD` and `PIXEL_TOLERANCE` work together. NOAA re-renders its images with faint, scattered pixel differences even when nothing meaningful changed, and `PIXEL_TOLERANCE` ignores those so `THRESHOLD` can stay small enough to catch real changes such as a new storm marker. Set `PIXEL_TOLERANCE=0` to count every pixel difference. Each comparison is logged, for example `0.23% pixels differ by more than 16/255 (threshold 0.10%)`, which helps when tuning the two values.

You can mix command line arguments and environment variables - command line arguments take precedence over environment variables.
