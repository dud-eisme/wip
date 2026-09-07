// Real endpoints, real field names, matching Model 2's actual FastAPI app.
// USE_MOCK still works fully offline for UI development.

import { mockSources, mockWorkerStatus } from '../data/mockSources'
import { mockEvents } from '../data/mockEvents'
import { mockRegistryCameras } from '../data/mockRegistryCameras'

export const USE_MOCK = true
const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8001/api/v2'
const REGISTRY_API_BASE = import.meta.env.VITE_REGISTRY_API_BASE || 'http://localhost:8000/api/v1'

function authHeaders(token) {
  return { Authorization: `Bearer ${token}` }
}

// ---------------------------------------------------------------------------
// Registry cameras (from Model 1 - needed to pick a camera_id when
// registering a new source)
// ---------------------------------------------------------------------------

export async function getRegistryCameras(token) {
  if (USE_MOCK) {
    await simulateLatency()
    return mockRegistryCameras
  }
  const res = await fetch(`${REGISTRY_API_BASE}/cameras`, { headers: authHeaders(token) })
  if (!res.ok) throw new Error('Failed to fetch cameras from Model 1 registry')
  const data = await res.json()
  return data.items
}

// ---------------------------------------------------------------------------
// Sources
// ---------------------------------------------------------------------------

export async function getSources(token) {
  if (USE_MOCK) {
    await simulateLatency()
    return mockSources
  }
  const res = await fetch(`${API_BASE}/sources`, { headers: authHeaders(token) })
  if (!res.ok) throw new Error('Failed to fetch sources')
  const data = await res.json()
  return data.items
}

export async function createSource(token, { cameraId, sourceName, sourceType, sourceUrl }) {
  if (USE_MOCK) {
    await simulateLatency()
    const newSource = {
      id: `src-${Date.now()}`,
      camera_id: cameraId,
      source_name: sourceName,
      source_type: sourceType,
      source_url: sourceUrl,
      is_active: true,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    }
    mockSources.push(newSource)
    mockWorkerStatus[newSource.id] = { source_id: newSource.id, is_running: false, frames_captured: 0, last_frame_at: null, last_error: null }
    return newSource
  }
  const res = await fetch(`${API_BASE}/sources`, {
    method: 'POST',
    headers: { ...authHeaders(token), 'Content-Type': 'application/json' },
    body: JSON.stringify({
      camera_id: cameraId,
      source_name: sourceName,
      source_type: sourceType,
      source_url: sourceUrl,
      is_active: true,
    }),
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail ? JSON.stringify(data.detail) : 'Failed to register source')
  return data
}

// ---------------------------------------------------------------------------
// Worker control (start/stop the background decoder for a source)
// ---------------------------------------------------------------------------

export async function getWorkersStatus(token) {
  if (USE_MOCK) {
    await simulateLatency()
    return {
      max_concurrent_sources: 50,
      active_worker_count: Object.values(mockWorkerStatus).filter((w) => w.is_running).length,
      workers: Object.values(mockWorkerStatus),
    }
  }
  const res = await fetch(`${API_BASE}/sources/status/workers`, { headers: authHeaders(token) })
  if (!res.ok) throw new Error('Failed to fetch worker status')
  return res.json()
}

export async function startSourceWorker(token, sourceId) {
  if (USE_MOCK) {
    await simulateLatency()
    mockWorkerStatus[sourceId] = {
      source_id: sourceId, is_running: true, frames_captured: 1, last_frame_at: new Date().toISOString(), last_error: null,
    }
    return mockWorkerStatus[sourceId]
  }
  const res = await fetch(`${API_BASE}/sources/${sourceId}/start`, {
    method: 'POST',
    headers: authHeaders(token),
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || 'Failed to start source worker')
  return data
}

export async function stopSourceWorker(token, sourceId) {
  if (USE_MOCK) {
    await simulateLatency()
    if (mockWorkerStatus[sourceId]) mockWorkerStatus[sourceId].is_running = false
    return true
  }
  const res = await fetch(`${API_BASE}/sources/${sourceId}/stop`, {
    method: 'POST',
    headers: authHeaders(token),
  })
  if (!res.ok && res.status !== 204) throw new Error('Failed to stop source worker')
  return true
}

// Builds the actual <img src> URL for a running source's live feed.
// Token goes in the query string here specifically because a plain <img>
// tag cannot send an Authorization header — see Model 2's stream.py patch
// (get_current_user_flexible) for the backend side of this fix.
export function buildStreamUrl(sourceId, token) {
  return `${API_BASE}/sources/${sourceId}/stream?token=${encodeURIComponent(token)}`
}

// ---------------------------------------------------------------------------
// ANPR jobs
// ---------------------------------------------------------------------------

export async function createAnprJob(token, sourceId) {
  if (USE_MOCK) {
    await simulateLatency()
    return { id: `job-${Date.now()}`, source_id: sourceId, status: 'queued', processed_frames: 0, events_found: 0 }
  }
  const res = await fetch(`${API_BASE}/anpr/jobs`, {
    method: 'POST',
    headers: { ...authHeaders(token), 'Content-Type': 'application/json' },
    body: JSON.stringify({ source_id: sourceId }),
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || 'Failed to start ANPR job')
  return data
}

export async function getAnprJob(token, jobId) {
  if (USE_MOCK) {
    await simulateLatency()
    return { id: jobId, status: 'completed', processed_frames: 42, events_found: 3 }
  }
  const res = await fetch(`${API_BASE}/anpr/jobs/${jobId}`, { headers: authHeaders(token) })
  if (!res.ok) throw new Error('Failed to fetch job status')
  return res.json()
}

// ---------------------------------------------------------------------------
// Events
// ---------------------------------------------------------------------------

export async function getEvents(token, filters = {}) {
  if (USE_MOCK) {
    await simulateLatency()
    return applyEventFilters(mockEvents, filters)
  }
  const params = new URLSearchParams(
    Object.fromEntries(Object.entries(filters).filter(([, v]) => v))
  )
  const res = await fetch(`${API_BASE}/anpr/events?${params}`, { headers: authHeaders(token) })
  if (!res.ok) throw new Error('Failed to fetch events')
  const data = await res.json()
  return data.items
}

export async function flagEvent(token, eventId, isFlagged, flaggedNote = null) {
  if (USE_MOCK) {
    await simulateLatency()
    const event = mockEvents.find((e) => e.id === eventId)
    if (event) {
      event.is_flagged = isFlagged
      event.flagged_note = flaggedNote
    }
    return event
  }
  const res = await fetch(`${API_BASE}/anpr/events/${eventId}/flag`, {
    method: 'PATCH',
    headers: { ...authHeaders(token), 'Content-Type': 'application/json' },
    body: JSON.stringify({ is_flagged: isFlagged, flagged_note: flaggedNote }),
  })
  if (!res.ok) throw new Error('Failed to update event')
  return res.json()
}

function applyEventFilters(events, { plate, camera_id, is_flagged } = {}) {
  return events.filter((e) => {
    if (plate && !e.plate_text.toLowerCase().includes(plate.toLowerCase())) return false
    if (camera_id && e.camera_id !== camera_id) return false
    if (is_flagged !== undefined && is_flagged !== '' && e.is_flagged !== is_flagged) return false
    return true
  })
}

function simulateLatency() {
  return new Promise((resolve) => setTimeout(resolve, 150))
}
