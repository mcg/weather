import pytest
import tempfile
import os
import sys
from unittest.mock import patch, MagicMock
from weather import main


@pytest.fixture(autouse=True)
def clean_env():
    """Clean up environment variables before and after each test."""
    env_vars_to_clean = [
        'RSS_FILE_PATH', 'IMAGE_FILE_PATH', 'SLACK_WEBHOOK_URL', 
        'SLACK_TOKEN', 'UPLOAD_CHANNEL', 'DISCORD_WEBHOOK_URL', 'LOG_FILE', 'THRESHOLD'
    ]
    
    # Store original values
    original_values = {}
    for var in env_vars_to_clean:
        original_values[var] = os.environ.get(var)
        if var in os.environ:
            del os.environ[var]
    
    yield
    
    # Restore original values
    for var in env_vars_to_clean:
        if var in os.environ:
            del os.environ[var]
        if original_values[var] is not None:
            os.environ[var] = original_values[var]


def test_env_file_loading():
    """Test that .env file values are loaded correctly."""
    # Create a temporary .env file
    with tempfile.NamedTemporaryFile(mode='w', suffix='.env', delete=False) as f:
        f.write("""RSS_FILE_PATH=test-feed.xml
IMAGE_FILE_PATH=test-images/
SLACK_WEBHOOK_URL=https://hooks.slack.com/test
SLACK_TOKEN=test-token
UPLOAD_CHANNEL=test-channel
DISCORD_WEBHOOK_URL=https://discord.com/test
LOG_FILE=test.log
""")
        env_file_path = f.name
    
    try:
        # Mock the main functionality to avoid network calls and file operations
        with patch('weather.fetch_xml_feed') as mock_fetch, \
             patch('weather.delete_storm_images') as mock_delete, \
             patch('weather.process_single_image') as mock_process, \
             patch('weather.generate_rss_feed') as mock_rss, \
             patch('weather.upload_files_to_slack') as mock_slack, \
             patch('weather.upload_files_to_discord') as mock_discord, \
             patch('weather.setup_logging') as mock_logging, \
             patch('sys.argv', ['weather.py', '--env-file', env_file_path]):
            
            mock_fetch.return_value = (True, None)  # No storms
            
            # Mock the static image processing
            mock_static_image = MagicMock()
            mock_static_image.is_new = False
            mock_process.return_value = mock_static_image
            
            # This should not raise an error - all required args should be loaded from .env
            main()
            
            # Verify that setup_logging was called with the log file from .env
            mock_logging.assert_called_once_with('test.log')
            
            # Verify that delete_storm_images was called with the image path from .env
            mock_delete.assert_called_once_with('test-images/')
            
    finally:
        # Clean up
        os.unlink(env_file_path)


def test_command_line_args_override_env():
    """Test that command line arguments take precedence over .env values."""
    # Create a temporary .env file
    with tempfile.NamedTemporaryFile(mode='w', suffix='.env', delete=False) as f:
        f.write("""RSS_FILE_PATH=env-feed.xml
IMAGE_FILE_PATH=env-images/
SLACK_WEBHOOK_URL=https://hooks.slack.com/env
SLACK_TOKEN=env-token
UPLOAD_CHANNEL=env-channel
DISCORD_WEBHOOK_URL=https://discord.com/env
LOG_FILE=env.log
""")
        env_file_path = f.name
    
    try:
        # Mock the main functionality to avoid network calls and file operations
        with patch('weather.fetch_xml_feed') as mock_fetch, \
             patch('weather.delete_storm_images') as mock_delete, \
             patch('weather.process_single_image') as mock_process, \
             patch('weather.generate_rss_feed') as mock_rss, \
             patch('weather.upload_files_to_slack') as mock_slack, \
             patch('weather.upload_files_to_discord') as mock_discord, \
             patch('weather.setup_logging') as mock_logging, \
             patch('sys.argv', [
                 'weather.py', 
                 '--env-file', env_file_path,
                 'cli-feed.xml',  # This should override the .env value
                 'cli-images/',
                 'https://hooks.slack.com/cli',
                 'cli-token',
                 'cli-channel',
                 'https://discord.com/cli',
                 '--log-file', 'cli.log'  # Override the log file from .env
             ]):
            
            mock_fetch.return_value = (True, None)  # No storms
            
            # Mock the static image processing
            mock_static_image = MagicMock()
            mock_static_image.is_new = False
            mock_process.return_value = mock_static_image
            
            # This should work and use CLI args over .env values
            main()
            
            # The function should have been called with CLI args, not .env values
            mock_logging.assert_called_once_with('cli.log')  # CLI log file, not env
    finally:
        # Clean up
        os.unlink(env_file_path)


def test_env_fallback_for_optional_args():
    """Test that .env values are used for optional args when not provided via CLI."""
    # Create a temporary .env file
    with tempfile.NamedTemporaryFile(mode='w', suffix='.env', delete=False) as f:
        f.write("""RSS_FILE_PATH=env-feed.xml
IMAGE_FILE_PATH=env-images/
SLACK_WEBHOOK_URL=https://hooks.slack.com/env
SLACK_TOKEN=env-token
UPLOAD_CHANNEL=env-channel
DISCORD_WEBHOOK_URL=https://discord.com/env
LOG_FILE=env.log
""")
        env_file_path = f.name
    
    try:
        # Mock the main functionality to avoid network calls and file operations
        with patch('weather.fetch_xml_feed') as mock_fetch, \
             patch('weather.delete_storm_images') as mock_delete, \
             patch('weather.process_single_image') as mock_process, \
             patch('weather.generate_rss_feed') as mock_rss, \
             patch('weather.upload_files_to_slack') as mock_slack, \
             patch('weather.upload_files_to_discord') as mock_discord, \
             patch('weather.setup_logging') as mock_logging, \
             patch('sys.argv', [
                 'weather.py', 
                 '--env-file', env_file_path,
                 'cli-feed.xml',  # Override RSS path
                 'cli-images/',   # Override image path
                 'https://hooks.slack.com/cli',  # Override slack webhook
                 'cli-token',     # Override slack token
                 'cli-channel',   # Override upload channel
                 'https://discord.com/cli'  # Override discord webhook
                 # Note: No --log-file specified, should use .env value
             ]):
            
            mock_fetch.return_value = (True, None)  # No storms
            
            # Mock the static image processing
            mock_static_image = MagicMock()
            mock_static_image.is_new = False
            mock_process.return_value = mock_static_image
            
            main()
            
            # Should use the log file from .env since not specified in CLI
            mock_logging.assert_called_once_with('env.log')
            # Should use CLI args for required params
            mock_delete.assert_called_once_with('cli-images/')
            
    finally:
        # Clean up
        os.unlink(env_file_path)


def test_missing_required_args_error():
    """Test that missing required arguments raise an error."""
    with patch('sys.argv', ['weather.py']):
        with pytest.raises(SystemExit):  # argparse calls sys.exit on error
            main()


def test_env_file_with_storms():
    """Test .env file loading when storms are detected."""
    # Create a temporary .env file
    with tempfile.NamedTemporaryFile(mode='w', suffix='.env', delete=False) as f:
        f.write("""RSS_FILE_PATH=test-feed.xml
IMAGE_FILE_PATH=test-images/
SLACK_WEBHOOK_URL=https://hooks.slack.com/test
SLACK_TOKEN=test-token
UPLOAD_CHANNEL=test-channel
DISCORD_WEBHOOK_URL=https://discord.com/test
""")
        env_file_path = f.name
    
    try:
        # Mock the main functionality to simulate storm conditions
        with patch('weather.fetch_xml_feed') as mock_fetch, \
             patch('weather.fetch_all_weather_images') as mock_fetch_images, \
             patch('weather.generate_rss_feed') as mock_rss, \
             patch('weather.upload_files_to_slack') as mock_slack, \
             patch('weather.upload_files_to_discord') as mock_discord, \
             patch('weather.setup_logging') as mock_logging, \
             patch('sys.argv', ['weather.py', '--env-file', env_file_path]):
            
            # Simulate storms detected
            mock_soup = MagicMock()
            mock_fetch.return_value = (False, mock_soup)  # Storms detected
            
            # Mock images with one static and one new
            mock_static_image = MagicMock()
            mock_static_image.image_type = 'static'
            mock_static_image.is_new = False
            
            mock_new_image = MagicMock()
            mock_new_image.image_type = 'storm'
            mock_new_image.is_new = True
            
            mock_fetch_images.return_value = [mock_static_image, mock_new_image]
            
            main()
            
            # Verify that fetch_all_weather_images was called with the image path from .env
            mock_fetch_images.assert_called_once()
            args = mock_fetch_images.call_args[0]
            assert args[1] == 'test-images/'  # image_file_path from .env
            
            # Verify upload functions were called for new images
            mock_slack.assert_called_once()
            mock_discord.assert_called_once()
            
    finally:
        # Clean up
        os.unlink(env_file_path)


def test_threshold_env_variable():
    """Test that THRESHOLD environment variable is read and converted to float correctly."""
    # Create a temporary .env file with THRESHOLD
    with tempfile.NamedTemporaryFile(mode='w', suffix='.env', delete=False) as f:
        f.write("""RSS_FILE_PATH=test-feed.xml
IMAGE_FILE_PATH=test-images/
SLACK_WEBHOOK_URL=https://hooks.slack.com/test
SLACK_TOKEN=test-token
UPLOAD_CHANNEL=test-channel
DISCORD_WEBHOOK_URL=https://discord.com/test
THRESHOLD=0.005
""")
        env_file_path = f.name
    
    try:
        # Mock the main functionality to avoid network calls and file operations
        with patch('weather.fetch_xml_feed') as mock_fetch, \
             patch('weather.fetch_all_weather_images') as mock_fetch_images, \
             patch('weather.generate_rss_feed') as mock_rss, \
             patch('weather.upload_files_to_slack') as mock_slack, \
             patch('weather.upload_files_to_discord') as mock_discord, \
             patch('sys.argv', ['weather.py', '--env-file', env_file_path]):
            
            # Mock no storms scenario but we'll change to storms to test threshold
            mock_fetch.return_value = (False, MagicMock())  # has storms
            
            # Mock images
            mock_static_image = MagicMock()
            mock_static_image.image_type = 'static'
            mock_static_image.is_new = False
            
            mock_fetch_images.return_value = [mock_static_image]
            
            main()
            
            # Verify that fetch_all_weather_images was called with the threshold from .env (0.005)
            mock_fetch_images.assert_called_once()
            args = mock_fetch_images.call_args[0]
            assert args[2] == 0.005  # threshold from .env as float
            
    finally:
        # Clean up
        os.unlink(env_file_path)


def test_threshold_command_line_override():
    """Test that command line threshold argument overrides environment variable."""
    
    try:
        # Mock the main functionality
        with patch('weather.fetch_xml_feed') as mock_fetch, \
             patch('weather.fetch_all_weather_images') as mock_fetch_images, \
             patch('weather.generate_rss_feed') as mock_rss, \
             patch('weather.upload_files_to_slack') as mock_slack, \
             patch('weather.upload_files_to_discord') as mock_discord, \
             patch('sys.argv', ['weather.py', 'test-feed.xml', 'test-images/', 
                               'slack_webhook', 'slack_token', 'channel', 'discord_webhook',
                               '--threshold', '0.008']):
            
            mock_fetch.return_value = (False, MagicMock())  # has storms
            
            # Mock images
            mock_static_image = MagicMock()
            mock_static_image.image_type = 'static'
            mock_static_image.is_new = False
            
            mock_fetch_images.return_value = [mock_static_image]
            
            main()
            
            # Verify that fetch_all_weather_images was called with command line threshold (0.008)
            mock_fetch_images.assert_called_once()
            args = mock_fetch_images.call_args[0]
            assert args[2] == 0.008  # command line threshold should override env
            
    finally:
        # Clean up environment
        if 'THRESHOLD' in os.environ:
            del os.environ['THRESHOLD']
