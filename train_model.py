import pandas as pd
import numpy as np
import json
import os

# --- CONFIGURATION ---
INPUT_FILE = "data/final_markov_training_set.csv"
MODEL_FILE = "data/markov_model.json"

# Grid Precision: 0.1 degrees is roughly 11km (Approx 7 miles)
# Smaller number = More detailed grid, but needs MORE data to train.
GRID_SIZE = 0.1 

def get_grid_key(lat, lon):
    """
    Converts a precise GPS coordinate into a Grid ID.
    Example: 40.123, -73.987 -> "401_-739"
    """
    lat_idx = int(lat / GRID_SIZE)
    lon_idx = int(lon / GRID_SIZE)
    return f"{lat_idx}_{lon_idx}"

def get_center_from_key(key):
    """
    Converts a Grid ID back to the Lat/Lon of the center of that square.
    Used for prediction later.
    """
    lat_idx, lon_idx = map(int, key.split('_'))
    lat = (lat_idx * GRID_SIZE) + (GRID_SIZE / 2)
    lon = (lon_idx * GRID_SIZE) + (GRID_SIZE / 2)
    return lat, lon

def train():
    print("Loading training data...")
    if not os.path.exists(INPUT_FILE):
        print("Error: CSV not found!")
        return

    # Load data
    df = pd.read_csv(INPUT_FILE)
    
    # Sort by ICAO24 (the transponder hex code) and Time to ensure we track the path correctly
    df = df.sort_values(by=['icao24', 'timestamp'])

    transitions = {}
    print("Training Markov Model (Counting transitions)...")

    # Group by flight so we don't connect the end of one flight to the start of another
    for flight_id, flight_data in df.groupby('icao24'):
        
        # Get all coordinates for this single flight as a list
        coords = list(zip(flight_data['latitude'], flight_data['longitude']))
        
        # Loop through the path
        for i in range(len(coords) - 1):
            current_lat, current_lon = coords[i]
            next_lat, next_lon = coords[i+1]

            # Convert to Grid IDs
            curr_state = get_grid_key(current_lat, current_lon)
            next_state = get_grid_key(next_lat, next_lon)

            # If the plane stayed in the same box, ignore it because we're trying to predict where planes go AFTER they live this box
            if curr_state == next_state: continue 

            # Initialize dictionary if new
            if curr_state not in transitions:
                transitions[curr_state] = {}
            
            if next_state not in transitions[curr_state]:
                transitions[curr_state][next_state] = 0
            
            # Count the transition
            transitions[curr_state][next_state] += 1

    print(f"Processed {len(transitions)} unique grid cells.")

    # --- NORMALIZE (Convert Counts to Probabilities) ---
    # Example: { "A": {"B": 9, "C": 1} } -> { "A": {"B": 0.9, "C": 0.1} }
    markov_model = {}
    
    for state, outcomes in transitions.items():
        total_moves = sum(outcomes.values())
        markov_model[state] = []
        
        for next_state, count in outcomes.items():
            prob = count / total_moves
            # Store as list of objects for easy use in Frontend/API
            # We calculate the center Lat/Lon of the target box now to save time later
            target_lat, target_lon = get_center_from_key(next_state)
            
            markov_model[state].append({
                "next_grid": next_state,
                "prob": round(prob, 4),
                "target_lat": round(target_lat, 4),
                "target_lon": round(target_lon, 4)
            })
            
        # Sort by highest probability first
        markov_model[state].sort(key=lambda x: x['prob'], reverse=True)

    # Save to JSON
    with open(MODEL_FILE, 'w') as f:
        json.dump(markov_model, f)
    
    print(f"Model saved to {MODEL_FILE}")

if __name__ == "__main__":
    train()