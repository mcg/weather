# pyright: reportMissingTypeStubs=false, reportAny=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false, reportUnknownLambdaType=false, reportImplicitStringConcatenation=false, reportUnnecessaryComparison=false

from __future__ import annotations

import argparse
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import TypedDict, cast

import requests
import requests_cache
from bs4 import BeautifulSoup
from discord import File, SyncWebhook
from dotenv import load_dotenv
from feedgen.feed import FeedGenerator
from PIL import Image, ImageChops, ImageSequence
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

# Configure logging
logger = logging.getLogger(__name__)


class CycloneInfo(TypedDict):
    storm_name: str
    storm_type: str
    image_url: str
    speg_model: str | None


@dataclass
class CliArgs:
    env_file: str | None
    rss_file_path: str | None
    image_file_path: str | None
    slack_webhook_url: str | None
    slack_token: str | None
    upload_channel: str | None
    discord_webhook_url: str | None
    log_file: str | None
    threshold: float | str | None


@dataclass
class WeatherImage:
    """Represents a weather image with all its metadata."""

    name: str
    png_path: str
    gif_path: str
    url: str
    is_new: bool
    image_type: str  # 'static', 'cone', 'speg', 'cached', 'processed'


# Set up caching
urls_expire_after = {
    "https://web.uwm.edu/hurricane-models/models/*": timedelta(hours=1)
}
requests_cache.install_cache(
    "weather_cache", cache_control=True, urls_expire_after=urls_expire_after
)

# Regex patterns
STORM_PATTERN = re.compile(
    r".*(Tropical Storm|Tropical Depression|Hurricane).*Graphics.*", re.IGNORECASE
)
SPEG_PATTERN = re.compile(r".*Summary for (Tropical\sStorm|Hurricane).*", re.IGNORECASE)
STORM_NAME_PATTERN = re.compile(
    r"(Tropical\sStorm|Tropical\sDepression|Hurricane) (.*?) Graphics", re.IGNORECASE
)


def setup_logging(log_file_path: str | None = None) -> None:
    """Set up logging configuration."""
    if log_file_path:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(levelname)s - %(message)s",
            handlers=[logging.StreamHandler(), logging.FileHandler(log_file_path)],
        )
    else:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(levelname)s - %(message)s",
            handlers=[logging.StreamHandler()],
        )


def fetch_xml_feed() -> tuple[int, BeautifulSoup]:
    """Fetch and parse the XML feed from NOAA."""
    logger.info("Fetching XML feed from NOAA")

    url = "https://www.nhc.noaa.gov/index-at.xml"
    response = requests.get(url)
    soup = BeautifulSoup(response.content.decode("utf-8"), "xml")

    # Check for active storms
    storm_titles = cast(list[object], soup.find_all("title", string=STORM_PATTERN))
    active_storm_count = len(storm_titles)

    if active_storm_count > 0:
        logger.info(f"Found {active_storm_count} active storms")
    else:
        logger.info("No active storms found")

    return active_storm_count, soup


def extract_storm_info(title_source: str | object) -> dict[str, str] | None:
    """Extract storm information from a title string or title-like object."""
    title_text = title_source if isinstance(title_source, str) else str(getattr(title_source, "text", ""))
    match = STORM_NAME_PATTERN.search(title_text)
    if not match:
        return None

    storm_type = match.group(1).strip()
    storm_name = match.group(2).strip()

    if storm_type not in ["Hurricane", "Tropical Storm", "Tropical Depression"]:
        return None

    return {"name": storm_name, "type": storm_type}


def find_speg_model(soup: BeautifulSoup, storm_name: str) -> str | None:
    """Find SPEG model ID for a given storm."""
    speg_titles = cast(list[object], soup.find_all("title", string=SPEG_PATTERN))

    for speg_title in speg_titles:
        title_text = str(getattr(speg_title, "text", ""))
        if storm_name.lower() in title_text.lower():
            find_parent = getattr(speg_title, "find_parent", None)
            if not callable(find_parent):
                continue

            item = find_parent("item")
            if not item:
                continue

            item_find = getattr(item, "find", None)
            if not callable(item_find):
                continue

            cyclone_tag = item_find("nhc:Cyclone")
            if not cyclone_tag:
                continue

            cyclone_find = getattr(cyclone_tag, "find", None)
            if not callable(cyclone_find):
                continue

            name_tag = cyclone_find("nhc:name")
            if name_tag:
                name_text = str(getattr(name_tag, "text", ""))
                if name_text.lower() != storm_name.lower():
                    continue

            atcf_tag = cyclone_find("nhc:atcf")
            if atcf_tag:
                atcf_text = str(getattr(atcf_tag, "text", ""))
                return atcf_text.lower()

    return None


def find_cyclones_in_feed(soup: BeautifulSoup) -> list[CycloneInfo]:
    """Find all cyclones in the XML feed."""
    logger.info("Searching for cyclones in feed")

    storm_titles = cast(list[object], soup.find_all("title", string=STORM_PATTERN))
    cyclones: list[CycloneInfo] = []

    for title in storm_titles:
        title_text = str(getattr(title, "text", ""))
        storm_info = extract_storm_info(title_text)
        if not storm_info:
            continue

        find_next = getattr(title, "find_next", None)
        if not callable(find_next):
            continue

        description_tag = find_next("description")
        if description_tag is None:
            continue

        description = str(getattr(description_tag, "text", ""))
        cdata_soup = BeautifulSoup(description, "html.parser")

        # Find the 5-day cone image
        img_tag = cdata_soup.find(
            "img",
            src=lambda src: (
                isinstance(src, str) and "5day_cone_with_line_and_wind" in src
            ),
        )
        if img_tag is None:
            continue

        image_url = img_tag.get("src")
        if not isinstance(image_url, str):
            continue

        speg_model = find_speg_model(soup, storm_info["name"])
        cyclones.append(
            {
                "storm_name": storm_info["name"],
                "storm_type": storm_info["type"],
                "image_url": image_url,
                "speg_model": speg_model,
            }
        )
        logger.info(
            f"Found cyclone: {storm_info['name']} ({storm_info['type']}) with SPEG model: {speg_model}"
        )

    logger.info(f"Found {len(cyclones)} cyclones")
    return cyclones


def images_are_different(
    new_image_path: str, existing_image_path: str, threshold: float = 0.001
) -> bool:
    """Compare two images to determine if they're different."""
    if not os.path.exists(existing_image_path):
        logger.info(f"No existing image found at {existing_image_path}")
        return True

    try:
        with (
            Image.open(new_image_path) as new_img,
            Image.open(existing_image_path) as existing_img,
        ):
            if new_img.size != existing_img.size:
                logger.info(
                    f"Image comparison: size changed from {existing_img.size} to {new_img.size}"
                )
                return True

            if new_img.mode != "RGB":
                new_img = new_img.convert("RGB")
            if existing_img.mode != "RGB":
                existing_img = existing_img.convert("RGB")

            diff = ImageChops.difference(new_img, existing_img)
            diff_gray = diff.convert("L")
            histogram = diff_gray.histogram()
            total_pixels = sum(histogram)
            if total_pixels == 0:
                return False

            # Histogram bin 0 = identical pixels; bins 1..255 = changed pixels
            different_pixels = sum(histogram[1:])
            difference_percentage = different_pixels / total_pixels

            logger.info(
                f"Image comparison: {difference_percentage:.4f} ({difference_percentage * 100:.2f}%) pixels different"
            )
            return difference_percentage > threshold

    except Exception as exc:
        logger.error(f"Error comparing images: {exc}")
        return True


def update_gif(png_path: str, gif_path: str, max_frames: int = 10) -> None:
    """Create or update a GIF with the new PNG frame."""
    if not os.path.exists(gif_path):
        with Image.open(png_path) as img:
            img.save(gif_path, "GIF")
        logger.info(f"Created new GIF: {gif_path}")
        return

    with Image.open(gif_path) as gif:
        frames = [frame.copy() for frame in ImageSequence.Iterator(gif)]

    with Image.open(png_path) as new_frame:
        frames.append(new_frame.convert("RGBA"))

    if len(frames) > max_frames:
        frames = frames[-max_frames:]

    frames[0].save(
        gif_path, save_all=True, append_images=frames[1:], loop=0, duration=500
    )
    logger.info(f"Updated GIF: {gif_path} (frames: {len(frames)})")


def process_single_image(
    url: str,
    base_name: str,
    image_dir: str,
    threshold: float = 0.001,
) -> WeatherImage:
    """Download and process a single image, returning a WeatherImage object."""
    logger.info(f"Processing image: {base_name}")

    response = requests.get(url)
    png_path = f"{image_dir}/{base_name}.png"
    gif_path = f"{image_dir}/{base_name}.gif"

    if bool(getattr(response, "from_cache", False)):
        logger.info(f"{base_name} from cache")
        return WeatherImage(base_name, png_path, gif_path, url, False, "cached")

    temp_path = f"{png_path}.tmp"
    with open(temp_path, "wb") as temp_file:
        _ = temp_file.write(response.content)

    is_different = images_are_different(temp_path, png_path, threshold)

    if is_different:
        logger.info(f"{base_name} is new/different")
        if os.path.exists(png_path):
            os.remove(png_path)
        os.rename(temp_path, png_path)
        update_gif(png_path, gif_path)
    else:
        logger.info(f"{base_name} unchanged")
        os.remove(temp_path)

    return WeatherImage(base_name, png_path, gif_path, url, is_different, "processed")


def fetch_all_weather_images(
    soup: BeautifulSoup, image_dir: str, threshold: float = 0.001
) -> list[WeatherImage]:
    """Fetch all weather images and return a list of WeatherImage objects."""
    logger.info("Fetching all weather images")
    images: list[WeatherImage] = []

    # Static seven-day outlook
    static_url = "https://www.nhc.noaa.gov/xgtwo/two_atl_7d0.png"
    static_image = process_single_image(static_url, "two_atl_7d0", image_dir, threshold)
    static_image.image_type = "static"
    images.append(static_image)

    # Cyclone images
    cyclones = find_cyclones_in_feed(soup)
    for cyclone in cyclones:
        storm_name = cyclone["storm_name"]

        # NHC cone image
        cone_image = process_single_image(
            cyclone["image_url"],
            f"{storm_name}_5day_cone_with_line_and_wind",
            image_dir,
            threshold,
        )
        cone_image.image_type = "cone"
        images.append(cone_image)

        # Hurricane models image (if available)
        if cyclone["speg_model"]:
            models_url = f"https://web.uwm.edu/hurricane-models/models/{cyclone['speg_model']}.png"
            try:
                models_image = process_single_image(
                    models_url,
                    f"{storm_name}_hurricane_models",
                    image_dir,
                    threshold,
                )
                models_image.image_type = "speg"
                images.append(models_image)
                logger.info(f"Fetched hurricane models for {storm_name}")
            except Exception as exc:
                logger.warning(
                    f"Failed to fetch hurricane models for {storm_name}: {exc}"
                )

    logger.info(f"Processed {len(images)} images total")
    return images


def generate_rss_feed(static_image: WeatherImage, rss_file_path: str) -> None:
    """Generate RSS feed for the static weather image."""
    timestamp = int(time.time())
    img_size = 0
    if os.path.exists(static_image.png_path):
        img_size = os.path.getsize(static_image.png_path)

    fg = FeedGenerator()
    _ = fg.title("Seven-Day Atlantic Graphical Tropical Weather Outlook")
    _ = fg.description(
        "Extracted graphic from the NOAA National Hurricane Center. Updated every six hours."
    )
    _ = fg.link(href=static_image.url)

    fe = fg.add_entry()
    _ = fe.title("Weather Image")
    _ = fe.link(href=static_image.url)
    _ = fe.description(
        f'Atlantic Weather Image. <img src="{static_image.url}#{timestamp}" alt="Weather Image"/>'
    )
    _ = fe.enclosure(static_image.url, img_size, "image/png")
    _ = fe.id(f"{static_image.url}#{timestamp}")

    fg.rss_file(rss_file_path)


def upload_files_to_slack(
    images: list[WeatherImage], slack_token: str, upload_channel: str
) -> None:
    """Upload images to Slack."""
    client = WebClient(token=slack_token)
    file_uploads: list[dict[str, str]] = []

    for image in images:
        if image.image_type == "static":
            file_uploads.extend(
                [
                    {"file": image.png_path, "title": "Seven-Day Outlook"},
                    {"file": image.gif_path, "title": "Last 10 maps"},
                ]
            )
        elif image.image_type == "cone":
            file_uploads.extend(
                [
                    {"file": image.png_path, "title": image.name},
                    {"file": image.gif_path, "title": f"{image.name} Loop"},
                ]
            )
        elif image.image_type == "speg":
            file_uploads.extend(
                [
                    {"file": image.png_path, "title": f"{image.name} Models"},
                    {"file": image.gif_path, "title": f"{image.name} Models Loop"},
                ]
            )

    if not file_uploads:
        return

    try:
        logger.info(f"Uploading {len(file_uploads)} files to Slack")
        _ = client.files_upload_v2(
            file_uploads=file_uploads,
            channel=upload_channel,
            initial_comment="Atlantic Tropical Weather Update",
        )
        logger.info("Successfully uploaded to Slack")
    except SlackApiError as exc:
        logger.error(f"Slack API error: {exc.response['error']}")
        raise


def upload_files_to_discord(
    images: list[WeatherImage], discord_webhook_url: str
) -> None:
    """Upload images to Discord."""
    webhook = SyncWebhook.from_url(discord_webhook_url)

    for image in images:
        if image.image_type == "static":
            with open(image.png_path, "rb") as png, open(image.gif_path, "rb") as gif:
                _ = webhook.send(
                    content="Seven-Day Outlook and Map Loop",
                    files=[
                        File(png, filename="outlook.png"),
                        File(gif, filename="outlook.gif"),
                    ],
                )
        elif image.image_type == "cone":
            with open(image.png_path, "rb") as png, open(image.gif_path, "rb") as gif:
                _ = webhook.send(
                    content=f"**{image.name} - NHC Cone**",
                    files=[
                        File(png, filename=f"{image.name}.png"),
                        File(gif, filename=f"{image.name}.gif"),
                    ],
                )
        elif image.image_type == "speg":
            with open(image.png_path, "rb") as png, open(image.gif_path, "rb") as gif:
                _ = webhook.send(
                    content=f"**{image.name} - Hurricane Models**",
                    files=[
                        File(png, filename=f"{image.name}_models.png"),
                        File(gif, filename=f"{image.name}_models.gif"),
                    ],
                )

    logger.info("Successfully uploaded to Discord")


def delete_images(image_dir: str) -> None:
    """Delete all PNG and GIF files in the directory."""
    logger.info(f"Deleting images from {image_dir}")
    count = 0

    for filename in os.listdir(image_dir):
        if filename.endswith((".png", ".gif")):
            try:
                os.remove(os.path.join(image_dir, filename))
                count += 1
            except (OSError, PermissionError) as exc:
                logger.error(f"Failed to delete {filename}: {exc}")

    logger.info(f"Deleted {count} image files")


def delete_storm_images(image_dir: str) -> None:
    """Delete storm-related PNG and GIF files, but keep static outlook images."""
    logger.info(f"Deleting storm images from {image_dir}")
    count = 0

    for filename in os.listdir(image_dir):
        if not filename.endswith((".png", ".gif")):
            continue

        if "two_atl_7d0" in filename:
            continue

        try:
            os.remove(os.path.join(image_dir, filename))
            count += 1
            logger.debug(f"Deleted storm image: {filename}")
        except (OSError, PermissionError) as exc:
            logger.error(f"Failed to delete {filename}: {exc}")

    logger.info(f"Deleted {count} storm image files")


def no_storm_upload_marker_path(image_dir: str) -> str:
    """Path for marker indicating no-storm image has already been uploaded."""
    return os.path.join(image_dir, ".no_storm_uploaded")


def clear_no_storm_upload_marker(image_dir: str) -> None:
    """Clear no-storm marker so next calm period can upload once again."""
    marker_path = no_storm_upload_marker_path(image_dir)
    if not os.path.exists(marker_path):
        return

    try:
        os.remove(marker_path)
        logger.info("Cleared no-storm upload marker")
    except (OSError, PermissionError) as exc:
        logger.error(f"Failed to clear no-storm upload marker: {exc}")


def mark_no_storm_uploaded(image_dir: str) -> None:
    """Mark that no-storm image has been uploaded for current calm period."""
    try:
        os.makedirs(image_dir, exist_ok=True)
        with open(no_storm_upload_marker_path(image_dir), "w", encoding="utf-8") as marker:
            _ = marker.write(str(int(time.time())))
    except (OSError, PermissionError) as exc:
        logger.error(f"Failed to persist no-storm upload marker: {exc}")


def delete_no_storm_gif(image_dir: str) -> None:
    """Delete only the no-storm GIF loop file."""
    gif_path = os.path.join(image_dir, "two_atl_7d0.gif")
    if not os.path.exists(gif_path):
        return

    try:
        os.remove(gif_path)
        logger.info(f"Deleted no-storm GIF: {gif_path}")
    except (OSError, PermissionError) as exc:
        logger.error(f"Failed to delete no-storm GIF {gif_path}: {exc}")


def require_str(value: str | None, name: str) -> str:
    """Narrow a validated optional string to a string for type checkers."""
    if value is None or value == "":
        raise ValueError(f"{name} is required")
    return value


def parse_threshold(
    raw_threshold: float | str | None, parser: argparse.ArgumentParser
) -> float:
    """Parse threshold from CLI/env values."""
    if raw_threshold is None:
        return 0.001

    if isinstance(raw_threshold, (float, int)):
        return float(raw_threshold)

    try:
        return float(raw_threshold)
    except ValueError:
        parser.error(
            f"Invalid THRESHOLD value {raw_threshold!r}. Must be a valid float."
        )


def get_config_str(arg_value: str | None, env_key: str) -> str | None:
    """Get string config value from CLI arg first, then environment variable."""
    if arg_value is not None:
        return arg_value
    return os.getenv(env_key)


def get_config_threshold(arg_value: float | str | None, env_key: str) -> float | str | None:
    """Get threshold config from CLI arg first, then environment variable."""
    if arg_value is not None:
        return arg_value
    return os.getenv(env_key)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch and process weather images.")
    _ = parser.add_argument(
        "--env-file", help="Path to .env file to load environment variables from."
    )
    _ = parser.add_argument(
        "rss_file_path", nargs="?", help="Path to save the RSS feed file."
    )
    _ = parser.add_argument(
        "image_file_path", nargs="?", help="Path to save image files."
    )
    _ = parser.add_argument(
        "slack_webhook_url", nargs="?", help="Slack webhook URL (unused)."
    )
    _ = parser.add_argument("slack_token", nargs="?", help="Slack API token.")
    _ = parser.add_argument(
        "upload_channel", nargs="?", help="Slack channel ID for uploading files."
    )
    _ = parser.add_argument(
        "discord_webhook_url", nargs="?", help="Discord webhook URL."
    )
    _ = parser.add_argument("--log-file", help="Path to log file (optional).")
    _ = parser.add_argument(
        "--threshold",
        type=float,
        help="Threshold for image difference detection (default: 0.001).",
    )

    namespace = parser.parse_args()
    args = CliArgs(
        env_file=getattr(namespace, "env_file", None),
        rss_file_path=getattr(namespace, "rss_file_path", None),
        image_file_path=getattr(namespace, "image_file_path", None),
        slack_webhook_url=getattr(namespace, "slack_webhook_url", None),
        slack_token=getattr(namespace, "slack_token", None),
        upload_channel=getattr(namespace, "upload_channel", None),
        discord_webhook_url=getattr(namespace, "discord_webhook_url", None),
        log_file=getattr(namespace, "log_file", None),
        threshold=getattr(namespace, "threshold", None),
    )

    if args.env_file:
        _ = load_dotenv(args.env_file)

    rss_file_path = get_config_str(args.rss_file_path, "RSS_FILE_PATH")
    image_file_path = get_config_str(args.image_file_path, "IMAGE_FILE_PATH")
    slack_webhook_url = get_config_str(args.slack_webhook_url, "SLACK_WEBHOOK_URL")
    slack_token = get_config_str(args.slack_token, "SLACK_TOKEN")
    upload_channel = get_config_str(args.upload_channel, "UPLOAD_CHANNEL")
    discord_webhook_url = get_config_str(args.discord_webhook_url, "DISCORD_WEBHOOK_URL")
    log_file = get_config_str(args.log_file, "LOG_FILE")
    threshold = parse_threshold(
        get_config_threshold(args.threshold, "THRESHOLD"), parser
    )

    required_args: dict[str, str | None] = {
        "rss_file_path": rss_file_path,
        "image_file_path": image_file_path,
        "slack_webhook_url": slack_webhook_url,
        "slack_token": slack_token,
        "upload_channel": upload_channel,
        "discord_webhook_url": discord_webhook_url,
    }

    missing_args = [name for name, value in required_args.items() if not value]
    if missing_args:
        parser.error(
            f"Missing required arguments: {', '.join(missing_args)}. Provide them as command line arguments or set them in the .env file."
        )

    rss_file_path_str = require_str(rss_file_path, "rss_file_path")
    image_file_path_str = require_str(image_file_path, "image_file_path")
    slack_token_str = require_str(slack_token, "slack_token")
    upload_channel_str = require_str(upload_channel, "upload_channel")
    discord_webhook_url_str = require_str(discord_webhook_url, "discord_webhook_url")

    setup_logging(log_file)
    logger.info("Starting weather image processing")

    active_storm_count, soup = fetch_xml_feed()

    if active_storm_count > 0:
        logger.info("Processing weather images - storms detected")
        clear_no_storm_upload_marker(image_file_path_str)

        all_images = fetch_all_weather_images(soup, image_file_path_str, threshold)

        static_image = next(
            (img for img in all_images if img.image_type == "static"), None
        )
        if static_image:
            generate_rss_feed(static_image, rss_file_path_str)

        new_images = [img for img in all_images if img.is_new]

        if new_images:
            logger.info(f"Uploading {len(new_images)} new images")
            upload_files_to_slack(new_images, slack_token_str, upload_channel_str)
            upload_files_to_discord(new_images, discord_webhook_url_str)
        else:
            logger.info("No new images to upload")

        logger.info(f"Processing complete - handled {len(all_images)} total images")
        return

    logger.info("No tropical cyclones expected - checking static image only")

    static_url = "https://www.nhc.noaa.gov/xgtwo/two_atl_7d0.png"
    static_image = process_single_image(
        static_url,
        "two_atl_7d0",
        image_file_path_str,
        threshold,
    )
    static_image.image_type = "static"

    generate_rss_feed(static_image, rss_file_path_str)

    marker_path = no_storm_upload_marker_path(image_file_path_str)

    if os.path.exists(marker_path):
        logger.info("No-storm image already uploaded for current calm period - no upload needed")
    elif static_image.image_type == "cached":
        logger.info("No-storm image came from cache - no upload needed")
    elif static_image.is_new:
        logger.info("First no-storm run in current calm period - uploading static image once")
        upload_files_to_slack([static_image], slack_token_str, upload_channel_str)
        upload_files_to_discord([static_image], discord_webhook_url_str)

        # After first no-storm upload, clear storm artifacts and reset static GIF loop.
        delete_storm_images(image_file_path_str)
        delete_no_storm_gif(image_file_path_str)
        mark_no_storm_uploaded(image_file_path_str)
    else:
        logger.info("No-storm image unchanged - no upload needed")

    logger.info("Processing complete - handled static image only")


if __name__ == "__main__":
    main()
