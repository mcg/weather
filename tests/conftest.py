"""
Pytest configuration and shared fixtures for weather application tests.
"""

import pytest
import tempfile
import os
import shutil
from PIL import Image
from unittest.mock import Mock


@pytest.fixture(autouse=True)
def clean_app_environment(monkeypatch, tmp_path):
    """Keep configuration and generated files isolated from the real checkout."""
    monkeypatch.chdir(tmp_path)
    names = (
        "RSS_FILE_PATH",
        "IMAGE_FILE_PATH",
        "SLACK_WEBHOOK_URL",
        "SLACK_TOKEN",
        "UPLOAD_CHANNEL",
        "DISCORD_WEBHOOK_URL",
        "LOG_FILE",
        "THRESHOLD",
        "PIXEL_TOLERANCE",
    )
    for name in names:
        # Register even absent keys so values loaded by dotenv are removed too.
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    shutil.rmtree(temp_dir)


@pytest.fixture
def test_image_path(temp_dir):
    """Create a test PNG image file."""
    image_path = os.path.join(temp_dir, "test_image.png")
    test_img = Image.new("RGB", (100, 100), color="red")
    test_img.save(image_path)
    return image_path


@pytest.fixture
def test_gif_path(temp_dir):
    """Create a test GIF image file."""
    gif_path = os.path.join(temp_dir, "test_image.gif")
    test_img = Image.new("RGB", (100, 100), color="blue")
    test_img.save(gif_path, "GIF")
    return gif_path


@pytest.fixture
def sample_xml_with_storms():
    """Sample XML content with storms."""
    return """<?xml version="1.0" encoding="UTF-8"?>
    <rss xmlns:nhc="https://www.nhc.noaa.gov">
    <channel>
    <item>
        <title>Hurricane Maria Graphics</title>
        <description><![CDATA[
            <img src="https://example.com/maria_5day_cone_with_line_and_wind.png" />
        ]]></description>
    </item>
    <item>
        <title>Summary for Hurricane Maria</title>
        <nhc:Cyclone>
            <nhc:atcf>AL152017</nhc:atcf>
        </nhc:Cyclone>
    </item>
    </channel>
    </rss>"""


@pytest.fixture
def sample_xml_no_storms():
    """Sample XML content with no storms expected."""
    return """<?xml version="1.0" encoding="UTF-8"?>
    <rss><channel>
    <description>Tropical cyclone formation is not expected during the next 7 days</description>
    </channel></rss>"""


@pytest.fixture
def mock_weather_image():
    """Create a mock WeatherImage object."""
    from weather.models import WeatherImage

    return WeatherImage(
        name="test_image",
        png_path="/path/to/test.png",
        gif_path="/path/to/test.gif",
        url="http://example.com/test.png",
        is_new=True,
        image_type="static",
    )


@pytest.fixture
def mock_requests_response():
    """Create a mock requests response."""
    mock_response = Mock()
    mock_response.content = b"fake_image_data"
    mock_response.from_cache = False
    mock_response.status_code = 200
    return mock_response


@pytest.fixture
def sample_cyclone_data():
    """Sample cyclone data structure."""
    return {
        "storm_name": "Maria",
        "storm_type": "Hurricane",
        "image_url": "https://example.com/maria_5day_cone_with_line_and_wind.png",
        "speg_model": "al152017",
    }
