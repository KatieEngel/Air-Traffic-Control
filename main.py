import requests
from requests.auth import HTTPBasicAuth # For the token request
import pandas as pd

import plotly.express as px

# --- Step 1: Get credentials (replace with your actual ID/Secret) ---
CLIENT_ID = "katie.engel31006@gmail.com-api-client"
CLIENT_SECRET = "kiS868RdfTTVwtVpJMbsukWtXmUvMJhR"

# --- Step 2: Get Access Token ---
# OpenSky's token endpoint (example structure)
TOKEN_URL = "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token" # Check OpenSky docs for exact URL

# Request token using client credentials
token_response = requests.post(
    TOKEN_URL,
    auth=HTTPBasicAuth(CLIENT_ID, CLIENT_SECRET), # Or send as 'Authorization: Basic base64(id:secret)'
    data={'grant_type': 'client_credentials'} # Might vary, check API docs
)
token_response.raise_for_status() # Raise an exception for bad status codes
access_token = token_response.json().get("access_token")

# --- Step 3: Use Token for API Call ---

#AREA EXTENT COORDINATE WGS4
lon_min,lat_min=-74.974,40.038
lon_max,lat_max=-72.748,41.214

API_URL = 'https://opensky-network.org/api/states/all?'+'lamin='+str(lat_min)+'&lomin='+str(lon_min)+'&lamax='+str(lat_max)+'&lomax='+str(lon_max)

headers = {
    "Authorization": f"Bearer {access_token}"
}

response = requests.get(API_URL, headers=headers)
response.raise_for_status()
data = response.json()


#LOAD TO PANDAS DATAFRAME
col_name=['icao24','callsign','origin_country','time_position','last_contact','long','lat','baro_altitude','on_ground','velocity',       
'true_track','vertical_rate','sensors','geo_altitude','squawk','spi','position_source']
flight_df=pd.DataFrame(data['states'])
flight_df=flight_df.loc[:,0:16]
flight_df.columns=col_name
flight_df=flight_df.fillna('No Data') #replace NAN with No Data
print(flight_df.head())



# Create the map
fig = px.scatter_map(
    flight_df,
    lat="lat",
    lon="long",
    hover_name="callsign",
    hover_data=["geo_altitude", "velocity", "origin_country"],
    color="geo_altitude", # Color the dots by altitude
    height=600
)

# Set the map style to OpenStreetMap (no API token required)
fig.update_layout(map_style="open-street-map")
fig.update_layout(
    map=dict(
        bounds=dict(
            west=lon_min,  # Your Minimum Longitude (-125...)
            east=lon_max,  # Your Maximum Longitude (-68...)
            south=lat_min, # Your Minimum Latitude (30...)
            north=lat_max  # Your Maximum Latitude (52...)
        )
    )
)
fig.update_layout(margin={"r":0,"t":0,"l":0,"b":0})

# Show the map
fig.show()