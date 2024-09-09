import os
import time
import argparse
import requests
import requests_cache
from PIL import Image, ImageSequence
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from feedgen.feed import FeedGenerator

# Set up argument parser
parser = argparse.ArgumentParser(description='Fetch weather image and update RSS feed.')
parser.add_argument('rss_file_path', type=str, help='Path to save the RSS feed file.')
parser.add_argument('image_file_path', type=str, help='Path to save the image file.')
parser.add_argument('slack_webhook_url', type=str, help='Slack webhook URL to send notifications.')
parser.add_argument('slack_token', type=str, help='Slack API token for uploading files.')

args = parser.parse_args()

# Set up the cache to respect HTTP cache headers
requests_cache.install_cache('weather_cache', cache_control=True)

# URL to fetch
url = 'https://www.nhc.noaa.gov/xgtwo/two_atl_7d0.png'

# Make a conditional GET request
response = requests.get(url)

# Handle the response
if not response.from_cache:
    timestamp = int(time.time())
    # Save the image to a file
    image_file_name = f"{args.image_file_path}/two_atl_7d0.png"
    gif_file_name = f"{args.image_file_path}/two_atl_7d0.gif"

    with open(image_file_name, 'wb') as image_file:
        image_file.write(response.content)
    
    # Check if GIF file exists, if not copy the PNG file to GIF
    if not os.path.exists(gif_file_name):
        # Open the PNG file
        with Image.open(image_file_name) as img:
            # Convert to GIF and save
            img.save(gif_file_name, 'GIF')
    else:
        # Open the existing GIF file
        with Image.open(gif_file_name) as gif:
            # Extract all frames from the existing GIF
           frames = [frame.copy() for frame in ImageSequence.Iterator(gif)]

        # Open the new frame image
        with Image.open(image_file_name) as new_frame:
            new_frame = new_frame.convert('RGBA')
            # Append the new frame to the list of frames
            frames.append(new_frame)

        # Check if the number of frames is more than 30
        if len(frames) >= 10:
            # Remove the first frame
            frames.pop(0)

        # Save the updated GIF with the new frame
        frames[0].save(gif_file_name, save_all=True, append_images=frames[1:], loop=0, duration=500)

    # Generate RSS feed
    fg = FeedGenerator()
    fg.title('Seven-Day Atlantic Graphical Tropical Weather Outlook')
    fg.description('Extracted graphic from the NOAA National Hurricane Center. Updated every six hours.')
    fg.link(href=url)

    fe = fg.add_entry()
    fe.title('Weather Image')
    fe.link(href=url)
    fe.description(f'Atlantic Weather Image. <img src="{url}#{timestamp}" alt="Weather Image"/>')
    fe.enclosure(url, str(len(response.content)), 'image/png')
    guid = f"{url}#{timestamp}"
    fe.id(guid)

    # Save the RSS feed to a file
    fg.rss_file(args.rss_file_path)

    # Setup Slack 
    slack_token = args.slack_token
    client = WebClient(token=slack_token)

    try:
        # Upload the files to Slack 
        response = client.files_upload_v2(
            file_uploads=[
                {
                "file": image_file_name,
                "title": "Seven-Day Outlook",
                },
                {
                "file": gif_file_name,
                "title": "Last 30 maps",
                },
            ],
            channel="C2BRCNET1",
            #channel="C07KTS31M1T", test channel
            initial_comment="Atlantic Tropical Weather Update",
        )

    except SlackApiError as e:
        raise ValueError(f"Slack API error: {e.response['error']}")