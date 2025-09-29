import os
import re
import time
import requests
import requests_cache
from datetime import timedelta
from bs4 import BeautifulSoup
from PIL import Image, ImageSequence, ImageChops
from feedgen.feed import FeedGenerator
from discord import SyncWebhook, File
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
import logging
from dataclasses import dataclass
from typing import List, Optional, Tuple, Dict
from dotenv import load_dotenv

# Configure logging
logger = logging.getLogger(__name__)

def setup_logging(log_file_path=None):
    """Set up logging configuration."""
    if log_file_path:
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[logging.StreamHandler(), logging.FileHandler(log_file_path)]
        )
    else:
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[logging.StreamHandler()]
        )

# Set up caching
urls_expire_after = {'https://web.uwm.edu/hurricane-models/models/*': timedelta(hours=1)}
requests_cache.install_cache('weather_cache', cache_control=True, urls_expire_after=urls_expire_after)

# Regex patterns
STORM_PATTERN = re.compile(r'.*(Tropical Storm|Tropical Depression|Hurricane).*Graphics.*', re.IGNORECASE)
SPEG_PATTERN = re.compile(r'.*Summary for (Tropical\sStorm|Hurricane).*', re.IGNORECASE)
STORM_NAME_PATTERN = re.compile(r'(Tropical\sStorm|Tropical\sDepression|Hurricane) (.*?) Graphics', re.IGNORECASE)

@dataclass
class WeatherImage:
    """Represents a weather image with all its metadata."""
    name: str
    png_path: str
    gif_path: str
    url: str
    is_new: bool
    image_type: str  # 'static', 'cone', 'speg'

def fetch_xml_feed() -> Tuple[int, BeautifulSoup]:
    """Fetch and parse the XML feed from NOAA."""
    logger.info("Fetching XML feed from NOAA")
    
    url = 'https://www.nhc.noaa.gov/index-at.xml'
    response = requests.get(url)
    soup = BeautifulSoup(response.content, 'xml')
    
    # Check for "no storms expected" message
    no_storms_text = "Tropical cyclone formation is not expected during the next 7 days"
    no_storms = soup.find(string=re.compile(no_storms_text, re.IGNORECASE)) is not None
    
    # Check for active storms
    storm_titles = soup.find_all('title', string=STORM_PATTERN)
    no_storms = len(storm_titles)
    logger.info(f"Found {len(storm_titles)} active storms" if storm_titles else "No active storms found")
    if no_storms:
        logger.info("No tropical cyclones expected in the next 7 days")
    
    return no_storms, soup

def extract_storm_info(title_element) -> Optional[Dict[str, str]]:
    """Extract storm information from a title element."""
    match = STORM_NAME_PATTERN.search(title_element.text)
    if not match:
        return None
    
    storm_type = match.group(1).strip()
    storm_name = match.group(2).strip()
    
    if storm_type not in ['Hurricane', 'Tropical Storm', 'Tropical Depression']:
        return None
    
    return {'name': storm_name, 'type': storm_type}

def find_speg_model(soup, storm_name: str) -> Optional[str]:
    """Find SPEG model ID for a given storm."""
    speg_titles = soup.find_all('title', string=SPEG_PATTERN)
    
    for speg_title in speg_titles:
        item = speg_title.find_parent('item')
        if item:
            cyclone_tag = item.find('nhc:Cyclone')
            if cyclone_tag:
                atcf_tag = cyclone_tag.find('nhc:atcf')
                if atcf_tag:
                    return atcf_tag.text.lower()
    return None

def find_cyclones_in_feed(soup) -> List[Dict[str, str]]:
    """Find all cyclones in the XML feed."""
    logger.info("Searching for cyclones in feed")
    
    storm_titles = soup.find_all('title', string=STORM_PATTERN)
    cyclones = []
    
    for title in storm_titles:
        storm_info = extract_storm_info(title)
        if not storm_info:
            continue
        
        description = title.find_next('description').text
        cdata_soup = BeautifulSoup(description, 'html.parser')
        
        # Find the 5-day cone image
        img_tag = cdata_soup.find('img', src=lambda src: '5day_cone_with_line_and_wind' in src if src else False)
        if img_tag:
            speg_model = find_speg_model(soup, storm_info['name'])
            
            cyclones.append({
                'storm_name': storm_info['name'],
                'storm_type': storm_info['type'],
                'image_url': img_tag['src'], # pyright: ignore[reportArgumentType]
                'speg_model': speg_model
            })
            logger.info(f"Found cyclone: {storm_info['name']} ({storm_info['type']}) with SPEG model: {speg_model}")
    
    logger.info(f"Found {len(cyclones)} cyclones")
    return cyclones

def images_are_different(new_image_path: str, existing_image_path: str, threshold: float = 0.001) -> bool:
    """Compare two images to determine if they're different."""
    if not os.path.exists(existing_image_path):
        logger.info(f"No existing image found at {existing_image_path}")
        return True
    
    try:
        with Image.open(new_image_path) as new_img, Image.open(existing_image_path) as existing_img:
            # Normalize images for comparison
            if new_img.mode != 'RGB':
                new_img = new_img.convert('RGB')
            if existing_img.mode != 'RGB':
                existing_img = existing_img.convert('RGB')
            
            if new_img.size != existing_img.size:
                logger.info(f"Images have different sizes: {new_img.size} vs {existing_img.size}")
                return True
            
            # Calculate pixel differences
            diff = ImageChops.difference(new_img, existing_img)
            diff_gray = diff.convert('L')
            pixels = list(diff_gray.getdata()) # pyright: ignore[reportArgumentType]
            
            different_pixels = sum(1 for pixel in pixels if pixel > 0)
            difference_percentage = different_pixels / len(pixels)
            
            logger.info(f"Image comparison: {difference_percentage:.4f} ({difference_percentage*100:.2f}%) pixels different")
            return difference_percentage > threshold
            
    except Exception as e:
        logger.error(f"Error comparing images: {e}")
        return True

def update_gif(png_path: str, gif_path: str, max_frames: int = 10):
    """Create or update a GIF with the new PNG frame."""
    if not os.path.exists(gif_path):
        # Create new GIF
        with Image.open(png_path) as img:
            img.save(gif_path, 'GIF')
        logger.info(f"Created new GIF: {gif_path}")
    else:
        # Append to existing GIF
        with Image.open(gif_path) as gif:
            frames = [frame.copy() for frame in ImageSequence.Iterator(gif)]
        
        with Image.open(png_path) as new_frame:
            frames.append(new_frame.convert('RGBA'))
        
        # Limit frames
        if len(frames) > max_frames:
            frames = frames[-max_frames:]
        
        frames[0].save(gif_path, save_all=True, append_images=frames[1:], loop=0, duration=500)
        logger.info(f"Updated GIF: {gif_path} (frames: {len(frames)})")

def process_single_image(url: str, base_name: str, image_dir: str, threshold: float = 0.001) -> WeatherImage:
    """Download and process a single image, returning WeatherImage object."""
    logger.info(f"Processing image: {base_name}")
    
    response = requests.get(url)
    png_path = f"{image_dir}/{base_name}.png"
    gif_path = f"{image_dir}/{base_name}.gif"
    
    # Handle caching
    if response.from_cache: # pyright: ignore[reportAttributeAccessIssue]
        logger.info(f"{base_name} from cache")
        return WeatherImage(base_name, png_path, gif_path, url, False, "cached")
    
    # Save and compare
    temp_path = f"{png_path}.tmp"
    with open(temp_path, 'wb') as f:
        f.write(response.content)
    
    is_different = images_are_different(temp_path, png_path, threshold)
    
    if is_different:
        logger.info(f"{base_name} is new/different")
        if os.path.exists(png_path):
            os.remove(png_path)
        os.rename(temp_path, png_path)
        update_gif(png_path, gif_path)
    else:
        logger.info(f"{base_name} unchanged")
        os.remove(temp_path)
    
    return WeatherImage(base_name, png_path, gif_path, url, is_different, "processed")

def fetch_all_weather_images(soup, image_dir: str, threshold: float = 0.001) -> List[WeatherImage]:
    """Fetch all weather images and return a list of WeatherImage objects."""
    logger.info("Fetching all weather images")
    images = []
    
    # Static seven-day outlook
    static_url = 'https://www.nhc.noaa.gov/xgtwo/two_atl_7d0.png'
    static_image = process_single_image(static_url, 'two_atl_7d0', image_dir, threshold)
    static_image.image_type = 'static'
    images.append(static_image)
    
    # Cyclone images
    cyclones = find_cyclones_in_feed(soup)
    for cyclone in cyclones:
        storm_name = cyclone['storm_name']
        
        # NHC cone image
        cone_image = process_single_image(
            cyclone['image_url'], 
            f"{storm_name}_5day_cone_with_line_and_wind", 
            image_dir, 
            threshold
        )
        cone_image.image_type = 'cone'
        images.append(cone_image)
        
        # Hurricane models image (if available)
        if cyclone['speg_model']:
            models_url = f"https://web.uwm.edu/hurricane-models/models/{cyclone['speg_model']}.png"
            try:
                models_image = process_single_image(
                    models_url, 
                    f"{storm_name}_hurricane_models", 
                    image_dir, 
                    threshold
                )
                models_image.image_type = 'speg'
                images.append(models_image)
                logger.info(f"Fetched hurricane models for {storm_name}")
            except Exception as e:
                logger.warning(f"Failed to fetch hurricane models for {storm_name}: {e}")
    
    logger.info(f"Processed {len(images)} images total")
    return images

def generate_rss_feed(static_image: WeatherImage, rss_file_path: str):
    """Generate RSS feed for the static weather image."""
    timestamp = int(time.time())
    img_size = 0
    if os.path.exists(static_image.png_path):
        img_size = str(os.path.getsize(static_image.png_path))
    
    fg = FeedGenerator()
    fg.title('Seven-Day Atlantic Graphical Tropical Weather Outlook')
    fg.description('Extracted graphic from the NOAA National Hurricane Center. Updated every six hours.')
    fg.link(href=static_image.url)

    fe = fg.add_entry()
    fe.title('Weather Image')
    fe.link(href=static_image.url)
    fe.description(f'Atlantic Weather Image. <img src="{static_image.url}#{timestamp}" alt="Weather Image"/>')
    fe.enclosure(static_image.url, img_size, 'image/png')
    fe.id(f"{static_image.url}#{timestamp}")

    fg.rss_file(rss_file_path)

def upload_files_to_slack(images: List[WeatherImage], slack_token: str, upload_channel: str):
    """Upload images to Slack."""
    client = WebClient(token=slack_token)
    file_uploads = []
    
    for image in images:
        if image.image_type == 'static':
            file_uploads.extend([
                {"file": image.png_path, "title": "Seven-Day Outlook"},
                {"file": image.gif_path, "title": "Last 10 maps"},
            ])
        elif image.image_type == 'cone':
            file_uploads.extend([
                {"file": image.png_path, "title": image.name},
                {"file": image.gif_path, "title": f"{image.name} Loop"},
            ])
        elif image.image_type == 'speg':
            file_uploads.extend([
                {"file": image.png_path, "title": f"{image.name} Models"},
                {"file": image.gif_path, "title": f"{image.name} Models Loop"},
            ])
    
    if file_uploads:
        try:
            logger.info(f"Uploading {len(file_uploads)} files to Slack")
            client.files_upload_v2(
                file_uploads=file_uploads,
                channel=upload_channel,
                initial_comment="Atlantic Tropical Weather Update",
            )
            logger.info("Successfully uploaded to Slack")
        except SlackApiError as e:
            logger.error(f"Slack API error: {e.response['error']}")
            raise

def upload_files_to_discord(images: List[WeatherImage], discord_webhook_url: str):
    """Upload images to Discord."""
    webhook = SyncWebhook.from_url(discord_webhook_url)
    
    for image in images:
        if image.image_type == 'static':
            with open(image.png_path, 'rb') as png, open(image.gif_path, 'rb') as gif:
                webhook.send(
                    content="Seven-Day Outlook and Map Loop",
                    files=[File(png, filename="outlook.png"), File(gif, filename="outlook.gif")]
                )
        elif image.image_type == 'cone':
            with open(image.png_path, 'rb') as png, open(image.gif_path, 'rb') as gif:
                webhook.send(
                    content=f"**{image.name} - NHC Cone**",
                    files=[File(png, filename=f"{image.name}.png"), File(gif, filename=f"{image.name}.gif")]
                )
        elif image.image_type == 'speg':
            with open(image.png_path, 'rb') as png, open(image.gif_path, 'rb') as gif:
                webhook.send(
                    content=f"**{image.name} - Hurricane Models**",
                    files=[File(png, filename=f"{image.name}_models.png"), File(gif, filename=f"{image.name}_models.gif")]
                )
    
    logger.info("Successfully uploaded to Discord")

def delete_images(image_dir: str):
    """Delete all PNG and GIF files in the directory."""
    logger.info(f"Deleting images from {image_dir}")
    count = 0
    
    for filename in os.listdir(image_dir):
        if filename.endswith(('.png', '.gif')):
            try:
                os.remove(os.path.join(image_dir, filename))
                count += 1
            except (OSError, PermissionError) as e:
                logger.error(f"Failed to delete {filename}: {e}")
    
    logger.info(f"Deleted {count} image files")

def delete_storm_images(image_dir: str):
    """Delete storm-related PNG and GIF files, but keep static outlook images."""
    logger.info(f"Deleting storm images from {image_dir}")
    count = 0
    
    for filename in os.listdir(image_dir):
        if filename.endswith(('.png', '.gif')):
            # Keep static outlook images (two_atl_7d0)
            if 'two_atl_7d0' not in filename:
                try:
                    os.remove(os.path.join(image_dir, filename))
                    count += 1
                    logger.debug(f"Deleted storm image: {filename}")
                except (OSError, PermissionError) as e:
                    logger.error(f"Failed to delete {filename}: {e}")
    
    logger.info(f"Deleted {count} storm image files")

def main():
    import argparse

    parser = argparse.ArgumentParser(description='Fetch and process weather images.')
    parser.add_argument('--env-file', help='Path to .env file to load environment variables from.')
    parser.add_argument('rss_file_path', nargs='?', help='Path to save the RSS feed file.')
    parser.add_argument('image_file_path', nargs='?', help='Path to save image files.')
    parser.add_argument('slack_webhook_url', nargs='?', help='Slack webhook URL (unused).')
    parser.add_argument('slack_token', nargs='?', help='Slack API token.')
    parser.add_argument('upload_channel', nargs='?', help='Slack channel ID for uploading files.')
    parser.add_argument('discord_webhook_url', nargs='?', help='Discord webhook URL.')
    parser.add_argument('--log-file', help='Path to log file (optional).')
    parser.add_argument('--threshold', type=float, 
                       help='Threshold for image difference detection (default: 0.001).')

    args = parser.parse_args()
    
    # Load .env file if specified
    if args.env_file:
        load_dotenv(args.env_file)
    
    # Helper to get config value from arg or environment
    def get_config_value(arg_value, env_key):
        return arg_value if arg_value is not None else os.getenv(env_key)

    # Get values from args or environment variables
    rss_file_path = get_config_value(args.rss_file_path, 'RSS_FILE_PATH')
    image_file_path = get_config_value(args.image_file_path, 'IMAGE_FILE_PATH')
    slack_webhook_url = get_config_value(args.slack_webhook_url, 'SLACK_WEBHOOK_URL')
    slack_token = get_config_value(args.slack_token, 'SLACK_TOKEN')
    upload_channel = get_config_value(args.upload_channel, 'UPLOAD_CHANNEL')
    discord_webhook_url = get_config_value(args.discord_webhook_url, 'DISCORD_WEBHOOK_URL')
    log_file = get_config_value(args.log_file, 'LOG_FILE')
    
    # Handle threshold with proper type conversion - command line args take precedence
    if args.threshold is not None:
        threshold = args.threshold
    else:
        # Try to get from environment variable, fallback to default
        threshold_env = os.getenv('THRESHOLD')
        if threshold_env is not None:
            try:
                threshold = float(threshold_env)
            except ValueError:
                parser.error(f"Invalid THRESHOLD value '{threshold_env}'. Must be a valid float.")
        else:
            threshold = 0.001  # Default value
    
    # Validate required arguments
    required_args = {
        'rss_file_path': rss_file_path,
        'image_file_path': image_file_path,
        'slack_webhook_url': slack_webhook_url,
        'slack_token': slack_token,
        'upload_channel': upload_channel,
        'discord_webhook_url': discord_webhook_url
    }
    
    missing_args = [name for name, value in required_args.items() if not value]
    if missing_args:
        parser.error(f"Missing required arguments: {', '.join(missing_args)}. "
                    f"Provide them as command line arguments or set them in the .env file.")
    
    setup_logging(log_file)
    logger.info("Starting weather image processing")

    no_storms, soup = fetch_xml_feed()
    
    if no_storms > 0:
        logger.info("Processing weather images - storms detected")
        
        # Fetch all images
        all_images = fetch_all_weather_images(soup, image_file_path, threshold) # pyright: ignore[reportArgumentType]
        
        # Find static image and generate RSS
        static_image = next((img for img in all_images if img.image_type == 'static'), None)
        if static_image:
            generate_rss_feed(static_image, rss_file_path) # pyright: ignore[reportArgumentType]

        # Filter new images for upload
        new_images = [img for img in all_images if img.is_new]
        
        if new_images:
            logger.info(f"Uploading {len(new_images)} new images")
            if slack_token and upload_channel:
                upload_files_to_slack(new_images, slack_token, upload_channel)
            else:
                logger.info("Skipping Slack upload: missing slack_token or upload_channel.")
            if discord_webhook_url:
                upload_files_to_discord(new_images, discord_webhook_url)
            else:
                logger.info("Skipping Discord upload: missing discord_webhook_url.")
        else:
            logger.info("No new images to upload")
        
        logger.info(f"Processing complete - handled {len(all_images)} total images")
    else:
        logger.info("No tropical cyclones expected - checking static image only")
        
        # Clean up old storm images but keep static images
        delete_storm_images(image_file_path) # pyright: ignore[reportArgumentType]
        
        # Process only the static image when no storms are active
        static_url = 'https://www.nhc.noaa.gov/xgtwo/two_atl_7d0.png'
        static_image = process_single_image(static_url, 'two_atl_7d0', image_file_path, args.threshold) # pyright: ignore[reportArgumentType]
        static_image.image_type = 'static'
        
        # Generate RSS feed for the static image
        generate_rss_feed(static_image, rss_file_path) # pyright: ignore[reportArgumentType]
        
        # Only upload if the static image has been updated
        if static_image.is_new:
            logger.info("Static image has been updated - uploading")
            if slack_token and upload_channel:
                upload_files_to_slack([static_image], slack_token, upload_channel)
            else:
                logger.info("Skipping Slack upload: missing Slack token or upload channel")
            if discord_webhook_url:
                upload_files_to_discord([static_image], discord_webhook_url)
            else:
                logger.info("Skipping Discord upload: missing Discord webhook URL")
        else:
            logger.info("Static image unchanged - no upload needed")
        
        logger.info("Processing complete - handled static image only")

if __name__ == "__main__":
    main()