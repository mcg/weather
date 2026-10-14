import os
import unittest

from bs4 import BeautifulSoup

from weather import build_cone_url, find_cone_url, find_cyclones_in_feed

EXAMPLE_FEED = os.path.join(
    os.path.dirname(__file__), "xml-examples", "index-at-storm.xml"
)

EXPECTED_CONE_URL = (
    "https://www.nhc.noaa.gov/storm_graphics/AT09/refresh/"
    "AL092026_5day_cone+png/070852_5day_cone.png"
)


def load_example_feed() -> BeautifulSoup:
    with open(EXAMPLE_FEED, "rb") as feed:
        return BeautifulSoup(feed.read().decode("utf-8"), "xml")


class TestBuildConeUrl(unittest.TestCase):
    def test_atlantic_storm(self):
        self.assertEqual(build_cone_url("AL092026", "070852"), EXPECTED_CONE_URL)

    def test_lowercase_atcf_is_normalized(self):
        self.assertEqual(build_cone_url("al092026", "070852"), EXPECTED_CONE_URL)

    def test_invalid_atcf_returns_none(self):
        self.assertIsNone(build_cone_url("bogus", "070852"))

    def test_unsupported_basin_returns_none(self):
        self.assertIsNone(build_cone_url("EP092026", "070852"))


class TestFindConeUrl(unittest.TestCase):
    def test_example_feed(self):
        self.assertEqual(find_cone_url(load_example_feed(), "Isaias"), EXPECTED_CONE_URL)

    def test_unknown_storm_returns_none(self):
        self.assertIsNone(find_cone_url(load_example_feed(), "Nobody"))

    def test_stamp_falls_back_to_guid(self):
        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <rss xmlns:nhc="https://www.nhc.noaa.gov"><channel>
        <item>
            <title>Summary for Tropical Storm Isaias (AT4/AL092026)</title>
            <guid isPermaLink="false">summary-al092026-202610070852</guid>
            <nhc:Cyclone>
                <nhc:name>Isaias</nhc:name>
                <nhc:atcf>AL092026</nhc:atcf>
            </nhc:Cyclone>
        </item>
        </channel></rss>"""
        soup = BeautifulSoup(xml, "xml")
        self.assertEqual(find_cone_url(soup, "Isaias"), EXPECTED_CONE_URL)


class TestFindCyclonesWithExampleFeed(unittest.TestCase):
    def test_cone_url_built_when_graphics_item_has_no_cone_image(self):
        cyclones = find_cyclones_in_feed(load_example_feed())

        self.assertEqual(len(cyclones), 1)
        self.assertEqual(cyclones[0]["storm_name"], "Isaias")
        self.assertEqual(cyclones[0]["storm_type"], "Tropical Storm")
        self.assertEqual(cyclones[0]["image_url"], EXPECTED_CONE_URL)
        self.assertEqual(cyclones[0]["speg_model"], "al092026")


if __name__ == "__main__":
    unittest.main()
