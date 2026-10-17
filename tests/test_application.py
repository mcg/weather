"""Lifecycle regressions using real image files and caller-owned HTTP sessions."""

import io
from datetime import timedelta
from unittest.mock import Mock, call

import pytest
import requests
from PIL import Image

from weather import application, cli, feeds, images, publishing
from weather.config import Settings


FEED_URL = "https://www.nhc.noaa.gov/index-at.xml"
QUIET_FEED = b"""<rss><channel><item>
<title>Atlantic Tropical Weather Outlook</title>
<description>Tropical cyclone formation is not expected during the next 7 days.</description>
</item></channel></rss>"""
FORMATION_FEED = b"""<rss><channel><item>
<title>Atlantic Tropical Weather Outlook</title>
<description>Formation chance through 7 days...20 percent.</description>
</item></channel></rss>"""


def png_bytes(color):
    stream = io.BytesIO()
    Image.new("RGB", (10, 10), color).save(stream, "PNG")
    return stream.getvalue()


def response(content):
    return Mock(content=content, from_cache=False)


def seed_history(directory, names):
    directory.mkdir(parents=True, exist_ok=True)
    for name in names:
        Image.new("RGB", (10, 10), "blue").save(directory / f"{name}.png")
        Image.new("RGB", (10, 10), "blue").save(directory / f"{name}.gif")
    return snapshot(directory)


def snapshot(directory):
    return {path.name: path.read_bytes() for path in directory.iterdir()}


@pytest.fixture
def settings(tmp_path):
    return Settings(
        rss_file_path=tmp_path / "rss" / "feed.xml",
        image_file_path=tmp_path / "images",
        slack_token="test-token",
        upload_channel="test-channel",
        discord_webhook_url="https://example.com/webhook",
        threshold=0.005,
        pixel_tolerance=7,
    )


@pytest.fixture
def session(monkeypatch):
    forbidden_get = Mock(side_effect=AssertionError("Use the injected session"))
    monkeypatch.setattr(requests, "get", forbidden_get)
    return Mock(spec=requests.Session)


@pytest.fixture
def uploads(monkeypatch):
    slack = Mock()
    discord = Mock()
    monkeypatch.setattr(publishing, "upload_files_to_slack", slack)
    monkeypatch.setattr(publishing, "upload_files_to_discord", discord)
    return slack, discord


def test_final_update_publishes_before_deleting_all_images(
    settings, session, uploads, monkeypatch
):
    seed_history(settings.image_file_path, ["two_atl_7d0", "old_storm"])
    feed_response = response(QUIET_FEED)
    image_response = response(png_bytes("red"))
    session.get.side_effect = [feed_response, image_response]
    cache = Mock()
    monkeypatch.setattr(application, "CachedSession", cache)
    lifecycle = Mock()
    for name in (
        "delete_storm_images",
        "process_single_image",
        "generate_rss_feed",
        "publish_images",
        "delete_images",
    ):
        tracked = Mock(wraps=getattr(application, name))
        lifecycle.attach_mock(tracked, name)
        monkeypatch.setattr(application, name, tracked)

    application.run(settings, session=session)

    assert [event[0] for event in lifecycle.mock_calls] == [
        "delete_storm_images",
        "process_single_image",
        "generate_rss_feed",
        "publish_images",
        "delete_images",
    ]
    process_call = lifecycle.process_single_image.call_args
    assert process_call.args[3] == 0.0
    assert process_call.kwargs == {"pixel_tolerance": 7, "session": session}
    assert list(settings.image_file_path.iterdir()) == []
    assert settings.rss_file_path.is_file()
    for upload in uploads:
        upload.assert_called_once()
    assert session.get.call_args_list == [
        call(FEED_URL, timeout=30),
        call(application.OUTLOOK.url, timeout=30),
    ]
    feed_response.raise_for_status.assert_called_once_with()
    image_response.raise_for_status.assert_called_once_with()
    session.close.assert_not_called()
    cache.assert_not_called()


@pytest.mark.parametrize("failed_destinations", ["slack", "discord", "both"])
def test_final_update_does_not_delete_history_when_publishing_raises(
    failed_destinations, settings, session, uploads, monkeypatch
):
    before = seed_history(settings.image_file_path, ["two_atl_7d0"])
    new_body = png_bytes("red")
    session.get.side_effect = [response(QUIET_FEED), response(new_body)]
    slack, discord = uploads
    if failed_destinations in ("slack", "both"):
        slack.side_effect = RuntimeError("slack down")
    if failed_destinations in ("discord", "both"):
        discord.side_effect = RuntimeError("discord down")
    cleanup = Mock(wraps=application.delete_images)
    monkeypatch.setattr(application, "delete_images", cleanup)

    with pytest.raises(RuntimeError, match="down"):
        application.run(settings, session=session)

    cleanup.assert_not_called()
    slack.assert_called_once()
    discord.assert_called_once()
    after = snapshot(settings.image_file_path)
    assert set(after) == set(before)
    if failed_destinations == "both":
        assert after == before
    else:
        assert after["two_atl_7d0.png"] == new_body
        assert after["two_atl_7d0.gif"] != before["two_atl_7d0.gif"]


@pytest.mark.parametrize("storms", [False, True])
def test_rss_failure_rolls_back_all_prepared_images_before_any_upload(
    storms, settings, session, uploads, monkeypatch, sample_xml_with_storms
):
    names = ["two_atl_7d0"]
    urls = [application.OUTLOOK.url]
    feed_body = FORMATION_FEED
    if storms:
        names += ["Maria_5day_cone_with_line_and_wind", "Maria_hurricane_models"]
        urls += [
            "https://example.com/maria_5day_cone_with_line_and_wind.png",
            "https://web.uwm.edu/hurricane-models/models/al152017.png",
        ]
        feed_body = sample_xml_with_storms.encode()
    before = seed_history(settings.image_file_path, names)
    responses = [response(feed_body), *(response(png_bytes("red")) for _ in names)]
    session.get.side_effect = responses
    rss = Mock(side_effect=OSError("RSS disk full"))
    monkeypatch.setattr(application, "generate_rss_feed", rss)

    with pytest.raises(OSError, match="RSS disk full"):
        application.run(settings, session=session)

    assert snapshot(settings.image_file_path) == before
    assert session.get.call_args_list == [
        call(url, timeout=30) for url in [FEED_URL, *urls]
    ]
    for downloaded in responses:
        downloaded.raise_for_status.assert_called_once_with()
    rss.assert_called_once()
    for upload in uploads:
        upload.assert_not_called()
    session.close.assert_not_called()


def test_fetch_feed_uses_injected_session_and_checks_http_status(session):
    downloaded = response(QUIET_FEED)
    session.get.return_value = downloaded

    count, soup = feeds.fetch_xml_feed(session=session)

    assert count == 0
    assert soup.find("title").get_text() == "Atlantic Tropical Weather Outlook"
    session.get.assert_called_once_with(FEED_URL, timeout=30)
    downloaded.raise_for_status.assert_called_once_with()
    session.close.assert_not_called()


def test_feed_http_error_is_raised_before_parsing(session, monkeypatch):
    downloaded = response(b"not XML")
    downloaded.raise_for_status.side_effect = requests.HTTPError("503 unavailable")
    session.get.return_value = downloaded
    parse = Mock()
    monkeypatch.setattr(feeds, "BeautifulSoup", parse)

    with pytest.raises(requests.HTTPError, match="503 unavailable"):
        feeds.fetch_xml_feed(session=session)

    session.get.assert_called_once_with(FEED_URL, timeout=30)
    downloaded.raise_for_status.assert_called_once_with()
    parse.assert_not_called()


def test_image_http_error_with_injected_session_does_not_modify_history(
    session, tmp_path
):
    before = seed_history(tmp_path, ["outlook"])
    downloaded = response(b"not an image")
    downloaded.raise_for_status.side_effect = requests.HTTPError("404 missing")
    session.get.return_value = downloaded

    with pytest.raises(requests.HTTPError, match="404 missing"):
        images.process_single_image(
            "https://example.com/outlook.png", "outlook", str(tmp_path), session=session
        )

    session.get.assert_called_once_with("https://example.com/outlook.png", timeout=30)
    downloaded.raise_for_status.assert_called_once_with()
    assert snapshot(tmp_path) == before


def test_run_without_injected_session_scopes_a_cached_session(settings, monkeypatch):
    cache = Mock()
    cached_session = Mock(spec=requests.Session)
    cache.return_value.__enter__ = Mock(return_value=cached_session)
    cache.return_value.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(application, "CachedSession", cache)
    fetch = Mock(return_value=(0, feeds.BeautifulSoup(QUIET_FEED, "xml")))
    monkeypatch.setattr(application, "fetch_xml_feed", fetch)

    application.run(settings)

    cache.assert_called_once_with(
        "weather_cache",
        cache_control=True,
        urls_expire_after={
            "https://web.uwm.edu/hurricane-models/models/*": timedelta(hours=1)
        },
    )
    fetch.assert_called_once_with(session=cached_session)
    cache.return_value.__exit__.assert_called_once_with(None, None, None)


def test_cli_delegates_settings_without_creating_its_own_session(settings, monkeypatch):
    load = Mock(return_value=settings)
    setup = Mock()
    run = Mock()
    monkeypatch.setattr(cli, "load_settings", load)
    monkeypatch.setattr(cli, "setup_logging", setup)
    monkeypatch.setattr(cli, "run", run)

    cli.main(["--threshold", "0.005"])

    load.assert_called_once_with(["--threshold", "0.005"])
    setup.assert_called_once_with(None)
    run.assert_called_once_with(settings)
