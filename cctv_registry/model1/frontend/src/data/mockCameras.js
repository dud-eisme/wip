// Mock dataset shaped exactly like the PostGIS-backed API response Person A
// will eventually serve from /api/cameras. Swap `getCameras()` in
// src/api/registry.js to a real fetch once that endpoint exists — nothing
// in the components below needs to change.

const DEPARTMENTS = ['Police', 'Municipal Corporation', 'Transport Department', 'Institution']
const CAMERA_TYPES = ['Fixed Dome', 'PTZ', 'Bullet', 'ANPR-enabled']
const STATUS = ['active', 'inactive', 'maintenance']
const OWNERSHIP = ['State Govt', 'Municipal', 'Private (Contracted)']

// A handful of real Gujarat city centers to scatter sample points around.
const CITY_CENTERS = [
  { name: 'Ahmedabad', lat: 23.0225, lng: 72.5714 },
  { name: 'Surat', lat: 21.1702, lng: 72.8311 },
  { name: 'Vadodara', lat: 22.3072, lng: 73.1812 },
  { name: 'Rajkot', lat: 22.3039, lng: 70.8022 },
  { name: 'Gandhinagar', lat: 23.2156, lng: 72.6369 },
]

function jitter(base, spread = 0.06) {
  return base + (Math.random() - 0.5) * spread
}

function pick(arr) {
  return arr[Math.floor(Math.random() * arr.length)]
}

function generateCameras(count = 42) {
  const cameras = []
  for (let i = 1; i <= count; i++) {
    const city = pick(CITY_CENTERS)
    const status = pick(STATUS)
    const id = `CAM-${String(i).padStart(4, '0')}`
    cameras.push({
      id,
      name: `${city.name} - ${pick(['Junction', 'Market', 'Station Rd', 'Ring Rd', 'Ward Office'])} ${i}`,
      department: pick(DEPARTMENTS),
      cameraType: pick(CAMERA_TYPES),
      ownership: pick(OWNERSHIP),
      connectivityStatus: status === 'inactive' ? 'offline' : 'online',
      healthStatus: status,
      storageDetails: pick(['Local DVR - 30 days', 'Cloud - 60 days', 'NVR - 15 days']),
      streamEndpoint: `rtsp://sample-vms.local/stream/${id}`,
      lastUpdated: new Date(Date.now() - Math.floor(Math.random() * 30) * 86400000).toISOString(),
      lat: jitter(city.lat),
      lng: jitter(city.lng),
      city: city.name,
    })
  }
  return cameras
}

export const mockCameras = generateCameras()

export const FILTER_OPTIONS = {
  departments: DEPARTMENTS,
  cameraTypes: CAMERA_TYPES,
  status: STATUS,
}
