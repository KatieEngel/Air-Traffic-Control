<script setup>
// No complex logic needed for the maintenance page!
</script>

<template>
  <div class="maintenance-container">
    <div class="radar">
      <div class="sweep"></div>
    </div>
    
    <div class="text-content">
      <h1>SkyWatch Infrastructure Upgrade</h1>
      <p class="status-badge">MAINTENANCE IN PROGRESS</p>
      <p class="description">
        I am currently migrating SkyWatch's real-time flight feeds from a shared cloud edge-function to a 
        <strong>Dedicated Virtual Private Server (VPS)</strong> equipped with intelligent request caching. 
      </p>
      <p class="description">
        This architectural upgrade permanently bypasses OpenSky's third-party datacenter firewalls and rate limits, 
        ensuring enterprise-grade reliability for the live map and Kalman filter predictions.
      </p>
      <p class="eta">Tracking will resume shortly.</p>
    </div>
  </div>
</template>

<style scoped>
.maintenance-container {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100vh;
  width: 100vw;
  background-color: #121212;
  color: #e0e0e0;
  font-family: 'Inter', sans-serif;
  text-align: center;
  padding: 20px;
  box-sizing: border-box;
}

/* Cool Radar Animation */
.radar {
  position: relative;
  width: 150px;
  height: 150px;
  border-radius: 50%;
  border: 2px solid #00e5ff;
  background: radial-gradient(circle, rgba(0,229,255,0.1) 0%, rgba(0,0,0,0) 70%);
  margin-bottom: 40px;
  box-shadow: 0 0 20px rgba(0, 229, 255, 0.2);
  overflow: hidden;
}

.radar::before {
  content: '';
  position: absolute;
  top: 50%;
  left: 0;
  right: 0;
  height: 1px;
  background: rgba(0, 229, 255, 0.3);
}

.radar::after {
  content: '';
  position: absolute;
  left: 50%;
  top: 0;
  bottom: 0;
  width: 1px;
  background: rgba(0, 229, 255, 0.3);
}

.sweep {
  position: absolute;
  top: 50%;
  left: 50%;
  width: 75px;
  height: 75px;
  background: linear-gradient(45deg, rgba(0, 229, 255, 0.8) 0%, transparent 50%);
  transform-origin: 0% 0%;
  animation: spin 2s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

/* Text Styling */
.text-content {
  max-width: 600px;
}

h1 {
  font-size: 2rem;
  color: #ffffff;
  margin-bottom: 15px;
  letter-spacing: 1px;
}

.status-badge {
  display: inline-block;
  background: #332b00;
  color: #ffcc00;
  padding: 8px 16px;
  border: 1px solid #ffcc00;
  border-radius: 20px;
  font-size: 0.85rem;
  font-weight: bold;
  letter-spacing: 1.5px;
  margin-bottom: 25px;
}

.description {
  font-size: 1.1rem;
  line-height: 1.6;
  color: #b0b3b8;
  margin-bottom: 15px;
}

strong {
  color: #00e5ff;
}

.eta {
  margin-top: 30px;
  font-family: 'JetBrains Mono', monospace;
  color: #888;
}
</style>