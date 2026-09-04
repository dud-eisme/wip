import { useEffect, useState } from 'react'
import MapView from './components/MapView'
import FilterPanel from './components/FilterPanel'
import GapAnalysisPanel from './components/GapAnalysisPanel'
import CameraList from './components/CameraList'
import { getCameras, getGapAnalysis } from './api/registry'
import { login } from './api/auth'
import { exportCamerasToCsv } from './utils/exportCsv'
import OnboardCameraModal from './components/OnboardCameraModal'
import BulkUploadModal from './components/BulkUploadModal'

export default function App() {
  const [filters, setFilters] = useState({})
  const [searchInput, setSearchInput] = useState('')
  const [debounce, setDebounce] = useState('')
  const [cameras, setCameras] = useState([])
  const [selectedCamera, setSelectedCamera] = useState(null)
  const [gapData, setGapData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [token, setToken] = useState(null)
  const [authError, setAuthError] = useState(null)
  const [isOnboardOpen, setIsOnboardOpen] = useState(false)
  const [refreshTick, setRefreshTick] = useState(0)
  const [isBulkUploadOpen, setIsBulkUploadOpen] = useState(false)

  useEffect(() => {
    const timerId = setTimeout(() => {
      setDebounce(searchInput)
    }, 300)
    
    return () => {
      clearTimeout(timerId)
    }
  }, [searchInput])

  useEffect(() => {
    if (!token) return

    let active = true
    setLoading(true)
    setError(null)
    getCameras({ ...filters, search: debounce, token }).then((data) => {
      if (active) {
        setCameras(data)
        setLoading(false)
      }
    })
    .catch((err) => {
      if (active) {
        setError(err.message)
        setLoading(false)
      }
    })

    return () => { active = false }
  }, [filters, debounce, token, refreshTick])

  useEffect(() => {
    if (!token) return

    getGapAnalysis(token).then(setGapData)
  }, [token])

  useEffect(() => {
    login('user@example.com', 'stringst')
      .then(setToken)
      .catch((err) => setAuthError(err.message))
  }, [])

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="topbar__title">
          CCTV Registry &amp; GIS Foundation
          <small>Model 1 · Gujarat Police Innovation Hackathon 2026</small>
        </div>

        <div className="topbar__search">
          <input
            type="text"
            placeholder="Search cameras by name…"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
          />
        </div>

        <span>Showing {cameras.length} cameras</span>

        <div className="topbar__actions">
          <button className="btn" onClick={() => exportCamerasToCsv(cameras)}>
            Export CSV
          </button>

          <button className="btn" onClick={() => setIsBulkUploadOpen(true)}>
            Bulk Import
          </button>

          <button className="btn btn--primary" onClick={() => setIsOnboardOpen(true)}>
            Onboard camera
          </button>
        </div>
      </header>

      <div className="main-split">
        <aside className="sidebar">
          <FilterPanel filters={filters} onChange={setFilters} />
          <GapAnalysisPanel gapData={gapData} />
          <CameraList cameras={cameras} onSelect={setSelectedCamera} />
        </aside>

        <MapView cameras={cameras} selectedCamera={selectedCamera} loading={loading} />
      </div>

      {error && (
        <div style={{ position: 'fixed', bottom: 12, right: 12, fontSize: 12, color: 'var(--status-offline, #b13a2e)' }}>
          Couldn't load cameras: {error}
        </div>
      )}

      {loading && cameras.length === 0 && (
        <div style={{ position: 'fixed', bottom: 12, right: 12, fontSize: 12, color: 'var(--ink-soft)' }}>
          Loading cameras…
        </div>
      )}

      {isBulkUploadOpen && (
        <BulkUploadModal
          token={token}
          onClose={() => setIsBulkUploadOpen(false)}
          onSuccess={() => setRefreshTick((t) => t + 1)}
        />
      )}

      {isOnboardOpen && (
        <OnboardCameraModal
          token={token}
          onClose={() => setIsOnboardOpen(false)}
          onSuccess={() => setRefreshTick((t) => t + 1)}
        />
      )}
    </div>
  )
}
