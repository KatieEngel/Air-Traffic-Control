<script setup>
import { onMounted, ref, onUnmounted, computed} from "vue";
import axios from "axios";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { KalmanFilter } from './utils/KalmanFilter.js';
import { checkCollisionRisk } from './utils/CollisionMath.js';

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
let trendLayerGroup = null; // For the path lines - Initialize as null, create it in initMap
const showCollisionToggle = ref(false); // The Toggle Switch
const collisionRisks = {}; // Stores "RED", "YELLOW", "SAFE" for each icao24
let collisionLayerGroup = null; // For the collision lines
let collisionConnections = []; // store collision lines for animation
let collisionPucks = []; // Track the active "Pucks" (Safety Zone Circles)
const POLL_INTERVAL = 20; // How often we fetch (seconds)
const timer = ref(POLL_INTERVAL);
let intervalId = null;
let pollingInterval = null; // Store the ID here
const currentBbox = ref({
  minLat: 40.038,  // South
  maxLat: 41.214,  // North
  minLng: -74.974, // West
  maxLng: -72.748  // East
});
const selectionHistory = ref([]);
let spotlightLayer = null;
let borderLayer = null;
let historyLayer = null; // Stores the Cyan history line

// --- CONFIG ---
// If we are in production, use the real URL. If local, use localhost.
const BASE_URL = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000";
const API_URL = `${BASE_URL}/flights`;
const REFRESH_RATE = 20000; // 20 seconds (OpenSky limit is strict, be careful!)

// Linear Interpolation: Calculates a point 't' percent between start and end
// t=0.1 means "Move 10% of the distance towards the target"
const lerp = (start, end, t) => {
  return start + (end - start) * t;
};

// --- FUNCTIONS ---

const startCountdown = () => {
  // Clear any existing timer to prevent duplicates
  if (intervalId) clearInterval(intervalId);
  
  // Reset the number
  timer.value = POLL_INTERVAL;
  
  // Start counting down every 1 second
  intervalId = setInterval(() => {
    if (timer.value > 0) {
      timer.value--;
    }
  }, 1000);
};

// SORTED LIST: Selected plane always jumps to the top
const sortedFlights = computed(() => {
  // Create a shallow copy
  const list = [...flights.value];
  
  return list.sort((a, b) => {
    const indexA = selectionHistory.value.indexOf(a.icao24);
    const indexB = selectionHistory.value.indexOf(b.icao24);

    // Case 1: Both are in history
    if (indexA !== -1 && indexB !== -1) {
      return indexA - indexB; // Lower index (more recent) goes first
    }

    // Case 2: Only A is in history
    if (indexA !== -1) return -1;

    // Case 3: Only B is in history
    if (indexB !== -1) return 1;

    // Case 4: Neither is in history (Keep original order)
    return 0;
  });
});

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

// HELPER: Convert Altitude to Color
const getAltitudeColor = (alt) => {
  // 1. Safety check for missing data (Ground/Null)
  if (!alt || alt < 0) return "#aaaaaa"; // Grey for ground/null

  // 2. Define the ceiling (12,000m is approx 40,000ft)
  const ceiling = 12000;
  const floor = 500; // Below this, we treat it as "White/Landing"

  // 3. Ground/Landing Phase (< 500m)
  if (alt < floor) return "#ffffff"; // Pure White for landing

  // 4. Calculate Ratio (0.0 to 1.0)
  // We clamp the altitude to the ceiling so 50,000ft doesn't break the math
  const val = Math.min(alt, ceiling);
  const ratio = (val - floor) / (ceiling - floor);

  // 5. HSL Calculation
  // We want to transition from White (Low) to Vivid Purple (High)
  
  // Hue: Starts at 190 (Cyan) and goes to 280 (Purple)
  const hue = 190 + (ratio * 90);
  
  // Saturation: Starts at 0% (White) and goes to 100% (Vivid color)
  const sat = ratio * 100;
  
  // Lightness: Starts at 100% (White) and drops to 60% (Bright Color)
  // We don't go to 50% because we want it to "glow" on the dark map
  const light = 100 - (ratio * 40);

  return `hsl(${hue}, ${sat}%, ${light}%)`;
};

// Helper: Determines the correct color for a plane based on ALL states
const getPlaneColor = (flight) => {
  if (!flight) return "#ffffff"; // Safety fallback
  // 1. Priority: Selection (Always Gold)
  if (selectedPlane.value === flight.icao24) {
    return "#ffcc00"; // Gold
  }

  // 2. Priority: Collision Risk (Only if Toggle is ON)
  if (showCollisionToggle.value) {
    const risk = collisionRisks[flight.icao24];
    if (risk === "RED") return "#ff0000";
    if (risk === "YELLOW") return "#ffaa00";
  }

  // 3. Altitude gradient (default)
  return getAltitudeColor(flight.geo_altitude);
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

const drawSpotlight = () => {
  if (!currentBbox.value) return;
  // 1. Remove old layers if they exist
  if (spotlightLayer) map.value.removeLayer(spotlightLayer);
  if (borderLayer) map.value.removeLayer(borderLayer);

  // 2. Define World vs Hole
  const world = [[90, -180], [90, 180], [-90, 180], [-90, -180]];
  const hole = [
    [currentBbox.value.maxLat, currentBbox.value.minLng], // Top Left
    [currentBbox.value.maxLat, currentBbox.value.maxLng], // Top Right
    [currentBbox.value.minLat, currentBbox.value.maxLng], // Bottom Right
    [currentBbox.value.minLat, currentBbox.value.minLng]  // Bottom Left
  ];

  // 3. Draw Mask
  spotlightLayer = L.polygon([world, hole], {
    color: 'transparent',
    fillColor: '#000000',
    fillOpacity: 0.65,
    interactive: false 
  }).addTo(map.value);

  // 4. Draw Cyan Border
  borderLayer = L.rectangle(
    [[currentBbox.value.minLat, currentBbox.value.minLng], 
     [currentBbox.value.maxLat, currentBbox.value.maxLng]], 
    {
      color: '#00e5ff', 
      weight: 2,
      fill: false,
      dashArray: '10, 5',
      opacity: 0.8
    }
  ).addTo(map.value);
};

const searchCurrentArea = () => {
  const bounds = map.value.getBounds();
  
  // Update the BBOX state
  currentBbox.value = {
    minLat: bounds.getSouth(),
    maxLat: bounds.getNorth(),
    minLng: bounds.getWest(),
    maxLng: bounds.getEast()
  };

  // Redraw the dark mask to match new area
  drawSpotlight();

  // Fetch new data!
  fetchFlights();
};

const drawFlightHistory = async (icao24) => {
  // 1. Cleanup old line
  if (historyLayer) {
    map.value.removeLayer(historyLayer);
    historyLayer = null;
  }

  try {
    // 2. Fetch from your new backend endpoint
    // Note: Use backticks ` ` for string interpolation
    const response = await axios.get(`${BASE_URL}/flight-track/${icao24}`);
    const path = response.data.path;

    if (path && path.length > 0) {
      // 3. Draw the Line
      historyLayer = L.polyline(path, {
        color: '#00e5ff', // Cyan (Tron style)
        weight: 3,
        opacity: 0.8,
        lineCap: 'round'
      }).addTo(map.value);

      // Optional: Zoom map to fit the whole path?
      // map.value.fitBounds(historyLayer.getBounds());
    }
  } catch (error) {
    console.error("Could not fetch history:", error);
  }
};

// --- CORE FUNCTIONS ---

// 1. Initialize the Map
const initMap = () => {
  // Centered on Long Island, NY with Zoom 9
  map.value = L.map("mapContainer", {
    minZoom: 6,        // Prevent zooming out too far
    maxZoom: 18,       // Prevent zooming in too close
    zoomControl: false // (Optional) hides the +/- buttons if you want a cleaner look
  }).setView([40.626, -73.861], 9);

  setTimeout(() => { map.value.invalidateSize(); }, 100);

  // DARK MODE MAP TILES
  L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
    subdomains: 'abcd',
    maxZoom: 20
  }).addTo(map.value);

  trendLayerGroup = L.layerGroup().addTo(map.value); // initialize layer grouping for trends

  collisionLayerGroup = L.layerGroup().addTo(map.value); // initialize layer grouping for collision lines
  
  // DESELECT ON MAP CLICK
  map.value.on('click', () => {
    // 1. If a plane was selected, turn it back to WHITE
    if (selectedPlane.value && markers[selectedPlane.value]) {
       const oldId = selectedPlane.value;
       const oldFlight = flights.value.find(f => f.icao24 === oldId);
       const oldRotation = oldFlight ? (oldFlight.true_track || 0) : 0;

       // FIX: Clear the selection variable FIRST, then ask for the color
       selectedPlane.value = null; 
       const correctColor = getPlaneColor(oldFlight);
       
       markers[oldId].setIcon(createPlaneIcon(oldRotation, 1.0, correctColor));
       markers[oldId].setZIndexOffset(0);
    }
    clearAllVisuals();
    selectedPlane.value = null;
  });

  drawSpotlight();
};

// 2. Fetch Data from Python Backend
const fetchFlights = async () => {
  try {
    // 1. RESET TIMER IMMEDIATELY
    startCountdown();

    // Pass the coordinates as Query Parameters
    const params = {
      min_lat: currentBbox.value.minLat,
      max_lat: currentBbox.value.maxLat,
      min_long: currentBbox.value.minLng,
      max_long: currentBbox.value.maxLng
    };

    const response = await axios.get(API_URL, { params });
    flights.value = response.data;

    updateMap();

    if (showCollisionToggle.value) {
       runCollisionCheck();
    } else {
      if (collisionLayerGroup && collisionLayerGroup.getLayers().length > 0) {
           collisionLayerGroup.clearLayers();
           collisionConnections = [];
           collisionPucks = [];
       }
    }

  } catch (error) {
    console.error("Error fetching flights:", error);
  }
};

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
  
  // --- FIX: CALCULATE ROTATION FROM VELOCITY ---
  // Instead of trusting the API's 'true_track', we calculate the angle 
  // of the green line itself.
  // Math.atan2(x, y) returns radians. We convert to degrees.
  let rotation = 0;
  if (Math.abs(vLat) > 0 || Math.abs(vLng) > 0) {
      // Note: atan2(x, y) gives standard math angle. 
      // Navigation bearing (0=North, 90=East) requires atan2(lng, lat).
      rotation = Math.atan2(vLng, vLat) * (180 / Math.PI);
  } else {
      // Fallback to API if stationary
      rotation = flight.true_track || 0;
  }
  // ----------------------------------------------

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
    ghostMarkers[flight.icao24].setIcon(createPlaneIcon(rotation, 0.5, "#ffcc00"));
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

  if (historyLayer) {
    map.value.removeLayer(historyLayer);
    historyLayer = null;
  }
};

// UNIFIED SELECTION HANDLER
// Works for both Map Clicks and Sidebar Clicks
const selectFlight = (flight) => {
  // 1. Prevent re-selecting the same plane (optional, but saves performance)
  if (selectedPlane.value === flight.icao24) return;

  // 2. Clear old Visuals (Lines/Ghosts)
  clearAllVisuals();

  // --- FIX: CLOSE ANY OPEN POPUPS FIRST ---
  map.value.closePopup();

  // 3. DESELECT OLD PLANE (Reset Visuals)
  if (selectedPlane.value && markers[selectedPlane.value]) {
    const oldId = selectedPlane.value;
    const oldFlight = flights.value.find(f => f.icao24 === oldId);
    // Safety check if old flight disappeared
    const oldRotation = oldFlight ? (oldFlight.true_track || 0) : 0;
    
    // Temporarily nullify selection to get the correct "idle" color
    const temp = selectedPlane.value;
    selectedPlane.value = null; 
    const correctColor = getPlaneColor(oldFlight);
    selectedPlane.value = temp;

    markers[oldId].setIcon(createPlaneIcon(oldRotation, 1.0, correctColor));
    markers[oldId].setZIndexOffset(0);
  }

  // 4. UPDATE STATE
  selectedPlane.value = flight.icao24;

  // --- NEW: UPDATE HISTORY STACK ---
  // 1. Remove it if it's already in the list (so we don't have duplicates)
  const index = selectionHistory.value.indexOf(flight.icao24);
  if (index > -1) {
    selectionHistory.value.splice(index, 1);
  }
  
  // 2. Add to the FRONT of the array
  selectionHistory.value.unshift(flight.icao24);

  // 5. HIGHLIGHT NEW PLANE (Gold)
  if (markers[flight.icao24]) {
    const rotation = flight.true_track || 0;
    markers[flight.icao24].setIcon(createPlaneIcon(rotation, 1.0, "#ffcc00"));
    markers[flight.icao24].setZIndexOffset(1000);

    // --- FIX: MANUALLY OPEN THE NEW POPUP ---
    markers[flight.icao24].openPopup();
    
  }

  // 6. DRAW GHOST/PREDICTION
  drawGhostPlane(flight);
  drawFlightHistory(flight.icao24);
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

      // Determine Base Color
      const planeColor = getPlaneColor(flight);
      const isSelected = selectedPlane.value === flight.icao24;

      // 2. If marker exists, move it and update rotation
      if (markers[flight.icao24]) {
        // Update the icon rotation
        markers[flight.icao24].setIcon(createPlaneIcon(rotation, 1.0, planeColor));

        // Fix z-index: Selected plane should always be on TOP of others
        markers[flight.icao24].setZIndexOffset(isSelected ? 1000 : 0);

        // Update popup text
        markers[flight.icao24].setPopupContent(`
          <b>${flight.callsign || "Unknown"}</b><br>
          Altitude: ${flight.geo_altitude}m<br>
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
            Altitude: ${flight.geo_altitude}m
        `);

        // Click Handler for Selection
        newMarker.on('click', (e) => {
          // Prevent the map background click from firing immediately
          L.DomEvent.stopPropagation(e);

          selectFlight(flight);
        });

        newMarker.addTo(map.value);
        markers[flight.icao24] = newMarker;
      }
      // Update the line logic on every refresh (to keep it synced with the plane)
      drawGhostPlane(flight);
    }
  });

  // --- GARBAGE COLLECTION ---
  // Remove markers for planes that are no longer in the flights list
  Object.keys(markers).forEach(icao24 => {
    const stillExists = flights.value.find(f => f.icao24 === icao24);
    
    if (!stillExists) {
      // 1. Remove the Marker
      map.value.removeLayer(markers[icao24]);
      delete markers[icao24];
      
      // 2. Remove the Kalman Filter (Save memory)
      if (filters[icao24]) delete filters[icao24];

      // 3. Remove Ghost Visuals (if selected)
      if (predictionLines[icao24]) {
        map.value.removeLayer(predictionLines[icao24]);
        delete predictionLines[icao24];
      }
      if (ghostMarkers[icao24]) {
        map.value.removeLayer(ghostMarkers[icao24]);
        delete ghostMarkers[icao24];
      }
    }
  });
};

const animate = () => {
  requestAnimationFrame(animate);

  Object.keys(markers).forEach(icao24 => {
    const filter = filters[icao24];
    const marker = markers[icao24];

    // --- ANIMATE MARKERS AND PREDICTION LINES ---
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

  // --- ANIMATE COLLISION LINES ---
  if (showCollisionToggle.value) {
    collisionConnections.forEach(connection => {
      const markerA = markers[connection.planeA];
      const markerB = markers[connection.planeB];

      // Only draw if both planes still have valid markers on screen
      if (markerA && markerB) {
        const posA = markerA.getLatLng();
        const posB = markerB.getLatLng();
        
        // Update line coordinates instantly
        connection.line.setLatLngs([posA, posB]);
      }
    });
  }

  // --- ANIMATE PUCKS ---
  if (showCollisionToggle.value) {
    collisionPucks.forEach(item => {
      const marker = markers[item.icao];
      
      // Only move if the plane marker actually exists on screen
      if (marker) {
        const currentPos = marker.getLatLng();
        item.circle.setLatLng(currentPos);
      }
    });
  }
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

const runCollisionCheck = () => {
  // Clear everything regardless of whether the toggle is on or off (because we have to redraw anyway)
  if (collisionLayerGroup) {
    collisionLayerGroup.clearLayers();
  }
  collisionConnections = [];
  collisionPucks = [];

  // If the toggle is OFF, STOP immediately.
  if (!showCollisionToggle.value) {
    Object.keys(collisionRisks).forEach(key => delete collisionRisks[key]);
    updateMap(); // Clear the colors from the planes
    return;      // <--- Stop here!
  }
  // 1. Create a lightweight copy for sorting
  // We only need the data relevant for collision
  const sortedFlights = [...flights.value].sort((a, b) => a.lat - b.lat);
  flights.value.forEach(f => collisionRisks[f.icao24] = "SAFE");

  // 2. The "Sweep and Prune" Loop
  for (let i = 0; i < sortedFlights.length; i++) {
    const planeA = sortedFlights[i];

    // Inner Loop: Only look forward
    for (let j = i + 1; j < sortedFlights.length; j++) {
      const planeB = sortedFlights[j];

      // A. LATITUDE CHECK (The Optimization)
      const latDiff = planeB.lat - planeA.lat; // We know B > A because it's sorted
      
      // If Plane B is more than ~0.15 degrees (approx 10 miles) away in Latitude...
      // ...then Plane C, D, E will ALSO be too far away.
      // STOP CHECKING IMMEDIATELY.
      if (latDiff > 0.15) break;

      // B. LONGITUDE CHECK
      // Even if Lat is close, Longitude might be far. Check this before doing heavy math.
      if (Math.abs(planeA.long - planeB.long) > 0.15) continue;

      // C. ALTITUDE CHECK (Vertical Separation)
      if (Math.abs(planeA.geo_altitude - planeB.geo_altitude) > 300) continue;

      // --- 1. GROUND FILTER (The Fix) ---
      // If either plane is on the ground, skip the check.
      // We also check altitude/speed as a backup because 'on_ground' isn't always perfect.
      const isGroundA = planeA.on_ground || (planeA.geo_altitude < 600 && planeA.velocity < 80);
      const isGroundB = planeB.on_ground || (planeB.geo_altitude < 600 && planeB.velocity < 80);
      if (isGroundA || isGroundB) continue;

      // D. THE EXPENSIVE CHECK (Kalman & Future Prediction)
      // Only runs if planes are physically close in 3D space
      if (filters[planeA.icao24] && filters[planeB.icao24]) {
        const risk = checkCollisionRisk(planeA, planeB, filters[planeA.icao24], filters[planeB.icao24]);
        
        if (risk === "RED" || risk === "YELLOW") {
          // Update Risk Dictionary
          if (risk === "RED") {
             collisionRisks[planeA.icao24] = "RED";
             collisionRisks[planeB.icao24] = "RED";
          } else {
             if (collisionRisks[planeA.icao24] !== "RED") collisionRisks[planeA.icao24] = "YELLOW";
             if (collisionRisks[planeB.icao24] !== "RED") collisionRisks[planeB.icao24] = "YELLOW";
          }
          // DRAW LINE
          const color = risk === "RED" ? '#ff0000' : '#ffaa00';
          const weight = risk === "RED" ? 3 : 2;
          const dash = risk === "RED" ? null : '5, 10';

          const line = L.polyline([[planeA.lat, planeA.long], [planeB.lat, planeB.long]], {
             color: color, 
             weight: weight, 
             dashArray: dash,
             opacity: 0.7
          }).addTo(collisionLayerGroup);
          
          // SAVE TO TRACKER (New Step!)
          collisionConnections.push({
            line: line,
            planeA: planeA.icao24,
            planeB: planeB.icao24
          });

          // --- DRAW PUCKS ---
          // We only draw the full "Safety Zone" for RED alerts (Critical)
          if (risk === "RED") {
            const createPuck = (plane) => {
              const puck = L.circle([plane.lat, plane.long], {
                color: 'transparent',   // No border (cleaner look)
                fillColor: '#ff0000',   // Red fill
                fillOpacity: 0.2,       // See-through
                radius: 4800,           // 3 Miles / 4.8km
                interactive: false      // Click-through
              }).addTo(collisionLayerGroup);
              
              collisionPucks.push({
                circle: puck,
                icao: plane.icao24
              });
            };

            // Create pucks for BOTH planes involved
            createPuck(planeA);
            createPuck(planeB);
          }
        }
      }
    }
  }
  
  updateMap();
};

// --- LIFECYCLE ---
onMounted(() => {
  initMap();

  fetchFlights(); // Initial fetch

  // Set up the loop
  pollingInterval = setInterval(fetchFlights, POLL_INTERVAL * 1000);

  animate();
});

onUnmounted(() => {
  if (pollingInterval) {
    clearInterval(pollingInterval); // Stop the old loop
    pollingInterval = null;
  }
  
  // Also clean up the countdown timer if needed
  if (intervalId) {
    clearInterval(intervalId);
  }
});
</script>

<template>
  <div class="app-container">
    <div class="sidebar">
      

      <div class="sidebar-header">
        <h2>Air Traffic</h2>
        
        <div class="timer-badge" :class="{ 'refreshing': timer === 0 }">
          <span v-if="timer > 0">Update in {{ timer }}s</span>
          <span v-else>Fetching...</span>
        </div>
      </div>

      <div class="toggle-container">
        <span class="toggle-label">Show Flight Trends</span>
        <label class="switch">
          <input type="checkbox" v-model="showTrendsToggle" @change="toggleTrends">
          <span class="slider round"></span>
        </label>
      </div>

      <div class="toggle-container">
        <span class="toggle-label" style="color: #ff4444">Collision View</span>
        <label class="switch">
          <input type="checkbox" v-model="showCollisionToggle" @change="runCollisionCheck">
          <span class="slider round"></span>
        </label>
      </div>

      <hr>
      <h2>Flight List</h2>

      <ul>
        <li 
          v-for="flight in sortedFlights" 
          :key="flight.icao24"
          @click="selectFlight(flight)"
          :class="{ 'active-row': selectedPlane === flight.icao24 }"
        >
          <div class="flight-info">
            <strong>{{ flight.callsign || "N/A" }}</strong>
            <br>
            <small>{{ flight.origin_country }}</small>
          </div>
          <div class="flight-stats">
            <span>Altitude: {{ Math.round(flight.geo_altitude) }}m</span>
            <br>
            <span>Velocity: {{ Math.round(flight.velocity) }}m/s</span>
          </div>
        </li>
      </ul>
    </div>

    <div class="map-view">
      <div id="mapContainer"></div>
      
      <button class="search-btn" @click="searchCurrentArea">
        Search This Area 🔍
      </button>
    </div>
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

.sidebar-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 1rem;
  padding-bottom: 0.5rem;
  border-bottom: 1px solid #333;
}

h2 {
  margin: 0;
  font-size: 1.2rem;
  color: #fff;
}

/* Timer Badge */
.timer-badge {
  background: #2c2c2c;
  color: #bbb;
  padding: 4px 8px;
  border-radius: 4px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.75rem;
  border: 1px solid #444;
  min-width: 80px;
  text-align: center;
  transition: all 0.3s ease;
}

/* Green Pulse when fetching */
.timer-badge.refreshing {
  background: #1b5e20; /* Dark Green */
  color: #fff;
  border-color: #4caf50;
  box-shadow: 0 0 8px rgba(76, 175, 80, 0.4);
}

#mapContainer {
  width: 100%;
  height: 100vh; /* Takes up full screen height */
  background: #000; /* Fallback color so you see if it loads */
  z-index: 0;
}

/* Ensure the parent container also has height if needed */
.map-view {
  height: 100vh;
  width: 100vw;
  position: relative; /* Needed for the absolute position button */
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

/* Active Sidebar Item */
li.active-row {
  background: #3a3a3a;      /* Lighter grey background */
  border-left: 4px solid #ffcc00; /* Gold stripe on the left */
  transform: translateX(4px); /* Slight pop-out effect */
}

/* Hover vs Active conflict resolution */
li.active-row:hover {
  background: #444; 
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

.search-btn {
  position: absolute;
  top: 20px;
  left: 50%;
  transform: translateX(-50%); /* Center perfectly */
  z-index: 1000; /* Above the map */
  
  background: #00e5ff; /* Cyan */
  color: #000;
  border: none;
  padding: 10px 20px;
  border-radius: 20px;
  font-weight: bold;
  font-family: 'Inter', sans-serif;
  cursor: pointer;
  box-shadow: 0 4px 15px rgba(0, 229, 255, 0.4);
  transition: all 0.2s ease;
}

.search-btn:hover {
  transform: translateX(-50%) scale(1.05);
  background: #fff;
}

.search-btn:active {
  transform: translateX(-50%) scale(0.95);
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