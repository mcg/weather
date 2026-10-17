"""Small data structures shared by the weather pipeline."""

from dataclasses import dataclass
from typing import Literal, TypedDict

ImageKind = Literal["static", "cone", "speg", "processed"]


class CycloneInfo(TypedDict):
    storm_name: str
    storm_type: str
    image_url: str
    speg_model: str | None


@dataclass(frozen=True, slots=True)
class ImageSpec:
    name: str
    url: str
    image_type: ImageKind


@dataclass
class WeatherImage:
    """A processed image and the backups held until delivery is resolved."""

    name: str
    png_path: str
    gif_path: str
    url: str
    is_new: bool
    image_type: ImageKind
    previous_png: str | None = None
    previous_gif: str | None = None
