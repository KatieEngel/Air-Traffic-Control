export class KalmanFilter {
  constructor(initialLat, initialLng, initialVelocityLat = 0, initialVelocityLng = 0) {
    // STATE: [lat, lng, velocity_lat, velocity_lng]
    // We start with 0 velocity
    this.x = [initialLat, initialLng, initialVelocityLat, initialVelocityLng];

    // COVARIANCE (Uncertainty): Start high
    this.P = [
      [100, 0, 0, 0],
      [0, 100, 0, 0],
      [0, 0, 100, 0],
      [0, 0, 0, 100]
    ];

    // TRANSITION MATRIX (F): Physics model
    // New Pos = Old Pos + (Velocity * Time)
    // We will update 'dt' (delta time) dynamically
    this.F = (dt) => [
      [1, 0, dt, 0],
      [0, 1, 0, dt],
      [0, 0, 1, 0],
      [0, 0, 0, 1]
    ];

    // MEASUREMENT MATRIX (H): We only measure Lat/Lng, not velocity directly
    this.H = [
      [1, 0, 0, 0],
      [0, 1, 0, 0]
    ];

    // MEASUREMENT NOISE (R): How much do we trust the GPS "Jump"?
    // INCREASE THIS. Tell the filter "The GPS is noisy, don't snap to it instantly."
    this.R = [
      [0.5, 0],  // Was 0.1
      [0, 0.5]
    ];

    // PROCESS NOISE (Q): How much does a plane jitter?
    // DECREASE THIS. Planes are heavy; they don't jitter. 
    // Make this tiny so the filter prefers straight lines.
    this.Q = [
      [0.000001, 0, 0, 0],
      [0, 0.000001, 0, 0],
      [0, 0, 0.000001, 0],
      [0, 0, 0, 0.000001]
    ];
    
    // Identity Matrix
    this.I = [
      [1, 0, 0, 0],
      [0, 1, 0, 0],
      [0, 0, 1, 0],
      [0, 0, 0, 1]
    ];

    this.lastUpdate = Date.now();
  }

  // PREDICT: Move state forward based on velocity
  predict() {
    const now = Date.now();
    const dt = (now - this.lastUpdate) / 1000; // Time in seconds
    this.lastUpdate = now;

    // Simple Matrix Multiplication for State Prediction: x = F * x
    const F = this.F(dt);
    const newLat = (F[0][0] * this.x[0]) + (F[0][2] * this.x[2]);
    const newLng = (F[1][1] * this.x[1]) + (F[1][3] * this.x[3]);
    const newVLat = this.x[2]; // Velocity assumed constant for prediction step
    const newVLng = this.x[3];

    this.x = [newLat, newLng, newVLat, newVLng];

    // Update Uncertainty P = F * P * F^t + Q (Simplified diagonal update for performance)
    // Real matrix math is heavy in JS without libraries, simplified approximation:
    this.P[0][0] += this.Q[0][0];
    this.P[1][1] += this.Q[1][1];
    
    return { lat: this.x[0], lng: this.x[1] };
  }

  // NEW: Allow us to manually overwrite velocity from the API
  setVelocity(vLat, vLng) {
    this.x[2] = vLat;
    this.x[3] = vLng;
  }

  // UPDATE: Correct state based on new real data
  update(measuredLat, measuredLng) {
    // 1. LATITUDE UPDATE
    const K_lat = this.P[0][0] / (this.P[0][0] + this.R[0][0]);
    this.x[0] = this.x[0] + K_lat * (measuredLat - this.x[0]);
    this.P[0][0] = (1 - K_lat) * this.P[0][0];

    // 2. LONGITUDE UPDATE
    const K_lng = this.P[1][1] / (this.P[1][1] + this.R[1][1]);
    this.x[1] = this.x[1] + K_lng * (measuredLng - this.x[1]);
    this.P[1][1] = (1 - K_lng) * this.P[1][1];
  }
}