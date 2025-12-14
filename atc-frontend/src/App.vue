<script setup>
import { onMounted, ref } from "vue";
import axios from "axios";
import L from "leaflet";
import "leaflet/dist/leaflet.css"; // Import Leaflet CSS (Crucial!)

// A simple SVG plane icon pointing North (0 degrees)
const PLANE_ICON_SVG = `
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24" style="filter: drop-shadow(1px 1px 2px rgba(0,0,0,0.5));">
  <path fill="#007bff" d="M21,16V14L13,9V3.5A1.5,1.5 0 0,0 11.5,2A1.5,1.5 0 0,0 10,3.5V9L2,14V16L10,13.5V19L8,20.5V22L11.5,21L15,22V20.5L13,19V13.5L21,16Z"/>
</svg>
`;

// --- STATE ---
const flights = ref([]); // Stores the raw flight data
const map = ref(null); // Stores the map instance
const markers = {}; // Dictionary to track existing markers by ICAO24 ID

// --- CONFIG ---
const API_URL = "http://127.0.0.1:8000/flights";
const REFRESH_RATE = 20000; // 20 seconds (OpenSky limit is strict, be careful!)

// --- FUNCTIONS ---

// 1. Initialize the Map
const initMap = () => {
  // Centered on Long Island, NY with Zoom 9
  // IF TESTING: Center on Switzerland (Zoom 8)
  // map.value = L.map("mapContainer").setView([46.818, 8.227], 8);

  // IF PRODUCTION: Center on Long Island
  map.value = L.map("mapContainer").setView([40.626, -73.861], 9);

  // ... rest of code

  // Add the tile layer (OpenStreetMap)
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "&copy; OpenStreetMap contributors",
  }).addTo(map.value);
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

// Function to display the plane icon at the correct angle
const createPlaneIcon = (rotation) => {
  return L.divIcon({
    className: "custom-plane-icon", // We will style this slightly in CSS
    html: `<div style="transform: rotate(${rotation}deg); transform-origin: center;">
             ${PLANE_ICON_SVG}
           </div>`,
    iconSize: [24, 24],
    iconAnchor: [12, 12], // Center the rotation point
  });
};

const predictionLines = {};
// 3. Update Markers on the Map
const updateMap = () => {
  // Loop through every flight we fetched
  flights.value.forEach((flight) => {
    // CHECK: Does this plane have valid coordinates?
    // Remember: We set missing data to null in Python.
    if (flight.lat && flight.long) {
      // Flight marker:
      // 1. Calculate Rotation (Default to 0 if missing)
      const rotation = flight.true_track || 0;

      // 2. If marker exists, move it and update rotation
      if (markers[flight.icao24]) {
        markers[flight.icao24].setLatLng([flight.lat, flight.long]);

        // Update the icon rotation
        markers[flight.icao24].setIcon(createPlaneIcon(rotation));

        // Update popup text
        markers[flight.icao24].setPopupContent(`
          <b>${flight.callsign || "Unknown"}</b><br>
          Alt: ${flight.geo_altitude}m<br>
          Head: ${rotation}°
        `);
      } else {
        // 3. Create new marker with the icon
        const newMarker = L.marker([flight.lat, flight.long], {
          icon: createPlaneIcon(rotation),
        }).bindPopup(`
          <b>${flight.callsign || "Unknown"}</b><br>
          Alt: ${flight.geo_altitude}m
        `);

        newMarker.addTo(map.value);
        markers[flight.icao24] = newMarker;
      }

      // Prediction Line:
      if (flight.predicted_lat && flight.predicted_long) {
        const start = [flight.lat, flight.long];
        const end = [flight.predicted_lat, flight.predicted_long];

        // If a line already exists for this plane, remove it so we can draw the new one
        if (predictionLines[flight.icao24]) {
          map.value.removeLayer(predictionLines[flight.icao24]);
        }

        // Draw the new line (Green = Prediction)
        const line = L.polyline([start, end], {
          color: "#800080", // Purple
          weight: 2,
          opacity: 0.8,
          dashArray: "5, 5", // Dashed line
        }).addTo(map.value);

        predictionLines[flight.icao24] = line;
      }
    }
  });
};

// --- LIFECYCLE ---
onMounted(() => {
  initMap();
  fetchFlights(); // Initial fetch

  // Set up the loop
  setInterval(fetchFlights, REFRESH_RATE);
});
</script>

<template>
  <div class="app-container">
    <div class="sidebar">
      <h2>Flight List ({{ flights.length }})</h2>
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
.app-container {
  display: flex;
  height: 100vh;
  width: 100vw;
}

.sidebar {
  width: 300px;
  background: #f4f4f4;
  overflow-y: auto;
  padding: 1rem;
  border-right: 2px solid #ddd;
}

.map-view {
  flex-grow: 1; /* Take up remaining space */
  height: 100%;
}

ul {
  list-style: none;
  padding: 0;
}

li {
  background: white;
  margin-bottom: 0.5rem;
  padding: 0.5rem;
  border-radius: 4px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
}

.warning {
  color: red;
  font-size: 0.8em;
}

/* Note: Use :deep() or remove 'scoped' to affect Leaflet elements */
:deep(.leaflet-div-icon) {
  background: transparent;
  border: none;
}
</style>
