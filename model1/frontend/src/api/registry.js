// This file is the single seam between the frontend and Model 1's backend.

import { mockCameras } from '../data/mockCameras'

const USE_MOCK = false
const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api/v1'

// ---------------------------------------------------------------------------
// Field-name / value mapping between the backend's schema and the frontend's
// existing shape (see conversation history for why these exist).
// ---------------------------------------------------------------------------

function mapHealthStatus(backendValue) {
  const map = { 'Operational': 'active', 'Maintenance Required': 'maintenance', 'Defective': 'inactive' }
  return map[backendValue] || 'inactive'
}
function reverseMapHealthStatus(frontendValue) {
  const map = { 'active': 'Operational', 'maintenance': 'Maintenance Required', 'inactive': 'Defective' }
  return map[frontendValue] || frontendValue
}
function mapDepartment(backendValue) {
  const map = { 'Police': 'Police', 'Transport': 'Transport Department', 'Municipal': 'Municipal Corporation', 'Admin': 'Institution' }
  return map[backendValue] || backendValue
}
function reverseMapDepartment(frontendValue) {
  const map = { 'Police': 'Police', 'Transport Department': 'Transport', 'Municipal Corporation': 'Municipal', 'Institution': 'Admin' }
  return map[frontendValue] || frontendValue
}
function mapCameraType(backendValue) {
  const map = { 'PTZ': 'PTZ', 'Fixed Bullet': 'Bullet', 'Dome': 'Fixed Dome', 'ANPR': 'ANPR-enabled' }
  return map[backendValue] || backendValue
}
function reverseMapCameraType(frontendValue) {
  const map = { 'PTZ': 'PTZ', 'Bullet': 'Fixed Bullet', 'Fixed Dome': 'Dome', 'ANPR-enabled': 'ANPR' }
  return map[frontendValue] || frontendValue
}

function normalizeCamera(apiCamera) {
  return {
    id: apiCamera.camera_identifier,
    dbId: apiCamera.id, // the real UUID primary key - needed for PATCH/DELETE, which the human-readable id can't be used for
    name: apiCamera.camera_name,
    department: mapDepartment(apiCamera.department),
    cameraType: mapCameraType(apiCamera.camera_type),
    ownership: apiCamera.ownership,
    connectivityStatus: apiCamera.connectivity_status.toLowerCase(),
    healthStatus: mapHealthStatus(apiCamera.health_status),
    storageDetails: `${apiCamera.storage_details.storage_type} - ${apiCamera.storage_details.retention_days} days`,
    streamEndpoint: apiCamera.stream_endpoint,
    lastUpdated: apiCamera.updated_at,
    lat: apiCamera.latitude,
    lng: apiCamera.longitude,
  }
}

// ---------------------------------------------------------------------------
// Read
// ---------------------------------------------------------------------------

export async function getCameras(filters = {}) {
  if (USE_MOCK) {
    await simulateLatency()
    return applyFilters(mockCameras, filters)
  }
  const { token, search, department, cameraType, status } = filters
  const backendParams = {
    department: department ? reverseMapDepartment(department) : undefined,
    camera_type: cameraType ? reverseMapCameraType(cameraType) : undefined,
    health_status: status ? reverseMapHealthStatus(status) : undefined,
  }
  const params = new URLSearchParams(
    Object.fromEntries(Object.entries(backendParams).filter(([, v]) => v !== undefined))
  )
  const res = await fetch(`${API_BASE}/cameras?${params}`, { headers: { Authorization: `Bearer ${token}` } })
  if (!res.ok) throw new Error('Failed to fetch cameras')
  const data = await res.json()
  let normalized = data.items.map(normalizeCamera)
  if (search) {
    normalized = normalized.filter((c) => c.name.toLowerCase().includes(search.toLowerCase()))
  }
  return normalized
}

export async function getGapAnalysis(token) {
  if (USE_MOCK) {
    await simulateLatency()
    const offline = mockCameras.filter((c) => c.connectivityStatus === 'offline')
    const maintenance = mockCameras.filter((c) => c.healthStatus === 'maintenance')
    return { uncoveredZones: offline.length, ageingInfrastructure: maintenance.length, details: [...offline, ...maintenance] }
  }
  const res = await fetch(`${API_BASE}/analytics/gap-analysis`, { headers: { Authorization: `Bearer ${token}` } })
  if (!res.ok) throw new Error('Failed to fetch gap analysis')
  const data = await res.json()
  return {
    uncoveredZones: data.coverage_density.dead_zone_cell_count,
    ageingInfrastructure: data.ageing_infrastructure.flagged_count,
    headline: data.summary.headline,
    recommendedActions: data.summary.recommended_actions,
    raw: data,
  }
}

// ---------------------------------------------------------------------------
// Write: create
// ---------------------------------------------------------------------------

export async function addCamera(cameraData, token) {
  if (USE_MOCK) {
    await simulateLatency()
    const newCamera = { id: `CAM-${String(mockCameras.length + 1).padStart(4, '0')}`, lastUpdated: new Date().toISOString(), ...cameraData }
    mockCameras.push(newCamera)
    return newCamera
  }
  const res = await fetch(`${API_BASE}/cameras`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: JSON.stringify(cameraData),
  })
  if (!res.ok) {
    const errBody = await res.json().catch(() => ({}))
    throw new Error(errBody.detail ? JSON.stringify(errBody.detail) : 'Failed to add camera')
  }
  return res.json()
}

export async function bulkImportCameras(file, token) {
  if (USE_MOCK) {
    await simulateLatency()
    return { total_rows: 0, inserted_count: 0, failed_count: 0, errors: [] }
  }
  const formData = new FormData()
  formData.append('file', file)
  const res = await fetch(`${API_BASE}/cameras/bulk-upload`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}` },
    body: formData,
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || 'Bulk upload failed')
  return data
}

// ---------------------------------------------------------------------------
// Write: update / delete (NEW)
// ---------------------------------------------------------------------------

// NOTE: uses camera.dbId (the real UUID), NOT camera.id (the human-readable
// camera_identifier) — the backend's PATCH/DELETE routes are keyed by UUID.
// This is why normalizeCamera() above now keeps both.

export async function updateCamera(dbId, updates, token) {
  if (USE_MOCK) {
    await simulateLatency()
    const cam = mockCameras.find((c) => c.dbId === dbId || c.id === dbId)
    if (cam) Object.assign(cam, updates)
    return cam
  }
  // updates should already be in backend shape (department/camera_type/etc.,
  // not the frontend's mapped values) — callers build this from a form, not
  // from a normalized camera object.
  const res = await fetch(`${API_BASE}/cameras/${dbId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: JSON.stringify(updates),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(data.detail ? JSON.stringify(data.detail) : 'Failed to update camera')
  return data
}

export async function deleteCamera(dbId, token) {
  if (USE_MOCK) {
    await simulateLatency()
    const idx = mockCameras.findIndex((c) => c.dbId === dbId || c.id === dbId)
    if (idx !== -1) mockCameras.splice(idx, 1)
    return true
  }
  const res = await fetch(`${API_BASE}/cameras/${dbId}`, {
    method: 'DELETE',
    headers: { Authorization: `Bearer ${token}` },
  })
  // Backend returns 204 No Content on success - no JSON body to parse.
  if (!res.ok && res.status !== 204) {
    const data = await res.json().catch(() => ({}))
    throw new Error(data.detail || 'Failed to delete camera')
  }
  return true
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function applyFilters(cameras, { department, cameraType, status, search } = {}) {
  return cameras.filter((c) => {
    if (department && c.department !== department) return false
    if (cameraType && c.cameraType !== cameraType) return false
    if (status && c.healthStatus !== status) return false
    if (search && !c.name.toLowerCase().includes(search.toLowerCase())) return false
    return true
  })
}

function simulateLatency() {
  return new Promise((resolve) => setTimeout(resolve, 150))
}
