✈️ SkyWatch: Real-Time Air Traffic Control & Collision Prevention
SkyWatch is a real-time flight tracking application that visualizes live air traffic data, predicts flight paths using physics-based modeling, and detects potential collisions in real-time.

🔗 Live Demo
(Note: If the demo takes a moment to load, the server is spinning up from sleep mode!)

🚀 Key Features
Real-Time Tracking: Fetches live flight data from the OpenSky Network API.

Predictive Modeling: Uses Kalman Filters to smooth erratic GPS data and predict a plane's position 60 seconds into the future (Ghost Plane visualization).

Collision Detection: Algorithms run continuously to identify aircraft on converging paths, highlighting them in Red/Yellow based on proximity and closure rate.

Live Breadcrumbs: "Tron-style" trails visualize the recent history of flight paths, smoothed to remove API jitter.

Region Search: "Search This Area" feature allows users to query any bounding box on Earth, complete with a spotlight effect for visual focus.

Altitude Visualization: Aircraft are color-coded based on altitude (White for ground/landing, Cyan for climbing, Purple for cruising).

🛠️ Engineering Highlights
1. The Jitter Problem & Kalman Filters
Raw API data is often noisy, with planes "teleporting" or freezing between updates. To solve this, I implemented a Kalman Filter for each aircraft.

State Estimation: The app maintains a physics state (Position + Velocity) for every plane.

Prediction vs. Correction: Between API updates (every 20s), the app predicts the plane's movement. When new data arrives, it corrects the prediction based on the reliability of the signal.

Result: Smooth, realistic animations at 60fps instead of jumpy static updates.

2. Geodetic Coordinate Smoothing
Visualizing rotation on a Mercator projection caused alignment issues at higher latitudes (the "Squashed Map" bug).

Solution: Implemented a Cosine Correction algorithm in the rotation logic.

Math: Calculated the flight vector using Math.atan2(vLng * Math.cos(lat), vLat) to ensure aircraft icons and prediction lines align perfectly with their true path over the ground.

3. Smart History Management
Storing global flight history is resource-prohibitive.

Optimization: Instead of a database, the app uses a "Live Breadcrumb" system stored in browser memory.

Smoothing: Points are only recorded if the aircraft moves >50m, filtering out micro-jitters and creating clean, dotted flight trails.

💻 Tech Stack
Frontend: Vue 3 (Composition API), Leaflet.js (Mapping), CSS3 (Animations)

Backend: Python, FastAPI, Pandas (Data processing), NumPy (Physics calculations)

Deployment: Render (Web Service for Python, Static Site for Vue)

API: OpenSky Network

🔧 How to Run Locally
Clone the Repository

Bash

git clone https://github.com/[YOUR-USERNAME]/[REPO-NAME].git
cd [REPO-NAME]
Backend Setup

Bash

# Create virtual environment
python -m venv venv
source venv/bin/activate  # (or venv\Scripts\activate on Windows)

# Install dependencies
pip install -r requirements.txt

# Run Server
uvicorn api:app --reload
Frontend Setup

Bash

cd atc-frontend
npm install
npm run dev
View App: Open http://localhost:5173

🔮 Future Roadmap
Weather Overlay: Integrating OpenWeatherMap to visualize storms impacting flight paths.

Runway Incursion Detection: Geofencing airport runways to detect unauthorized ground movement.

3D View: Porting the visualization to Mapbox GL JS for 3D altitude rendering.

Built by [Your Name]

LinkedIn

GitHub
