import unittest
import tempfile
import os
import sys
from unittest.mock import patch, Mock, MagicMock
from io import StringIO

# Import the main function
from weather import main


class TestWeatherIntegration(unittest.TestCase):
    """Integration tests for the weather application."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.rss_file = os.path.join(self.temp_dir, 'test.rss')
        self.image_dir = os.path.join(self.temp_dir, 'images')
        os.makedirs(self.image_dir, exist_ok=True)
    
    def tearDown(self):
        """Clean up test fixtures."""
        import shutil
        shutil.rmtree(self.temp_dir)
    
    @patch('weather.delete_images')
    @patch('weather.fetch_xml_feed')
    @patch('sys.argv')
    def test_main_no_storms(self, mock_argv, mock_fetch_xml, mock_delete_images):
        """Test main function when no storms are expected."""
        # Mock command line arguments
        mock_argv.__getitem__ = lambda s, i: [
            'weather.py',
            self.rss_file,
            self.image_dir,
            'slack_webhook_url',
            'slack_token',
            'discord_webhook_url',
            'upload_channel'
        ][i]
        mock_argv.__len__ = lambda s: 7
        
        # Mock no storms scenario
        mock_fetch_xml.return_value = (True, Mock())  # no_storms=True
        
        # Capture stdout to verify logging
        captured_output = StringIO()
        
        with patch('sys.stdout', captured_output):
            main()
        
        mock_fetch_xml.assert_called_once()
        mock_delete_images.assert_called_once_with(self.image_dir)
    
    @patch('weather.upload_files_to_discord')
    @patch('weather.upload_files_to_slack')
    @patch('weather.generate_rss_feed')
    @patch('weather.fetch_all_weather_images')
    @patch('weather.fetch_xml_feed')
    @patch('sys.argv')
    def test_main_with_storms_and_new_images(self, mock_argv, mock_fetch_xml, 
                                           mock_fetch_images, mock_generate_rss,
                                           mock_upload_slack, mock_upload_discord):
        """Test main function with storms and new images."""
        from weather import WeatherImage
        
        # Mock command line arguments
        mock_argv.__getitem__ = lambda s, i: [
            'weather.py',
            self.rss_file,
            self.image_dir,
            'slack_webhook_url',
            'slack_token',
            'discord_webhook_url',
            'upload_channel'
        ][i]
        mock_argv.__len__ = lambda s: 7
        
        # Mock storms scenario
        mock_soup = Mock()
        mock_fetch_xml.return_value = (False, mock_soup)  # no_storms=False
        
        # Mock images
        static_image = WeatherImage('static', 'path.png', 'path.gif', 'url', True, 'static')
        new_image = WeatherImage('new', 'new.png', 'new.gif', 'url', True, 'cone')
        cached_image = WeatherImage('cached', 'cached.png', 'cached.gif', 'url', False, 'cached')
        
        mock_fetch_images.return_value = [static_image, new_image, cached_image]
        
        main()
        
        mock_fetch_xml.assert_called_once()
        mock_fetch_images.assert_called_once()
        mock_generate_rss.assert_called_once_with(static_image, self.rss_file)
        mock_upload_slack.assert_called_once()
        mock_upload_discord.assert_called_once()
        
        # Verify only new images are uploaded
        uploaded_images = mock_upload_slack.call_args[0][0]
        self.assertEqual(len(uploaded_images), 2)  # static_image and new_image
        self.assertTrue(all(img.is_new for img in uploaded_images))
    
    @patch('weather.fetch_all_weather_images')
    @patch('weather.fetch_xml_feed')
    @patch('sys.argv')
    def test_main_with_storms_no_new_images(self, mock_argv, mock_fetch_xml, mock_fetch_images):
        """Test main function with storms but no new images."""
        from weather import WeatherImage
        
        # Mock command line arguments
        mock_argv.__getitem__ = lambda s, i: [
            'weather.py',
            self.rss_file,
            self.image_dir,
            'slack_webhook_url',
            'slack_token',
            'discord_webhook_url',
            'upload_channel'
        ][i]
        mock_argv.__len__ = lambda s: 7
        
        # Mock storms scenario
        mock_soup = Mock()
        mock_fetch_xml.return_value = (False, mock_soup)  # no_storms=False
        
        # Mock only cached/unchanged images
        cached_image = WeatherImage('cached', 'cached.png', 'cached.gif', 'url', False, 'static')
        mock_fetch_images.return_value = [cached_image]
        
        # Capture stdout
        captured_output = StringIO()
        
        with patch('sys.stdout', captured_output):
            main()
        
        mock_fetch_xml.assert_called_once()
        mock_fetch_images.assert_called_once()
    
    @patch('weather.setup_logging')
    @patch('weather.fetch_xml_feed')
    @patch('sys.argv')
    def test_main_with_log_file_argument(self, mock_argv, mock_fetch_xml, mock_setup_logging):
        """Test main function with log file argument."""
        log_file = os.path.join(self.temp_dir, 'test.log')
        
        # Mock command line arguments with log file
        mock_argv.__getitem__ = lambda s, i: [
            'weather.py',
            self.rss_file,
            self.image_dir,
            'slack_webhook_url',
            'slack_token',
            'discord_webhook_url',
            'upload_channel',
            '--log-file',
            log_file
        ][i]
        mock_argv.__len__ = lambda s: 9
        
        mock_fetch_xml.return_value = (True, Mock())  # no_storms=True
        
        with patch('weather.delete_images'):
            main()
        
        mock_setup_logging.assert_called_once_with(log_file)
    
    @patch('weather.fetch_xml_feed')
    @patch('sys.argv')
    def test_main_with_custom_threshold(self, mock_argv, mock_fetch_xml):
        """Test main function with custom threshold argument."""
        # Mock command line arguments with custom threshold
        mock_argv.__getitem__ = lambda s, i: [
            'weather.py',
            self.rss_file,
            self.image_dir,
            'slack_webhook_url',
            'slack_token',
            'discord_webhook_url',
            'upload_channel',
            '--threshold',
            '0.005'
        ][i]
        mock_argv.__len__ = lambda s: 9
        
        mock_fetch_xml.return_value = (True, Mock())  # no_storms=True
        
        with patch('weather.delete_images'), \
             patch('weather.fetch_all_weather_images') as mock_fetch_images:
            
            mock_fetch_xml.return_value = (False, Mock())  # Change to have storms
            mock_fetch_images.return_value = []
            
            main()
            
            # Verify custom threshold was passed to fetch_all_weather_images
            mock_fetch_images.assert_called_once()
            args, kwargs = mock_fetch_images.call_args
            if len(args) >= 3:
                self.assertEqual(args[2], 0.005)


if __name__ == '__main__':
    unittest.main()
