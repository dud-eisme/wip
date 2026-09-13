import { useEffect, useState } from 'react'
import VideoWall from './components/VideoWall'
import EventsTable from './components/EventsTable'
import RegisterSourceModal from './components/RegisterSourceModal'
import { login } from './api/auth'
import { getSources, getWorkersStatus, getEvents, getRegistryCameras, USE_MOCK } from './api/viewer'

export default function App() {
  const [view, setView] = useState('wall') // 'wall' | 'events'
  const [token, setToken] = useState(null)
  const [authError, setAuthError] = useState(null)

  const [sources, setSources] = useState([])
  const [workerStatuses, setWorkerStatuses] = useState({}) // { [sourceId]: status }
  const [cameraNameLookup, setCameraNameLookup] = useState({}) // { [cameraId]: camera_name }

  const [events, setEvents] = useState([])
  const [searchInput, setSearchInput] = useState('')
  const [debounced, setDebounced] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const [isRegisterOpen, setIsRegisterOpen] = useState(false)
  const [refreshTick, setRefreshTick] = useState(0)

  // --- Login (against Model 1 - Model 2 has no login of its own).
  // Skipped entirely in mock mode so the UI is fully previewable with no
  // backend running at all. ---
  useEffect(() => {
    if (USE_MOCK) {
      setToken('mock-token')
      return
    }
    login('user@example.com', 'password')
      .then(setToken)
      .catch((err) => setAuthError(err.message))
  }, [])

  // --- Registry camera names, for showing readable names instead of raw UUIDs ---
  useEffect(() => {
    if (!token) return
    getRegistryCameras(token).then((cams) => {
      const lookup = {}
      cams.forEach((c) => { lookup[c.id] = c.camera_name })
      setCameraNameLookup(lookup)
    })
  }, [token])

  // --- Sources + worker status ---
  useEffect(() => {
    if (!token) return
    getSources(token).then(setSources)
    getWorkersStatus(token).then((data) => {
      const byId = {}
      data.workers.forEach((w) => { byId[w.source_id] = w })
      setWorkerStatuses(byId)
    })
  }, [token, refreshTick])

  function handleWorkerChange(sourceId, status) {
    setWorkerStatuses((prev) => ({ ...prev, [sourceId]: status }))
  }

  // --- Events (debounced search) ---
  useEffect(() => {
    const timerId = setTimeout(() => setDebounced(searchInput), 300)
    return () => clearTimeout(timerId)
  }, [searchInput])

  useEffect(() => {
    if (!token) return
    setLoading(true)
    setError(null)
    getEvents(token, { plate: debounced })
      .then((data) => { setEvents(data); setLoading(false) })
      .catch((err) => { setError(err.message); setLoading(false) })
  }, [token, debounced])

  function handleEventChange(updatedEvent) {
    setEvents((prev) => prev.map((e) => (e.id === updatedEvent.id ? updatedEvent : e)))
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="topbar__title">
          Live Feed Relay &amp; ANPR
          <small>Model 2 · Gujarat Police Innovation Hackathon 2026</small>
        </div>

        <div className="tab-switch">
          <button className={view === 'wall' ? 'active' : ''} onClick={() => setView('wall')}>
            Video Wall
          </button>
          <button className={view === 'events' ? 'active' : ''} onClick={() => setView('events')}>
            Events
          </button>
        </div>

        {view === 'events' && (
          <div className="topbar__search">
            <input
              type="text"
              placeholder="Search by plate number…"
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
            />
          </div>
        )}

        {view === 'wall' && (
          <button className="btn btn--primary" onClick={() => setIsRegisterOpen(true)} style={{ marginLeft: 'auto' }}>
            Register Source
          </button>
        )}
      </header>

      <main className="main-area">
        {authError && (
          <div style={{ color: 'var(--status-offline)', fontSize: 13 }}>
            Login failed: {authError} — confirm Model 1 is running and the test account exists.
          </div>
        )}

        {view === 'wall' && (
          <>
            <p className="section-title">SOURCES ({sources.length})</p>
            <VideoWall
              sources={sources}
              workerStatuses={workerStatuses}
              token={token}
              onWorkerChange={handleWorkerChange}
            />
          </>
        )}

        {view === 'events' && (
          <EventsTable
            events={events}
            token={token}
            onEventChange={handleEventChange}
            cameraNameLookup={cameraNameLookup}
          />
        )}

        {loading && view === 'events' && (
          <p style={{ fontSize: 12, color: 'var(--ink-soft)' }}>Loading events…</p>
        )}
        {error && (
          <p style={{ fontSize: 12, color: 'var(--status-offline)' }}>Couldn't load events: {error}</p>
        )}
      </main>

      {isRegisterOpen && (
        <RegisterSourceModal
          token={token}
          onClose={() => setIsRegisterOpen(false)}
          onSuccess={() => setRefreshTick((t) => t + 1)}
        />
      )}
    </div>
  )
}
