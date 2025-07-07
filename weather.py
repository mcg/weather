import os
import pytz
import re
import time
import requests
import requests_cache
from bs4 import BeautifulSoup
import xml.etree.ElementTree as ET
from PIL import Image, ImageSequence
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

# Set up the cache to respect HTTP cache headers
requests_cache.install_cache('weather_cache', cache_control=True)

storm_pattern = re.compile(r'.*(Post-Tropical\sCyclone|Tropical Storm|Hurricane).*Graphics.*', re.IGNORECASE)

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

    cyclones = []
    for title in titles:
        description = title.find_next('description').text
        cdata_soup = BeautifulSoup(description, 'html.parser')

        # Find the img tag for the specific map
        img_tag = cdata_soup.find('img', src=lambda src: map_name in src if src else False)
        if img_tag:
            match = storm_pattern.search(title.text)
            if match:
                storm_name = match.group(2).strip()
                storm_type = match.group(1).strip()
                if storm_type in ['Hurricane', 'Tropical Storm']:
                    cyclones.append({
                        'storm_name': storm_name, 
                        'image_url': img_tag['src']
                    })
                    logger.info(f"Found cyclone: {storm_name} ({storm_type}) with image URL: {img_tag['src']}")
    
    logger.info(f"Found {len(cyclones)} cyclones")
    return cyclones

def fetch_and_process_single_image(url, image_file_path, name, max_frames=10):
    """
    Fetch a single image and process it into PNG and GIF formats.
    
    Args:
        url: Image URL to fetch
        image_file_path: Directory to save images
        name: Name for the image files
        max_frames: Maximum number of frames to keep in GIF
    
    Returns:
        tuple: (png_filename, gif_filename, response)
    """
    logger.info(f"Fetching image: {name}")
    response = requests.get(url)
    
    png_filename = f"{image_file_path}/{name}.png"
    gif_filename = f"{image_file_path}/{name}.gif"
    
    # Handle caching
    if response.from_cache:
        logger.info(f"{name} image from cache")
        return png_filename, gif_filename, response
    
    print(f"{name} image not from cache")
    
    # Save PNG file
    with open(png_filename, 'wb') as image_file:
        image_file.write(response.content)
    
    # Process GIF
    _process_gif(png_filename, gif_filename, max_frames)
    
    return png_filename, gif_filename, response

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

def fetch_all_weather_images(soup, image_file_path):
    """
    Fetch all weather images including static outlook and cyclone images.
    
    Returns:
        tuple: (static_images_dict, cyclone_images_list)
    """
    logger.info("Starting to fetch all weather images")
    all_images = {}
    
    # Fetch static seven-day outlook
    static_url = 'https://www.nhc.noaa.gov/xgtwo/two_atl_7d0.png'
    png_file, gif_file, response = fetch_and_process_single_image(
        static_url, image_file_path, 'two_atl_7d0', max_frames=10
    )
    
    all_images['static'] = {
        'url': static_url,
        'png': png_file,
        'gif': gif_file,
        'response': response
    }
    
    # Fetch cyclone images
    cyclones = find_cyclones_in_feed(soup, '5day_cone_with_line_and_wind')
    cyclone_images = []
    
    for cyclone in cyclones:
        storm_name = cyclone['storm_name']
        image_url = cyclone['image_url']
        
        png_file, gif_file, response = fetch_and_process_single_image(
            image_url, image_file_path, f"{storm_name}_5day_cone_with_line_and_wind", max_frames=10
        )
        
        cyclone_images.append({
            'name': storm_name,
            'png': png_file,
            'gif': gif_file,
            'url': image_url,
            'response': response
        })
    
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
    """Upload images to Slack and Discord."""
    logger.info("Starting upload to Slack and Discord")
    
    # Parse Last-Modified header for static image
    static_response = static_image_data['response']
    last_modified_utc = parsedate_to_datetime(static_response.headers['Last-Modified'])
    eastern = pytz.timezone('US/Eastern')
    last_modified_et = last_modified_utc.astimezone(eastern)
    last_modified = last_modified_et.strftime('%Y-%m-%d %I:%M %p %Z')

    # Setup Slack
    client = WebClient(token=slack_token)
    file_uploads = []
    
    # Add static images if not from cache
    if not static_response.from_cache:
        file_uploads.extend([
            {"file": static_image_data['png'], "title": "Seven-Day Outlook"},
            {"file": static_image_data['gif'], "title": "Last 10 maps"},
        ])
        logger.info("Added static images to Slack upload queue")
    
    # Add cyclone images
    for cyclone in cyclone_images:
        if not cyclone['response'].from_cache:
            file_uploads.extend([
                {"file": cyclone['png'], "title": cyclone['name']},
                {"file": cyclone['gif'], "title": f"{cyclone['name']} Loop"},
            ])
            logger.info(f"Added {cyclone['name']} images to Slack upload queue")

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
    
    # Upload static images to Discord if not from cache
    if not static_response.from_cache:
        logger.info("Uploading static images to Discord")
        with open(static_image_data['png'], 'rb') as img_file, open(static_image_data['gif'], 'rb') as gif_file:
            webhook.send(
                content="Seven-Day Outlook and Map Loop",
                files=[File(img_file, filename="outlook.png"), File(gif_file, filename="outlook.gif")]
            )
        logger.info("Successfully uploaded static images to Discord")

    # Upload cyclone images to Discord
    for cyclone in cyclone_images:
        if not cyclone['response'].from_cache:
            logger.info(f"Uploading {cyclone['name']} to Discord")
            with open(cyclone['png'], 'rb') as img_file, open(cyclone['gif'], 'rb') as gif_file:
                webhook.send(
                    content=f"**{cyclone['name']}**",
                    files=[File(img_file, filename=f"{cyclone['name']}.png"), File(gif_file, filename=f"{cyclone['name']}.gif")]
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

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description='Fetch and process weather images.')
    parser.add_argument('rss_file_path', type=str, help='Path to save the RSS feed file.')
    parser.add_argument('image_file_path', type=str, help='Path to save the image file.')
    parser.add_argument('slack_webhook_url', type=str, help='Slack webhook URL to send notifications.')
    parser.add_argument('slack_token', type=str, help='Slack API token for uploading files.')
    parser.add_argument('discord_webhook_url', type=str, help='Discord webhook URL for posting images.')
    parser.add_argument('--log-file', type=str, help='Path to log file (optional).')

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
        static_images, cyclone_images = fetch_all_weather_images(soup, args.image_file_path)
        
        # Generate RSS feed
        logger.info("Generating RSS feed")
        generate_rss_feed(static_images['static'], args.rss_file_path)
        
        # Upload to platforms
        upload_to_slack_and_discord(
            static_images['static'], 
            cyclone_images, 
            args.slack_token, 
            args.discord_webhook_url
        )
        
        logger.info(f"Processing complete - handled {len(cyclone_images)} cyclone images")
    else:
        logger.info("No tropical cyclones expected in the next 7 days")
        logger.info("Cleaning up old image cache files")
        delete_images(args.image_file_path)
        logger.info("Cleanup complete")