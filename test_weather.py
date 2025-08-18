import unittest
import tempfile
import os
import shutil
from unittest.mock import Mock, patch, mock_open
from PIL import Image
from bs4 import BeautifulSoup

# Import the functions we want to test
from weather import (
    fetch_xml_feed, extract_storm_info, find_speg_model, find_cyclones_in_feed,
    images_are_different, update_gif, process_single_image, fetch_all_weather_images,
    generate_rss_feed, upload_files_to_slack, upload_files_to_discord, delete_images,
    WeatherImage, setup_logging
)


class TestWeatherFunctions(unittest.TestCase):
    """Test suite for weather.py functions."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.test_image_path = os.path.join(self.temp_dir, 'test_image.png')
        self.test_gif_path = os.path.join(self.temp_dir, 'test_image.gif')
        
        # Create a simple test image
        test_img = Image.new('RGB', (100, 100), color='red')
        test_img.save(self.test_image_path)
    
    def tearDown(self):
        """Clean up test fixtures."""
        shutil.rmtree(self.temp_dir)
    
    @patch('weather.requests.get')
    def test_fetch_xml_feed_no_storms(self, mock_get):
        """Test fetching XML feed when no storms are expected."""
        mock_response = Mock()
        mock_response.content = b'''<?xml version="1.0" encoding="UTF-8"?>
        <rss><channel>
        <description>Tropical cyclone formation is not expected during the next 7 days</description>
        </channel></rss>'''
        mock_get.return_value = mock_response
        
        no_storms, soup = fetch_xml_feed()
        
        self.assertTrue(no_storms)
        self.assertIsInstance(soup, BeautifulSoup)
        mock_get.assert_called_once_with('https://www.nhc.noaa.gov/index-at.xml')
    
    @patch('weather.requests.get')
    def test_fetch_xml_feed_with_storms(self, mock_get):
        """Test fetching XML feed with active storms."""
        mock_response = Mock()
        mock_response.content = b'''<?xml version="1.0" encoding="UTF-8"?>
        <rss><channel>
        <item><title>Hurricane Maria Graphics</title></item>
        </channel></rss>'''
        mock_get.return_value = mock_response
        
        no_storms, soup = fetch_xml_feed()
        
        self.assertFalse(no_storms)
        self.assertIsInstance(soup, BeautifulSoup)
    
    def test_extract_storm_info_hurricane(self):
        """Test extracting hurricane information from title element."""
        mock_title = Mock()
        mock_title.text = "Hurricane Maria Graphics"
        
        result = extract_storm_info(mock_title)
        
        self.assertIsNotNone(result)
        if result is not None:
            self.assertEqual(result['name'], 'Maria')
            self.assertEqual(result['type'], 'Hurricane')
    
    def test_extract_storm_info_tropical_storm(self):
        """Test extracting tropical storm information from title element."""
        mock_title = Mock()
        mock_title.text = "Tropical Storm Alex Graphics"
        
        result = extract_storm_info(mock_title)
        
        self.assertIsNotNone(result)
        if result is not None:
            self.assertEqual(result['name'], 'Alex')
            self.assertEqual(result['type'], 'Tropical Storm')
    
    def test_extract_storm_info_invalid(self):
        """Test extracting storm info from invalid title."""
        mock_title = Mock()
        mock_title.text = "Random Weather Update"
        
        result = extract_storm_info(mock_title)
        
        self.assertIsNone(result)
    
    def test_find_speg_model(self):
        """Test finding SPEG model from XML soup."""
        xml_content = '''<?xml version="1.0" encoding="UTF-8"?>
        <rss xmlns:nhc="https://www.nhc.noaa.gov">
        <channel>
        <item>
            <title>Summary for Hurricane Maria</title>
            <nhc:Cyclone>
                <nhc:atcf>AL152017</nhc:atcf>
            </nhc:Cyclone>
        </item>
        </channel>
        </rss>'''
        
        soup = BeautifulSoup(xml_content, 'xml')
        result = find_speg_model(soup, 'Maria')
        
        self.assertEqual(result, 'al152017')
    
    def test_find_speg_model_not_found(self):
        """Test finding SPEG model when none exists."""
        xml_content = '''<?xml version="1.0" encoding="UTF-8"?>
        <rss><channel></channel></rss>'''
        
        soup = BeautifulSoup(xml_content, 'xml')
        result = find_speg_model(soup, 'Maria')
        
        self.assertIsNone(result)
    
    def test_find_cyclones_in_feed(self):
        """Test finding cyclones in XML feed."""
        xml_content = '''<?xml version="1.0" encoding="UTF-8"?>
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
        </rss>'''
        
        soup = BeautifulSoup(xml_content, 'xml')
        cyclones = find_cyclones_in_feed(soup)
        
        self.assertEqual(len(cyclones), 1)
        self.assertEqual(cyclones[0]['storm_name'], 'Maria')
        self.assertEqual(cyclones[0]['storm_type'], 'Hurricane')
        self.assertEqual(cyclones[0]['speg_model'], 'al152017')
    
    def test_images_are_different_no_existing_file(self):
        """Test image comparison when existing file doesn't exist."""
        new_image_path = self.test_image_path
        non_existent_path = os.path.join(self.temp_dir, 'nonexistent.png')
        
        result = images_are_different(new_image_path, non_existent_path)
        
        self.assertTrue(result)
    
    def test_images_are_different_same_images(self):
        """Test image comparison with identical images."""
        # Create two identical images
        img1_path = os.path.join(self.temp_dir, 'img1.png')
        img2_path = os.path.join(self.temp_dir, 'img2.png')
        
        test_img = Image.new('RGB', (100, 100), color='blue')
        test_img.save(img1_path)
        test_img.save(img2_path)
        
        result = images_are_different(img1_path, img2_path)
        
        self.assertFalse(result)
    
    def test_images_are_different_different_images(self):
        """Test image comparison with different images."""
        img1_path = os.path.join(self.temp_dir, 'img1.png')
        img2_path = os.path.join(self.temp_dir, 'img2.png')
        
        img1 = Image.new('RGB', (100, 100), color='blue')
        img2 = Image.new('RGB', (100, 100), color='red')
        img1.save(img1_path)
        img2.save(img2_path)
        
        result = images_are_different(img1_path, img2_path)
        
        self.assertTrue(result)
    
    def test_update_gif_new_file(self):
        """Test creating a new GIF file."""
        gif_path = os.path.join(self.temp_dir, 'new.gif')
        
        update_gif(self.test_image_path, gif_path)
        
        self.assertTrue(os.path.exists(gif_path))
    
    def test_update_gif_existing_file(self):
        """Test updating an existing GIF file."""
        # Create initial GIF
        gif_path = os.path.join(self.temp_dir, 'existing.gif')
        initial_img = Image.new('RGB', (100, 100), color='green')
        initial_img.save(gif_path, 'GIF')
        
        update_gif(self.test_image_path, gif_path)
        
        self.assertTrue(os.path.exists(gif_path))
    
    @patch('weather.requests.get')
    def test_process_single_image_from_cache(self, mock_get):
        """Test processing a single image from cache."""
        mock_response = Mock()
        mock_response.content = b'fake_image_data'
        mock_response.from_cache = True
        mock_get.return_value = mock_response
        
        result = process_single_image(
            'http://example.com/test.png',
            'test_image',
            self.temp_dir
        )
        
        self.assertIsInstance(result, WeatherImage)
        self.assertEqual(result.name, 'test_image')
        self.assertFalse(result.is_new)
        self.assertEqual(result.image_type, 'cached')
    
    @patch('weather.update_gif')
    @patch('weather.images_are_different')
    @patch('weather.requests.get')
    def test_process_single_image_new_image(self, mock_get, mock_images_diff, mock_update_gif):
        """Test processing a new/different image."""
        mock_response = Mock()
        mock_response.content = b'fake_image_data'
        mock_response.from_cache = False
        mock_get.return_value = mock_response
        
        mock_images_diff.return_value = True
        
        result = process_single_image(
            'http://example.com/test.png',
            'test_image',
            self.temp_dir
        )
        
        self.assertIsInstance(result, WeatherImage)
        self.assertTrue(result.is_new)
        self.assertEqual(result.image_type, 'processed')
        mock_update_gif.assert_called_once()
    
    @patch('weather.find_cyclones_in_feed')
    @patch('weather.process_single_image')
    def test_fetch_all_weather_images(self, mock_process_image, mock_find_cyclones):
        """Test fetching all weather images."""
        mock_soup = Mock()
        
        # Mock cyclones data
        mock_find_cyclones.return_value = [
            {
                'storm_name': 'Maria',
                'storm_type': 'Hurricane',
                'image_url': 'http://example.com/maria_cone.png',
                'speg_model': 'al152017'
            }
        ]
        
        # Mock process_single_image to return WeatherImage objects
        def mock_process_side_effect(url, name, image_dir, threshold=0.001):
            if 'two_atl_7d0' in name:
                img = WeatherImage(name, f'{image_dir}/{name}.png', f'{image_dir}/{name}.gif', url, True, 'static')
            elif 'cone' in name:
                img = WeatherImage(name, f'{image_dir}/{name}.png', f'{image_dir}/{name}.gif', url, True, 'cone')
            else:
                img = WeatherImage(name, f'{image_dir}/{name}.png', f'{image_dir}/{name}.gif', url, True, 'speg')
            return img
        
        mock_process_image.side_effect = mock_process_side_effect
        
        images = fetch_all_weather_images(mock_soup, self.temp_dir)
        
        # Should have static + cone + speg images
        self.assertEqual(len(images), 3)
        self.assertEqual(images[0].image_type, 'static')
        self.assertEqual(images[1].image_type, 'cone')
        self.assertEqual(images[2].image_type, 'speg')
    
    @patch('weather.FeedGenerator')
    def test_generate_rss_feed(self, mock_fg_class):
        """Test RSS feed generation."""
        mock_fg = Mock()
        mock_fg_class.return_value = mock_fg
        mock_fe = Mock()
        mock_fg.add_entry.return_value = mock_fe
        
        static_image = WeatherImage(
            'test_image', 
            self.test_image_path, 
            self.test_gif_path, 
            'http://example.com/test.png', 
            True, 
            'static'
        )
        
        rss_path = os.path.join(self.temp_dir, 'test.rss')
        
        generate_rss_feed(static_image, rss_path)
        
        mock_fg.title.assert_called_once()
        mock_fg.description.assert_called_once()
        mock_fg.link.assert_called_once()
        mock_fe.title.assert_called_once()
        mock_fe.link.assert_called_once()
        mock_fe.description.assert_called_once()
        mock_fg.rss_file.assert_called_once_with(rss_path)
    
    @patch('weather.WebClient')
    def test_upload_files_to_slack(self, mock_webclient_class):
        """Test uploading files to Slack."""
        mock_client = Mock()
        mock_webclient_class.return_value = mock_client
        
        images = [
            WeatherImage('static', self.test_image_path, self.test_gif_path, 'url', True, 'static'),
            WeatherImage('cone', self.test_image_path, self.test_gif_path, 'url', True, 'cone')
        ]
        
        upload_files_to_slack(images, 'fake_token', 'fake_channel')
        
        mock_webclient_class.assert_called_once_with(token='fake_token')
        mock_client.files_upload_v2.assert_called_once()
    
    @patch('weather.SyncWebhook')
    def test_upload_files_to_discord(self, mock_webhook_class):
        """Test uploading files to Discord."""
        mock_webhook = Mock()
        mock_webhook_class.from_url.return_value = mock_webhook
        
        images = [
            WeatherImage('static', self.test_image_path, self.test_gif_path, 'url', True, 'static')
        ]
        
        # Mock file opening
        with patch('builtins.open', mock_open(read_data=b'fake_file_data')):
            upload_files_to_discord(images, 'fake_webhook_url')
        
        mock_webhook_class.from_url.assert_called_once_with('fake_webhook_url')
        mock_webhook.send.assert_called()
    
    def test_delete_images(self):
        """Test deleting images from directory."""
        # Create test files
        png_file = os.path.join(self.temp_dir, 'test.png')
        gif_file = os.path.join(self.temp_dir, 'test.gif')
        txt_file = os.path.join(self.temp_dir, 'test.txt')
        
        with open(png_file, 'w') as f:
            f.write('fake png')
        with open(gif_file, 'w') as f:
            f.write('fake gif')
        with open(txt_file, 'w') as f:
            f.write('fake txt')
        
        delete_images(self.temp_dir)
        
        # PNG and GIF should be deleted, TXT should remain
        self.assertFalse(os.path.exists(png_file))
        self.assertFalse(os.path.exists(gif_file))
        self.assertTrue(os.path.exists(txt_file))
    
    def test_weather_image_dataclass(self):
        """Test WeatherImage dataclass."""
        image = WeatherImage(
            name='test',
            png_path='/path/to/test.png',
            gif_path='/path/to/test.gif',
            url='http://example.com/test.png',
            is_new=True,
            image_type='static'
        )
        
        self.assertEqual(image.name, 'test')
        self.assertEqual(image.png_path, '/path/to/test.png')
        self.assertEqual(image.gif_path, '/path/to/test.gif')
        self.assertEqual(image.url, 'http://example.com/test.png')
        self.assertTrue(image.is_new)
        self.assertEqual(image.image_type, 'static')
    
    @patch('weather.logging.basicConfig')
    def test_setup_logging_no_file(self, mock_basic_config):
        """Test logging setup without file."""
        setup_logging()
        
        mock_basic_config.assert_called_once()
        args, kwargs = mock_basic_config.call_args
        self.assertEqual(len(kwargs['handlers']), 1)  # Only StreamHandler
    
    @patch('weather.logging.FileHandler')
    @patch('weather.logging.basicConfig')
    def test_setup_logging_with_file(self, mock_basic_config, mock_file_handler):
        """Test logging setup with file."""
        mock_file_handler.return_value = Mock()
        
        setup_logging('/path/to/log.txt')
        
        mock_basic_config.assert_called_once()
        mock_file_handler.assert_called_once_with('/path/to/log.txt')


if __name__ == '__main__':
    unittest.main()
