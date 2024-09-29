import os
import re
import time
import requests
import requests_cache
from bs4 import BeautifulSoup
import xml.etree.ElementTree as ET
from PIL import Image, ImageSequence
from feedgen.feed import FeedGenerator
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

# Set up the cache to respect HTTP cache headers
requests_cache.install_cache('weather_cache', cache_control=True)

def fetch_and_process_xml_feed(map_name):
    # Fetch the XML content from the URL
    url = 'https://www.nhc.noaa.gov/index-at.xml'
    response = requests.get(url)
    xml_content = response.content

    # Parse the XML content
    soup = BeautifulSoup(xml_content, 'xml')

    # Find all titles that include "Tropical Storm"
    pattern = re.compile(r'.*(Tropical Storm|Hurricane).*Graphics.*', re.IGNORECASE)
    titles = soup.find_all('title', string=pattern)

    cyclones = []
    # Extract the img src
    for title in titles:
        description = title.find_next('description').text
        cdata_soup = BeautifulSoup(description, 'html.parser')

        #this assumes the first img tag we fins it the one we want, if noaa changes this, we break
        img_tag = cdata_soup.find('img', src=lambda src: map_name in src if src else False)
        if img_tag:
            storm_name = ''
            pattern = re.compile(r'(Tropical\sStorm|Hurricane) (.*?) Graphics', re.IGNORECASE)
            match = pattern.search(title.text)
            if match:
                storm_name = match.group(2).strip()
                # storm_type = match.group(1).strip()
                # if storm_type == 'Hurricane':
                cyclones.append({'storm_name': storm_name, 'image_url': img_tag['src']})
    return cyclones

def generate_cyclone_images(map_name, image_file_path):

    cyclones = fetch_and_process_xml_feed(map_name)
    all_images = []

    for cyclone in cyclones:
        print(f"Fetching image for {cyclone['storm_name']}")
        response = requests.get(cyclone['image_url'])
        image_file_name = f"{image_file_path}/{cyclone['storm_name']}_{map_name}.png"
        gif_file_name = f"{image_file_path}/{cyclone['storm_name']}_{map_name}.gif"

        # Handle the response
        if not response.from_cache:
            print(f"{cyclone['storm_name']} image not from cache")
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
                all_images.append({'png': image_file_name, 'gif': gif_file_name, 'name': cyclone['storm_name']})

    return url, all_images

def fetch_and_process_image(image_file_path):
    # URL to fetch
    url = 'https://www.nhc.noaa.gov/xgtwo/two_atl_7d0.png'

    print(f"Fetching static image from {url}")
    # Make a conditional GET request
    response = requests.get(url)

    # Save the image to a file
    image_file_name = f"{image_file_path}/two_atl_7d0.png"
    gif_file_name = f"{image_file_path}/two_atl_7d0.gif"

    # Handle the response
    if not response.from_cache:
        print(f"Saving new static image to {image_file_name}")
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

    return url, image_file_name, gif_file_name, response

def generate_rss_feed(url, rss_file_path, response):
    timestamp = int(time.time())
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
    fg.rss_file(rss_file_path)


def upload_to_slack(images, image_file_name, gif_file_name, slack_token, image_response):
    # Setup Slack
    client = WebClient(token=slack_token)

    file_uploads = []
    if not image_response.from_cache:
        file_uploads=[
            {
            "file": image_file_name,
            "title": "Seven-Day Outlook",
            },
            {
            "file": gif_file_name,
            "title": "Last 30 maps",
            },
        ]
    for i in images:
        print(f"Uploading {i['name']} to slack")
        file_uploads.append(
            {
            "file": i['png'],
            "title": i['name'],
            }
        )
        file_uploads.append(
            {
            "file": i['gif'],
            "title": i['name'],
            }
        )

    try:
        # Upload the files to Slack
        response = client.files_upload_v2(
            file_uploads=file_uploads,
            # Active channel
            channel="C2BRCNET1",
            # test channel
            #channel="C07KTS31M1T",
            initial_comment="Atlantic Tropical Weather Update",
        )

    except SlackApiError as e:
        raise ValueError(f"Slack API error: {e.response['error']}")

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description='Fetch and process weather images.')
    parser.add_argument('rss_file_path', type=str, help='Path to save the RSS feed file.')
    parser.add_argument('image_file_path', type=str, help='Path to save the image file.')
    parser.add_argument('slack_webhook_url', type=str, help='Slack webhook URL to send notifications.')
    parser.add_argument('slack_token', type=str, help='Slack API token for uploading files.')

    args = parser.parse_args()

    url, image_file_name, gif_file_name, image_response = fetch_and_process_image(args.image_file_path)
    url, images = generate_cyclone_images('5day_cone_with_line_and_wind',args.image_file_path)
    generate_rss_feed(url, args.rss_file_path, image_response)
    upload_to_slack(images, image_file_name, gif_file_name, args.slack_token, image_response)