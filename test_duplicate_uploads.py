import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from weather import WeatherImage, gif_has_loop, upload_files_to_discord, upload_files_to_slack


class TestDuplicateUploads(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.png_path = os.path.join(self.temp_dir, "cone.png")
        self.single_gif = os.path.join(self.temp_dir, "single.gif")
        self.loop_gif = os.path.join(self.temp_dir, "loop.gif")

        red = Image.new("RGB", (50, 50), "red")
        blue = Image.new("RGB", (50, 50), "blue")
        red.save(self.png_path)
        red.save(self.single_gif, "GIF")
        red.save(self.loop_gif, save_all=True, append_images=[blue], duration=100, loop=0)

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def image(self, gif_path: str, image_type: str = "cone") -> WeatherImage:
        return WeatherImage("Isaias", self.png_path, gif_path, "url", True, image_type)

    def test_gif_has_loop(self):
        self.assertFalse(gif_has_loop(self.single_gif))
        self.assertTrue(gif_has_loop(self.loop_gif))

    def test_gif_has_loop_missing_file(self):
        self.assertFalse(gif_has_loop(os.path.join(self.temp_dir, "missing.gif")))

    @patch("weather.WebClient")
    def test_slack_skips_single_frame_gif(self, mock_client_class):
        client = mock_client_class.return_value

        for image_type in ("static", "cone", "speg"):
            client.reset_mock()
            upload_files_to_slack([self.image(self.single_gif, image_type)], "token", "chan")
            uploads = client.files_upload_v2.call_args.kwargs["file_uploads"]
            self.assertEqual([u["file"] for u in uploads], [self.png_path], image_type)

    @patch("weather.WebClient")
    def test_slack_includes_multi_frame_gif(self, mock_client_class):
        client = mock_client_class.return_value

        upload_files_to_slack([self.image(self.loop_gif)], "token", "chan")

        uploads = client.files_upload_v2.call_args.kwargs["file_uploads"]
        self.assertEqual([u["file"] for u in uploads], [self.png_path, self.loop_gif])

    @patch("weather.WebClient")
    def test_slack_all_single_frame_images_post_one_file_each(self, mock_client_class):
        client = mock_client_class.return_value
        images = [self.image(self.single_gif, "cone"), self.image(self.single_gif, "speg")]

        upload_files_to_slack(images, "token", "chan")

        uploads = client.files_upload_v2.call_args.kwargs["file_uploads"]
        self.assertEqual(len(uploads), 2)

    @patch("weather.SyncWebhook")
    def test_discord_skips_single_frame_gif(self, mock_webhook_class):
        webhook = mock_webhook_class.from_url.return_value

        upload_files_to_discord([self.image(self.single_gif)], "https://example.com/hook")

        files = webhook.send.call_args.kwargs["files"]
        self.assertEqual([f.filename for f in files], ["Isaias.png"])

    @patch("weather.SyncWebhook")
    def test_discord_includes_multi_frame_gif(self, mock_webhook_class):
        webhook = mock_webhook_class.from_url.return_value

        upload_files_to_discord([self.image(self.loop_gif)], "https://example.com/hook")

        files = webhook.send.call_args.kwargs["files"]
        self.assertEqual([f.filename for f in files], ["Isaias.png", "Isaias.gif"])


if __name__ == "__main__":
    unittest.main()
