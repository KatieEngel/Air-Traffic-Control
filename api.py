from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import requests
from requests.auth import HTTPBasicAuth
import pandas as pd
import os
import csv 
from datetime import datetime
import json
from dotenv import load_dotenv
import time

load_dotenv()

# Load the Markov Model once
MODEL_FILE = "data/markov_model.json"
MARKOV_MODEL = {}

if os.path.exists(MODEL_FILE):
    with open(MODEL_FILE, 'r') as f:
        MARKOV_MODEL = json.load(f)

GRID_SIZE = 0.1 # Must match trainer!

app = FastAPI()

# --- 1. CORS CONFIGURATION ---
# Allows Vue app (running on a different port) to talk to this Python backend.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- 2. CONFIGURATION ---
CLIENT_ID = os.getenv("OPENSKY_CLIENT_ID")
CLIENT_SECRET = os.getenv("OPENSKY_CLIENT_SECRET")

if not CLIENT_ID or not CLIENT_SECRET:
    print("WARNING: OpenSky credentials not found in .env file!")


# --- CONFIGURATION ---

# SET THIS TO TRUE TO SEE PREDICTIONS (SWITZERLAND)
# SET THIS TO FALSE TO COLLECT DATA (LONG ISLAND)
TEST_MODE = False

# --- HELPER FUNCTIONS ---

# Predicting next position of plane using markov model
def predict_next_pos(lat, lon):
    """Looks up the current location in the Markov Model."""
    lat_idx = int(lat / GRID_SIZE)
    lon_idx = int(lon / GRID_SIZE)
    key = f"{lat_idx}_{lon_idx}"

    # Return the most likely next step (first item in list)
    if key in MARKOV_MODEL:
        best_guess = MARKOV_MODEL[key][0] # 0 is highest probability
        return best_guess['target_lat'], best_guess['target_lon']
    return None, None


# Auth Token for API requests
def get_opensky_token():
    TOKEN_URL = "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"
    try:
        response = requests.post(
            TOKEN_URL,
            auth=HTTPBasicAuth(CLIENT_ID, CLIENT_SECRET),
            data={'grant_type': 'client_credentials'},
            timeout=20
        )
        response.raise_for_status()
        return response.json().get("access_token")
    except Exception as e:
        print(f"Token Error: {e}")
        return None
    



# --- 4. API ENDPOINTS ---

@app.get("/")
def home():
    return {"message": "ATC Backend is running!"}

@app.get("/flights")
def get_flights(
    min_lat: float = 40.45, 
    max_lat: float = 41.30, 
    min_long: float = -74.30, 
    max_long: float = -71.80
):
    """
    Fetches live flight data, cleans it, and returns it as JSON.
    """

    token = get_opensky_token()
    if not token:
        raise HTTPException(status_code=500, detail="Failed to authenticate with OpenSky")

    # FIX: Use the parameters (min_lat, etc.) instead of hardcoded LAT_MIN globals
    api_url = f'https://opensky-network.org/api/states/all?lamin={min_lat}&lomin={min_long}&lamax={max_lat}&lomax={max_long}'
    headers = {"Authorization": f"Bearer {token}"}

    try:
        response = requests.get(api_url, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()

        if not data or data['states'] is None:
            return []

        # Convert to DataFrame for easy cleaning (reusing your logic)
        col_names = ['icao24','callsign','origin_country','time_position','last_contact',
                    'long','lat','baro_altitude','on_ground','velocity',       
                    'true_track','vertical_rate','sensors','geo_altitude','squawk','spi','position_source']
        
        df = pd.DataFrame(data['states'], columns=col_names)
        
        # Clean the data
        df['callsign'] = df['callsign'].str.strip()
        df = df.replace({float('nan'): None})
        
        # Convert to list of dicts
        records = df.to_dict(orient="records")

        # ADD PREDICTIONS
        for flight in records:
            if flight['lat'] and flight['long']:
                pred_lat, pred_lon = predict_next_pos(flight['lat'], flight['long'])
                flight['predicted_lat'] = pred_lat
                flight['predicted_long'] = pred_lon
        
        # "records" mode: [{'callsign': 'UAL123', 'lat': 30.1}, ...]
        return records

    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=503, detail=f"OpenSky API Error: {str(e)}")
    
@app.get("/history")
def get_flight_history(hours: int = 24):
    history_file = "data/flight_history.csv"
    
    if not os.path.exists(history_file):
        return {}

    try:
        # 1. Read the CSV
        df = pd.read_csv(history_file)
        
        # --- FIX: Convert String timestamps to DateTime objects ---
        # 'utc=False' keeps it as local time (matching your CSV format)
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        
        # Calculate cutoff time using Pandas Timedelta
        cutoff_time = pd.Timestamp.now() - pd.Timedelta(hours=hours)
        
        print(f"DEBUG: Filtering data after {cutoff_time}")
        
        # Filter
        recent_data = df[df['timestamp'] > cutoff_time]
        print(f"DEBUG: Found {len(recent_data)} points.")
        
        if len(recent_data) == 0:
            return {}

        # 3. Group
        paths = {}
        recent_data = recent_data.sort_values(by=['icao24', 'timestamp'])

        for icao, group in recent_data.groupby('icao24'):
            if len(group) < 2:
                continue
            coords = group[['lat', 'long']].values.tolist()
            paths[icao] = coords

        return paths

    except Exception as e:
        print(f"Error processing history: {e}")
        return {}

@app.get("/flight-track/{icao24}")
def get_flight_track(icao24: str):
    token = get_opensky_token()
    if not token:
        raise HTTPException(status_code=500, detail="Auth failed")

    # OpenSky /tracks endpoint
    # "time=0" asks for the track of the current active flight
    url = f"https://opensky-network.org/api/tracks/?icao24={icao24}&time=0"
    headers = {"Authorization": f"Bearer {token}"}

    try:
        response = requests.get(url, headers=headers, timeout=10)
        
        # 404 means no track available (common for ground planes)
        if response.status_code == 404:
            return {"path": []}
            
        response.raise_for_status()
        data = response.json()
        
        # OpenSky returns data as: {'path': [[time, lat, lon, alt, heading, on_ground], ...]}
        # We only need lat/lon for the map
        raw_path = data.get('path', [])
        
        # Clean it up: simple list of [lat, long]
        clean_path = [[p[1], p[2]] for p in raw_path]
        
        return {"path": clean_path}

    except requests.exceptions.RequestException as e:
        print(f"Track Error: {e}")
        return {"path": []}
