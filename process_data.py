from traffic.data.samples import switzerland  # A built-in large sample
import pandas as pd

# --- 1. Load Sample Data ---
print("Loading sample trajectory data...")
# This dataset comes with the library and has full paths!
flight_data = switzerland 

print(f"Loaded {len(flight_data)} flights.")

# --- 2. Convert to DataFrame ---
df = flight_data.data # Extract the raw pandas dataframe

# --- 3. Filter/Process ---
# The columns in this dataset are standard: 'latitude', 'longitude', 'flight_id'
print("Columns found:", df.columns)

# Save this as your training set
OUTPUT_FILE = "data/final_markov_training_set.csv"
df.to_csv(OUTPUT_FILE, index=False)

print(f"Saved training data to {OUTPUT_FILE}")