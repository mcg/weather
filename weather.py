# pyright: reportMissingTypeStubs=false, reportAny=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false, reportUnknownLambdaType=false, reportImplicitStringConcatenation=false, reportUnnecessaryComparison=false

from __future__ import annotations

import argparse
import logging
import os
import re
import shutil
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
    pixel_tolerance: int | str | None = None


@dataclass
class WeatherImage:
    """Represents a weather image with all its metadata."""

    name: str
    png_path: str
    gif_path: str
    url: str
    is_new: bool
    image_type: str  # 'static', 'cone', 'speg', 'processed'
    # Backups of the files this image replaced. They let a failed upload be rolled
    # back, so the next run sees the image as new again and retries it.
    previous_png: str | None = None
    previous_gif: str | None = None


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
SUMMARY_TITLE_PATTERN = re.compile(
    r"Summary for (Tropical\sStorm|Tropical\sDepression|Hurricane)", re.IGNORECASE
)
ATCF_PATTERN = re.compile(r"^(?P<basin>[A-Z]{2})(?P<number>\d{2})\d{4}$", re.IGNORECASE)
# The advisory time (DDHHMM) is the last six digits of the summary guid
# (summary-al092026-202610070852) or the file name in its link (.../070852.shtml).
SUMMARY_GUID_STAMP_PATTERN = re.compile(r"-\d{6}(\d{6})$")
SUMMARY_LINK_STAMP_PATTERN = re.compile(r"/(\d{6})\.shtml")
OUTLOOK_TITLE_PATTERN = re.compile(r"Tropical Weather Outlook", re.IGNORECASE)
FORMATION_CHANCE_PATTERN = re.compile(r"Formation chance", re.IGNORECASE)

# NHC graphics directories use their own basin prefix (AL -> AT for the Atlantic).
GRAPHICS_BASIN_PREFIXES = {"AL": "AT"}
CONE_URL_TEMPLATE = (
    "https://www.nhc.noaa.gov/storm_graphics/{graphics_id}/refresh/"
    "{atcf}_5day_cone+png/{stamp}_5day_cone.png"
)

# The final "no storms, no formation chance" outlook is posted exactly once before
# all images are deleted. Any pixel difference must count as new, otherwise a small
# change (e.g. a tiny formation area disappearing) falls under the normal threshold
# and the last image is never posted.
FINAL_UPDATE_THRESHOLD = 0.0

# NOAA re-renders its images with faint, scattered pixel differences (a few gray
# levels out of 255 on coastlines, titles and the legend) even when nothing
# meaningful changed. A pixel only counts as changed if it differs by more than
# this many gray levels, which lets the percentage threshold stay small enough to
# catch real changes such as a new storm marker.
PIXEL_TOLERANCE = 16


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


def build_cone_url(atcf: str, stamp: str) -> str | None:
    """Build the NHC 5-day cone image URL from an ATCF id and an advisory DDHHMM stamp.

    For example ``AL092026`` and ``070852`` give
    ``.../storm_graphics/AT09/refresh/AL092026_5day_cone+png/070852_5day_cone.png``.
    """
    match = ATCF_PATTERN.match(atcf.strip())
    if not match:
        return None

    basin = match.group("basin").upper()
    graphics_basin = GRAPHICS_BASIN_PREFIXES.get(basin)
    if graphics_basin is None:
        return None

    return CONE_URL_TEMPLATE.format(
        graphics_id=f"{graphics_basin}{match.group('number')}",
        atcf=atcf.strip().upper(),
        stamp=stamp,
    )


def find_cone_url(soup: BeautifulSoup, storm_name: str) -> str | None:
    """Build the 5-day cone image URL for a storm from its "Summary for" feed item.

    The summary item carries the ATCF id (``nhc:atcf``) and the advisory time
    (in its guid and link), which together make up the cone image URL.
    """
    summary_titles = cast(
        list[object], soup.find_all("title", string=SUMMARY_TITLE_PATTERN)
    )

    for summary_title in summary_titles:
        title_text = str(getattr(summary_title, "text", ""))
        if storm_name.lower() not in title_text.lower():
            continue

        find_parent = getattr(summary_title, "find_parent", None)
        item = find_parent("item") if callable(find_parent) else None
        if item is None:
            continue

        cyclone_tag = item.find("nhc:Cyclone")
        if cyclone_tag is None:
            continue

        name_tag = cyclone_tag.find("nhc:name")
        if name_tag is not None and str(name_tag.text).strip().lower() != storm_name.lower():
            continue

        atcf_tag = cyclone_tag.find("nhc:atcf")
        if atcf_tag is None:
            continue

        stamp: str | None = None
        link_tag = item.find("link")
        if link_tag is not None:
            link_match = SUMMARY_LINK_STAMP_PATTERN.search(str(link_tag.text).strip())
            if link_match:
                stamp = link_match.group(1)
        if stamp is None:
            guid_tag = item.find("guid")
            if guid_tag is not None:
                guid_match = SUMMARY_GUID_STAMP_PATTERN.search(str(guid_tag.text).strip())
                if guid_match:
                    stamp = guid_match.group(1)
        if stamp is None:
            continue

        cone_url = build_cone_url(str(atcf_tag.text), stamp)
        if cone_url:
            return cone_url

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

        # Find the 5-day cone image. Prefer an image embedded in the graphics
        # item; the NHC feed often only lists other graphics there, so otherwise
        # build the cone URL from the storm's summary item.
        img_tag = cdata_soup.find(
            "img",
            src=lambda src: (
                isinstance(src, str) and "5day_cone_with_line_and_wind" in src
            ),
        )
        image_url: str | None = None
        if img_tag is not None:
            src_value = img_tag.get("src")
            if isinstance(src_value, str):
                image_url = src_value

        if image_url is None:
            image_url = find_cone_url(soup, storm_info["name"])
        if image_url is None:
            logger.warning(f"No cone image found for {storm_info['name']}")
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


def has_formation_chance(soup: BeautifulSoup) -> bool:
    """Check whether the Tropical Weather Outlook mentions a formation chance for development."""
    outlook_title = soup.find("title", string=OUTLOOK_TITLE_PATTERN)
    if outlook_title is None:
        return False

    find_next = getattr(outlook_title, "find_next", None)
    if not callable(find_next):
        return False

    description_tag = find_next("description")
    if description_tag is None:
        return False

    description_text = str(getattr(description_tag, "text", ""))
    found = bool(FORMATION_CHANCE_PATTERN.search(description_text))

    if found:
        logger.info("Formation chance detected in Tropical Weather Outlook")
    else:
        logger.info("No formation chance mentioned in Tropical Weather Outlook")

    return found


def images_are_different(
    new_image_path: str,
    existing_image_path: str,
    threshold: float = 0.001,
    pixel_tolerance: int = PIXEL_TOLERANCE,
) -> bool:
    """Compare two images to determine if they're different.

    A pixel counts as changed if it differs by more than ``pixel_tolerance`` gray
    levels. The images are different if the fraction of changed pixels is greater
    than ``threshold``.
    """
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

            # Histogram bin N = pixels that differ by N gray levels. Skip bins
            # 0..pixel_tolerance (identical or only faintly different pixels).
            different_pixels = sum(histogram[pixel_tolerance + 1 :])
            difference_percentage = different_pixels / total_pixels

            logger.info(
                f"Image comparison: {difference_percentage:.4f} ({difference_percentage * 100:.2f}%) "
                f"pixels differ by more than {pixel_tolerance}/255 "
                f"(threshold {threshold:.4f} / {threshold * 100:.2f}%)"
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


BACKUP_SUFFIX = ".prev"


def stash_file(path: str, move: bool) -> str | None:
    """Back up ``path`` to ``path.prev``; return the backup path, or None if there was no file."""
    if not os.path.exists(path):
        return None

    backup_path = f"{path}{BACKUP_SUFFIX}"
    if move:
        os.replace(path, backup_path)
    else:
        _ = shutil.copy2(path, backup_path)
    return backup_path


def restore_file(path: str, backup_path: str | None) -> None:
    """Put a backed-up file back, or remove ``path`` if there was nothing to back up."""
    if backup_path is not None and os.path.exists(backup_path):
        os.replace(backup_path, path)
    elif backup_path is None and os.path.exists(path):
        os.remove(path)


def commit_images(images: list[WeatherImage]) -> None:
    """Discard backups once the new images have been published."""
    for image in images:
        for backup_path in (image.previous_png, image.previous_gif):
            if backup_path is not None and os.path.exists(backup_path):
                os.remove(backup_path)


def rollback_images(images: list[WeatherImage]) -> None:
    """Restore the files that new images replaced so the next run retries them."""
    for image in images:
        if not image.is_new:
            continue
        logger.warning(f"Rolling back {image.name} so it is retried on the next run")
        for path, backup_path in (
            (image.png_path, image.previous_png),
            (image.gif_path, image.previous_gif),
        ):
            try:
                restore_file(path, backup_path)
            except OSError as exc:
                logger.error(f"Failed to roll back {path}: {exc}")


def process_single_image(
    url: str,
    base_name: str,
    image_dir: str,
    threshold: float = 0.001,
    pixel_tolerance: int = PIXEL_TOLERANCE,
) -> WeatherImage:
    """Download and process a single image, returning a WeatherImage object.

    The downloaded body is compared with the local PNG on every call, even when
    the HTTP cache answered the request. The cache only says that we have seen a
    body before, not that it was ever published.

    If the image is new, the files it replaces are kept as backups so that
    ``rollback_images`` can undo the change if publishing fails.
    """
    logger.info(f"Processing image: {base_name}")

    response = requests.get(url)
    response.raise_for_status()
    if bool(getattr(response, "from_cache", False)):
        logger.info(f"{base_name} served from HTTP cache - comparing with local copy")

    png_path = f"{image_dir}/{base_name}.png"
    gif_path = f"{image_dir}/{base_name}.gif"

    temp_path = f"{png_path}.tmp"
    with open(temp_path, "wb") as temp_file:
        _ = temp_file.write(response.content)

    is_different = images_are_different(
        temp_path, png_path, threshold, pixel_tolerance
    )

    if not is_different:
        logger.info(f"{base_name} unchanged")
        os.remove(temp_path)
        return WeatherImage(base_name, png_path, gif_path, url, False, "processed")

    logger.info(f"{base_name} is new/different")
    previous_png = stash_file(png_path, move=True)
    previous_gif = stash_file(gif_path, move=False)
    try:
        os.rename(temp_path, png_path)
        update_gif(png_path, gif_path)
    except Exception:
        restore_file(png_path, previous_png)
        restore_file(gif_path, previous_gif)
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise

    return WeatherImage(
        base_name,
        png_path,
        gif_path,
        url,
        True,
        "processed",
        previous_png,
        previous_gif,
    )


def fetch_all_weather_images(
    soup: BeautifulSoup,
    image_dir: str,
    threshold: float = 0.001,
    pixel_tolerance: int = PIXEL_TOLERANCE,
) -> list[WeatherImage]:
    """Fetch all weather images and return a list of WeatherImage objects."""
    logger.info("Fetching all weather images")
    images: list[WeatherImage] = []

    try:
        # Static seven-day outlook
        static_url = "https://www.nhc.noaa.gov/xgtwo/two_atl_7d0.png"
        static_image = process_single_image(
            static_url,
            "two_atl_7d0",
            image_dir,
            threshold,
            pixel_tolerance=pixel_tolerance,
        )
        static_image.image_type = "static"
        images.append(static_image)

        # Cyclone images. A failure for one image must not stop the others (in
        # particular the static outlook) from being published.
        cyclones = find_cyclones_in_feed(soup)
        for cyclone in cyclones:
            storm_name = cyclone["storm_name"]

            # NHC cone image
            try:
                cone_image = process_single_image(
                    cyclone["image_url"],
                    f"{storm_name}_5day_cone_with_line_and_wind",
                    image_dir,
                    threshold,
                    pixel_tolerance=pixel_tolerance,
                )
                cone_image.image_type = "cone"
                images.append(cone_image)
            except Exception as exc:
                logger.warning(f"Failed to fetch cone image for {storm_name}: {exc}")

            # Hurricane models image (if available)
            if cyclone["speg_model"]:
                models_url = f"https://web.uwm.edu/hurricane-models/models/{cyclone['speg_model']}.png"
                try:
                    models_image = process_single_image(
                        models_url,
                        f"{storm_name}_hurricane_models",
                        image_dir,
                        threshold,
                        pixel_tolerance=pixel_tolerance,
                    )
                    models_image.image_type = "speg"
                    images.append(models_image)
                    logger.info(f"Fetched hurricane models for {storm_name}")
                except Exception as exc:
                    logger.warning(
                        f"Failed to fetch hurricane models for {storm_name}: {exc}"
                    )
    except Exception:
        # Nothing will be published, so undo any files already replaced.
        rollback_images(images)
        raise

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


def publish_images(
    images: list[WeatherImage],
    slack_token: str,
    upload_channel: str,
    discord_webhook_url: str,
) -> None:
    """Upload new images to every destination, then commit or roll back the local files.

    Slack and Discord are attempted independently so one outage doesn't block the
    other. If every destination fails, the local files are rolled back so the next
    run sees the images as new and retries. If only some fail, the images are
    kept (rolling back would re-post to the destinations that worked on every
    run), and the error is still raised so the failure is visible.
    """
    new_images = [image for image in images if image.is_new]
    if not new_images:
        logger.info("No new images to upload")
        return

    logger.info(f"Uploading {len(new_images)} new images")
    failures: list[Exception] = []
    destinations = (
        ("Slack", lambda: upload_files_to_slack(new_images, slack_token, upload_channel)),
        ("Discord", lambda: upload_files_to_discord(new_images, discord_webhook_url)),
    )
    for destination, upload in destinations:
        try:
            upload()
        except Exception as exc:
            logger.error(f"{destination} upload failed: {exc}")
            failures.append(exc)

    if len(failures) == len(destinations):
        rollback_images(new_images)
        raise failures[0]

    commit_images(new_images)
    if failures:
        raise failures[0]


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


def has_image_files(image_dir: str) -> bool:
    """Check whether any tracked PNG or GIF files remain in the directory."""
    return any(
        filename.endswith((".png", ".gif")) for filename in os.listdir(image_dir)
    )


def process_and_publish_static_image(
    image_file_path: str,
    rss_file_path: str,
    threshold: float,
    slack_token: str,
    upload_channel: str,
    discord_webhook_url: str,
    pixel_tolerance: int = PIXEL_TOLERANCE,
) -> WeatherImage:
    """Fetch the static outlook image, update the RSS feed, and upload it if it changed."""
    static_url = "https://www.nhc.noaa.gov/xgtwo/two_atl_7d0.png"
    static_image = process_single_image(
        static_url,
        "two_atl_7d0",
        image_file_path,
        threshold,
        pixel_tolerance=pixel_tolerance,
    )
    static_image.image_type = "static"

    generate_rss_feed(static_image, rss_file_path)

    if static_image.is_new:
        logger.info("Static image has been updated - uploading")
        publish_images(
            [static_image], slack_token, upload_channel, discord_webhook_url
        )
    else:
        logger.info("Static image unchanged - no upload needed")

    return static_image


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


def parse_pixel_tolerance(
    raw_tolerance: int | str | None, parser: argparse.ArgumentParser
) -> int:
    """Parse the per-pixel tolerance from CLI/env values (an integer from 0 to 255)."""
    if raw_tolerance is None:
        return PIXEL_TOLERANCE

    try:
        tolerance = int(raw_tolerance)
    except ValueError:
        parser.error(
            f"Invalid PIXEL_TOLERANCE value {raw_tolerance!r}. Must be an integer from 0 to 255."
        )

    if not 0 <= tolerance <= 255:
        parser.error(
            f"Invalid PIXEL_TOLERANCE value {raw_tolerance!r}. Must be an integer from 0 to 255."
        )

    return tolerance


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


def get_config_pixel_tolerance(
    arg_value: int | str | None, env_key: str
) -> int | str | None:
    """Get pixel tolerance config from CLI arg first, then environment variable."""
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
    _ = parser.add_argument(
        "--pixel-tolerance",
        type=int,
        help=(
            "Ignore pixels that differ by at most this many gray levels (0-255) when "
            f"comparing images (default: {PIXEL_TOLERANCE})."
        ),
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
        pixel_tolerance=getattr(namespace, "pixel_tolerance", None),
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
    pixel_tolerance = parse_pixel_tolerance(
        get_config_pixel_tolerance(args.pixel_tolerance, "PIXEL_TOLERANCE"), parser
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

        all_images = fetch_all_weather_images(
            soup, image_file_path_str, threshold, pixel_tolerance
        )

        static_image = next(
            (img for img in all_images if img.image_type == "static"), None
        )
        if static_image:
            generate_rss_feed(static_image, rss_file_path_str)

        publish_images(
            all_images, slack_token_str, upload_channel_str, discord_webhook_url_str
        )

        logger.info(f"Processing complete - handled {len(all_images)} total images")
        return

    if not has_formation_chance(soup):
        if not has_image_files(image_file_path_str):
            logger.info(
                "No named storms, no formation chance, and no images remaining - nothing to do"
            )
            return

        logger.info(
            "No named storms and no formation chance - posting final outlook update before clearing images"
        )
        delete_storm_images(image_file_path_str)

        _ = process_and_publish_static_image(
            image_file_path_str,
            rss_file_path_str,
            FINAL_UPDATE_THRESHOLD,
            slack_token_str,
            upload_channel_str,
            discord_webhook_url_str,
            pixel_tolerance,
        )

        logger.info("Deleting all image files")
        delete_images(image_file_path_str)
        return

    logger.info(
        "No named storms, but formation chance detected - checking static image only"
    )

    delete_storm_images(image_file_path_str)

    _ = process_and_publish_static_image(
        image_file_path_str,
        rss_file_path_str,
        threshold,
        slack_token_str,
        upload_channel_str,
        discord_webhook_url_str,
        pixel_tolerance,
    )

    logger.info("Processing complete - handled static image only")


if __name__ == "__main__":
    main()
