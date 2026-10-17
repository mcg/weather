"""Compare, prepare, and manage local weather images and their backups."""

from __future__ import annotations

import logging
import os
import shutil

import requests
from PIL import Image, ImageChops, ImageSequence

from .config import DEFAULT_THRESHOLD, PIXEL_TOLERANCE
from .models import WeatherImage

logger = logging.getLogger(__name__)
BACKUP_SUFFIX = ".prev"


def images_are_different(
    new_image_path: str,
    existing_image_path: str,
    threshold: float = DEFAULT_THRESHOLD,
    pixel_tolerance: int = PIXEL_TOLERANCE,
) -> bool:
    """Compare the fraction of pixels changed by more than ``pixel_tolerance``."""
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

            new_rgb = new_img.convert("RGB") if new_img.mode != "RGB" else new_img
            existing_rgb = (
                existing_img.convert("RGB")
                if existing_img.mode != "RGB"
                else existing_img
            )
            diff = ImageChops.difference(new_rgb, existing_rgb)
            histogram = diff.convert("L").histogram()
            total_pixels = sum(histogram)
            if total_pixels == 0:
                return False

            # Ignore identical pixels and faint rendering noise.
            different_pixels = sum(histogram[pixel_tolerance + 1 :])
            difference_percentage = different_pixels / total_pixels
            logger.info(
                "Image comparison: %.4f (%.2f%%) pixels differ by more than %s/255 (threshold %.4f / %.2f%%)",
                difference_percentage,
                difference_percentage * 100,
                pixel_tolerance,
                threshold,
                threshold * 100,
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


def stash_file(path: str, move: bool) -> str | None:
    """Back up ``path`` to ``path.prev``, or return None if it does not exist."""
    if not os.path.exists(path):
        return None
    backup_path = f"{path}{BACKUP_SUFFIX}"
    if move:
        os.replace(path, backup_path)
    else:
        backup_existed = os.path.exists(backup_path)
        try:
            _ = shutil.copy2(path, backup_path)
        except Exception:
            # A failed copy can leave a truncated backup, but the source is intact.
            if not backup_existed:
                _remove_preparation_file(backup_path)
            raise
    return backup_path


def restore_file(path: str, backup_path: str | None) -> None:
    """Restore a backup, or remove a newly created file with no predecessor."""
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
    """Restore replaced files so the next run retries unpublished images."""
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


def _remove_preparation_file(path: str) -> None:
    """Clean up without hiding the original preparation failure."""
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError as exc:
        logger.error(f"Failed to clean up {path}: {exc}")


def process_single_image(
    url: str,
    base_name: str,
    image_dir: str,
    threshold: float = DEFAULT_THRESHOLD,
    pixel_tolerance: int = PIXEL_TOLERANCE,
    *,
    session: requests.Session | None = None,
) -> WeatherImage:
    """Download and compare an image, keeping backups until publishing succeeds.

    Compare cached responses too: an HTTP cache hit does not mean that the body
    was successfully published on a previous run.
    """
    logger.info(f"Processing image: {base_name}")
    client = session or requests
    response = client.get(url, timeout=30)
    response.raise_for_status()
    if bool(getattr(response, "from_cache", False)):
        logger.info(f"{base_name} served from HTTP cache - comparing with local copy")

    png_path = os.path.join(image_dir, f"{base_name}.png")
    gif_path = os.path.join(image_dir, f"{base_name}.gif")
    temp_path = f"{png_path}.tmp"
    prepared: list[tuple[str, str | None]] = []
    try:
        with open(temp_path, "wb") as temp_file:
            _ = temp_file.write(response.content)
        if not images_are_different(temp_path, png_path, threshold, pixel_tolerance):
            logger.info(f"{base_name} unchanged")
            os.remove(temp_path)
            return WeatherImage(base_name, png_path, gif_path, url, False, "processed")

        logger.info(f"{base_name} is new/different")
        previous_png = stash_file(png_path, move=True)
        prepared.append((png_path, previous_png))
        previous_gif = stash_file(gif_path, move=False)
        prepared.append((gif_path, previous_gif))
        os.rename(temp_path, png_path)
        update_gif(png_path, gif_path)
    except Exception:
        for path, backup_path in prepared:
            try:
                restore_file(path, backup_path)
            except OSError as exc:
                logger.error(f"Failed to roll back {path}: {exc}")
        _remove_preparation_file(temp_path)
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


def gif_has_loop(gif_path: str) -> bool:
    """Whether a GIF has multiple frames rather than duplicating its PNG."""
    try:
        with Image.open(gif_path) as gif:
            return getattr(gif, "n_frames", 1) > 1
    except OSError, ValueError:
        return False


def _delete_image_files(image_dir: str, *, preserve_static: bool) -> None:
    label = "storm image" if preserve_static else "image"
    logger.info(
        f"Deleting {'storm images' if preserve_static else 'images'} from {image_dir}"
    )
    count = 0
    for filename in os.listdir(image_dir):
        stem, extension = os.path.splitext(filename)
        if extension not in (".png", ".gif"):
            continue
        if preserve_static and stem == "two_atl_7d0":
            continue
        try:
            os.remove(os.path.join(image_dir, filename))
            count += 1
            if preserve_static:
                logger.debug(f"Deleted storm image: {filename}")
        except OSError as exc:
            logger.error(f"Failed to delete {filename}: {exc}")
    logger.info(f"Deleted {count} {label} files")


def delete_images(image_dir: str) -> None:
    """Delete all PNG and GIF files in the directory."""
    _delete_image_files(image_dir, preserve_static=False)


def delete_storm_images(image_dir: str) -> None:
    """Delete storm PNG and GIF files, keeping the exact static outlook stem."""
    _delete_image_files(image_dir, preserve_static=True)


def has_image_files(image_dir: str) -> bool:
    """Check whether any tracked PNG or GIF files remain in the directory."""
    return any(
        filename.endswith((".png", ".gif")) for filename in os.listdir(image_dir)
    )
