<script setup>
import { onMounted, ref, onUnmounted, computed, watch} from "vue";
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
const breadcrumbs = {}; 
const trailLayers = {}; // Store the Polyline objects
const lastTrailUpdate = {}; // Timing: { icao24: timestamp } to control update speed

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

  // WIPE ALL TRAILS
  Object.keys(trailLayers).forEach(key => {
     map.value.removeLayer(trailLayers[key]);
     delete trailLayers[key];
  });
  Object.keys(breadcrumbs).forEach(key => delete breadcrumbs[key]);

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
    // Fetch directly from OpenSky Tracks API
    const response = await axios.get(`https://opensky-network.org/api/tracks/all?icao24=${icao24}&time=0`);

    // Race Condition Check
    if (selectedPlane.value !== icao24) return;



    if (response.data && response.data.path) {
      // OpenSky returns: [time, lat, lon, baro, true_track, on_ground]
      // Leaflet just wants: [lat, lon]
      const pathCoordinates = response.data.path.map(pt => [pt[1], pt[2]]);

      historyLayer = L.polyline(pathCoordinates, {
        color: '#00e5ff', // Cyan (Tron style)
        weight: 3,
        opacity: 0.8,
        lineCap: 'round'
      }).addTo(map.value);
    }
  } catch (error) {
    // OpenSky restricts history for anonymous users sometimes, so we just log a warning instead of breaking the app.
    console.warn("History not available for this flight (Anonymous limit reached).");
  }
};

watch(showTrendsToggle, (newVal) => {
  if (!newVal) {
    // 1. CLEAR EVERYTHING when toggled OFF
    Object.keys(trailLayers).forEach(key => {
        if (map.value && trailLayers[key]) {
            map.value.removeLayer(trailLayers[key]);
        }
    });
    // 2. Delete the Layer Objects (So 'animate' knows to recreate them)
    Object.keys(trailLayers).forEach(key => delete trailLayers[key]);
  }
});

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

const isApiDown = ref(false);
// 2. Fetch Data from Python Backend
const fetchFlights = async () => {
  try {
    // 1. RESET TIMER IMMEDIATELY
    startCountdown();

    // 2. OpenSky's exact parameter names for bounding boxes
    const params = {
      lamin: currentBbox.value.minLat,
      lamax: currentBbox.value.maxLat,
      lomin: currentBbox.value.minLng,
      lomax: currentBbox.value.maxLng
    };

    // 3. FETCH DIRECTLY FROM OPENSKY (Bypassing our Python Backend!)
    const response = await axios.get("https://opensky-network.org/api/states/all", { params });
    isApiDown.value = false;
    
    // 4. DATA MAPPING: Convert OpenSky's raw arrays into our objects
    if (response.data && response.data.states) {
      flights.value = response.data.states.map(state => ({
        icao24: state[0],
        callsign: state[1] ? state[1].trim() : "N/A",
        origin_country: state[2],
        time_position: state[3],
        last_contact: state[4],
        long: state[5],
        lat: state[6],
        baro_altitude: state[7],
        on_ground: state[8],
        velocity: state[9],
        true_track: state[10],
        vertical_rate: state[11],
        sensors: state[12],
        geo_altitude: state[13] || state[7], // Fallback to baro if geo is missing
        squawk: state[14],
        spi: state[15],
        position_source: state[16]
      })).filter(f => f.lat && f.long); // Only keep planes that have valid GPS coords
    } else {
      flights.value = []; // No planes in the sky here right now
    }

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
    // Check if it's a 503 (Server Error)
    if (error.response && error.response.status === 503) {
       isApiDown.value = true; // Trigger the UI warning
    }
  }
};

const drawGhostPlane = (flight) => {
  // Only draw if SELECTED
  if (selectedPlane.value !== flight.icao24) return;

  let vLat, vLng, startLat, startLng;

  // GET DATA (Kalman or fallback)
  if (filters[flight.icao24]) {
    // USE THE FILTER'S MEMORY (Robust against missing API data)
    vLat = filters[flight.icao24].x[2];
    vLng = filters[flight.icao24].x[3];
    startLat = filters[flight.icao24].x[0];
    startLng = filters[flight.icao24].x[1];
  } else {
    // Fallback if filter is missing (e.g. first frame)
    [vLat, vLng] = getVelocityComponents(flight.velocity, flight.true_track, flight.lat);
    startLat = flight.lat;
    startLng = flight.long;
  }

  // Check if moving
  if (Math.abs(vLat) < 0.000001 && Math.abs(vLng) < 0.000001) {
    // Cleanup if stopped
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
  
  // DRAW LINE
  const latLngs = [[startLat, startLng], [futureLat, futureLng]];

  if (predictionLines[flight.icao24]) {
    // UPDATE EXISTING LINE (No flicker)
    predictionLines[flight.icao24].setLatLngs(latLngs);
  } else {
    predictionLines[flight.icao24] = L.polyline(latLngs, {
      color: "#ffcc00",  // Gold/Amber
      weight: 2,
      opacity: 0.7,
      dashArray: "5, 10", // Sparse dots look more "predictive"
    }).addTo(map.value);
  }

  // CALCULATE ROTATION (Velocity Based + Cosine Corrected)
  // We use the velocity vector (vLat, vLng) because that is the 'Truth' of the ghost path.
  const latRad = startLat * (Math.PI / 180);
  
  // atan2(lng, lat) gives standard navigation angle (0 = North)
  // vLng * cos(lat) fixes the "squashed map" projection issue
  const rotation = Math.atan2(vLng * Math.cos(latRad), vLat) * (180 / Math.PI);

  // B. HANDLE THE GHOST MARKER
  if (ghostMarkers[flight.icao24]) {
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
  
  clearAllVisuals();
  map.value.closePopup();

  // DESELECT OLD PLANE (Reset Visuals)
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

  // UPDATE STATE
  selectedPlane.value = flight.icao24;

  // UPDATE HISTORY STACK ---
  // Remove it if it's already in the list (so we don't have duplicates)
  const index = selectionHistory.value.indexOf(flight.icao24);
  if (index > -1) {
    selectionHistory.value.splice(index, 1);
  }
  
  // Add to the FRONT of the array
  selectionHistory.value.unshift(flight.icao24);

  // HIGHLIGHT NEW PLANE (Gold)
  if (markers[flight.icao24]) {
    const marker = markers[flight.icao24];

    const visualRotation = marker.currentRotation || flight.true_track || 0;

    marker.setIcon(createPlaneIcon(visualRotation, 1.0, "#ffcc00"));
    marker.setZIndexOffset(1000);
    marker.openPopup();
  }

  // DRAW GHOST/PREDICTION
  drawGhostPlane(flight);
  drawFlightHistory(flight.icao24);
};

const updateMap = () => {
  const now = Date.now() / 1000;

  flights.value.forEach((flight) => {
    if (flight.lat && flight.long) {

      // --- 1. PHYSICS ENGINE (Kalman Filter) ---
      const [vLat, vLng] = getVelocityComponents(flight.velocity, flight.true_track, flight.lat);
      const dataTimestamp = flight.time_position || flight.last_contact || now;
      const lagSeconds = now - dataTimestamp;

      // Project position
      let adjustedLat = flight.lat + (vLat * lagSeconds);
      let adjustedLng = flight.long + (vLng * lagSeconds);

      // Update filter
      if (!filters[flight.icao24]) {
        filters[flight.icao24] = new KalmanFilter(adjustedLat, adjustedLng, vLat, vLng);
      } 
      filters[flight.icao24].update(adjustedLat, adjustedLng);
      filters[flight.icao24].setVelocity(vLat, vLng);

      // --- 2. ROTATION CALCULATION ---
      let rotation = flight.true_track;
      
      if (rotation === null || rotation === undefined) {
         // Fallback: If we have history, keep the old one to prevent jitter
         if (markers[flight.icao24] && markers[flight.icao24].currentRotation) {
            rotation = markers[flight.icao24].currentRotation;
         } else if (filters[flight.icao24]) {
            // Calculate from Kalman velocity with Cosine Correction
            const vx = filters[flight.icao24].x[2];
            const vy = filters[flight.icao24].x[3];
            const latRad = flight.lat * (Math.PI / 180);
            rotation = Math.atan2(vy * Math.cos(latRad), vx) * (180 / Math.PI);
         } else {
            rotation = 0;
         }
      }

      // --- 3. COLOR & STATE ---
      const planeColor = getPlaneColor(flight);
      const isSelected = selectedPlane.value === flight.icao24;

      // --- 4. MARKER UPDATE / CREATION (Merged Logic) ---
      if (markers[flight.icao24]) {
        // A. UPDATE EXISTING
        const marker = markers[flight.icao24];

        // Update Icon & Rotation
        marker.setIcon(createPlaneIcon(rotation, 1.0, planeColor));
        marker.currentRotation = rotation; // Save for click handler

        // Update Z-Index (Selected goes on top)
        marker.setZIndexOffset(isSelected ? 1000 : 0);

        // Update Popup Text (Live updates!)
        marker.setPopupContent(`
          <b>${flight.callsign || "Unknown"}</b><br>
          Altitude: ${Math.round(flight.geo_altitude)}m<br>
          Speed: ${Math.round(flight.velocity)}m/s
        `);

      } else {
        // B. CREATE NEW
        const newMarker = L.marker([flight.lat, flight.long], {
          icon: createPlaneIcon(rotation, 1.0, planeColor),
        });
        
        // Save Rotation
        newMarker.currentRotation = rotation;

        // Bind Popup (Static structure)
        newMarker.bindPopup(`
           <b>${flight.callsign || "Unknown"}</b><br>
           Altitude: ${Math.round(flight.geo_altitude)}m<br>
           Speed: ${Math.round(flight.velocity)}m/s
        `, {
           autoPan: false
        });

        // Add Click Listener
        newMarker.on('click', (e) => {
          L.DomEvent.stopPropagation(e);
          selectFlight(flight);
        });

        newMarker.addTo(map.value);
        markers[flight.icao24] = newMarker;
      }

      // --- 5. UPDATE GHOST PLANE ---
      // If this plane is selected, ensure the ghost stays synced
      if (isSelected) {
         drawGhostPlane(flight);
      }
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
      // REMOVE LIVE TRAIL
      if (trailLayers[icao24]) {
        map.value.removeLayer(trailLayers[icao24]);
        delete trailLayers[icao24];
      }
      if (breadcrumbs[icao24]) delete breadcrumbs[icao24];
      if (lastTrailUpdate[icao24]) delete lastTrailUpdate[icao24];
    }
  });
};

const animate = () => {
  requestAnimationFrame(animate);

  // 1. HELPER: Define the style in ONE place
  const createTrail = (latLngs) => {
    return L.polyline(latLngs, {
      className: 'flight-trail-line',
      color: '#4fc3f7', 
      weight: 2,       
      opacity: 0.6,
      dashArray: '4, 8', // <--- Dotted Style (4px dash, 8px gap)
      smoothFactor: 2.0 
    });
  };

  Object.keys(markers).forEach(icao24 => {
    const filter = filters[icao24];
    const marker = markers[icao24];

    // --- ANIMATE MARKERS AND PREDICTION LINES ---
    if (filter && marker) {
      const targetPos = filter.predict();
      const currentLatLng = marker.getLatLng();
      const SMOOTHING_FACTOR = 0.05; 
      
      const newLat = lerp(currentLatLng.lat, targetPos.lat, SMOOTHING_FACTOR);
      const newLng = lerp(currentLatLng.lng, targetPos.lng, SMOOTHING_FACTOR);
      
      marker.setLatLng([newLat, newLng]);

      // UPDATE PREDICTION VISUALS (If this plane is selected)
      if (selectedPlane.value === icao24) {
        const vLat = filter.x[2];
        const vLng = filter.x[3];
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

    // Only run if Toggle is ON and the plane actually exists
    if (showTrendsToggle.value && markers[icao24]) {
      const pos = markers[icao24].getLatLng();
      
      // Initialize array
      if (!breadcrumbs[icao24]) breadcrumbs[icao24] = [];
      const trail = breadcrumbs[icao24];

      // RESTORE OLD TRAIL
      if (trail.length > 0 && !trailLayers[icao24]) {
          trailLayers[icao24] = createTrail(trail).addTo(map.value);
      }

      let shouldAdd = false;
      if (trail.length === 0) {
        shouldAdd = true;
      } else {
        const lastPoint = trail[trail.length - 1];
        // Calculate distance
        const dist = Math.sqrt(
          Math.pow(pos.lat - lastPoint[0], 2) + 
          Math.pow(pos.lng - lastPoint[1], 2)
        );
        // Threshold: roughly 0.0005 degrees is ~50 meters
        if (dist > 0.0005) {
          shouldAdd = true;
        }
      }

      // ADD NEW POINT
      if (shouldAdd) {
        trail.push([pos.lat, pos.lng]);
        if (trail.length > 50) trail.shift();

        // DRAW THE LINE
        if (trailLayers[icao24]) {
          trailLayers[icao24].setLatLngs(trail);
        } else {
          trailLayers[icao24] = createTrail(trail).addTo(map.value);
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
      <div v-if="isApiDown" class="error-badge">
          OpenSky Systems Busy
      </div>

      <div class="sidebar-header">
        <h2>Air Traffic</h2>
        <div class="timer-badge" :class="{ 'refreshing': timer === 0 }">
          <span v-if="timer > 0">Update in {{ timer }}s</span>
          <span v-else>Fetching...</span>
        </div>
      </div>

      <div class="toggle-container">
        <span class="toggle-label">Show Live Trails</span>
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
        Search This Area
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

.error-badge {
  background: #d32f2f; /* Red */
  color: white;
  padding: 8px;
  border-radius: 4px;
  text-align: center;
  margin-bottom: 10px;
  font-size: 0.8rem;
  font-weight: bold;
  animation: pulse 2s infinite;
}

@keyframes pulse {
  0% { opacity: 1; }
  50% { opacity: 0.7; }
  100% { opacity: 1; }
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

/* FORCE DOTTED LINES */
/* Target the SVG path element created by Leaflet */
:deep(.flight-trail-line) {
  stroke-dasharray: 4, 8 !important; /* 4px line, 8px gap */
  stroke-linecap: round; /* Makes the dots look like little pills */
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