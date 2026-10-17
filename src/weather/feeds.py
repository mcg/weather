"""Fetch NHC feeds, extract cyclone metadata, and generate the image RSS feed."""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Protocol, cast

import requests
from bs4 import BeautifulSoup, Tag
from feedgen.feed import FeedGenerator  # pyright: ignore[reportMissingTypeStubs]

from .models import CycloneInfo, WeatherImage

logger = logging.getLogger(__name__)


# feedgen has no type stubs; keep its untyped API at this narrow boundary.
class _FeedMetadata(Protocol):
    def title(self, title: str) -> object: ...
    def description(self, description: str) -> object: ...
    def link(self, *, href: str) -> object: ...


class _FeedEntry(_FeedMetadata, Protocol):
    def enclosure(self, url: str, length: int, type: str) -> object: ...
    def id(self, id: str) -> object: ...


class _Feed(_FeedMetadata, Protocol):
    def add_entry(self) -> _FeedEntry: ...
    def rss_file(self, filename: str) -> None: ...


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
# Summary GUIDs end in YYYYMMDDHHMM; graphics filenames use DDHHMM.
SUMMARY_GUID_STAMP_PATTERN = re.compile(r"-\d{6}(\d{6})$")
SUMMARY_LINK_STAMP_PATTERN = re.compile(r"/(\d{6})\.shtml")
OUTLOOK_TITLE_PATTERN = re.compile(r"Tropical Weather Outlook", re.IGNORECASE)
FORMATION_CHANCE_PATTERN = re.compile(r"Formation chance", re.IGNORECASE)

GRAPHICS_BASIN_PREFIXES = {"AL": "AT"}
CONE_URL_TEMPLATE = (
    "https://www.nhc.noaa.gov/storm_graphics/{graphics_id}/refresh/"
    "{atcf}_5day_cone+png/{stamp}_5day_cone.png"
)


@dataclass(frozen=True, slots=True)
class _SummaryMetadata:
    title: str
    name: str | None
    atcf: str
    stamp: str | None
    speg_eligible: bool


def fetch_xml_feed(
    *, session: requests.Session | None = None
) -> tuple[int, BeautifulSoup]:
    """Fetch and parse the NOAA XML feed using an optional caller-owned session."""
    logger.info("Fetching XML feed from NOAA")
    client = requests if session is None else session
    response = client.get("https://www.nhc.noaa.gov/index-at.xml", timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.content.decode("utf-8"), "xml")
    active_storm_count = len(soup.find_all("title", string=STORM_PATTERN))
    if active_storm_count:
        logger.info("Found %s active storms", active_storm_count)
    else:
        logger.info("No active storms found")
    return active_storm_count, soup


def extract_storm_info(title_source: str | object) -> dict[str, str] | None:
    """Extract storm information from a title string, tag, or title-like object."""
    if isinstance(title_source, str):
        title_text = title_source
    elif isinstance(title_source, Tag):
        title_text = title_source.get_text()
    else:
        title_text = str(getattr(title_source, "text", ""))
    match = STORM_NAME_PATTERN.search(title_text)
    if not match:
        return None
    storm_type = match.group(1).strip()
    storm_name = match.group(2).strip()
    if storm_type not in ("Hurricane", "Tropical Storm", "Tropical Depression"):
        return None
    return {"name": storm_name, "type": storm_type}


def _index_summaries(soup: BeautifulSoup) -> list[_SummaryMetadata]:
    summaries: list[_SummaryMetadata] = []
    for title in soup.find_all("title", string=SUMMARY_TITLE_PATTERN):
        item = title.find_parent("item")
        if not isinstance(item, Tag):
            continue
        cyclone = item.find("nhc:Cyclone")
        if not isinstance(cyclone, Tag):
            continue
        atcf = cyclone.find("nhc:atcf")
        if not isinstance(atcf, Tag):
            continue
        name = cyclone.find("nhc:name")
        stamp = None
        for tag_name, pattern in (
            ("link", SUMMARY_LINK_STAMP_PATTERN),
            ("guid", SUMMARY_GUID_STAMP_PATTERN),
        ):
            tag = item.find(tag_name)
            if isinstance(tag, Tag):
                match = pattern.search(tag.get_text().strip())
                if match:
                    stamp = match.group(1)
                    break
        title_text = title.get_text()
        summaries.append(
            _SummaryMetadata(
                title=title_text,
                name=name.get_text() if isinstance(name, Tag) else None,
                atcf=atcf.get_text(),
                stamp=stamp,
                speg_eligible=SPEG_PATTERN.search(title_text) is not None,
            )
        )
    return summaries


def _find_speg_model(summaries: list[_SummaryMetadata], storm_name: str) -> str | None:
    name = storm_name.lower()
    for summary in summaries:
        if not summary.speg_eligible or name not in summary.title.lower():
            continue
        # SPEG historically requires an exact, untrimmed name match when present.
        if summary.name is not None and summary.name.lower() != name:
            continue
        return summary.atcf.lower()
    return None


def find_speg_model(soup: BeautifulSoup, storm_name: str) -> str | None:
    """Find the SPEG model ID for a tropical storm or hurricane."""
    return _find_speg_model(_index_summaries(soup), storm_name)


def build_cone_url(atcf: str, stamp: str) -> str | None:
    """Build an NHC 5-day cone URL from an ATCF ID and a DDHHMM stamp."""
    match = ATCF_PATTERN.match(atcf.strip())
    if not match:
        return None
    graphics_basin = GRAPHICS_BASIN_PREFIXES.get(match.group("basin").upper())
    if graphics_basin is None:
        return None
    return CONE_URL_TEMPLATE.format(
        graphics_id=f"{graphics_basin}{match.group('number')}",
        atcf=atcf.strip().upper(),
        stamp=stamp,
    )


def _find_cone_url(summaries: list[_SummaryMetadata], storm_name: str) -> str | None:
    name = storm_name.lower()
    for summary in summaries:
        if name not in summary.title.lower():
            continue
        if summary.name is not None and summary.name.strip().lower() != name:
            continue
        if summary.stamp is None:
            continue
        cone_url = build_cone_url(summary.atcf, summary.stamp)
        if cone_url:
            return cone_url
    return None


def find_cone_url(soup: BeautifulSoup, storm_name: str) -> str | None:
    """Build a cone URL from a summary's ATCF ID and link (or fallback GUID)."""
    return _find_cone_url(_index_summaries(soup), storm_name)


def find_cyclones_in_feed(soup: BeautifulSoup) -> list[CycloneInfo]:
    """Find cyclones, preferring embedded cone images over summary-derived URLs."""
    logger.info("Searching for cyclones in feed")
    summaries = _index_summaries(soup)
    cyclones: list[CycloneInfo] = []
    for title in soup.find_all("title", string=STORM_PATTERN):
        storm_info = extract_storm_info(title)
        if not storm_info:
            continue
        description = title.find_next("description")
        if not isinstance(description, Tag):
            continue
        cdata_soup = BeautifulSoup(description.get_text(), "html.parser")
        img = cdata_soup.find(
            "img",
            src=lambda src: (
                isinstance(src, str) and "5day_cone_with_line_and_wind" in src
            ),
        )
        image_url: str | None = None
        if isinstance(img, Tag):
            src = img.get("src")
            if isinstance(src, str):
                image_url = src
        if image_url is None:
            image_url = _find_cone_url(summaries, storm_info["name"])
        if image_url is None:
            logger.warning("No cone image found for %s", storm_info["name"])
            continue
        speg_model = _find_speg_model(summaries, storm_info["name"])
        cyclones.append(
            {
                "storm_name": storm_info["name"],
                "storm_type": storm_info["type"],
                "image_url": image_url,
                "speg_model": speg_model,
            }
        )
        logger.info(
            "Found cyclone: %s (%s) with SPEG model: %s",
            storm_info["name"],
            storm_info["type"],
            speg_model,
        )
    logger.info("Found %s cyclones", len(cyclones))
    return cyclones


def has_formation_chance(soup: BeautifulSoup) -> bool:
    """Check whether the first Tropical Weather Outlook mentions formation chance."""
    outlook_title = soup.find("title", string=OUTLOOK_TITLE_PATTERN)
    if not isinstance(outlook_title, Tag):
        return False
    description = outlook_title.find_next("description")
    if not isinstance(description, Tag):
        return False
    found = bool(FORMATION_CHANCE_PATTERN.search(description.get_text()))
    if found:
        logger.info("Formation chance detected in Tropical Weather Outlook")
    else:
        logger.info("No formation chance mentioned in Tropical Weather Outlook")
    return found


def generate_rss_feed(static_image: WeatherImage, rss_file_path: str) -> None:
    """Generate the RSS feed for the static weather image."""
    timestamp = int(time.time())
    img_size = 0
    if os.path.exists(static_image.png_path):
        img_size = os.path.getsize(static_image.png_path)

    fg = cast(_Feed, FeedGenerator())
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
