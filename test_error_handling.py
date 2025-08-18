import unittest
import tempfile
import os
import shutil
from unittest.mock import Mock, patch, MagicMock
from PIL import Image
import requests

from weather import (
    images_are_different, process_single_image, fetch_xml_feed,
    upload_files_to_slack, upload_files_to_discord, WeatherImage
)
from slack_sdk.errors import SlackApiError


class TestWeatherErrorHandling(unittest.TestCase):
    """Test error handling and edge cases in weather functions."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.test_image_path = os.path.join(self.temp_dir, 'test_image.png')
        
        # Create a simple test image
        test_img = Image.new('RGB', (100, 100), color='red')
        test_img.save(self.test_image_path)
    
    def tearDown(self):
        """Clean up test fixtures."""
        shutil.rmtree(self.temp_dir)
    
    def test_images_are_different_corrupted_image(self):
        """Test image comparison with corrupted image file."""
        # Create a corrupted image file
        corrupted_path = os.path.join(self.temp_dir, 'corrupted.png')
        with open(corrupted_path, 'w') as f:
            f.write('not an image')
        
        # Should return True when comparison fails
        result = images_are_different(self.test_image_path, corrupted_path)
        self.assertTrue(result)
    
    def test_images_are_different_permission_error(self):
        """Test image comparison when file cannot be read."""
        non_readable_path = os.path.join(self.temp_dir, 'non_readable.png')
        
        # Create file and remove read permissions (Unix-like systems)
        if os.name != 'nt':  # Skip on Windows
            with open(non_readable_path, 'w') as f:
                f.write('test')
            os.chmod(non_readable_path, 0o000)
            
            try:
                result = images_are_different(self.test_image_path, non_readable_path)
                self.assertTrue(result)
            finally:
                # Restore permissions for cleanup
                os.chmod(non_readable_path, 0o644)
    
    @patch('weather.requests.get')
    def test_fetch_xml_feed_network_error(self, mock_get):
        """Test XML feed fetching with network error."""
        mock_get.side_effect = requests.RequestException("Network error")
        
        with self.assertRaises(requests.RequestException):
            fetch_xml_feed()
    
    @patch('weather.requests.get')
    def test_fetch_xml_feed_invalid_xml(self, mock_get):
        """Test XML feed fetching with invalid XML."""
        mock_response = Mock()
        mock_response.content = b'<invalid xml content'
        mock_get.return_value = mock_response
        
        # Should not raise exception, BeautifulSoup handles malformed XML gracefully
        no_storms, soup = fetch_xml_feed()
        self.assertIsNotNone(soup)
    
    @patch('weather.update_gif')
    @patch('weather.images_are_different')
    @patch('weather.requests.get')
    def test_process_single_image_download_error(self, mock_get, mock_images_diff, mock_update_gif):
        """Test processing image when download fails."""
        mock_get.side_effect = requests.RequestException("Download failed")
        
        with self.assertRaises(requests.RequestException):
            process_single_image(
                'http://example.com/nonexistent.png',
                'test_image',
                self.temp_dir
            )
    
    @patch('weather.os.rename')
    @patch('weather.update_gif')
    @patch('weather.images_are_different')
    @patch('weather.requests.get')
    def test_process_single_image_file_operation_error(self, mock_get, mock_images_diff, 
                                                      mock_update_gif, mock_rename):
        """Test processing image when file operations fail."""
        mock_response = Mock()
        mock_response.content = b'fake_image_data'
        mock_response.from_cache = False
        mock_get.return_value = mock_response
        
        mock_images_diff.return_value = True
        mock_rename.side_effect = OSError("Permission denied")
        
        with self.assertRaises(OSError):
            process_single_image(
                'http://example.com/test.png',
                'test_image',
                self.temp_dir
            )
    
    @patch('weather.WebClient')
    def test_upload_files_to_slack_api_error(self, mock_webclient_class):
        """Test Slack upload with API error."""
        mock_client = Mock()
        mock_webclient_class.return_value = mock_client
        
        # Mock Slack API error
        error_response = {'error': 'invalid_auth'}
        mock_client.files_upload_v2.side_effect = SlackApiError("API Error", error_response)
        
        images = [
            WeatherImage('test', self.test_image_path, self.test_image_path, 'url', True, 'static')
        ]
        
        with self.assertRaises(SlackApiError):
            upload_files_to_slack(images, 'invalid_token', 'test_channel')
    
    @patch('weather.SyncWebhook')
    def test_upload_files_to_discord_webhook_error(self, mock_webhook_class):
        """Test Discord upload with webhook error."""
        mock_webhook = Mock()
        mock_webhook_class.from_url.return_value = mock_webhook
        mock_webhook.send.side_effect = Exception("Webhook error")
        
        images = [
            WeatherImage('test', self.test_image_path, self.test_image_path, 'url', True, 'static')
        ]
        
        with patch('builtins.open'):
            with self.assertRaises(Exception):
                upload_files_to_discord(images, 'http://invalid-webhook.com')
    
    def test_weather_image_string_representation(self):
        """Test WeatherImage string representation."""
        image = WeatherImage(
            name='test_storm',
            png_path='/path/to/test.png',
            gif_path='/path/to/test.gif',
            url='http://example.com/test.png',
            is_new=True,
            image_type='cone'
        )
        
        # Test that the object can be converted to string without error
        str_repr = str(image)
        self.assertIn('test_storm', str_repr)
    
    def test_weather_image_equality(self):
        """Test WeatherImage equality comparison."""
        image1 = WeatherImage('test', 'path1', 'gif1', 'url1', True, 'static')
        image2 = WeatherImage('test', 'path1', 'gif1', 'url1', True, 'static')
        image3 = WeatherImage('test2', 'path2', 'gif2', 'url2', False, 'cone')
        
        self.assertEqual(image1, image2)
        self.assertNotEqual(image1, image3)
    
    @patch('weather.os.listdir')
    def test_delete_images_permission_error(self, mock_listdir):
        """Test delete_images with permission error."""
        mock_listdir.return_value = ['test.png', 'test.gif', 'test.txt']
        
        with patch('weather.os.remove') as mock_remove:
            mock_remove.side_effect = PermissionError("Permission denied")
            
            # Should not raise exception, just log error
            from weather import delete_images
            delete_images(self.temp_dir)
            
            # Should attempt to remove PNG and GIF files
            self.assertEqual(mock_remove.call_count, 2)
    
    def test_images_are_different_different_sizes(self):
        """Test image comparison with different sized images."""
        # Create two images with different sizes
        small_path = os.path.join(self.temp_dir, 'small.png')
        large_path = os.path.join(self.temp_dir, 'large.png')
        
        small_img = Image.new('RGB', (50, 50), color='red')
        large_img = Image.new('RGB', (100, 100), color='red')
        
        small_img.save(small_path)
        large_img.save(large_path)
        
        result = images_are_different(small_path, large_path)
        self.assertTrue(result)
    
    def test_images_are_different_different_modes(self):
        """Test image comparison with different color modes."""
        rgb_path = os.path.join(self.temp_dir, 'rgb.png')
        rgba_path = os.path.join(self.temp_dir, 'rgba.png')
        
        rgb_img = Image.new('RGB', (100, 100), color='red')
        rgba_img = Image.new('RGBA', (100, 100), color='red')
        
        rgb_img.save(rgb_path)
        rgba_img.save(rgba_path)
        
        # Should handle mode conversion and compare successfully
        result = images_are_different(rgb_path, rgba_path)
        self.assertFalse(result)  # Same content, just different modes


if __name__ == '__main__':
    unittest.main()
