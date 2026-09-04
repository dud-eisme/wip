// This file is the single seam between the frontend and Person A's backend.
// Every function here currently returns mock data. Once the real registry
// API is live, only this file needs to change — swap each function body for
// a fetch() call against the real endpoint, keep the same return shape.

import { mockCameras } from '../data/mockCameras'

const USE_MOCK = true
const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'

export async function getCameras(filters = {}) {
  if (USE_MOCK) {
    await simulateLatency()
    return applyFilters(mockCameras, filters)
  }
  const params = new URLSearchParams(filters)
  const res = await fetch(`${API_BASE}/cameras?${params}`)
  if (!res.ok) throw new Error('Failed to fetch cameras')
  return res.json()
}

export async function getGapAnalysis() {
  if (USE_MOCK) {
    await simulateLatency()
    const offline = mockCameras.filter((c) => c.connectivityStatus === 'offline')
    const maintenance = mockCameras.filter((c) => c.healthStatus === 'maintenance')
    return {
      uncoveredZones: offline.length,
      ageingInfrastructure: maintenance.length,
      details: [...offline, ...maintenance],
    }
  }
  const res = await fetch(`${API_BASE}/gap-analysis`)
  if (!res.ok) throw new Error('Failed to fetch gap analysis')
  return res.json()
}

export async function bulkImportCameras(file) {
  if (USE_MOCK) {
    await simulateLatency()
    return { imported: 0, message: 'Mock mode: connect real API to enable import' }
  }
  const formData = new FormData()
  formData.append('file', file)
  const res = await fetch(`${API_BASE}/cameras/bulk-import`, { method: 'POST', body: formData })
  if (!res.ok) throw new Error('Bulk import failed')
  return res.json()
}

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
