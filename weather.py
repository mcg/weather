import os
import pytz
import re
import time
import requests
import requests_cache
from datetime import timedelta
from bs4 import BeautifulSoup
import xml.etree.ElementTree as ET
from PIL import Image, ImageSequence, ImageChops
from feedgen.feed import FeedGenerator
from discord import SyncWebhook, File, Embed
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from email.utils import parsedate_to_datetime
import logging

# Configure logging (will be set up later based on arguments)
logger = logging.getLogger(__name__)

def setup_logging(log_file_path=None):
    """Set up logging configuration."""
    handlers = [logging.StreamHandler()]
    
    if log_file_path:
        handlers.append(logging.FileHandler(log_file_path))
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=handlers
    )

# Define expiration times for model image, since it doesn't expire correctly
urls_expire_after = {
    'https://web.uwm.edu/hurricane-models/models/*': timedelta(hours=1)
}

# Set up the cache to respect HTTP cache headers
requests_cache.install_cache('weather_cache', cache_control=True, urls_expire_after=urls_expire_after)

storm_pattern = re.compile(r'.*(Tropical Storm|Hurricane).*Graphics.*', re.IGNORECASE)
speg_pattern = re.compile(r'.*Summary for (Tropical Storm|Hurricane).*', re.IGNORECASE)

def fetch_xml_feed():
    """Fetch and parse the XML feed from NOAA."""
    logger.info("Fetching XML feed from NOAA")
    
    url = 'https://www.nhc.noaa.gov/index-at.xml'
    response = requests.get(url)
    xml_content = response.content

    # Parse the XML content
    soup = BeautifulSoup(xml_content, 'xml')
    no_storms = soup.find(string=re.compile(r'Tropical cyclone formation is not expected during the next 7 days', re.IGNORECASE)) is not None

    if no_storms:
        logger.info("No tropical cyclones expected in the next 7 days found")
    
    # Check for active storms
    titles = soup.find_all('title', string=storm_pattern)
    if len(titles) > 0:
        no_storms = False
        logger.info(f"Found {len(titles)} active storms")
    else:
        logger.info("No active storms found")

    return no_storms, soup

def find_cyclones_in_feed(soup, map_name):
    """Find all cyclones in the XML feed for a specific map type."""
    logger.info(f"Searching for cyclones with map type: {map_name}")
    
    titles = soup.find_all('title', string=storm_pattern)
    speg_titles = soup.find_all('title', string=speg_pattern)

    cyclones = []
    for title in titles:
        description = title.find_next('description').text
        cdata_soup = BeautifulSoup(description, 'html.parser')

        # Find the img tag for the specific map
        img_tag = cdata_soup.find('img', src=lambda src: map_name in src if src else False)
        if img_tag:
            pattern = re.compile(r'(Tropical\sStorm|Hurricane) (.*?) Graphics', re.IGNORECASE)
            match = pattern.search(title.text)
            if match:
                storm_name = match.group(2).strip()
                storm_type = match.group(1).strip()
                if storm_type in ['Hurricane', 'Tropical Storm']:
                    # Find the associated nhc:atcf tag within nhc:Cyclone for this storm
                    speg_model = None
                    for speg_title in speg_titles:
                        item = speg_title.find_parent('item')
                        if item:
                            # Look for nhc:Cyclone tag first, then find nhc:atcf within it
                            cyclone_tag = item.find('nhc:Cyclone')
                            if cyclone_tag:
                                atcf_tag = cyclone_tag.find('nhc:atcf')
                                if atcf_tag:
                                    speg_model = atcf_tag.text.lower()
                    
                    cyclones.append({
                        'storm_name': storm_name, 
                        'image_url': img_tag['src'],
                        'speg_model': speg_model
                    })
                    logger.info(f"Found cyclone: {storm_name} ({storm_type}) with image URL: {img_tag['src']}, SPEG Model: {speg_model}")
    
    logger.info(f"Found {len(cyclones)} cyclones")
    return cyclones

def fetch_and_process_single_image(url, image_file_path, name, max_frames=10, threshold=0.001):
    """
    Fetch a single image and process it into PNG and GIF formats.
    
    Args:
        url: Image URL to fetch
        image_file_path: Directory to save images
        name: Name for the image files
        max_frames: Maximum number of frames to keep in GIF
        threshold: Threshold for image difference detection
    
    Returns:
        tuple: (png_filename, gif_filename, response, is_new_image)
    """
    logger.info(f"Fetching image: {name}")
    response = requests.get(url)
    
    png_filename = f"{image_file_path}/{name}.png"
    gif_filename = f"{image_file_path}/{name}.gif"
    
    # Handle caching
    if response.from_cache:
        logger.info(f"{name} image from cache")
        return png_filename, gif_filename, response, False
    
    logger.info(f"{name} image not from cache")
    
    # Save new image to temporary file first for comparison
    temp_png = f"{png_filename}.tmp"
    with open(temp_png, 'wb') as image_file:
        image_file.write(response.content)
    
    # Compare with existing image
    is_new_image = images_are_different(temp_png, png_filename, threshold)
    
    if is_new_image:
        logger.info(f"{name} image is different, processing")
        # Replace the old image with the new one
        if os.path.exists(png_filename):
            os.remove(png_filename)
        os.rename(temp_png, png_filename)
        
        # Process GIF
        _process_gif(png_filename, gif_filename, max_frames)
    else:
        logger.info(f"{name} image is identical to existing, skipping processing")
        # Remove temporary file since image hasn't changed
        os.remove(temp_png)
    
    return png_filename, gif_filename, response, is_new_image

def _process_gif(png_filename, gif_filename, max_frames):
    """Process PNG into GIF, either creating new or appending to existing."""
    if not os.path.exists(gif_filename):
        # Create new GIF from PNG
        with Image.open(png_filename) as img:
            img.save(gif_filename, 'GIF')
        print(f"Created new GIF: {gif_filename}")
    else:
        # Append to existing GIF
        with Image.open(gif_filename) as gif:
            frames = [frame.copy() for frame in ImageSequence.Iterator(gif)]
        
        with Image.open(png_filename) as new_frame:
            new_frame = new_frame.convert('RGBA')
            frames.append(new_frame)
        
        # Limit number of frames
        if len(frames) >= max_frames:
            frames.pop(0)
        
        # Save updated GIF
        frames[0].save(gif_filename, save_all=True, append_images=frames[1:], loop=0, duration=500)
        print(f"Updated GIF: {gif_filename} (frames: {len(frames)})")

def fetch_all_weather_images(soup, image_file_path, threshold=0.001):
    """
    Fetch all weather images including static outlook and cyclone images.
    
    Args:
        soup: BeautifulSoup object of the XML feed
        image_file_path: Directory to save images
        threshold: Threshold for image difference detection
    
    Returns:
        tuple: (static_images_dict, cyclone_images_list)
    """
    logger.info("Starting to fetch all weather images")
    all_images = {}
    
    # Fetch static seven-day outlook
    static_url = 'https://www.nhc.noaa.gov/xgtwo/two_atl_7d0.png'
    png_file, gif_file, response, is_new_image = fetch_and_process_single_image(
        static_url, image_file_path, 'two_atl_7d0', max_frames=10, threshold=threshold
    )
    
    all_images['static'] = {
        'url': static_url,
        'png': png_file,
        'gif': gif_file,
        'type': 'static',
        'response': response,
        'is_new_image': is_new_image
    }
    
    # Fetch cyclone images
    cyclones = find_cyclones_in_feed(soup, '5day_cone_with_line_and_wind')
    cyclone_images = []
    
    for cyclone in cyclones:
        storm_name = cyclone['storm_name']
        image_url = cyclone['image_url']
        speg_model = cyclone.get('speg_model')
        
        # Fetch NHC cone image
        png_file, gif_file, response, is_new_image = fetch_and_process_single_image(
            image_url, image_file_path, f"{storm_name}_5day_cone_with_line_and_wind", max_frames=10, threshold=threshold
        )
        
        cyclone_image = {
            'name': storm_name,
            'type': 'cone',
            'png': png_file,
            'gif': gif_file,
            'url': image_url,
            'response': response,
            'is_new_image': is_new_image
        }
        cyclone_images.append(cyclone_image)

        
        # If we have a SPEG model ID, also fetch hurricane models image
        if speg_model:
            hurricane_models_url = f"https://web.uwm.edu/hurricane-models/models/{speg_model}.png"
            logger.info(f"Fetching hurricane models image for {storm_name}: {hurricane_models_url}")
            
            try:
                hm_png_file, hm_gif_file, hm_response, hm_is_new_image = fetch_and_process_single_image(
                    hurricane_models_url, image_file_path, f"{storm_name}_hurricane_models", max_frames=10, threshold=threshold
                )
                
                cyclone_image = {
                    'name': f"{storm_name} Hurricane Models",
                    'type': 'speg',
                    'png': hm_png_file,
                    'gif': hm_gif_file,
                    'url': hurricane_models_url,
                    'response': hm_response,
                    'is_new_image': hm_is_new_image
                }
                cyclone_images.append(cyclone_image)
                logger.info(f"Successfully fetched hurricane models image for {storm_name}")
            except Exception as e:
                logger.warning(f"Failed to fetch hurricane models image for {storm_name}: {e}")
                cyclone_image = None
        
    
    logger.info(f"Processed {len(cyclone_images)} cyclone images")
    return all_images, cyclone_images

def generate_rss_feed(static_image_data, rss_file_path):
    """Generate RSS feed for the static weather image."""
    timestamp = int(time.time())
    url = static_image_data['url']
    response = static_image_data['response']
    
    fg = FeedGenerator()
    fg.title('Seven-Day Atlantic Graphical Tropical Weather Outlook')
    fg.description('Extracted graphic from the NOAA National Hurricane Center. Updated every six hours.')
    fg.link(href=url)

    fe = fg.add_entry()
    fe.title('Weather Image')
    fe.link(href=url)
    fe.description(f'Atlantic Weather Image. <img src="{url}#{timestamp}" alt="Weather Image"/>')
    fe.enclosure(url, str(len(response.content)), 'image/png')
    fe.id(f"{url}#{timestamp}")

    fg.rss_file(rss_file_path)

def upload_to_slack_and_discord(static_image_data, cyclone_images, slack_token, discord_webhook_url):
    """Upload images to Slack and Discord. Only call this with images that should be uploaded."""
    logger.info("Starting upload to Slack and Discord")
    
    # Parse Last-Modified header for static image
    # static_response = static_image_data['response']
    # last_modified_utc = parsedate_to_datetime(static_response.headers['Last-Modified'])
    # eastern = pytz.timezone('US/Eastern')
    # last_modified_et = last_modified_utc.astimezone(eastern)
    # last_modified = last_modified_et.strftime('%Y-%m-%d %I:%M %p %Z')

    # Setup Slack
    client = WebClient(token=slack_token)
    file_uploads = []
    
    # Add static images
    if static_image_data:
        file_uploads.extend([
            {"file": static_image_data['png'], "title": "Seven-Day Outlook"},
            {"file": static_image_data['gif'], "title": "Last 10 maps"},
        ])
        logger.info("Added static images to Slack upload queue")
    
    # Add cyclone images
    for cyclone in cyclone_images:
        if cyclone['type'] == 'cone':
            file_uploads.extend([
                {"file": cyclone['png'], "title": cyclone['name']},
                {"file": cyclone['gif'], "title": f"{cyclone['name']} Loop"},
            ])
            logger.info(f"Added {cyclone['name']} images to Slack upload queue")
        
        # Add hurricane models images if available
        if cyclone['type'] == 'speg':
            file_uploads.extend([
                {"file": cyclone['png'], "title": f"{cyclone['name']} Models"},
                {"file": cyclone['gif'], "title": f"{cyclone['name']} Models Loop"},
            ])
            logger.info(f"Added {cyclone['name']} hurricane models images to Slack upload queue")

    # Upload to Slack
    if file_uploads:
        try:
            logger.info(f"Uploading {len(file_uploads)} files to Slack")
            response = client.files_upload_v2(
                file_uploads=file_uploads,
                channel="C2BRCNET1",  # Active channel
                # test channel
                # channel="C07KTS31M1T",
                initial_comment="Atlantic Tropical Weather Update",
            )
            logger.info("Successfully uploaded files to Slack")
        except SlackApiError as e:
            logger.error(f"Slack API error: {e.response['error']}")
            raise ValueError(f"Slack API error: {e.response['error']}")
    else:
        logger.info("No new images to upload to Slack")
    
    # Setup Discord
    webhook = SyncWebhook.from_url(discord_webhook_url)
    
    # Upload static images to Discord
    if static_image_data:
        logger.info("Uploading static images to Discord")
        with open(static_image_data['png'], 'rb') as img_file, open(static_image_data['gif'], 'rb') as gif_file:
            webhook.send(
                content="Seven-Day Outlook and Map Loop",
                files=[File(img_file, filename="outlook.png"), File(gif_file, filename="outlook.gif")]
            )
        logger.info("Successfully uploaded static images to Discord")

    # Upload cyclone images to Discord
    for cyclone in cyclone_images:
        if cyclone['type'] == 'cone':
            logger.info(f"Uploading {cyclone['name']} to Discord")
        
            # Upload NHC cone images
            with open(cyclone['png'], 'rb') as img_file, open(cyclone['gif'], 'rb') as gif_file:
                webhook.send(
                    content=f"**{cyclone['name']} - NHC Cone**",
                    files=[File(img_file, filename=f"{cyclone['name']}.png"), File(gif_file, filename=f"{cyclone['name']}.gif")]
                )
        
        # Upload hurricane models images if available
        if cyclone['type'] == 'speg':
            logger.info(f"Uploading {cyclone['name']} hurricane models to Discord")
            with open(cyclone['png'], 'rb') as img_file, open(cyclone['gif'], 'rb') as gif_file:
                webhook.send(
                    content=f"**{cyclone['name']} - Hurricane Models**",
                    files=[File(img_file, filename=f"{cyclone['name']}_models.png"), File(gif_file, filename=f"{cyclone['name']}_models.gif")]
                )
        
        logger.info(f"Successfully uploaded {cyclone['name']} to Discord")
    
    logger.info("Completed upload to Slack and Discord")

def delete_images(image_file_path):
    """Delete all PNG and GIF files in the specified directory."""
    logger.info(f"Deleting images from {image_file_path}")
    deleted_count = 0
    
    for file_name in os.listdir(image_file_path):
        if file_name.endswith(('.png', '.gif')):
            file_path = os.path.join(image_file_path, file_name)
            os.remove(file_path)
            logger.info(f"Deleted {file_path}")
            deleted_count += 1
    
    logger.info(f"Deleted {deleted_count} image files")

def images_are_different(new_image_path, existing_image_path, threshold=0.001):
    """
    Compare two images using PIL ImageChops to determine if they're different.
    
    Args:
        new_image_path: Path to the newly downloaded image
        existing_image_path: Path to the existing image file
        threshold: Percentage threshold for considering images different (0.01 = 1%)
    
    Returns:
        bool: True if images are different, False if they're the same
    """
    if not os.path.exists(existing_image_path):
        logger.info(f"No existing image found at {existing_image_path}, treating as different")
        return True
    
    try:
        # Open both images and ensure they're the same size and mode
        with Image.open(new_image_path) as new_img, Image.open(existing_image_path) as existing_img:
            # Convert to RGB if necessary for comparison
            if new_img.mode != 'RGB':
                new_img = new_img.convert('RGB')
            if existing_img.mode != 'RGB':
                existing_img = existing_img.convert('RGB')
            
            # Ensure images are the same size
            if new_img.size != existing_img.size:
                logger.info(f"Images have different sizes: {new_img.size} vs {existing_img.size}")
                return True
            
            # Calculate the difference using ImageChops
            diff = ImageChops.difference(new_img, existing_img)
            
            # Calculate the percentage of different pixels
            # Convert to grayscale for easier analysis
            diff_gray = diff.convert('L')
            
            # Count non-zero pixels (different pixels)
            pixels = list(diff_gray.getdata())
            different_pixels = sum(1 for pixel in pixels if pixel > 0)
            total_pixels = len(pixels)
            
            difference_percentage = different_pixels / total_pixels
            
            logger.info(f"Image comparison: {difference_percentage:.4f} ({difference_percentage*100:.2f}%) pixels different")
            
            return difference_percentage > threshold
            
    except Exception as e:
        logger.error(f"Error comparing images: {e}")
        # If we can't compare, assume they're different to be safe
        return True

def get_images_to_upload(static_images, cyclone_images):
    """
    Filter images that should be uploaded based on cache status and whether they're new.
    
    Args:
        static_images: Dictionary containing static image data
        cyclone_images: List of cyclone image dictionaries
    
    Returns:
        tuple: (static_image_to_upload, cyclone_images_to_upload)
    """
    # Filter static image - upload if not from cache and is new
    static_data = static_images['static']
    upload_static = static_data if (not static_data['response'].from_cache and static_data.get('is_new_image', True)) else None
    
    # Filter cyclone images - upload if not from cache and is new
    upload_cyclones = []
    for cyclone in cyclone_images:
        # Check if main cyclone image should be uploaded
        should_upload_main = not cyclone['response'].from_cache and cyclone.get('is_new_image', True)
        
        # Check if hurricane models image should be uploaded
        should_upload_hm = False
        if cyclone.get('hurricane_models'):
        if cyclone.get('speg_model'):
            hm_data = cyclone['speg_model']
            should_upload_hm = not hm_data['response'].from_cache and hm_data.get('is_new_image', True)
        
        # Upload if either image is new
        if should_upload_main or should_upload_hm:
            upload_cyclones.append(cyclone)
    
    return upload_static, upload_cyclones

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description='Fetch and process weather images.')
    parser.add_argument('rss_file_path', type=str, help='Path to save the RSS feed file.')
    parser.add_argument('image_file_path', type=str, help='Path to save the image file.')
    parser.add_argument('slack_webhook_url', type=str, help='Slack webhook URL to send notifications.')
    parser.add_argument('slack_token', type=str, help='Slack API token for uploading files.')
    parser.add_argument('discord_webhook_url', type=str, help='Discord webhook URL for posting images.')
    parser.add_argument('--log-file', type=str, help='Path to log file (optional).')
    parser.add_argument('--threshold', type=float, default=0.001, help='Threshold for image difference detection (default: 0.001).')

    args = parser.parse_args()
    
    # Set up logging with optional file logging
    setup_logging(args.log_file)
    
    logger.info("Starting weather image processing")
    logger.info(f"RSS file path: {args.rss_file_path}")
    logger.info(f"Image file path: {args.image_file_path}")

    no_storms, soup = fetch_xml_feed()
    
    if not no_storms:
        logger.info("Processing weather images - storms detected")
        
        # Fetch all images in one place
        static_images, cyclone_images = fetch_all_weather_images(soup, args.image_file_path, args.threshold)
        
        # Generate RSS feed
        logger.info("Generating RSS feed")
        generate_rss_feed(static_images['static'], args.rss_file_path)
        
        # Filter images that should be uploaded
        upload_static, upload_cyclones = get_images_to_upload(static_images, cyclone_images)
        
        # Upload to platforms only if there are images to upload
        if upload_static or upload_cyclones:
            upload_to_slack_and_discord(
                upload_static, 
                upload_cyclones, 
                args.slack_token, 
                args.discord_webhook_url
            )
        else:
            logger.info("No new images to upload")
        
        logger.info(f"Processing complete - handled {len(cyclone_images)} cyclone images")
    else:
        logger.info("No tropical cyclones expected in the next 7 days")
        logger.info("Cleaning up old image cache files")
        delete_images(args.image_file_path)
        logger.info("Cleanup complete")