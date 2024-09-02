# weather.py
from feedgen.feed import FeedGenerator
import requests_cache
import requests

# Set up the cache to respect HTTP cache headers
requests_cache.install_cache('weather_cache', cache_control=True)

# URL to fetch
url = 'https://www.nhc.noaa.gov/xgtwo/two_atl_7d0.png'

# Make a conditional GET request
response = requests.get(url)

# Handle the response
if response.from_cache:
    print("Response was retrieved from cache")
else:
     # Generate RSS feed
    fg = FeedGenerator()
    fg.title('Seven-Day Atlantic Graphical Tropical Weather Outlook')
    fg.description('Extracted graphic from the NOAA National Hurricane Center. Updated every six hours.')

    fe = fg.add_entry()
    fe.title('Weather Image')
    fe.link(href=url)
    fe.description('Atlantic Weather Image')
    fe.enclosure(url, str(len(response.content)), 'image/png')

    # Save the RSS feed to a file
    fg.rss_file('weather_feed.xml')

# Save the image if needed
# with open('two_atl_7d0.png', 'wb') as file:
#     file.write(response.content)