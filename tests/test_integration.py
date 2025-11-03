import unittest
import tempfile
import os
from unittest.mock import patch, Mock
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
    
    @patch('weather.upload_files_to_discord')
    @patch('weather.upload_files_to_slack')
    @patch('weather.generate_rss_feed')
    @patch('weather.process_single_image')
    @patch('weather.delete_storm_images')
    @patch('weather.fetch_xml_feed')
    @patch('sys.argv')
    def test_main_no_storms_with_updated_static_image(self, mock_argv, mock_fetch_xml, 
                                                     mock_delete_storm_images, mock_process_image,
                                                     mock_generate_rss, mock_upload_slack, 
                                                     mock_upload_discord):
        """Test main function when no storms are expected and static image is updated."""
        from weather import WeatherImage
        
        # Mock command line arguments
        mock_argv.__getitem__ = lambda s, i: [
            'weather.py',
            self.rss_file,
            self.image_dir,
            'slack_webhook_url',
            'slack_token',
            'upload_channel',
            'discord_webhook_url'
        ][i]
        mock_argv.__len__ = lambda s: 7
        
        # Mock no storms scenario
        mock_fetch_xml.return_value = (0, Mock())  # no_storms=0 (count)
        
        # Mock updated static image
        static_image = WeatherImage('two_atl_7d0', 'path.png', 'path.gif', 'url', True, 'static')
        mock_process_image.return_value = static_image
        
        main()
        
        mock_fetch_xml.assert_called_once()
        mock_delete_storm_images.assert_called_once_with(self.image_dir)
        mock_process_image.assert_called_once()
        mock_generate_rss.assert_called_once_with(static_image, self.rss_file)
        mock_upload_slack.assert_called_once_with([static_image], 'slack_token', 'upload_channel')
        mock_upload_discord.assert_called_once_with([static_image], 'discord_webhook_url')
    
    @patch('weather.upload_files_to_discord')
    @patch('weather.upload_files_to_slack')
    @patch('weather.generate_rss_feed')
    @patch('weather.process_single_image')
    @patch('weather.delete_storm_images')
    @patch('weather.fetch_xml_feed')
    @patch('sys.argv')
    def test_main_no_storms_with_unchanged_static_image(self, mock_argv, mock_fetch_xml, 
                                                       mock_delete_storm_images, mock_process_image,
                                                       mock_generate_rss, mock_upload_slack, 
                                                       mock_upload_discord):
        """Test main function when no storms are expected and static image is unchanged."""
        from weather import WeatherImage
        
        # Mock command line arguments
        mock_argv.__getitem__ = lambda s, i: [
            'weather.py',
            self.rss_file,
            self.image_dir,
            'slack_webhook_url',
            'slack_token',
            'upload_channel',
            'discord_webhook_url'
        ][i]
        mock_argv.__len__ = lambda s: 7
        
        # Mock no storms scenario
        mock_fetch_xml.return_value = (0, Mock())  # no_storms=0 (count)
        
        # Mock unchanged static image
        static_image = WeatherImage('two_atl_7d0', 'path.png', 'path.gif', 'url', False, 'static')
        mock_process_image.return_value = static_image
        
        main()
        
        mock_fetch_xml.assert_called_once()
        mock_delete_storm_images.assert_called_once_with(self.image_dir)
        mock_process_image.assert_called_once()
        mock_generate_rss.assert_called_once_with(static_image, self.rss_file)
        
        # Should not upload since image is unchanged
        mock_upload_slack.assert_not_called()
        mock_upload_discord.assert_not_called()
    
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
            'upload_channel',
            'discord_webhook_url'
        ][i]
        mock_argv.__len__ = lambda s: 7
        
        # Mock storms scenario
        mock_soup = Mock()
        mock_fetch_xml.return_value = (1, mock_soup)  # storms=1 (count)
        
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
            'upload_channel',
            'discord_webhook_url'
        ][i]
        mock_argv.__len__ = lambda s: 7
        
        # Mock storms scenario
        mock_soup = Mock()
        mock_fetch_xml.return_value = (1, mock_soup)  # storms=1 (count)
        
        # Mock only cached/unchanged images
        cached_image = WeatherImage('cached', 'cached.png', 'cached.gif', 'url', False, 'static')
        mock_fetch_images.return_value = [cached_image]
        
        # Capture stdout
        captured_output = StringIO()
        
        with patch('sys.stdout', captured_output):
            main()
        
        mock_fetch_xml.assert_called_once()
        mock_fetch_images.assert_called_once()
    
    @patch('weather.upload_files_to_discord')
    @patch('weather.upload_files_to_slack')
    @patch('weather.generate_rss_feed')
    @patch('weather.process_single_image')
    @patch('weather.delete_storm_images')
    @patch('weather.setup_logging')
    @patch('weather.fetch_xml_feed')
    @patch('sys.argv')
    def test_main_with_log_file_argument(self, mock_argv, mock_fetch_xml, mock_setup_logging,
                                        mock_delete_storm_images, mock_process_image,
                                        mock_generate_rss, mock_upload_slack, mock_upload_discord):
        """Test main function with log file argument."""
        from weather import WeatherImage
        
        log_file = os.path.join(self.temp_dir, 'test.log')
        
        # Mock command line arguments with log file
        mock_argv.__getitem__ = lambda s, i: [
            'weather.py',
            self.rss_file,
            self.image_dir,
            'slack_webhook_url',
            'slack_token',
            'upload_channel',
            'discord_webhook_url',
            '--log-file',
            log_file
        ][i]
        mock_argv.__len__ = lambda s: 9
        
        mock_fetch_xml.return_value = (0, Mock())  # no_storms=0 (count)
        
        # Mock unchanged static image to avoid upload calls
        static_image = WeatherImage('two_atl_7d0', 'path.png', 'path.gif', 'url', False, 'static')
        mock_process_image.return_value = static_image
        
        main()
        
        mock_setup_logging.assert_called_once_with(log_file)
        mock_delete_storm_images.assert_called_once_with(self.image_dir)
        mock_process_image.assert_called_once()
        mock_generate_rss.assert_called_once_with(static_image, self.rss_file)
        
        # Should not upload since image is unchanged
        mock_upload_slack.assert_not_called()
        mock_upload_discord.assert_not_called()
    
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
            'upload_channel',
            'discord_webhook_url',
            '--threshold',
            '0.005'
        ][i]
        mock_argv.__len__ = lambda s: 9
        
        mock_fetch_xml.return_value = (0, Mock())  # no_storms=0 (count)
        
        with patch('weather.delete_images'), \
             patch('weather.fetch_all_weather_images') as mock_fetch_images:
            
            mock_fetch_xml.return_value = (1, Mock())  # Change to have storms (count=1)
            mock_fetch_images.return_value = []
            
            main()
            
            # Verify custom threshold was passed to fetch_all_weather_images
            mock_fetch_images.assert_called_once()
            args, kwargs = mock_fetch_images.call_args
            if len(args) >= 3:
                self.assertEqual(args[2], 0.005)


if __name__ == '__main__':
    unittest.main()
