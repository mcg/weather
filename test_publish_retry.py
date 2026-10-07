"""Tests that a changed image is retried until it has actually been published.

Background: the HTTP cache (requests_cache) can already hold the newest NOAA
image while the local copy is stale, e.g. because an earlier run saved the image
and then failed before uploading it. These tests make sure such an image is
still published on a later run.
"""

import io
import os
import shutil
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests
from PIL import Image

from weather import (
    WeatherImage,
    commit_images,
    fetch_all_weather_images,
    process_single_image,
    publish_images,
    rollback_images,
)

URL = "https://www.nhc.noaa.gov/xgtwo/two_atl_7d0.png"


def png_bytes(color: str, size: tuple[int, int] = (100, 100)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color=color).save(buffer, "PNG")
    return buffer.getvalue()


def response(content: bytes, from_cache: bool = False) -> Mock:
    resp = Mock()
    resp.content = content
    resp.from_cache = from_cache
    return resp


def read(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


class RetryTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.mkdtemp()
        self.png = os.path.join(self.dir, "two_atl_7d0.png")
        self.gif = os.path.join(self.dir, "two_atl_7d0.gif")

    def tearDown(self) -> None:
        shutil.rmtree(self.dir)

    def process(self, content: bytes, from_cache: bool = False) -> WeatherImage:
        with patch("weather.requests.get", return_value=response(content, from_cache)):
            return process_single_image(URL, "two_atl_7d0", self.dir)

    def leftover_files(self) -> list[str]:
        return sorted(os.listdir(self.dir))


class TestProcessSingleImage(RetryTestCase):
    def test_changed_image_served_from_http_cache_is_still_new(self) -> None:
        """The cache already holding the new body must not hide it from us."""
        _ = self.process(png_bytes("blue"))
        new_body = png_bytes("red")

        result = self.process(new_body, from_cache=True)

        self.assertTrue(result.is_new)
        self.assertEqual(read(self.png), new_body)

    def test_unchanged_image_served_from_http_cache_is_not_new(self) -> None:
        body = png_bytes("blue")
        _ = self.process(body)

        result = self.process(body, from_cache=True)

        self.assertFalse(result.is_new)
        self.assertEqual(self.leftover_files(), ["two_atl_7d0.gif", "two_atl_7d0.png"])

    def test_http_error_does_not_touch_local_files(self) -> None:
        _ = self.process(png_bytes("blue"))
        before = {name: read(os.path.join(self.dir, name)) for name in self.leftover_files()}

        error_response = response(b"<html>Not Found</html>")
        error_response.raise_for_status.side_effect = requests.HTTPError("404")
        with patch("weather.requests.get", return_value=error_response):
            with self.assertRaises(requests.HTTPError):
                _ = process_single_image(URL, "two_atl_7d0", self.dir)

        after = {name: read(os.path.join(self.dir, name)) for name in self.leftover_files()}
        self.assertEqual(before, after)

    def test_undecodable_body_restores_previous_files(self) -> None:
        _ = self.process(png_bytes("blue"))
        before = {name: read(os.path.join(self.dir, name)) for name in self.leftover_files()}

        with self.assertRaises(Exception):
            _ = self.process(b"<html>not an image</html>")

        after = {name: read(os.path.join(self.dir, name)) for name in self.leftover_files()}
        self.assertEqual(before, after)

    def test_undecodable_first_image_leaves_nothing_behind(self) -> None:
        with self.assertRaises(Exception):
            _ = self.process(b"<html>not an image</html>")

        self.assertEqual(self.leftover_files(), [])

    def test_new_image_records_backups_of_replaced_files(self) -> None:
        old_body = png_bytes("blue")
        _ = self.process(old_body)

        result = self.process(png_bytes("red"))

        self.assertIsNotNone(result.previous_png)
        self.assertIsNotNone(result.previous_gif)
        assert result.previous_png is not None
        self.assertEqual(read(result.previous_png), old_body)


class TestRollbackAndCommit(RetryTestCase):
    def test_rollback_restores_previous_files(self) -> None:
        old_body = png_bytes("blue")
        _ = self.process(old_body)
        old_gif = read(self.gif)
        new_image = self.process(png_bytes("red"))

        rollback_images([new_image])

        self.assertEqual(read(self.png), old_body)
        self.assertEqual(read(self.gif), old_gif)
        self.assertEqual(self.leftover_files(), ["two_atl_7d0.gif", "two_atl_7d0.png"])

    def test_rollback_of_first_ever_image_removes_it(self) -> None:
        new_image = self.process(png_bytes("red"))

        rollback_images([new_image])

        self.assertEqual(self.leftover_files(), [])

    def test_rollback_ignores_images_that_are_not_new(self) -> None:
        body = png_bytes("blue")
        _ = self.process(body)
        unchanged = self.process(body)

        rollback_images([unchanged])

        self.assertEqual(read(self.png), body)

    def test_commit_removes_backups(self) -> None:
        _ = self.process(png_bytes("blue"))
        new_image = self.process(png_bytes("red"))

        commit_images([new_image])

        self.assertEqual(self.leftover_files(), ["two_atl_7d0.gif", "two_atl_7d0.png"])

    def test_rollback_and_commit_tolerate_missing_files(self) -> None:
        image = WeatherImage("x", "missing.png", "missing.gif", "url", True, "static")

        rollback_images([image])
        commit_images([image])


class TestPublishImages(RetryTestCase):
    def new_image(self) -> WeatherImage:
        _ = self.process(png_bytes("blue"))
        return self.process(png_bytes("red"))

    @patch("weather.upload_files_to_discord")
    @patch("weather.upload_files_to_slack")
    def test_success_commits(self, mock_slack: Mock, mock_discord: Mock) -> None:
        image = self.new_image()

        publish_images([image], "token", "channel", "webhook")

        mock_slack.assert_called_once_with([image], "token", "channel")
        mock_discord.assert_called_once_with([image], "webhook")
        self.assertEqual(self.leftover_files(), ["two_atl_7d0.gif", "two_atl_7d0.png"])

    @patch("weather.upload_files_to_discord")
    @patch("weather.upload_files_to_slack")
    def test_nothing_to_upload(self, mock_slack: Mock, mock_discord: Mock) -> None:
        unchanged = WeatherImage("x", "x.png", "x.gif", "url", False, "static")

        publish_images([unchanged], "token", "channel", "webhook")

        mock_slack.assert_not_called()
        mock_discord.assert_not_called()

    @patch("weather.upload_files_to_discord")
    @patch("weather.upload_files_to_slack")
    def test_all_destinations_failing_rolls_back_and_raises(
        self, mock_slack: Mock, mock_discord: Mock
    ) -> None:
        old_body = png_bytes("blue")
        _ = self.process(old_body)
        image = self.process(png_bytes("red"))
        mock_slack.side_effect = RuntimeError("slack down")
        mock_discord.side_effect = RuntimeError("discord down")

        with self.assertRaisesRegex(RuntimeError, "slack down"):
            publish_images([image], "token", "channel", "webhook")

        self.assertEqual(read(self.png), old_body)

    @patch("weather.upload_files_to_discord")
    @patch("weather.upload_files_to_slack")
    def test_slack_failure_does_not_skip_discord(
        self, mock_slack: Mock, mock_discord: Mock
    ) -> None:
        image = self.new_image()
        new_body = read(self.png)
        mock_slack.side_effect = RuntimeError("slack down")

        with self.assertRaisesRegex(RuntimeError, "slack down"):
            publish_images([image], "token", "channel", "webhook")

        mock_discord.assert_called_once()
        # Discord got the image, so it is kept rather than re-posted on every run.
        self.assertEqual(read(self.png), new_body)
        self.assertEqual(self.leftover_files(), ["two_atl_7d0.gif", "two_atl_7d0.png"])


class TestFetchAllWeatherImages(RetryTestCase):
    @patch("weather.find_cyclones_in_feed")
    def test_cone_failure_does_not_block_static_image(self, mock_find: Mock) -> None:
        mock_find.return_value = [
            {
                "storm_name": "Isaias",
                "storm_type": "Hurricane",
                "image_url": "https://example.com/cone.png",
                "speg_model": None,
            }
        ]
        static_body = png_bytes("red")

        def fake_get(url: str) -> Mock:
            if url == URL:
                return response(static_body)
            not_found = response(b"<html>Not Found</html>")
            not_found.raise_for_status.side_effect = requests.HTTPError("404")
            return not_found

        with patch("weather.requests.get", side_effect=fake_get):
            images = fetch_all_weather_images(Mock(), self.dir)

        self.assertEqual([image.image_type for image in images], ["static"])
        self.assertTrue(images[0].is_new)

    @patch("weather.find_cyclones_in_feed")
    def test_unexpected_failure_rolls_back_static_image(self, mock_find: Mock) -> None:
        old_body = png_bytes("blue")
        _ = self.process(old_body)
        mock_find.side_effect = RuntimeError("feed parsing blew up")

        with patch("weather.requests.get", return_value=response(png_bytes("red"))):
            with self.assertRaises(RuntimeError):
                _ = fetch_all_weather_images(Mock(), self.dir)

        self.assertEqual(read(self.png), old_body)


class TestStuckImageScenario(RetryTestCase):
    """The original bug: local NINE image, NOAA/HTTP cache has ISAIAS, first run fails."""

    @patch("weather.upload_files_to_discord")
    @patch("weather.upload_files_to_slack")
    @patch("weather.find_cyclones_in_feed")
    def test_image_is_posted_on_a_later_run_after_failed_upload(
        self, mock_find: Mock, mock_slack: Mock, mock_discord: Mock
    ) -> None:
        mock_find.return_value = []
        nine = png_bytes("blue")
        isaias = png_bytes("red")
        _ = self.process(nine)

        # Run 1: NOAA returns the new image, but both uploads fail.
        mock_slack.side_effect = RuntimeError("slack down")
        mock_discord.side_effect = RuntimeError("discord down")
        with patch("weather.requests.get", return_value=response(isaias)):
            images = fetch_all_weather_images(Mock(), self.dir)
            with self.assertRaises(RuntimeError):
                publish_images(images, "token", "channel", "webhook")

        # Run 2: the HTTP cache now serves the same body (from_cache=True), and
        # uploads work. The image must be posted this time.
        mock_slack.side_effect = None
        mock_discord.side_effect = None
        mock_slack.reset_mock()
        mock_discord.reset_mock()
        with patch("weather.requests.get", return_value=response(isaias, from_cache=True)):
            images = fetch_all_weather_images(Mock(), self.dir)
            publish_images(images, "token", "channel", "webhook")

        self.assertTrue(images[0].is_new)
        mock_slack.assert_called_once()
        mock_discord.assert_called_once()
        self.assertEqual(read(self.png), isaias)

        # Run 3: nothing changed, so nothing is posted again.
        mock_slack.reset_mock()
        mock_discord.reset_mock()
        with patch("weather.requests.get", return_value=response(isaias, from_cache=True)):
            images = fetch_all_weather_images(Mock(), self.dir)
            publish_images(images, "token", "channel", "webhook")

        self.assertFalse(images[0].is_new)
        mock_slack.assert_not_called()
        mock_discord.assert_not_called()


if __name__ == "__main__":
    _ = unittest.main()
