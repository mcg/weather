"""Publish prepared weather images to Slack and Discord."""

from __future__ import annotations

import logging
from contextlib import ExitStack
from typing import Literal

from discord import File, SyncWebhook
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from .images import commit_images, gif_has_loop, rollback_images
from .models import WeatherImage

logger = logging.getLogger(__name__)


def _image_attachments(image: WeatherImage) -> list[tuple[str, Literal["png", "gif"]]]:
    """Select path/format pairs consistently for both destinations."""
    if image.image_type not in ("static", "cone", "speg"):
        return []
    attachments: list[tuple[str, Literal["png", "gif"]]] = [(image.png_path, "png")]
    if gif_has_loop(image.gif_path):
        attachments.append((image.gif_path, "gif"))
    return attachments


def upload_files_to_slack(
    images: list[WeatherImage], slack_token: str, upload_channel: str
) -> None:
    """Upload supported images in a single Slack batch."""
    client = WebClient(token=slack_token)
    file_uploads: list[dict[str, str]] = []
    for image in images:
        if image.image_type == "static":
            titles = {"png": "Seven-Day Outlook", "gif": "Last 10 maps"}
        elif image.image_type == "cone":
            titles = {"png": image.name, "gif": f"{image.name} Loop"}
        elif image.image_type == "speg":
            titles = {"png": f"{image.name} Models", "gif": f"{image.name} Models Loop"}
        else:
            continue
        for path, image_format in _image_attachments(image):
            file_uploads.append({"file": path, "title": titles[image_format]})

    if not file_uploads:
        return
    try:
        logger.info(f"Uploading {len(file_uploads)} files to Slack")
        _ = client.files_upload_v2(  # pyright: ignore[reportUnknownMemberType] -- SDK exposes untyped **kwargs.
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
    """Upload each supported image and its optional loop in one Discord message."""
    webhook = SyncWebhook.from_url(discord_webhook_url)
    for image in images:
        if image.image_type == "static":
            content = "Seven-Day Outlook and Map Loop"
            filename_stem = "outlook"
        elif image.image_type == "cone":
            content = f"**{image.name} - NHC Cone**"
            filename_stem = image.name
        elif image.image_type == "speg":
            content = f"**{image.name} - Hurricane Models**"
            filename_stem = f"{image.name}_models"
        else:
            continue

        with ExitStack() as stack:
            files: list[File] = []
            for path, image_format in _image_attachments(image):
                stream = stack.enter_context(open(path, "rb"))
                files.append(File(stream, filename=f"{filename_stem}.{image_format}"))
            _ = webhook.send(content=content, files=files)
    logger.info("Successfully uploaded to Discord")


def publish_images(
    images: list[WeatherImage],
    slack_token: str,
    upload_channel: str,
    discord_webhook_url: str,
) -> None:
    """Attempt both destinations; roll back only when the entire batch fails.

    Partial success commits local images to avoid repeatedly posting to the
    working destination, while still raising the first failure for visibility.
    """
    new_images = [image for image in images if image.is_new]
    if not new_images:
        logger.info("No new images to upload")
        return

    logger.info(f"Uploading {len(new_images)} new images")
    failures: list[Exception] = []
    destinations = (
        (
            "Slack",
            lambda: upload_files_to_slack(new_images, slack_token, upload_channel),
        ),
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
