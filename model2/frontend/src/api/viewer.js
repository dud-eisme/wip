// Real endpoints, real field names, matching Model 2's actual FastAPI app.
// USE_MOCK still works fully offline for UI development.

import { mockSources, mockWorkerStatus } from '../data/mockSources'
import { mockEvents } from '../data/mockEvents'
import { mockRegistryCameras } from '../data/mockRegistryCameras'

export const USE_MOCK = false
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

export async function createSource(token, { cameraId, sourceName, sourceType, sourceUrl, webrtcUrl = null }) {
  if (USE_MOCK) {
    await simulateLatency()
    const newSource = {
      id: `src-${Date.now()}`,
      camera_id: cameraId,
      source_name: sourceName,
      source_type: sourceType,
      source_url: sourceUrl,
      webrtc_url: webrtcUrl,
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
      webrtc_url: webrtcUrl,
      is_active: true,
    }),
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail ? JSON.stringify(data.detail) : 'Failed to register source')
  return data
}

// Partial update of an existing source. Matches schemas.CameraSourceUpdate
// on the backend, which only allows changing source_name, source_url,
// webrtc_url, and is_active — camera_id and source_type are NOT editable
// (re-pointing a source at a different camera or changing its ingest
// protocol isn't supported by this endpoint; register a new source for
// that instead). Only send fields the caller actually provided so a
// partial edit doesn't accidentally null out fields it didn't touch.
export async function updateSource(token, sourceId, { sourceName, sourceUrl, webrtcUrl, isActive } = {}) {
  const body = {}
  if (sourceName !== undefined) body.source_name = sourceName
  if (sourceUrl !== undefined) body.source_url = sourceUrl
  if (webrtcUrl !== undefined) body.webrtc_url = webrtcUrl
  if (isActive !== undefined) body.is_active = isActive

  if (USE_MOCK) {
    await simulateLatency()
    const source = mockSources.find((s) => s.id === sourceId)
    if (!source) throw new Error('Source not found')
    Object.assign(source, {
      ...(sourceName !== undefined ? { source_name: sourceName } : {}),
      ...(sourceUrl !== undefined ? { source_url: sourceUrl } : {}),
      ...(webrtcUrl !== undefined ? { webrtc_url: webrtcUrl } : {}),
      ...(isActive !== undefined ? { is_active: isActive } : {}),
      updated_at: new Date().toISOString(),
    })
    return source
  }

  const res = await fetch(`${API_BASE}/sources/${sourceId}`, {
    method: 'PATCH',
    headers: { ...authHeaders(token), 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail ? JSON.stringify(data.detail) : 'Failed to update source')
  return data
}

// NOTE: API_BASE already includes '/api/v2' (see const above) — every
// other function in this file calls `${API_BASE}/sources/...`, not
// `${API_BASE}/api/v2/sources/...`. An earlier version of this function
// doubled the prefix, which 404'd against no matching route (FastAPI's
// generic "Not Found", not the backend's "Source not found." — those are
// two different failures; only the latter means the row is actually
// missing from Postgres).
export async function deleteSource(token, sourceId) {
  if (USE_MOCK) {
    await simulateLatency()
    const idx = mockSources.findIndex((s) => s.id === sourceId)
    if (idx !== -1) mockSources.splice(idx, 1)
    delete mockWorkerStatus[sourceId]
    return true
  }
  const res = await fetch(`${API_BASE}/sources/${sourceId}`, {
    method: 'DELETE',
    headers: authHeaders(token),
  })
  if (!res.ok && res.status !== 204) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail || `Failed to delete source (${res.status})`)
  }
  return true
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

// In-memory store of mock job "start times", so getAnprJob can simulate
// realistic progress (queued -> running -> completed) instead of
// completing instantly - this is what actually exercises the frontend's
// polling loop when USE_MOCK is on.
const mockJobStartTimes = {}
// USE_MOCK equivalent of the backend's _source_continuous_jobs — lets the
// mock path support the same "toggle overlay on/off per source" flow.
const mockActiveContinuousJobs = {}

export async function createAnprJob(token, sourceId) {
  if (USE_MOCK) {
    await simulateLatency()
    const id = `job-${Date.now()}`
    mockJobStartTimes[id] = Date.now()
    return { id, source_id: sourceId, status: 'queued', processed_frames: 0, events_found: 0 }
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

// Starts (or, if one's already live for this source, just returns) a
// LONG-RUNNING ANPR job that keeps publishing detection boxes to the live
// overlay until explicitly stopped — this is what backs a simple "ANPR
// overlay on/off" toggle button, as opposed to createAnprJob's one-shot
// batch run that stops itself at max_frames.
export async function startContinuousAnprJob(token, sourceId) {
  if (USE_MOCK) {
    await simulateLatency()
    const existing = mockActiveContinuousJobs[sourceId]
    if (existing) return existing
    const job = { id: `live-job-${Date.now()}`, source_id: sourceId, status: 'running', processed_frames: 0, events_found: 0 }
    mockActiveContinuousJobs[sourceId] = job
    return job
  }
  const res = await fetch(`${API_BASE}/anpr/jobs`, {
    method: 'POST',
    headers: { ...authHeaders(token), 'Content-Type': 'application/json' },
    body: JSON.stringify({ source_id: sourceId, continuous: true }),
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || 'Failed to start ANPR overlay')
  return data
}

export async function stopAnprJob(token, jobId) {
  if (USE_MOCK) {
    await simulateLatency()
    for (const [sourceId, job] of Object.entries(mockActiveContinuousJobs)) {
      if (job.id === jobId) delete mockActiveContinuousJobs[sourceId]
    }
    return true
  }
  const res = await fetch(`${API_BASE}/anpr/jobs/${jobId}/stop`, {
    method: 'POST',
    headers: authHeaders(token),
  })
  if (!res.ok) throw new Error('Failed to stop ANPR overlay')
  return res.json()
}

// Lets the toggle button recover its on/off state after a page refresh —
// returns the live job for this source, or null if the overlay isn't
// currently running there. Call this once when a source's tile mounts.
export async function getActiveAnprJob(token, sourceId) {
  if (USE_MOCK) {
    await simulateLatency()
    return mockActiveContinuousJobs[sourceId] || null
  }
  const res = await fetch(`${API_BASE}/anpr/sources/${sourceId}/active-job`, { headers: authHeaders(token) })
  if (!res.ok) throw new Error('Failed to fetch ANPR overlay status')
  return res.json()
}

export async function getAnprJob(token, jobId) {
  if (USE_MOCK) {
    await simulateLatency()
    const elapsed = Date.now() - (mockJobStartTimes[jobId] || Date.now())
    if (elapsed < 2000) {
      return { id: jobId, status: 'queued', processed_frames: 0, events_found: 0 }
    }
    if (elapsed < 6000) {
      return { id: jobId, status: 'running', processed_frames: Math.floor(elapsed / 200), events_found: Math.floor(elapsed / 2000) }
    }
    return { id: jobId, status: 'completed', processed_frames: 30, events_found: 3 }
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
