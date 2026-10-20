"""Coordinate the weather lifecycle without mixing CLI or transport configuration."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import timedelta

import requests
from bs4 import BeautifulSoup
from requests_cache import CachedSession

from .config import DEFAULT_THRESHOLD, PIXEL_TOLERANCE, Settings
from .feeds import (
    fetch_xml_feed,
    find_cyclones_in_feed,
    generate_rss_feed,
    has_formation_chance,
)
from .images import (
    delete_images,
    delete_storm_images,
    has_image_files,
    process_single_image,
    rollback_images,
)
from .models import ImageSpec, WeatherImage
from .publishing import publish_images

logger = logging.getLogger(__name__)
OUTLOOK = ImageSpec(
    "two_atl_7d0", "https://www.nhc.noaa.gov/xgtwo/two_atl_7d0.png", "static"
)
# Preserve the final-update policy: no changed-pixel fraction is too small, while
# the configured per-pixel noise tolerance still applies.
FINAL_UPDATE_THRESHOLD = 0.0


def _image_specs(soup: BeautifulSoup) -> Iterator[ImageSpec]:
    yield OUTLOOK
    for cyclone in find_cyclones_in_feed(soup):
        name = cyclone["storm_name"]
        yield ImageSpec(
            f"{name}_5day_cone_with_line_and_wind", cyclone["image_url"], "cone"
        )
        if cyclone["speg_model"]:
            yield ImageSpec(
                f"{name}_hurricane_models",
                f"https://web.uwm.edu/hurricane-models/models/{cyclone['speg_model']}.png",
                "speg",
            )


def _process_spec(
    spec: ImageSpec,
    image_dir: str,
    threshold: float,
    pixel_tolerance: int,
    session: requests.Session | None,
) -> WeatherImage:
    image = process_single_image(
        spec.url,
        spec.name,
        image_dir,
        threshold,
        pixel_tolerance=pixel_tolerance,
        **({"session": session} if session is not None else {}),
    )
    image.image_type = spec.image_type
    return image


def fetch_all_weather_images(
    soup: BeautifulSoup,
    image_dir: str,
    threshold: float = DEFAULT_THRESHOLD,
    pixel_tolerance: int = PIXEL_TOLERANCE,
    *,
    session: requests.Session | None = None,
) -> list[WeatherImage]:
    """Prepare images, isolating storm-image failures and rolling back fatal errors."""
    images: list[WeatherImage] = []
    try:
        for spec in _image_specs(soup):
            try:
                images.append(
                    _process_spec(spec, image_dir, threshold, pixel_tolerance, session)
                )
            except Exception:
                if spec.image_type == "static":
                    raise
                logger.warning(
                    "Failed to fetch %s image: %s",
                    spec.image_type,
                    spec.name,
                    exc_info=True,
                )
    except Exception:
        rollback_images(images)
        raise
    return images


def _generate_and_publish(
    images: list[WeatherImage],
    rss_file_path: str,
    slack_token: str,
    upload_channel: str,
    discord_webhook_url: str,
) -> None:
    static = next((image for image in images if image.image_type == "static"), None)
    try:
        if static is not None:
            generate_rss_feed(static, rss_file_path)
    except Exception:
        # No destination has been attempted yet; all prepared images need retry.
        rollback_images(images)
        raise
    # Publishing owns commit/rollback from this point, including partial success.
    publish_images(images, slack_token, upload_channel, discord_webhook_url)


def process_and_publish_static_image(
    image_file_path: str,
    rss_file_path: str,
    threshold: float,
    slack_token: str,
    upload_channel: str,
    discord_webhook_url: str,
    pixel_tolerance: int = PIXEL_TOLERANCE,
    *,
    session: requests.Session | None = None,
) -> WeatherImage:
    image = _process_spec(OUTLOOK, image_file_path, threshold, pixel_tolerance, session)
    _generate_and_publish(
        [image], rss_file_path, slack_token, upload_channel, discord_webhook_url
    )
    return image


def run(settings: Settings, *, session: requests.Session | None = None) -> None:
    """Run once with a caller-owned session or an explicitly scoped HTTP cache."""
    settings.image_file_path.mkdir(parents=True, exist_ok=True)
    settings.rss_file_path.parent.mkdir(parents=True, exist_ok=True)
    if session is None:
        with CachedSession(
            "weather_cache",
            cache_control=True,
            urls_expire_after={
                "https://web.uwm.edu/hurricane-models/models/*": timedelta(hours=1)
            },
        ) as cached_session:
            _run(settings, cached_session)
    else:
        _run(settings, session)


def _run(settings: Settings, session: requests.Session) -> None:
    image_dir = str(settings.image_file_path)
    rss_path = str(settings.rss_file_path)
    active_storm_count, soup = fetch_xml_feed(session=session)
    if active_storm_count > 0:
        images = fetch_all_weather_images(
            soup,
            image_dir,
            settings.threshold,
            settings.pixel_tolerance,
            session=session,
        )
        _generate_and_publish(
            images,
            rss_path,
            settings.slack_token,
            settings.upload_channel,
            settings.discord_webhook_url,
        )
        logger.info("Processing complete - handled %s total images", len(images))
        return

    final_update = not has_formation_chance(soup)
    if final_update and not has_image_files(image_dir):
        logger.info("No storms, formation chance, or images remaining - nothing to do")
        return

    delete_storm_images(image_dir)
    _ = process_and_publish_static_image(
        image_dir,
        rss_path,
        FINAL_UPDATE_THRESHOLD if final_update else settings.threshold,
        settings.slack_token,
        settings.upload_channel,
        settings.discord_webhook_url,
        settings.pixel_tolerance,
        session=session,
    )
    if final_update:
        delete_images(image_dir)
    logger.info("Processing complete - handled static image only")
