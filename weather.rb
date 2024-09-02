require 'httpx'
require 'nokogiri'
require 'rss'

# Step 1: Fetch the XML content from the URL using a conditional GET with httpx and response cache
url = 'https://www.nhc.noaa.gov/xgtwo/two_atl_7d0.png'

# Configure HTTPX with a file cache
client = HTTPX.plugin(:response_cache)

response = client.get(url)

if response.status == 304
  puts "Content has not changed."
else
  # Process the response body as needed
  puts "Content has changed."
end