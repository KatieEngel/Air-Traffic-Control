<script setup>
import { onMounted, ref } from "vue";
import axios from "axios";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { KalmanFilter } from './utils/KalmanFilter.js';

// --- ICONS ---
const PLANE_ICON_SVG = `
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24" style="filter: drop-shadow(1px 1px 2px rgba(0,0,0,0.5));">
  <path fill="#007bff" d="M21,16V14L13,9V3.5A1.5,1.5 0 0,0 11.5,2A1.5,1.5 0 0,0 10,3.5V9L2,14V16L10,13.5V19L8,20.5V22L11.5,21L15,22V20.5L13,19V13.5L21,16Z"/>
</svg>
`;

// --- STATE ---
const flights = ref([]); // Stores the raw flight data
const map = ref(null); // Stores the map instance
const markers = {}; // Dictionary to track existing markers by ICAO24 ID
const filters = {}; // Store KalmanFilter instances by icao24
const predictionLines = {};
const ghostMarkers = {};
const selectedPlane = ref(null); // Track which plane is selected (store the icao24 string)
const showTrendsToggle = ref(false); // track the toggle state
let trendLayerGroup = null; // Initialize as null, create it in initMap

// --- CONFIG ---
const API_URL = "http://127.0.0.1:8000/flights";
const REFRESH_RATE = 20000; // 20 seconds (OpenSky limit is strict, be careful!)

// Linear Interpolation: Calculates a point 't' percent between start and end
// t=0.1 means "Move 10% of the distance towards the target"
const lerp = (start, end, t) => {
  return start + (end - start) * t;
};

// --- FUNCTIONS ---

// Helper to convert Speed/Heading to Lat/Lon velocity
const getVelocityComponents = (speed, heading, currentLat) => {
  if (!speed || speed === 0) return [0, 0];
  
  // Convert degrees to radians
  const rad = heading * (Math.PI / 180); 

  // 1. Calculate Velocity in Degrees per Second
  
  // LATITUDE (Y-Axis): Corresponds to Cosine in Aviation (0 deg = North = +Y)
  // 1 degree Lat is always ~111,111 meters
  const vLat = Math.cos(rad) * speed * (1 / 111111);

  // LONGITUDE (X-Axis): Corresponds to Sine in Aviation (90 deg = East = +X)
  // 1 degree Long shrinks as you go North. We divide by cos(lat) to compensate.
  // Example: At 40deg N (NY), 1 deg long is smaller, so you cover MORE degrees for the same speed.
  const latRad = currentLat * (Math.PI / 180);
  const vLng = Math.sin(rad) * speed * (1 / (111111 * Math.cos(latRad)));

  return [vLat, vLng]; 
};

// Function to display the plane icon at the correct angle
const createPlaneIcon = (rotation, opacity = 1.0, color = "#ffffff") => {
  // We embed the color directly into the SVG path fill
  const svgHtml = `
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24" 
         style="filter: drop-shadow(0px 2px 4px rgba(0,0,0,0.8));">
      <path fill="${color}" d="M21,16V14L13,9V3.5A1.5,1.5 0 0,0 11.5,2A1.5,1.5 0 0,0 10,3.5V9L2,14V16L10,13.5V19L8,20.5V22L11.5,21L15,22V20.5L13,19V13.5L21,16Z"/>
    </svg>
  `;
  return L.divIcon({
    className: "custom-plane-icon", // We will style this slightly in CSS
    html: `<div style="transform: rotate(${rotation}deg); transform-origin: center; opacity: ${opacity};">
             ${svgHtml}
           </div>`,
    iconSize: [24, 24],
    iconAnchor: [12, 12], // Center the rotation point
  });
};

// --- CORE FUNCTIONS ---

// 1. Initialize the Map
const initMap = () => {
  // Centered on Long Island, NY with Zoom 9
  map.value = L.map("mapContainer").setView([40.626, -73.861], 9);

  // DARK MODE MAP TILES
  L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
    subdomains: 'abcd',
    maxZoom: 20
  }).addTo(map.value);

  trendLayerGroup = L.layerGroup().addTo(map.value); // initialize layer grouping for trends

  // DESELECT ON MAP CLICK
  map.value.on('click', () => {
    // 1. If a plane was selected, turn it back to WHITE
    if (selectedPlane.value && markers[selectedPlane.value]) {
       const oldId = selectedPlane.value;
       const oldFlight = flights.value.find(f => f.icao24 === oldId);
       const oldRotation = oldFlight ? (oldFlight.true_track || 0) : 0;
       
       markers[oldId].setIcon(createPlaneIcon(oldRotation, 1.0, "#ffffff"));
       markers[oldId].setZIndexOffset(0);
    }
    clearAllVisuals();
    selectedPlane.value = null;
  });
};

// 2. Fetch Data from Python Backend
const fetchFlights = async () => {
  try {
    const response = await axios.get(API_URL);
    flights.value = response.data;
    updateMap();
  } catch (error) {
    console.error("Error fetching flights:", error);
  }
};

// // Helper: Draws the prediction line for a single flight
// const drawPredictionLine = (flight) => {
//   // 1. Remove existing line for this plane if it exists
//   if (predictionLines[flight.icao24]) {
//     map.value.removeLayer(predictionLines[flight.icao24]);
//     delete predictionLines[flight.icao24];
//   }

//   // 2. Only draw if this is the SELECTED plane
//   if (selectedPlane.value !== flight.icao24) return;

//   // 3. Calculate Physics (Same math as before)
//   const now = Date.now() / 1000;
//   const [vLat, vLng] = getVelocityComponents(flight.velocity, flight.true_track, flight.lat);
  
//   // Re-calculate the adjusted start point (latency comp) so line attaches to nose
//   const dataTimestamp = flight.time_position || flight.last_contact || now;
//   const lagSeconds = now - dataTimestamp;
//   const startLat = flight.lat + (vLat * lagSeconds);
//   const startLng = flight.long + (vLng * lagSeconds);

//   const PREDICTION_TIME = 60; // 1 minutes
//   const futureLat = startLat + (vLat * PREDICTION_TIME);
//   const futureLng = startLng + (vLng * PREDICTION_TIME);

  
// };

const drawGhostPlane = (flight) => {
  // Only draw if SELECTED
  if (selectedPlane.value !== flight.icao24) return;

  let vLat, vLng, startLat, startLng;
  const now = Date.now() / 1000;

  // Check if we have a Kalman Filter for this plane (We should!)
  if (filters[flight.icao24]) {
    // USE THE FILTER'S MEMORY (Robust against missing API data)
    vLat = filters[flight.icao24].x[2];
    vLng = filters[flight.icao24].x[3];
    
    // We can also use the filter's smoothed position as the start point
    startLat = filters[flight.icao24].x[0];
    startLng = filters[flight.icao24].x[1];
  } else {
    // Fallback if filter is missing (e.g. first frame)
    [vLat, vLng] = getVelocityComponents(flight.velocity, flight.true_track, flight.lat);
    startLat = flight.lat;
    startLng = flight.long;
  }

  // If velocity is basically zero...
  if (Math.abs(vLat) < 0.000001 && Math.abs(vLng) < 0.000001) {
    
    // ...we must REMOVE any existing visuals so they don't get stuck...
    if (predictionLines[flight.icao24]) {
      map.value.removeLayer(predictionLines[flight.icao24]);
      delete predictionLines[flight.icao24];
    }
    if (ghostMarkers[flight.icao24]) {
      map.value.removeLayer(ghostMarkers[flight.icao24]);
      delete ghostMarkers[flight.icao24];
    }
    return;
  }

  // PREDICT: 60 Seconds into the future
  const PREDICTION_TIME = 60; 
  const futureLat = startLat + (vLat * PREDICTION_TIME);
  const futureLng = startLng + (vLng * PREDICTION_TIME);
  const rotation = flight.true_track || 0;

  // A. HANDLE THE PREDICTION LINE
  if (predictionLines[flight.icao24]) {
    // UPDATE EXISTING LINE (No flicker)
    predictionLines[flight.icao24].setLatLngs([
      [startLat, startLng],
      [futureLat, futureLng]
    ]);
  } else {
    // LINE
    const line = L.polyline([[startLat, startLng], [futureLat, futureLng]], {
      color: "#ffcc00",  // Gold/Amber
      weight: 2,
      opacity: 0.9,
      dashArray: "4, 8", // Sparse dots look more "predictive"
    }).addTo(map.value);
    predictionLines[flight.icao24] = line;
  }

  // B. HANDLE THE GHOST MARKER
  if (ghostMarkers[flight.icao24]) {
    // UPDATE EXISTING MARKER (No flicker)
    ghostMarkers[flight.icao24].setLatLng([futureLat, futureLng]);
    ghostMarkers[flight.icao24].setIcon(createPlaneIcon(rotation, 0.4));
  } else {
    // CREATE NEW MARKER
    const ghost = L.marker([futureLat, futureLng], {
      icon: createPlaneIcon(rotation, 0.5, "#ffcc00"), 
      interactive: false 
    }).addTo(map.value);
    ghostMarkers[flight.icao24] = ghost;
  }
};

// Helper: robustly removes ANY visual elements associated with a plane
const clearAllVisuals = (icao24) => {
  Object.keys(predictionLines).forEach(icao => {
    if (predictionLines[icao]) {
      map.value.removeLayer(predictionLines[icao]);
    }
  });
  // Reset the object (delete all keys)
  for (const key in predictionLines) delete predictionLines[key];

  // 2. Wipe all Ghosts
  Object.keys(ghostMarkers).forEach(icao => {
    if (ghostMarkers[icao]) {
      map.value.removeLayer(ghostMarkers[icao]);
    }
  });
  // Reset the object
  for (const key in ghostMarkers) delete ghostMarkers[key];
};

const updateMap = () => {
  const now = Date.now() / 1000;

  flights.value.forEach((flight) => {
    if (flight.lat && flight.long) {

      // Calculate initial velocity estimate
      // Use true_track (heading) and velocity (speed)
      const [vLat, vLng] = getVelocityComponents(flight.velocity, flight.true_track, flight.lat);

      // How old is this data? (Current Time - GPS Time)
      // If time_position is missing, assume 0 lag (fallback)
      const dataTimestamp = flight.time_position || flight.last_contact || now;
      const lagSeconds = now - dataTimestamp;

      // Project the position forward to "Now"
      // If data is 5s old, move the plane 5s worth of travel forward
      let adjustedLat = flight.lat + (vLat * lagSeconds);
      let adjustedLng = flight.long + (vLng * lagSeconds);

      // If filter doesn't exist, create it
      if (!filters[flight.icao24]) {
        filters[flight.icao24] = new KalmanFilter(adjustedLat, adjustedLng, vLat, vLng);
      } 
    
      // Update with the ADJUSTED position (Real-time estimate)
      filters[flight.icao24].update(adjustedLat, adjustedLng);

      // 2. FORCE Update the Velocity (Stop the "Fly Away" bug)
      // This tells the filter: "I don't care what you calculated, THIS is the real speed."
      filters[flight.icao24].setVelocity(vLat, vLng);

      // Flight marker:
      // 1. Calculate Rotation (Default to 0 if missing)
      const rotation = flight.true_track || 0;

      const isSelected = selectedPlane.value === flight.icao24;
      const planeColor = isSelected ? "#ffcc00" : "#ffffff"; // Gold vs White

      // 2. If marker exists, move it and update rotation
      if (markers[flight.icao24]) {
        // Update the icon rotation
        markers[flight.icao24].setIcon(createPlaneIcon(rotation, 1.0, planeColor));

        // Fix z-index: Selected plane should always be on TOP of others
        markers[flight.icao24].setZIndexOffset(isSelected ? 1000 : 0);

        // Update popup text
        markers[flight.icao24].setPopupContent(`
          <b>${flight.callsign || "Unknown"}</b><br>
          Alt: ${flight.geo_altitude}m<br>
          Head: ${rotation}°
        `);
      } else {
        // 3. Create new marker with the icon
        const newMarker = L.marker([flight.lat, flight.long], {
          icon: createPlaneIcon(rotation, 1.0, planeColor),
        });

        // Add Popup normally
        newMarker.bindPopup(`
            <b>${flight.callsign || "Unknown"}</b><br>
            Alt: ${flight.geo_altitude}m
        `);

        // Click Handler for Selection
        newMarker.on('click', (e) => {
          // Prevent the map background click from firing immediately
          L.DomEvent.stopPropagation(e);
          clearAllVisuals(); // Remove lines/ghosts

          // 1. RESET THE *OLD* PLANE TO WHITE
          // We look up the old ID, find its marker, and reset its icon
          if (selectedPlane.value && markers[selectedPlane.value]) {
            const oldId = selectedPlane.value;
            
            // We need the old plane's rotation to redraw the icon correctly.
            // We find it in the current flights list.
            const oldFlight = flights.value.find(f => f.icao24 === oldId);
            const oldRotation = oldFlight ? (oldFlight.true_track || 0) : 0;

            markers[oldId].setIcon(createPlaneIcon(oldRotation, 1.0, "#ffffff")); // White
            markers[oldId].setZIndexOffset(0);
          }

          selectedPlane.value = flight.icao24;

          // 2. Turn THIS marker Gold immediately
          const freshRotation = flight.true_track || 0;
          newMarker.setIcon(createPlaneIcon(freshRotation, 1.0, "#ffcc00"));
          newMarker.setZIndexOffset(1000);

          drawGhostPlane(flight);
        });

        newMarker.addTo(map.value);
        markers[flight.icao24] = newMarker;
      }
      // Update the line logic on every refresh (to keep it synced with the plane)
      drawGhostPlane(flight);
    }
  });
};

const animate = () => {
  requestAnimationFrame(animate);

  Object.keys(markers).forEach(icao24 => {
    const filter = filters[icao24];
    const marker = markers[icao24];

    if (filter && marker) {
      // 1. Get the "Target" (Where the math says we should be)
      const targetPos = filter.predict();
      
      // 2. Get the "Current" (Where the icon actually is)
      const currentLatLng = marker.getLatLng();
      
      // 3. LERP: Move 5% of the way towards the target per frame
      // Lower number (0.01) = Very slow/smooth "drift"
      // Higher number (0.1) = Snappier response
      const SMOOTHING_FACTOR = 0.05; 
      
      const newLat = lerp(currentLatLng.lat, targetPos.lat, SMOOTHING_FACTOR);
      const newLng = lerp(currentLatLng.lng, targetPos.lng, SMOOTHING_FACTOR);
      
      // Update the marker
      marker.setLatLng([newLat, newLng]);

      // UPDATE PREDICTION VISUALS (If this plane is selected)
      if (selectedPlane.value === icao24) {
        
        // Retrieve velocity from the Kalman Filter state
        // x[2] is vLat, x[3] is vLng
        const vLat = filter.x[2];
        const vLng = filter.x[3];

        // Calculate Future Position (60 seconds from NOW)
        // We use 'newLat'/'newLng' so the line stays attached to the plane's nose
        const PREDICTION_TIME = 60;
        const futureLat = newLat + (vLat * PREDICTION_TIME);
        const futureLng = newLng + (vLng * PREDICTION_TIME);

        // Move the Ghost Marker
        if (ghostMarkers[icao24]) {
          ghostMarkers[icao24].setLatLng([futureLat, futureLng]);
        }

        // Move the Prediction Line
        if (predictionLines[icao24]) {
          predictionLines[icao24].setLatLngs([
            [newLat, newLng],       // Start at plane
            [futureLat, futureLng]  // End at ghost
          ]);
        }
      }
    }
  });
};

const toggleTrends = async () => {
  if (showTrendsToggle.value) {
    try {
      console.log("Fetching flight history...");
      
      // Request the last 24 hours of data
      const response = await axios.get("http://127.0.0.1:8000/history?hours=24");
      const historyPaths = response.data;
      
      trendLayerGroup.clearLayers();
      
      let count = 0;

      // Loop through each plane's path
      for (const [icao, path] of Object.entries(historyPaths)) {
        
        // Create a Polyline for this flight
        const line = L.polyline(path, {
          color: '#00e5ff',    // Cyan / Electric Blue
          weight: 1.5,         // Thinner lines look more elegant
          opacity: 0.4,
          className: 'history-line' 
        });

        // Add a tooltip so you can see which plane it was
        line.bindTooltip(icao, { sticky: true });

        trendLayerGroup.addLayer(line);
        count++;
      }
      
      console.log(`Drew ${count} historical flight paths.`);

    } catch (e) {
      console.error("Error loading history", e);
    }
  } else {
    trendLayerGroup.clearLayers();
  }
};

// --- LIFECYCLE ---
onMounted(() => {
  initMap();
  fetchFlights(); // Initial fetch

  // Set up the loop
  setInterval(fetchFlights, REFRESH_RATE);

  animate();
});
</script>

<template>
  <div class="app-container">
    <div class="sidebar">
      <h2>Flight List ({{ flights.length }})</h2>

      <div class="toggle-container">
        <span class="toggle-label">Show Flight Trends</span>
        <label class="switch">
          <input type="checkbox" v-model="showTrendsToggle" @change="toggleTrends">
          <span class="slider round"></span>
        </label>
      </div>

      <hr>

      <ul>
        <li v-for="flight in flights" :key="flight.icao24">
          <strong>{{ flight.callsign || "N/A" }}</strong>
          <span v-if="!flight.lat" class="warning"> (Locating...)</span>
          <br />
          <small>{{ flight.origin_country }}</small>
        </li>
      </ul>
    </div>

    <div id="mapContainer" class="map-view"></div>
  </div>
</template>

<style scoped>
/* 1. Main Container: Dark Background */
.app-container {
  display: flex;
  height: 100vh;
  width: 100%;
  overflow: hidden;
  background-color: #121212; /* Prevents white flashes */
  color: #e0e0e0; /* Light text */
  
}

/* 2. Sidebar: Dark Grey with Border */
.sidebar {
  width: 300px;
  background: #1e1e1e;
  overflow-y: auto;
  padding: 1rem;
  border-right: 1px solid #333;
  box-shadow: 2px 0 5px rgba(0,0,0,0.5);
  z-index: 1000; /* Ensure it sits above map controls if needed */
  scrollbar-color: #e0e0e0 #333;
  scrollbar-width: thin;
  font-family: 'Inter', sans-serif;
  color: #b0b3b8; /* Softer grey text, not pure white */
}

.map-view {
  flex-grow: 1;
  height: 100%;
}

/* 3. List Items: Card Style */
ul {
  list-style: none;
  padding: 0;
}

li {
  background: #2c2c2c;
  margin-bottom: 0.5rem;
  padding: 0.75rem;
  border-radius: 6px;
  border: 1px solid #333;
  transition: all 0.2s ease;
}

/* Call signs and Numbers */
li strong, 
.leaflet-popup-content b {
  color: #fff; /* Bright White */
  font-family: 'JetBrains Mono', monospace; /* Tech look */
  letter-spacing: -0.5px;
}

li small {
  color: #00e5ff; /* Cyan for metadata */
}

/* Hover effect for list items */
li:hover {
  background: #383838;
  border-color: #555;
  transform: translateX(2px);
}

/* Warning Text */
.warning {
  color: #ff4444; /* Brighter red for dark mode */
  font-size: 0.8em;
}

/* 4. Toggle Switch: Dark Theme */
.toggle-container {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 1rem;
  padding: 0.75rem;
  background: #252525;
  border-radius: 8px;
  border: 1px solid #333;
}

.toggle-label {
  font-weight: 600;
  font-size: 0.9rem;
  color: #ccc;
}

.switch {
  position: relative;
  display: inline-block;
  width: 46px;
  height: 24px;
}

.switch input { opacity: 0; width: 0; height: 0; }

.slider {
  position: absolute;
  cursor: pointer;
  top: 0; left: 0; right: 0; bottom: 0;
  background-color: #444; /* Darker off state */
  transition: .4s;
  border-radius: 34px;
}

.slider:before {
  position: absolute;
  content: "";
  height: 18px;
  width: 18px;
  left: 3px;
  bottom: 3px;
  background-color: white;
  transition: .4s;
  border-radius: 50%;
}

input:checked + .slider {
  background-color: #00bcd4; /* Cyan accent color */
}

input:checked + .slider:before {
  transform: translateX(22px);
}

/* Remove Leaflet icon background */
:deep(.leaflet-div-icon) {
  background: transparent;
  border: none;
}

/* Fix popup text color (Popups are white by default) */
:deep(.leaflet-popup-content-wrapper),
:deep(.leaflet-popup-tip) {
  background: #1e1e1e;
  color: #e0e0e0;
  border: 1px solid #444;
}

/* Make the history lines blend together */
:deep(.history-line) {
  mix-blend-mode: screen; /* On dark maps, this makes overlaps glow white */
}
</style>

<style>
body {
  margin: 0;
  padding: 0;
  overflow: hidden;
  background-color: #121212;
  /* Apply new font globally */
  font-family: 'Inter', sans-serif;
  -webkit-font-smoothing: antialiased;
}
</style>