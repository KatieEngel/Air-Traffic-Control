import requests
import time
import json

frames = []
print("Recording 15 frames (5 minutes) of live SkyWatch data...")
print("Make sure your local FastAPI server is running!")

for i in range(15):
    print(f"Recording frame {i+1}/15...")
    # This hits your local backend using your default New York bounding box
    url = "http://127.0.0.1:8000/flights?min_lat=40.038&max_lat=41.214&min_long=-74.974&max_long=-72.748"
    
    try:
        response = requests.get(url)
        frames.append(response.json())
    except Exception as e:
        print(f"Error: {e}. Is your uvicorn server running?")
        break
        
    time.sleep(20) # Wait 20 seconds just like the frontend does

with open("demo_data.json", "w") as f:
    json.dump(frames, f)
    
print("Success! Saved to demo_data.json")