// Returns the distance (in degrees) between two points
const getDistance = (lat1, lon1, lat2, lon2) => {
  const R = 6371e3; // Earth radius in meters
  const φ1 = lat1 * Math.PI/180;
  const φ2 = lat2 * Math.PI/180;
  const Δφ = (lat2-lat1) * Math.PI/180;
  const Δλ = (lon2-lon1) * Math.PI/180;

  const a = Math.sin(Δφ/2) * Math.sin(Δφ/2) +
            Math.cos(φ1) * Math.cos(φ2) *
            Math.sin(Δλ/2) * Math.sin(Δλ/2);
  const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1-a));

  return R * c; // Distance in meters
};

// PREDICT COLLISION
// Checks if Plane A and Plane B will be within 'minDist' meters in the next 'timeHorizon' seconds
export const checkCollisionRisk = (planeA, planeB, filterA, filterB) => {
  
  // --- SAFETY CHECK: INVALID DATA ---
  // If Kalman filters haven't initialized (NaN) or altitude is missing, abort.
  if (!filterA || !filterB || isNaN(filterA.x[0]) || isNaN(filterB.x[0])) {
      return "SAFE";
  }
  
  // Ensure altitudes exist (default to 0 if null) to prevent math errors
  const altA = planeA.geo_altitude || 0;
  const altB = planeB.geo_altitude || 0;

  // --- A. CALCULATE CURRENT DISTANCE ---
  const currentDist = getDistance(filterA.x[0], filterA.x[1], filterB.x[0], filterB.x[1]);
  
  // Safety: If distance is NaN (bad math), assume safe
  if (isNaN(currentDist)) return "SAFE";

  // --- B. VERTICAL CHECK ---
  // If they are more than 1000ft (300m) apart vertically, they are safe.
  if (Math.abs(altA - altB) > 300) {
    return "SAFE";
  }

  // 2. Get Velocities (from Kalman Filters)
  const vLatA = filterA.x[2]; 
  const vLngA = filterA.x[3];
  const vLatB = filterB.x[2];
  const vLngB = filterB.x[3];

  let currentLatA = filterA.x[0];
  let currentLngA = filterA.x[1];
  let currentLatB = filterB.x[0];
  let currentLngB = filterB.x[1];

  let minDistance = Infinity;
  let timeAtMinDist = 0;

  // 3. Simulate forward in time (e.g., check every 10 seconds for 2 minutes)
  const TIME_STEP = 10;
  const HORIZON = 120; // 2 minutes
  
  for (let t = 0; t <= HORIZON; t += TIME_STEP) {
    // Project positions
    const futureLatA = currentLatA + (vLatA * t);
    const futureLngA = currentLngA + (vLngA * t);
    const futureLatB = currentLatB + (vLatB * t);
    const futureLngB = currentLngB + (vLngB * t);

    // Check distance
    const dist = getDistance(futureLatA, futureLngA, futureLatB, futureLngB);
    
    if (dist < minDistance) {
        minDistance = dist;
        timeAtMinDist = t;
    }
  }

  // Debug: Print distances for close planes
  // E. LOGGING (Optional - good for debugging)
  if (currentDist < 10000) { // If closer than 10km, log it
     console.log(`[TCAS] Close Pair: ${planeA.callsign} <-> ${planeB.callsign}`);
     console.log(`       Current Dist: ${Math.round(currentDist)}m`);
     console.log(`       Min Future Dist: ${Math.round(minDistance)}m`);
  }

  // --- NEW LOGIC: CONVERGENCE CHECK ---
  
  // 1. Are they getting closer? 
  // If the minimum distance is essentially the same as current distance, 
  // they are moving away or parallel.
  if (minDistance >= currentDist * 0.95) {
      return "SAFE"; // Not converging
  }

  // 2. Is the threat imminent?
  // Real TCAS cares about "Time to Impact" (Tau). 
  // If the closest point is 60+ seconds away, it's just a Yellow warning.
  // If it's < 30 seconds, it's Red.
  
  if (minDistance < 3000) { // Less than 3km separation
     if (timeAtMinDist < 40) return "RED"; // Impact in < 40s
     else return "YELLOW"; // Impact is far future
  }
  
  if (minDistance < 8000) return "YELLOW"; // Proximity warning

  return "SAFE";
};